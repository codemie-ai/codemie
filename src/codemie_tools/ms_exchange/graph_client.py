# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Microsoft Graph implementation of the read-only Exchange client.

Uses ``ClientSecretCredential`` (azure-identity) and the msgraph-sdk ``GraphServiceClient``
against ``/users/{mailbox}/...``. The async SDK is bridged from sync callers with the
loop-detect / worker-thread / ``asyncio.run`` pattern used in ``codemie.service.mcp.toolkit``.

Timeouts and retries (named constants, not env-configurable):
- ``REQUEST_TIMEOUT_SECONDS``: bound on one Graph call. It cannot interrupt token acquisition: the
  credential is synchronous and kiota calls ``get_token`` without yielding to the loop. The credential
  therefore carries its own explicit timeouts (``TOKEN_TIMEOUT_SECONDS``, ``TOKEN_RETRY_TOTAL``).
- Transient failures (429/5xx) are retried only by ``retry.call_with_retry`` (``MAX_RETRIES``
  attempts, ``Retry-After`` honoured and capped). kiota's own ``RetryHandler`` is disabled so the
  two layers do not multiply. Only reads are issued, so retries are safe.
Provider exception text is never propagated; errors go through ``map_graph_exception``.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Awaitable, Callable, TypeVar

from azure.identity import ClientSecretCredential
from kiota_authentication_azure.azure_identity_authentication_provider import AzureIdentityAuthenticationProvider
from kiota_http.middleware.options import RetryHandlerOption
from msgraph import GraphRequestAdapter, GraphServiceClient
from msgraph_core import GraphClientFactory
from msgraph.generated.users.item.mail_folders.item.child_folders.child_folders_request_builder import (
    ChildFoldersRequestBuilder,
)
from msgraph.generated.users.item.mail_folders.mail_folders_request_builder import MailFoldersRequestBuilder
from msgraph.generated.users.item.mail_folders.item.messages.messages_request_builder import (
    MessagesRequestBuilder,
)
from msgraph.generated.users.item.messages.item.message_item_request_builder import MessageItemRequestBuilder

from codemie_tools.ms_exchange.client_base import (
    MAX_PAGE,
    AttachmentDict,
    FolderDict,
    MessageDict,
)
from codemie_tools.ms_exchange.errors import ExchangeError, FolderNotFoundError, map_graph_exception
from codemie_tools.ms_exchange.models import ExchangeToolConfig
from codemie_tools.ms_exchange.retry import call_with_retry

T = TypeVar("T")

REQUEST_TIMEOUT_SECONDS = 60
# Per attempt, connect and read each. Two attempts stay well under REQUEST_TIMEOUT_SECONDS.
TOKEN_TIMEOUT_SECONDS = 10
TOKEN_RETRY_TOTAL = 1
PREFER_TEXT_BODY = 'outlook.body-content-type="text"'
GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]
INBOX = "inbox"
FILE_ATTACHMENT_TYPE = "#microsoft.graph.fileAttachment"
PAGE_SIZE = 250
_WELL_KNOWN_FOLDERS = frozenset(
    {
        "inbox",
        "drafts",
        "sentitems",
        "deleteditems",
        "junkemail",
        "archive",
        "outbox",
        "msgfolderroot",
        "clutter",
        "conflicts",
        "conversationhistory",
        "localfailures",
        "recoverableitemsdeletions",
        "scheduled",
        "searchfolders",
        "serverfailures",
        "syncissues",
    }
)
_SUMMARY_FIELDS = ["id", "subject", "from", "receivedDateTime", "isRead", "hasAttachments"]


def build_credential(config: ExchangeToolConfig) -> ClientSecretCredential:
    """Synchronous credential with bounded HTTP timeouts; not tied to an event loop, so it can be reused."""
    return ClientSecretCredential(
        tenant_id=config.oauth_tenant_id,
        client_id=config.oauth_client_id,
        client_secret=config.oauth_client_secret,
        connection_timeout=TOKEN_TIMEOUT_SECONDS,
        read_timeout=TOKEN_TIMEOUT_SECONDS,
        retry_total=TOKEN_RETRY_TOTAL,
    )


def build_graph_client(credential: ClientSecretCredential) -> GraphServiceClient:
    """Graph client without kiota's retry handler (``call_with_retry`` owns the retry policy)."""
    http_client = GraphClientFactory.create_with_default_middleware(
        options={RetryHandlerOption.get_key(): RetryHandlerOption(should_retry=False)}
    )
    auth_provider = AzureIdentityAuthenticationProvider(credential, scopes=GRAPH_SCOPES)
    return GraphServiceClient(request_adapter=GraphRequestAdapter(auth_provider, http_client))


def run_sync(coro_factory: Callable[[], Awaitable[T]]) -> T:
    """Run an async call from sync code, with or without a running event loop."""

    async def _bounded() -> T:
        return await asyncio.wait_for(coro_factory(), timeout=REQUEST_TIMEOUT_SECONDS)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_bounded())
    # Inside a running loop: offload to a thread, where asyncio.run() is safe.
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(_bounded())).result(timeout=REQUEST_TIMEOUT_SECONDS + 5)


def _address(recipient: Any) -> str:
    addr = getattr(recipient, "email_address", None)
    if addr is None:
        return ""
    name, address = getattr(addr, "name", None), getattr(addr, "address", None) or ""
    return f"{name} <{address}>" if name else address


def _summary(msg: Any) -> MessageDict:
    received = getattr(msg, "received_date_time", None)
    return {
        "id": msg.id,
        "subject": msg.subject or "",
        "from": _address(getattr(msg, "from_", None)),
        "received": received.isoformat() if received else None,
        "is_read": bool(msg.is_read),
        "has_attachments": bool(msg.has_attachments),
    }


def _text_headers(cfg: Any) -> Any:
    cfg.headers.add("Prefer", PREFER_TEXT_BODY)
    return cfg


class GraphExchangeClient:
    """The ``GraphServiceClient`` is built inside each call's own event loop; the credential is shared.

    Every ``_call`` (and every retry) runs ``asyncio.run`` on a fresh loop, and the SDK's HTTP resources are
    bound to the loop that created them, so no async client is shared between calls. The synchronous
    credential is not loop-bound: it is built once so its token cache survives across calls and retries.
    """

    def __init__(self, config: ExchangeToolConfig) -> None:
        self._mailbox = config.mailbox
        self._credential = build_credential(config)

    def _call(self, op: Callable[[Any], Awaitable[T]]) -> T:
        """Run ``op(user_builder)`` on a fresh loop with a freshly built client."""

        async def _run() -> T:
            graph = build_graph_client(self._credential)
            return await op(graph.users.by_user_id(self._mailbox))

        try:
            return call_with_retry(lambda: run_sync(_run))
        except ExchangeError:
            raise
        except Exception as exc:  # noqa: BLE001 - sanitised below
            raise map_graph_exception(exc) from None

    @staticmethod
    async def _collect(builder: Any, cfg: Any | None = None) -> list[Any]:
        """All items of a paged collection, following ``@odata.nextLink``; a None response is empty."""
        resp = await builder.get(request_configuration=cfg) if cfg is not None else await builder.get()
        items: list[Any] = []
        while resp is not None:
            items.extend(resp.value or [])
            next_link = getattr(resp, "odata_next_link", None)
            if not next_link:
                break
            resp = await builder.with_url(next_link).get()
        return items

    @staticmethod
    def _folder_dict(f: Any) -> FolderDict:
        return {
            "id": f.id,
            "name": f.display_name or "",
            "total_count": f.total_item_count or 0,
            "unread_count": f.unread_item_count or 0,
        }

    def list_folders(self) -> list[FolderDict]:
        async def op(user: Any) -> list[FolderDict]:
            out: list[FolderDict] = []
            params = MailFoldersRequestBuilder.MailFoldersRequestBuilderGetQueryParameters(top=PAGE_SIZE)
            pending = await self._collect(
                user.mail_folders,
                MailFoldersRequestBuilder.MailFoldersRequestBuilderGetRequestConfiguration(query_parameters=params),
            )
            while pending:
                folder = pending.pop(0)
                out.append(self._folder_dict(folder))
                if getattr(folder, "child_folder_count", 0):
                    child_params = ChildFoldersRequestBuilder.ChildFoldersRequestBuilderGetQueryParameters(
                        top=PAGE_SIZE
                    )
                    children = user.mail_folders.by_mail_folder_id(folder.id).child_folders
                    pending[0:0] = await self._collect(
                        children,
                        ChildFoldersRequestBuilder.ChildFoldersRequestBuilderGetRequestConfiguration(
                            query_parameters=child_params
                        ),
                    )
            return out

        return self._call(op)

    def _resolve_folder(self, folder: str) -> str:
        name = (folder or "").strip()
        if not name:
            raise FolderNotFoundError
        if name.lower() in _WELL_KNOWN_FOLDERS:
            return name.lower()
        for candidate in self.list_folders():
            if name == candidate["id"] or name.lower() == candidate["name"].lower():
                return candidate["id"]
        raise FolderNotFoundError

    def _folder_messages(self, folder: str, top: int, skip: int, select: list[str], order: bool) -> list[Any]:
        folder_id = self._resolve_folder(folder)
        params = MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters(top=top, skip=skip, select=select)
        if order:
            params.orderby = ["receivedDateTime desc"]  # Graph may reject `id` in $orderby
        cfg = _text_headers(
            MessagesRequestBuilder.MessagesRequestBuilderGetRequestConfiguration(query_parameters=params)
        )

        async def op(user: Any) -> Any:
            return await user.mail_folders.by_mail_folder_id(folder_id).messages.get(request_configuration=cfg)

        resp = self._call(op)
        return list(resp.value or []) if resp else []

    def list_messages(self, folder: str, start: int, count: int) -> list[MessageDict]:
        top = max(1, min(count, MAX_PAGE))
        msgs = self._folder_messages(folder, top, max(0, start), _SUMMARY_FIELDS, order=True)
        summaries = [_summary(m) for m in msgs]
        # Stable two-pass sort: id ascending, then received descending (missing dates last).
        summaries.sort(key=lambda m: m["id"])
        summaries.sort(key=lambda m: m["received"] or "", reverse=True)
        return summaries

    def check_inbox(self) -> None:
        self._folder_messages(INBOX, 1, 0, ["id"], order=False)

    def _get_with_text_body(self, message_id: str, select: list[str]) -> Any:
        params = MessageItemRequestBuilder.MessageItemRequestBuilderGetQueryParameters(select=select)
        cfg = _text_headers(
            MessageItemRequestBuilder.MessageItemRequestBuilderGetRequestConfiguration(query_parameters=params)
        )

        async def op(user: Any) -> Any:
            return await user.messages.by_message_id(message_id).get(request_configuration=cfg)

        return self._call(op)

    def get_message(self, message_id: str) -> MessageDict:
        msg = self._get_with_text_body(message_id, _SUMMARY_FIELDS + ["toRecipients", "ccRecipients", "body"])
        result = _summary(msg)
        result["to"] = [_address(r) for r in (msg.to_recipients or [])]
        result["cc"] = [_address(r) for r in (msg.cc_recipients or [])]
        result["body"] = getattr(msg.body, "content", None) or ""
        return result

    def get_text_body(self, message_id: str) -> str:
        msg = self._get_with_text_body(message_id, ["id", "body"])
        return getattr(msg.body, "content", None) or ""

    def list_attachments(self, message_id: str) -> list[AttachmentDict]:
        async def op(user: Any) -> list[Any]:
            return await self._collect(user.messages.by_message_id(message_id).attachments)

        return [
            {
                "id": a.id,
                "name": a.name or "",
                "content_type": a.content_type or "",
                "size": a.size or 0,
                "is_file": a.odata_type == FILE_ATTACHMENT_TYPE,
            }
            for a in self._call(op)
        ]

    def get_attachment_bytes(self, message_id: str, attachment_id: str) -> bytes:
        async def op(user: Any) -> Any:
            return await user.messages.by_message_id(message_id).attachments.by_attachment_id(attachment_id).get()

        att = self._call(op)
        return bytes(getattr(att, "content_bytes", None) or b"")
