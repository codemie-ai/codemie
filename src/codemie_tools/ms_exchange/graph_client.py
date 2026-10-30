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
"""Microsoft Graph implementation of the Exchange client (reads and writes).

Uses ``ClientSecretCredential`` (azure-identity) and the msgraph-sdk ``GraphServiceClient``
against ``/users/{mailbox}/...``. The async SDK is bridged from sync callers with the
loop-detect / worker-thread / ``asyncio.run`` pattern used in ``codemie.service.mcp.toolkit``.

Timeouts and retries (named constants, not env-configurable):
- ``REQUEST_TIMEOUT_SECONDS``: bound on one Graph call. It cannot interrupt token acquisition: the
  credential is synchronous and kiota calls ``get_token`` without yielding to the loop. The credential
  therefore carries its own explicit timeouts (``TOKEN_TIMEOUT_SECONDS``, ``TOKEN_RETRY_TOTAL``).
- Reads: transient failures (429/5xx) are retried only by ``retry.call_with_retry`` (``MAX_RETRIES``
  attempts, ``Retry-After`` honoured and capped). kiota's own ``RetryHandler`` is disabled so the
  two layers do not multiply. Reads are idempotent, so retries are safe (writes: see below).
Provider exception text is never propagated; errors go through ``map_graph_exception``.

Writes (``send``, ``forward``, ``move``, ``delete``, ``tag``) never use ``_call``/``call_with_retry``. A write call creates one 90 s
``Deadline``, acquires the token explicitly under it (``_start_write``), reads inside the call are single
attempts (``_inner_read``), and the mutating request goes through ``write_retry.run_write``
(``_submit_write``), the only retrier for writes: at most 3 submissions, only after a proven 429.
Errors map through ``map_graph_write_exception`` into Not applied / Unknown outcomes.

Tag sends the message etag as ``If-Match`` and handles a 412 defensively (one reread and recompute, then
``CategoryConflictError``). Whether Graph enforces ``If-Match`` on messages is unverified: no concurrency
guarantee is claimed for the Graph transport.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TypeVar

from azure.identity import ClientSecretCredential
from kiota_authentication_azure.azure_identity_authentication_provider import AzureIdentityAuthenticationProvider
from kiota_http.middleware.options import RetryHandlerOption
from msgraph import GraphRequestAdapter, GraphServiceClient
from msgraph_core import GraphClientFactory
from msgraph.generated.models.body_type import BodyType
from msgraph.generated.models.email_address import EmailAddress
from msgraph.generated.models.item_body import ItemBody
from msgraph.generated.models.message import Message as GraphMessage
from msgraph.generated.models.recipient import Recipient
from msgraph.generated.users.item.mail_folders.item.child_folders.child_folders_request_builder import (
    ChildFoldersRequestBuilder,
)
from msgraph.generated.users.item.mail_folders.mail_folders_request_builder import MailFoldersRequestBuilder
from msgraph.generated.users.item.mail_folders.item.messages.messages_request_builder import (
    MessagesRequestBuilder,
)
from msgraph.generated.users.item.mail_folders.item.mail_folder_item_request_builder import (
    MailFolderItemRequestBuilder,
)
from msgraph.generated.users.item.messages.item.forward.forward_post_request_body import ForwardPostRequestBody
from msgraph.generated.users.item.messages.item.message_item_request_builder import MessageItemRequestBuilder
from msgraph.generated.users.item.messages.item.move.move_post_request_body import MovePostRequestBody
from msgraph.generated.users.item.send_mail.send_mail_post_request_body import SendMailPostRequestBody

from codemie_tools.ms_exchange.client_base import (
    DELETED,
    DELETED_ITEMS,
    MAX_PAGE,
    MOVED,
    SUBMITTED,
    TAGGED,
    AttachmentDict,
    FolderDict,
    MessageDict,
)
from codemie_tools.ms_exchange.errors import (
    DeadlineExhaustedError,
    ExchangeError,
    FolderNotFoundError,
    InvalidLocalInputError,
    UnknownOutcomeError,
    WriteFolderNotFoundError,
    graph_status,
    map_graph_exception,
    map_graph_write_exception,
)
from codemie_tools.ms_exchange.models import ExchangeToolConfig
from codemie_tools.ms_exchange.retry import call_with_retry
from codemie_tools.ms_exchange.write_retry import Classify, Deadline, Retry, Stop, run_write
from codemie_tools.ms_exchange.write_validation import (
    BODY_HTML,
    validate_category,
    validate_folder_name,
    validate_message_id,
    validate_recipients,
    validate_send_input,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

REQUEST_TIMEOUT_SECONDS = 60
# Per attempt, connect and read each. Two attempts stay well under REQUEST_TIMEOUT_SECONDS.
TOKEN_TIMEOUT_SECONDS = 10
TOKEN_RETRY_TOTAL = 1
# Extra seconds the outer thread wait allows beyond a read's own timeout; a write waits for exactly its timeout.
_THREAD_GRACE_SECONDS = 5
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_NOT_FOUND = 404
_HTTP_PRECONDITION_FAILED = 412
_ETAG_KEY = "@odata.etag"
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


def run_sync(
    coro_factory: Callable[[], Awaitable[T]],
    timeout: float | None = None,
    *,
    grace: float = _THREAD_GRACE_SECONDS,
) -> T:
    """Run an async call from sync code, with or without a running event loop.

    ``timeout`` bounds the whole call (default ``REQUEST_TIMEOUT_SECONDS``). Reads keep ``grace`` extra
    seconds on the outer thread wait; the write path passes ``grace=0`` so nothing outlives its timeout.
    """
    limit = REQUEST_TIMEOUT_SECONDS if timeout is None else timeout

    async def _bounded() -> T:
        return await asyncio.wait_for(coro_factory(), timeout=limit)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_bounded())
    # Inside a running loop: offload to a thread, where asyncio.run() is safe.
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(_bounded())).result(timeout=limit + grace)


def _retry_after_seconds(exc: BaseException) -> float | None:
    """The server's ``Retry-After`` in seconds; None when absent or not a number (HTTP-date is not used)."""
    headers = getattr(exc, "response_headers", None) or {}
    for key, value in dict(headers).items():
        if str(key).lower() == "retry-after":
            if isinstance(value, (list, tuple, set)):
                value = next(iter(value), None)
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _write_classifier(op: str) -> Classify:
    """Retry only a Graph 429 (proven no mutation); everything else is final and mapped."""

    def classify(exc: BaseException) -> Retry | Stop:
        if not isinstance(exc, ExchangeError) and graph_status(exc) == _HTTP_TOO_MANY_REQUESTS:
            return Retry(_retry_after_seconds(exc))
        return Stop(map_graph_write_exception(exc, op))

    return classify


@dataclass
class _TagFlow:
    """State of one tag call, shared by its attempt and its classifier (reset by each attempt)."""

    patching: bool = False
    conflicted: bool = False


def _tag_classifier(flow: _TagFlow) -> Classify:
    """The write classifier plus the tag rules: read failures are Not applied; the first 412 is retried once.

    The 412 retry is decided here, before mapping, because the mapper turns a 412 on ``tag`` into
    ``CategoryConflictError`` at once. Retrying runs the whole attempt again (reread, recompute), inside the
    same 3-submission budget and deadline. A 429 on the read is also retried (nothing was changed).
    """
    base = _write_classifier("tag")

    def classify(exc: BaseException) -> Retry | Stop:
        if isinstance(exc, ExchangeError):
            return base(exc)
        status = graph_status(exc)
        if not flow.patching:
            return (
                base(exc)
                if status == _HTTP_TOO_MANY_REQUESTS
                else Stop(map_graph_write_exception(exc, "tag", before_mutation=True))
            )
        if status == _HTTP_PRECONDITION_FAILED and not flow.conflicted:
            flow.conflicted = True
            return Retry(0.0, on_exhausted=map_graph_write_exception(exc, "tag"))
        return base(exc)

    return classify


def _recipients(addresses: list[str]) -> list[Recipient]:
    return [Recipient(email_address=EmailAddress(address=a)) for a in addresses]


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

    def __init__(
        self,
        config: ExchangeToolConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rand: Callable[[float, float], float] = random.uniform,
    ) -> None:
        """``clock``, ``sleep`` and ``rand`` are the write owner's injectable time and jitter sources."""
        self._mailbox = config.mailbox
        self._credential = build_credential(config)
        self._clock = clock
        self._sleep = sleep
        self._rand = rand

    def _bound(self, op: Callable[[Any], Awaitable[T]]) -> Callable[[], Awaitable[T]]:
        """A coroutine factory running ``op(user_builder)`` with a freshly built, loop-bound client."""

        async def _run() -> T:
            graph = build_graph_client(self._credential)
            return await op(graph.users.by_user_id(self._mailbox))

        return _run

    def _call(self, op: Callable[[Any], Awaitable[T]]) -> T:
        """Run ``op(user_builder)`` on a fresh loop with a freshly built client."""
        run = self._bound(op)
        try:
            return call_with_retry(lambda: run_sync(run))
        except ExchangeError:
            raise
        except Exception as exc:  # noqa: BLE001 - sanitised below
            raise map_graph_exception(exc) from None

    # ---- write plumbing: one Deadline per tool call, explicit token, single-attempt reads, one retry owner

    def _start_write(self, op: str) -> Deadline:
        """Create the call's Deadline and acquire the token under it; a token failure is Not applied.

        The token is requested with the CAE flag kiota's auth provider uses, so the write that follows
        finds it cached and performs no second, uninterruptible token request.
        """
        deadline = Deadline(clock=self._clock)
        try:
            self._credential.get_token(*GRAPH_SCOPES, enable_cae=True)
        except Exception as exc:  # noqa: BLE001 - sanitised below
            raise map_graph_write_exception(exc, op, before_mutation=True) from None
        return deadline

    def _attempt_timeout(self, deadline: Deadline) -> float:
        timeout = min(REQUEST_TIMEOUT_SECONDS, deadline.remaining())
        if timeout <= 0:
            raise DeadlineExhaustedError
        return timeout

    def _inner_read(
        self,
        op: str,
        deadline: Deadline,
        read: Callable[[Any], Awaitable[T]],
        *,
        before_mutation: bool = True,
    ) -> T:
        """One read attempt inside a write call (folder lookup, pre-read, reconciliation); never retried.

        Runs ``read(user_builder)`` under ``min(60, remaining)``. With ``before_mutation=True`` any failure is
        Not applied (429 gets rate-limit advice); with ``False`` (reconciliation) it stays Unknown.

        A raw ``ExchangeError`` raised by ``read`` itself (not just a provider exception) is routed through
        the mapper too, not re-raised bare: an outcome-less one (a read error) must still end up Not applied
        or Unknown, never leave the caller with no outcome at all.
        """
        try:
            try:
                timeout = self._attempt_timeout(deadline)
            except DeadlineExhaustedError:
                if before_mutation:
                    raise
                raise map_graph_write_exception(TimeoutError(), op) from None
            return run_sync(self._bound(read), timeout=timeout, grace=0)
        except DeadlineExhaustedError:
            raise
        except Exception as exc:  # noqa: BLE001 - sanitised below
            raise map_graph_write_exception(exc, op, before_mutation=before_mutation) from None

    def _submit_write(
        self,
        op: str,
        deadline: Deadline,
        attempt: Callable[[Any], Awaitable[T]],
        classify: Classify | None = None,
    ) -> T:
        """Submit the mutating request through the write owner (``run_write``).

        ``attempt(user_builder)`` is awaited once per submission, under ``min(60, remaining)``, with a fresh
        client. By default a Graph 429 is retried (server ``Retry-After`` honoured) and any other failure is
        final; ``classify`` replaces that rule for an op with its own (tag).
        """

        def submit() -> T:
            return run_sync(self._bound(attempt), timeout=self._attempt_timeout(deadline), grace=0)

        return run_write(submit, classify or _write_classifier(op), deadline, sleep=self._sleep, rand=self._rand)

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

    async def _folders_op(self, user: Any) -> list[FolderDict]:
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
                child_params = ChildFoldersRequestBuilder.ChildFoldersRequestBuilderGetQueryParameters(top=PAGE_SIZE)
                children = user.mail_folders.by_mail_folder_id(folder.id).child_folders
                pending[0:0] = await self._collect(
                    children,
                    ChildFoldersRequestBuilder.ChildFoldersRequestBuilderGetRequestConfiguration(
                        query_parameters=child_params
                    ),
                )
        return out

    def list_folders(self) -> list[FolderDict]:
        return self._call(self._folders_op)

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

    # ---- writes

    def send(self, to: list[str], subject: str, body: str, body_type: str = "text") -> dict[str, Any]:
        """Send a new message from the mailbox (``sendMail``, copy saved to Sent Items).

        Success means Graph accepted the submission, not delivery. ``html`` is passed unsanitised.
        """
        recipients, kind = validate_send_input(to, subject, body, body_type)
        message = GraphMessage(
            subject=subject,
            body=ItemBody(content_type=BodyType.Html if kind == BODY_HTML else BodyType.Text, content=body),
            to_recipients=_recipients(recipients),
        )
        request = SendMailPostRequestBody(message=message, save_to_sent_items=True)

        async def attempt(user: Any) -> None:
            await user.send_mail.post(request)

        deadline = self._start_write("send")
        self._submit_write("send", deadline, attempt)
        return {"status": SUBMITTED, "to": recipients, "subject": subject}

    def forward(self, message_id: str, to: list[str]) -> dict[str, Any]:
        """Forward a message to ``to`` (no comment). Success means Graph accepted the submission."""
        message_id = validate_message_id(message_id)
        recipients = validate_recipients(to)
        request = ForwardPostRequestBody(to_recipients=_recipients(recipients))

        async def attempt(user: Any) -> None:
            await user.messages.by_message_id(message_id).forward.post(request)

        deadline = self._start_write("forward")
        self._submit_write("forward", deadline, attempt)
        return {"status": SUBMITTED, "message_id": message_id, "to": recipients}

    # ---- move, delete, tag

    async def _target_folder_id(self, user: Any, folder: str) -> str:
        """The real id of ``folder`` (well-known name, display name or id), inside one inner read.

        Well-known names are looked up (a message's ``parentFolderId`` is an id, never an alias). Anything
        unknown, including a 404 for the folder, is ``WriteFolderNotFoundError`` (Not applied).
        """
        name = folder.lower()
        if name in _WELL_KNOWN_FOLDERS:
            cfg = MailFolderItemRequestBuilder.MailFolderItemRequestBuilderGetRequestConfiguration(
                query_parameters=MailFolderItemRequestBuilder.MailFolderItemRequestBuilderGetQueryParameters(
                    select=["id"]
                )
            )
            try:
                found = await user.mail_folders.by_mail_folder_id(name).get(request_configuration=cfg)
            except ExchangeError:
                raise
            except Exception as exc:  # noqa: BLE001 - only a 404 is a folder verdict
                if graph_status(exc) == _HTTP_NOT_FOUND:
                    raise WriteFolderNotFoundError from None
                raise
            if not getattr(found, "id", None):
                raise WriteFolderNotFoundError
            return found.id
        for candidate in await self._folders_op(user):
            if folder == candidate["id"] or name == candidate["name"].lower():
                return candidate["id"]
        raise WriteFolderNotFoundError

    @staticmethod
    async def _parent_folder_id(user: Any, message_id: str) -> tuple[str | None, str | None]:
        """``(id, parentFolderId)`` of a message, from a minimal ``$select`` read."""
        cfg = MessageItemRequestBuilder.MessageItemRequestBuilderGetRequestConfiguration(
            query_parameters=MessageItemRequestBuilder.MessageItemRequestBuilderGetQueryParameters(
                select=["id", "parentFolderId"]
            )
        )
        msg = await user.messages.by_message_id(message_id).get(request_configuration=cfg)
        return getattr(msg, "id", None), getattr(msg, "parent_folder_id", None)

    def _reconcile_move(self, op: str, deadline: Deadline, message_id: str, target_id: str) -> str | None:
        """After an Unknown move: the message id if the SAME id is now in the target, else None.

        A failed read, a spent deadline or a message that is gone (Graph ids usually change on a move) all
        leave the outcome Unknown; the caller then re-raises the original error.
        """

        async def read(user: Any) -> tuple[str | None, str | None]:
            return await self._parent_folder_id(user, message_id)

        try:
            found_id, parent = self._inner_read(op, deadline, read, before_mutation=False)
        except ExchangeError as exc:
            logger.warning(
                "Exchange write reconciliation read failed: op=%s exc_type=%s outcome=%s",
                op,
                type(exc).__name__,
                exc.OUTCOME,
            )
            return None
        return (found_id or message_id) if parent == target_id else None

    def _move_message(self, op: str, message_id: str, target_folder: str) -> tuple[str | None, bool]:
        """Move a message; returns ``(message_id after the move, already_done)``. Inputs are validated."""
        deadline = self._start_write(op)

        async def pre_read(user: Any) -> tuple[str, str | None]:
            target_id = await self._target_folder_id(user, target_folder)
            _, parent = await self._parent_folder_id(user, message_id)
            return target_id, parent

        target_id, parent = self._inner_read(op, deadline, pre_read)
        if parent == target_id:
            return message_id, True
        request = MovePostRequestBody(destination_id=target_id)

        async def attempt(user: Any) -> Any:
            return await user.messages.by_message_id(message_id).move.post(request)

        try:
            moved = self._submit_write(op, deadline, attempt)
        except UnknownOutcomeError:
            found = self._reconcile_move(op, deadline, message_id, target_id)
            if found is None:
                raise
            return found, False
        return getattr(moved, "id", None), False

    def move(self, message_id: str, target_folder: str) -> dict[str, Any]:
        """Move a message to ``target_folder`` (a well-known name, display name or folder id).

        Returns ``{"status": "moved", "message_id": <new id>, "target_folder": <as given>}``; a message that
        already is in the target returns the same dict plus ``"already_done": True`` with its current id and
        issues no write. After an unknown outcome the same id is re-read: found in the target is success,
        anything else stays Unknown (Graph ids usually change on a move, so a lost reply often ends Unknown).
        """
        message_id = validate_message_id(message_id)
        folder = validate_folder_name(target_folder)
        new_id, already = self._move_message("move", message_id, folder)
        result: dict[str, Any] = {"status": MOVED, "message_id": new_id, "target_folder": target_folder}
        if already:
            result["already_done"] = True
        return result

    def delete(self, message_id: str, soft: bool = True) -> dict[str, Any]:
        """Delete a message. ``soft`` (default) moves it to Deleted Items; ``soft=False`` is permanent.

        Soft: a message already in Deleted Items returns ``already_done`` without any write, and an unknown
        outcome never escalates to a permanent delete. Hard: ``permanentDelete`` after a minimal pre-read (a missing id is
        Not applied); a not-found on the delete itself stays Unknown (a missing item is not proof that this call
        deleted it). Retention or hold may
        preserve a permanently deleted item. Returns ``{"status": "deleted", "soft": bool, "message_id": id}``
        (soft: the id after the move) plus ``"already_done": True`` when nothing had to be done.
        """
        message_id = validate_message_id(message_id)
        if not isinstance(soft, bool):
            raise InvalidLocalInputError
        if soft:
            new_id, already = self._move_message("delete", message_id, DELETED_ITEMS)
            result: dict[str, Any] = {"status": DELETED, "soft": True, "message_id": new_id}
            if already:
                result["already_done"] = True
            return result

        async def attempt(user: Any) -> None:
            await user.messages.by_message_id(message_id).permanent_delete.post()

        async def pre_read(user: Any) -> tuple[str | None, str | None]:
            return await self._parent_folder_id(user, message_id)

        deadline = self._start_write("delete")
        # A missing or malformed id is Not applied (as on EWS), not an Unknown outcome of the delete itself.
        self._inner_read("delete", deadline, pre_read)
        self._submit_write("delete", deadline, attempt)
        return {"status": DELETED, "soft": False, "message_id": message_id}

    def tag(self, message_id: str, category: str) -> dict[str, Any]:
        """Add one category to a message, keeping the existing ones (additive union, order preserved).

        The category is compared case-insensitively. Present already: ``already_done`` and no write. Otherwise
        the full union is PATCHed with the message etag as ``If-Match`` (no header if Graph returned no etag).
        Whether Graph enforces ``If-Match`` on messages is UNVERIFIED, so no concurrency guarantee is claimed.
        A 412 triggers one reread and recompute inside the same deadline and the same 3-submission budget; a
        second 412 raises ``CategoryConflictError`` (Not applied). The whole attempt (read, then PATCH) is one
        submission for the write owner. Returns ``{"status": "tagged", "message_id": id, "categories": [...]}``
        plus ``"already_done": True`` when the category was already there.
        """
        message_id = validate_message_id(message_id)
        category = validate_category(category)
        flow = _TagFlow()
        read_cfg = MessageItemRequestBuilder.MessageItemRequestBuilderGetRequestConfiguration(
            query_parameters=MessageItemRequestBuilder.MessageItemRequestBuilderGetQueryParameters(
                select=["id", "categories"]
            )
        )

        async def attempt(user: Any) -> tuple[list[str], bool]:
            flow.patching = False
            item = user.messages.by_message_id(message_id)
            current = await item.get(request_configuration=read_cfg)
            existing = list(getattr(current, "categories", None) or [])
            if category.casefold() in {c.casefold() for c in existing}:
                return existing, True
            union = [*existing, category]
            patch_cfg = MessageItemRequestBuilder.MessageItemRequestBuilderPatchRequestConfiguration()
            etag = (getattr(current, "additional_data", None) or {}).get(_ETAG_KEY)
            if etag:
                patch_cfg.headers.try_add("If-Match", etag)
            flow.patching = True
            await item.patch(GraphMessage(categories=union), request_configuration=patch_cfg)
            return union, False

        deadline = self._start_write("tag")

        categories, already = self._submit_write("tag", deadline, attempt, _tag_classifier(flow))
        result: dict[str, Any] = {"status": TAGGED, "message_id": message_id, "categories": categories}
        if already:
            result["already_done"] = True
        return result
