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
"""EWS (NTLM) implementation of the read-only Exchange client.

The service account authenticates via NTLM against the explicit EWS endpoint; the target
mailbox is a distinct identity reached with DELEGATE access. Autodiscover is never used.
Only reads are issued: nothing is marked read, moved, sent or deleted.

Timeouts and retries (named constants):
- ``REQUEST_TIMEOUT_SECONDS``: HTTP timeout on the exchangelib connection.
- Transient failures are retried by ``retry.call_with_retry``; TLS, credential and config
  errors are never retried. Provider text is never propagated (see ``map_ews_exception``).

Ordering note: EWS sorts server-side by ``datetime_received`` descending; ties inside the
returned window are ordered by id. ``id`` is not a sortable EWS field, so ties that straddle
a window boundary are not guaranteed a stable split.
"""

from __future__ import annotations

import concurrent.futures
import html
import ipaddress
import os
import re
import socket
from typing import Any, Callable, TypeVar
from urllib.parse import urlparse

from exchangelib import DELEGATE, NTLM, Account, Configuration, Credentials, FileAttachment
from exchangelib import errors as ews

from codemie_tools.ms_exchange.client_base import (
    MAX_PAGE,
    AttachmentDict,
    FolderDict,
    MessageDict,
)
from codemie_tools.ms_exchange.errors import (
    BadEndpointError,
    DelegateAccessError,
    ExchangeError,
    FolderNotFoundError,
    MessageNotFoundError,
    UnreachableError,
    map_ews_exception,
)
from codemie_tools.ms_exchange.models import ExchangeToolConfig
from codemie_tools.ms_exchange.retry import call_with_retry

T = TypeVar("T")
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

REQUEST_TIMEOUT_SECONDS = 60
# Operator setting, never integration input: comma-separated host names and/or CIDRs whose
# private addresses an EWS endpoint may use (on-prem Exchange). Empty => public addresses only.
ALLOWED_HOSTS_ENV = "EXCHANGE_EWS_ALLOWED_HOSTS"
_DNS_TIMEOUT_SECONDS = 5
INBOX = "inbox"
MAIL_CONTAINER_CLASS = "IPF.Note"
# Graph well-known folder names (lower case) -> exchangelib Account attribute, so both transports accept
# the same names. ``inbox`` is handled separately (see ``_inbox``).
_WELL_KNOWN_FOLDERS = {
    "drafts": "drafts",
    "sentitems": "sent",
    "deleteditems": "trash",
    "junkemail": "junk",
    "outbox": "outbox",
    "msgfolderroot": "msg_folder_root",
    "conflicts": "conflicts",
    "conversationhistory": "conversation_history",
    "localfailures": "local_failures",
    "recoverableitemsdeletions": "recoverable_items_deletions",
    "searchfolders": "search_folders",
    "serverfailures": "server_failures",
    "syncissues": "sync_issues",
}
_RECEIVED_DESC = "-datetime_received"
_ID_ONLY = ("id",)
_SUMMARY_FIELDS = ("id", "subject", "sender", "datetime_received", "is_read", "has_attachments")
_NON_TEXT_BLOCKS = re.compile(r"<(script|style)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")
_HOST = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?|[0-9A-Fa-f:.]+")


def _ip(text: str) -> IPAddress | None:
    try:
        address = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _resolve(name: str) -> list[IPAddress]:
    """A and AAAA records of ``name``; unresolvable, empty or slow lookups are unreachable."""
    # getaddrinfo has no timeout of its own; shutdown(wait=False) so a slow lookup cannot block us.
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        infos = executor.submit(socket.getaddrinfo, name, None).result(timeout=_DNS_TIMEOUT_SECONDS)
    except (OSError, UnicodeError, concurrent.futures.TimeoutError):
        raise UnreachableError from None
    finally:
        executor.shutdown(wait=False)
    addresses = [address for *_, sockaddr in infos if (address := _ip(str(sockaddr[0]))) is not None]
    if not addresses:
        raise UnreachableError
    return addresses


def _allowlist() -> tuple[set[str], list[ipaddress.IPv4Network | ipaddress.IPv6Network]]:
    names, networks = set(), []
    for entry in os.getenv(ALLOWED_HOSTS_ENV, "").split(","):
        entry = entry.strip().strip("[]").rstrip(".").lower()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            names.add(entry)
    return names, networks


def _check_host(host: str) -> None:
    """Reject endpoints that reach the backend's own network.

    Loopback, link-local (cloud metadata), unspecified and multicast addresses are never allowed.
    Any other non-global address (RFC 1918, ULA, CGNAT, reserved) is allowed only when the host
    name or the address is in ``EXCHANGE_EWS_ALLOWED_HOSTS``, so on-prem Exchange is opted into
    on purpose. Every resolved address must pass, not just one.

    Residual risk: the name is resolved here and again by exchangelib when it connects, so a
    DNS-rebinding host can still switch addresses in between. Errors stay fixed sentences, so the
    remaining exposure is a blind, https-only reachability probe.
    """
    name = host.strip("[]").rstrip(".").lower()
    if name == "localhost" or name.endswith(".localhost"):
        raise BadEndpointError
    literal = _ip(name)
    addresses = [literal] if literal is not None else _resolve(name)
    allowed_names, allowed_networks = _allowlist()
    for address in addresses:
        if address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast:
            raise BadEndpointError
        if address.is_global or name in allowed_names:
            continue
        if not any(address in network for network in allowed_networks if network.version == address.version):
            raise BadEndpointError


def _endpoint(raw: str) -> dict[str, str]:
    """Validate the configured EWS endpoint: https only (or a bare host, which exchangelib serves over https)."""
    url = (raw or "").strip()
    if not url:
        raise BadEndpointError
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        host = parsed.hostname
        parsed.port  # noqa: B018 - raises ValueError on an invalid port
    except ValueError:
        raise BadEndpointError from None
    if parsed.scheme != "https" or not host or not _HOST.fullmatch(host) or parsed.username or parsed.password:
        raise BadEndpointError
    if "://" not in url and (parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise BadEndpointError
    _check_host(host)
    if "://" in url:
        return {"service_endpoint": url}
    return {"server": parsed.netloc}


def build_ews_client(config: ExchangeToolConfig) -> Account:
    endpoint = _endpoint(config.ews_url)
    credentials = Credentials(username=config.service_account_username, password=config.service_account_password)
    ews_config = Configuration(credentials=credentials, auth_type=NTLM, **endpoint)
    account = Account(
        primary_smtp_address=config.mailbox,
        config=ews_config,
        autodiscover=False,
        access_type=DELEGATE,
    )
    # Instance attribute shadows the class default: only this connection is affected, never BaseProtocol.
    account.protocol.TIMEOUT = REQUEST_TIMEOUT_SECONDS
    return account


def _address(mailbox: Any) -> str:
    if mailbox is None:
        return ""
    name, address = getattr(mailbox, "name", None), getattr(mailbox, "email_address", None) or ""
    return f"{name} <{address}>" if name else address


def _summary(item: Any) -> MessageDict:
    received = getattr(item, "datetime_received", None)
    return {
        "id": getattr(item, "id", None) or "",
        "subject": getattr(item, "subject", None) or "",
        "from": _address(getattr(item, "sender", None)),
        "received": received.isoformat() if received else None,
        "is_read": bool(getattr(item, "is_read", False)),
        "has_attachments": bool(getattr(item, "has_attachments", False)),
    }


def _plain_body(item: Any) -> str:
    text = getattr(item, "text_body", None)
    if text:
        return str(text)
    body = str(getattr(item, "body", None) or "")
    body = _TAGS.sub("", _NON_TEXT_BLOCKS.sub("", body))
    body = _SPACES.sub(" ", html.unescape(body))
    return _BLANK_LINES.sub("\n", body).strip()


class EwsExchangeClient:
    def __init__(self, config: ExchangeToolConfig) -> None:
        self._account = build_ews_client(config)
        self._last_fetch: tuple[str, Any] | None = None  # one message per client, reused across enrich calls

    @staticmethod
    def _call(fn: Callable[[], T]) -> T:
        try:
            return call_with_retry(fn)
        except ExchangeError:
            raise
        except Exception as exc:  # noqa: BLE001 - sanitised below
            raise map_ews_exception(exc) from None

    def _inbox(self) -> Any:
        """Resolve the Inbox with typed errors.

        ``account.inbox`` is lazy and hits the server. Every enabled mailbox has an Inbox, so a
        "folder not found" while resolving it means the mailbox is denied or absent, not that a
        folder is missing: report mailbox access instead of a misleading missing-folder error.
        """

        def resolve() -> Any:
            try:
                return self._account.inbox
            except (ews.ErrorFolderNotFound, ews.ErrorItemNotFound):
                raise DelegateAccessError from None

        return self._call(resolve)

    def _mail_folders(self) -> list[Any]:
        """Mail folders only: ``walk()`` also yields Calendar, Contacts, Tasks and other non-mail folders."""
        folders = self._call(lambda: list(self._account.msg_folder_root.walk()))
        return [f for f in folders if (getattr(f, "container_class", None) or "").startswith(MAIL_CONTAINER_CLASS)]

    def _folder(self, folder: str) -> Any:
        name = (folder or "").strip()
        if not name:
            raise FolderNotFoundError
        key = name.lower()
        if key == INBOX:
            return self._inbox()
        attribute = _WELL_KNOWN_FOLDERS.get(key)
        if attribute is not None:
            return self._call(lambda: getattr(self._account, attribute))
        for candidate in self._mail_folders():
            if name == candidate.id or key == (candidate.name or "").lower():
                return candidate
        raise FolderNotFoundError

    def list_folders(self) -> list[FolderDict]:
        return [
            {
                "id": f.id,
                "name": f.name or "",
                "total_count": f.total_count or 0,
                "unread_count": f.unread_count or 0,
            }
            for f in self._mail_folders()
        ]

    def list_messages(self, folder: str, start: int, count: int) -> list[MessageDict]:
        top = max(1, min(count, MAX_PAGE))
        first = max(0, start)
        target = self._folder(folder)
        items = self._call(
            lambda: list(target.all().only(*_SUMMARY_FIELDS).order_by(_RECEIVED_DESC)[first : first + top])
        )
        summaries = [_summary(i) for i in items]
        # Stable two-pass sort: id ascending, then received descending (missing dates last).
        summaries.sort(key=lambda m: m["id"])
        summaries.sort(key=lambda m: m["received"] or "", reverse=True)
        return summaries

    def check_inbox(self) -> None:
        inbox = self._inbox()
        self._call(lambda: list(inbox.all().only(*_ID_ONLY)[:1]))

    def _fetch(self, message_id: str) -> Any:
        if self._last_fetch is not None and self._last_fetch[0] == message_id:
            return self._last_fetch[1]
        item = self._fetch_uncached(message_id)
        self._last_fetch = (message_id, item)
        return item

    def _fetch_uncached(self, message_id: str) -> Any:
        items = self._call(lambda: list(self._account.fetch(ids=[(message_id, None)])))
        if not items:
            raise MessageNotFoundError
        item = items[0]
        if isinstance(item, BaseException):
            raise map_ews_exception(item) from None
        return item

    def get_message(self, message_id: str) -> MessageDict:
        item = self._fetch(message_id)
        result = _summary(item)
        result["to"] = [_address(r) for r in (item.to_recipients or [])]
        result["cc"] = [_address(r) for r in (item.cc_recipients or [])]
        result["body"] = _plain_body(item)
        return result

    def get_text_body(self, message_id: str) -> str:
        return _plain_body(self._fetch(message_id))

    def list_attachments(self, message_id: str) -> list[AttachmentDict]:
        item = self._fetch(message_id)
        return [
            {
                "id": a.attachment_id.id,
                "name": a.name or "",
                "content_type": a.content_type or "",
                "size": a.size or 0,
                "is_file": isinstance(a, FileAttachment),
            }
            for a in (item.attachments or [])
        ]

    def get_attachment_bytes(self, message_id: str, attachment_id: str) -> bytes:
        item = self._fetch(message_id)
        for a in item.attachments or []:
            if a.attachment_id.id == attachment_id and isinstance(a, FileAttachment):
                return bytes(self._call(lambda att=a: att.content) or b"")
        raise MessageNotFoundError
