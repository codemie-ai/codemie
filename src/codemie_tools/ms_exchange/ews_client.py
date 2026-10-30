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
"""EWS (NTLM) implementation of the Exchange client (reads and writes).

The service account authenticates via NTLM against the explicit EWS endpoint; the target
mailbox is a distinct identity reached with DELEGATE access. Autodiscover is never used.
Reads never mark anything read. Writes (``send``, ``forward``, ``move``, ``tag``, ``delete``) run on a
private per-call protocol under one 90 s ``Deadline`` (see ``build_ews_write_session``); ``send`` and
``forward`` assume the delegate holds Send As on the shared mailbox (live check outstanding).

Timeouts and retries (named constants):
- ``REQUEST_TIMEOUT_SECONDS``: HTTP timeout on the exchangelib connection.
- Reads: transient failures are retried by ``retry.call_with_retry``; TLS, credential and config
  errors are never retried. Provider text is never propagated (see ``map_ews_exception``).
- Writes: ``write_retry.run_write`` is the only retrier (at most 3 submissions, only after a
  body-proven ``ErrorServerBusy`` with ``BackOffMilliseconds``); ``call_with_retry`` is not used.

Ordering note: EWS sorts server-side by ``datetime_received`` descending; ties inside the
returned window are ordered by id. ``id`` is not a sortable EWS field, so ties that straddle
a window boundary are not guaranteed a stable split.
"""

from __future__ import annotations

import concurrent.futures
import html
import ipaddress
import logging
import os
import random
import re
import socket
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from functools import cached_property, partial
from typing import Any, Callable, Iterator, TypeVar
from urllib.parse import urlparse

from exchangelib import DELEGATE, NTLM, Account, Configuration, Credentials, FailFast, FileAttachment
from exchangelib import errors as ews
from exchangelib import Body, HTMLBody, Mailbox, Message
from exchangelib.fields import FieldPath
from exchangelib.folders import DeletedItems, Drafts, Folder, Inbox, JunkEmail, MsgFolderRoot, SentItems
from exchangelib.items import ID_ONLY, NEVER_OVERWRITE
from exchangelib.properties import DistinguishedFolderId
from exchangelib.protocol import Protocol
from exchangelib.services import GetFolder
from exchangelib.util import to_xml
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError as RequestsConnectionError

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
    BadEndpointError,
    DeadlineExhaustedError,
    DelegateAccessError,
    EwsWriteAccessDeniedError,
    ExchangeError,
    FolderNotFoundError,
    InternalError,
    InvalidLocalInputError,
    MessageNotFoundError,
    UnknownOutcomeError,
    UnreachableError,
    WriteFolderNotFoundError,
    WriteRateLimitedError,
    map_ews_exception,
    map_ews_write_exception,
    map_write_open_error,
)
from codemie_tools.ms_exchange.models import ExchangeToolConfig
from codemie_tools.ms_exchange.retry import call_with_retry
from codemie_tools.ms_exchange.write_validation import (
    BODY_HTML,
    validate_category,
    validate_folder_name,
    validate_message_id,
    validate_recipients,
    validate_send_input,
)
from codemie_tools.ms_exchange.ews_write_session import (
    REQUEST_TIMEOUT_SECONDS,
    EwsWriteSession,
    TransportEvidence,
    private_protocol,
)
from codemie_tools.ms_exchange.write_retry import Classify, Deadline, Retry, Stop, run_write

logger = logging.getLogger(__name__)

T = TypeVar("T")
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

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
    # Read path only. exchangelib caches the Protocol per endpoint and credentials, so this instance is shared
    # by every read client with the same config; the assignment shadows the class default for that cached
    # instance, not for BaseProtocol. Writes never touch it: they use a private protocol per call.
    account.protocol.TIMEOUT = REQUEST_TIMEOUT_SECONDS
    return account


def build_ews_write_session(config: ExchangeToolConfig, deadline: Deadline) -> EwsWriteSession:
    """An account for one write call, on its own protocol; nothing is shared with the read client.

    The private ``Configuration`` pins ``FailFast`` (the library never retries or waits on its own) and leaves
    ``max_connections`` unset (a session pool of one, so ``_get_elements`` re-raises ``ErrorTimeoutExpired`` and
    ``ErrorExceededConnectionCount`` instead of looping). ``_endpoint`` does perform a DNS lookup to validate
    the host; no EWS request is sent here. The caller closes the session.
    """
    endpoint = _endpoint(config.ews_url)
    credentials = Credentials(username=config.service_account_username, password=config.service_account_password)
    # ``Account()`` always asks the process-wide cache for a protocol. Give it the configuration the read path
    # would build, so a cache entry created here is indistinguishable from the read client's, then replace it.
    account = Account(
        primary_smtp_address=config.mailbox,
        config=Configuration(credentials=credentials, auth_type=NTLM, **endpoint),
        autodiscover=False,
        access_type=DELEGATE,
    )
    evidence = TransportEvidence()
    private_config = Configuration(credentials=credentials, auth_type=NTLM, retry_policy=FailFast(), **endpoint)
    account.protocol = private_protocol(private_config, deadline, evidence)
    return EwsWriteSession(account=account, deadline=deadline, evidence=evidence)


def _ews_write_classifier(op: str, evidence: TransportEvidence) -> Classify:
    """Retry only a body-proven ``ErrorServerBusy`` with a server back-off; everything else is final.

    HTTP 503, HTTP 500 "Server Error in '/EWS' Application", any non-200 reply with ``Connection: close``,
    ServerBusy without a back-off, ``ErrorInternalServerTransientError`` and ``ErrorTimeoutExpired`` are all
    mapped (Unknown unless the mapper proves a pre-mutation rejection) and never resubmitted. exchangelib
    re-raises several of these as ``ErrorServerBusy`` with its own 10 s ``back_off``; that value is ignored.
    """

    def classify(exc: BaseException) -> Retry | Stop:
        if evidence.deadline_refused:
            return Stop(DeadlineExhaustedError())  # a request the adapter refused never left: nothing was applied
        if isinstance(exc, ews.ErrorServerBusy):
            last = evidence.last
            if last is not None and last.proves_retryable_busy:
                return Retry(last.back_off_ms / 1000.0)
        return Stop(map_ews_write_exception(exc, op))

    return classify


def _is_mail_folder(folder: Any) -> bool:
    """Whether ``folder`` is a mail folder: exchangelib's ``folder_class`` starts with ``IPF.Note``."""
    return (getattr(folder, "folder_class", None) or "").startswith(MAIL_CONTAINER_CLASS)


def _match_mail_folder(folders: list[Any], name: str) -> Any | None:
    """The mail folder whose id equals ``name`` or whose display name equals it ignoring case.

    Mail folders are those whose ``folder_class`` (the real exchangelib field, parsed from ``FolderClass``)
    starts with ``IPF.Note``; Calendar, Contacts, Tasks and the like are never matched.
    """
    key = name.lower()
    for candidate in folders:
        if not _is_mail_folder(candidate):
            continue
        if name == candidate.id or key == (candidate.name or "").lower():
            return candidate
    return None


def _resolve_write_folder(account: Account, folder: str) -> Any:
    """Resolve a folder name or id on the write session's account, by the rules of ``EwsExchangeClient._folder``.

    Same order and matching: ``inbox``, then the well-known names, then a mail folder by id or display name. It runs inside
    one ``_inner_read`` (single attempt), so a failure is Not applied; an unknown folder is the write twin
    ``WriteFolderNotFoundError``, and a missing Inbox means mailbox access, as on the read path.
    """
    key = folder.lower()
    if key == INBOX:
        try:
            return account.inbox
        except (ews.ErrorFolderNotFound, ews.ErrorItemNotFound):
            raise EwsWriteAccessDeniedError from None
    attribute = _WELL_KNOWN_FOLDERS.get(key)
    if attribute is not None:
        return getattr(account, attribute)
    found = _match_mail_folder(list(account.msg_folder_root.walk()), folder)
    if found is None:
        raise WriteFolderNotFoundError
    return found


def _parent_folder_id(item: Any) -> str | None:
    parent = getattr(item, "parent_folder_id", None)
    return getattr(parent, "id", None)


_MAX_FOLDER_DEPTH = 32
_PARENT_FIELD = FieldPath(field=Folder.get_field_by_fieldname("parent_folder_id"))
_KNOWN_FOLDER_CLASSES = (MsgFolderRoot, Inbox, SentItems, DeletedItems, Drafts, JunkEmail)
# Reason codes of a failed mailbox check (logged; never shown to the caller).
_OUTSIDE_MAILBOX = "outside_mailbox"
_DEPTH_LIMIT = "depth_limit"
_FOLDER_UNREADABLE = "folder_unreadable"


def _known_folder_ids(account: Account) -> set[str]:
    """Ids of the configured mailbox's root and its main folders, from one batched ``GetFolder`` (ids only).

    Each folder is asked for by name together with the configured address, so the ids come from that mailbox.
    A folder the batch cannot return is left out; the walk in ``_mailbox_rejection`` still reaches the root.
    """
    root = account.root
    mailbox = Mailbox(email_address=account.primary_smtp_address)
    folders = [
        cls(_distinguished_id=DistinguishedFolderId(id=cls.DISTINGUISHED_FOLDER_ID, mailbox=mailbox), root=root)
        for cls in _KNOWN_FOLDER_CLASSES
    ]
    ids = {root.id}
    for folder in GetFolder(account=account).call(folders, [], ID_ONLY):
        if not isinstance(folder, Exception) and folder.id:
            ids.add(folder.id)
    return ids


def _mailbox_rejection(account: Account, folder_id: str | None, known: set[str]) -> str | None:
    """None when ``folder_id`` sits in the configured mailbox, else the reason code (fail closed).

    EWS ids are not scoped to a mailbox, so the folder is walked up (``parent_folder_id``, one ``GetFolder`` per
    level) until it reaches a folder already proven to belong to the mailbox. Folders passed on the way are added
    to ``known``. A chain that ends elsewhere, loops, is too deep or cannot be read is rejected. A transport
    failure is raised, not returned, so "could not check" stays apart from "not in the mailbox".
    """
    seen: list[str] = []
    for _ in range(_MAX_FOLDER_DEPTH):
        if folder_id is None or folder_id in seen:
            return _OUTSIDE_MAILBOX
        if folder_id in known:
            known.update(seen)
            return None
        seen.append(folder_id)
        folder = next(
            iter(GetFolder(account=account).call([Folder(root=account.root, id=folder_id)], [_PARENT_FIELD], ID_ONLY))
        )
        if isinstance(folder, Exception):  # ErrorFolderNotFound / ErrorAccessDenied come back in the response
            return _FOLDER_UNREADABLE
        folder_id = _parent_folder_id(folder)
    return _DEPTH_LIMIT


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
    def __init__(
        self,
        config: ExchangeToolConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        rand: Callable[[float, float], float] = random.uniform,
    ) -> None:
        """``clock``, ``sleep`` and ``rand`` are the write owner's injectable time and jitter sources."""
        self._config = config
        self._last_fetch: tuple[str, Any] | None = None  # one message per client, reused across enrich calls
        self._clock = clock
        self._sleep = sleep
        self._rand = rand

    @cached_property
    def _account(self) -> Account:
        """The read account, built on first read access: a write never uses it, so it must not pay for it.

        ``build_ews_client`` validates and DNS-resolves the endpoint. Typed errors pass through; anything else
        becomes ``InternalError`` so provider text (URLs, accounts) never surfaces, as ``get_client`` did when the
        account was built in ``__init__``.
        """
        try:
            return build_ews_client(self._config)
        except ExchangeError:
            raise
        except Exception:  # noqa: BLE001 - provider text (URLs, accounts) must not surface
            raise InternalError from None

    @staticmethod
    def _call(fn: Callable[[], T]) -> T:
        try:
            return call_with_retry(fn)
        except ExchangeError:
            raise
        except Exception as exc:  # noqa: BLE001 - sanitised below
            raise map_ews_exception(exc) from None

    # ---- write plumbing: one Deadline and one private protocol per tool call, single-attempt reads, one owner

    @contextmanager
    def _write_session(self, op: str) -> Iterator[EwsWriteSession]:
        """Open the call's write session: Deadline, private-protocol account, server version; close it at the end.

        Every EWS request of the write call (version probe, reads, the mutating submission) goes through the
        session's account, so all of it is clamped by the one Deadline and recorded as transport evidence. The
        version probe is done here, before any submission, so its failure is Not applied.
        """
        deadline = Deadline(clock=self._clock)
        session: EwsWriteSession | None = None
        try:
            try:
                session = build_ews_write_session(self._config, deadline)
            except ExchangeError as exc:
                raise map_write_open_error(exc) from None
            except Exception as exc:  # noqa: BLE001 - sanitised below
                raise map_ews_write_exception(exc, op, before_mutation=True) from None
            self._inner_read(op, session, lambda account: account.version)
            yield session
        finally:
            if session is not None:
                session.close()

    @staticmethod
    def _inner_read(
        op: str,
        session: EwsWriteSession,
        read: Callable[[Account], T],
        *,
        before_mutation: bool = True,
    ) -> T:
        """One read attempt inside a write call (folder lookup, pre-read, reconciliation); never retried.

        ``read(account)`` runs on the session's private account: the adapter clamps each request to
        ``min(60, remaining)``, and neither ``_call`` nor ``call_with_retry`` nor ``_last_fetch`` is involved.
        With ``before_mutation=True`` any failure is Not applied (ServerBusy gets the rate-limit advice); with
        ``False`` (reconciliation) a failure or a spent deadline stays Unknown.

        A raw ``ExchangeError`` raised by ``read`` itself (not just a provider exception) is routed through
        the mapper too, not re-raised bare: an outcome-less one (a read error) must still end up Not applied
        or Unknown, never leave the caller with no outcome at all.
        """
        try:
            if session.deadline.expired():
                raise DeadlineExhaustedError
            session.evidence.reset()
            return read(session.account)
        except Exception as exc:  # noqa: BLE001 - sanitised below
            # exchangelib re-raises the adapter's refusal as its own timeout error, so the evidence tells them apart.
            if isinstance(exc, DeadlineExhaustedError) or session.evidence.deadline_refused:
                if before_mutation:
                    raise DeadlineExhaustedError from None
                raise map_ews_write_exception(ews.ErrorTimeoutExpired("deadline"), op) from None
            raise map_ews_write_exception(exc, op, before_mutation=before_mutation) from None

    def _fetch_for_write(
        self,
        op: str,
        session: EwsWriteSession,
        message_id: str,
        *,
        only_fields: list[str],
        before_mutation: bool = True,
    ) -> Any:
        """Fetch one item with a single attempt, always from the server (``_last_fetch`` is neither read nor set).

        ``only_fields`` is required: without it exchangelib asks for every field (``mime_content`` and ``body``
        included), which downloads the whole message and its attachments to read one property. The item id and
        change key always come back.

        Before a write (``before_mutation=True``) the item's ``parent_folder_id`` is always fetched and checked:
        EWS ids are not scoped to a mailbox, so an item of another mailbox the service account can reach must not
        be changed through this one. A foreign item is reported exactly like a missing one (Not applied). The
        reconciliation read after an Unknown move skips the check; it compares against the target folder.
        """
        if before_mutation:
            only_fields = list(dict.fromkeys([*only_fields, "parent_folder_id"]))

        def read(account: Account) -> Any:
            items = list(account.fetch(ids=[(message_id, None)], only_fields=only_fields))
            if not items:
                raise ews.ErrorItemNotFound("Item not found")
            if isinstance(items[0], BaseException):
                raise items[0]
            return items[0]

        item = self._inner_read(op, session, read, before_mutation=before_mutation)
        if before_mutation:
            self._require_in_mailbox(op, session, item)
        return item

    def _require_in_mailbox(self, op: str, session: EwsWriteSession, item: Any) -> None:
        """Refuse ``item`` unless it is in the configured mailbox, with exactly the error of a missing id.

        The refusal goes through the same mapping as a missing id, so the error never tells whether the id exists
        in another mailbox: Not applied on forward and tag, Unknown on move and delete.
        """

        def check(account: Account) -> str | None:
            if session.known_folders is None:
                session.known_folders = _known_folder_ids(account)
            return _mailbox_rejection(account, _parent_folder_id(item), session.known_folders)

        reason = self._inner_read(op, session, check)
        if reason is not None:
            logger.warning("Exchange write refused, message not in the configured mailbox: op=%s reason=%s", op, reason)
            raise map_ews_write_exception(ews.ErrorItemNotFound("Item not found"), op, before_mutation=True)

    def _submit_write(
        self,
        op: str,
        session: EwsWriteSession,
        attempt: Callable[[Account], T],
        classify: Classify | None = None,
    ) -> T:
        """Submit the mutating request through the write owner (``run_write``): at most 3 submissions.

        ``attempt(account)`` performs exactly one submission on the private account. By default only a
        body-proven ServerBusy with a server back-off is retried (the server wait is honoured exactly); any
        other failure is final and mapped. ``classify`` replaces that rule for an op with its own (tag).
        """

        def submit() -> T:
            session.evidence.reset()
            return attempt(session.account)

        return run_write(
            submit,
            classify or _ews_write_classifier(op, session.evidence),
            session.deadline,
            sleep=self._sleep,
            rand=self._rand,
        )

    # ---- send and forward

    def _shared_mailbox(self) -> Mailbox:
        """The configured shared mailbox as the From of a write: Send As is the assumed delegate right."""
        return Mailbox(email_address=self._config.mailbox)

    def send(self, to: list[str], subject: str, body: str, body_type: str = "text") -> dict[str, Any]:
        """Send a new message from the shared mailbox; the copy goes to its Sent Items.

        Success means Exchange accepted the submission, not delivery. ``html`` is passed unsanitised.
        """
        recipients, kind = validate_send_input(to, subject, body, body_type)
        content = HTMLBody(body) if kind == BODY_HTML else Body(body)

        with self._write_session("send") as session:
            sent = self._inner_read("send", session, lambda account: account.sent)

            def attempt(account: Account) -> None:
                Message(
                    account=account,
                    author=self._shared_mailbox(),
                    subject=subject,
                    body=content,
                    to_recipients=[Mailbox(email_address=address) for address in recipients],
                ).send(copy_to_folder=sent)

            self._submit_write("send", session, attempt)
        return {"status": SUBMITTED, "to": recipients, "subject": subject}

    def forward(self, message_id: str, to: list[str]) -> dict[str, Any]:
        """Forward a message to ``to`` (no comment) from the shared mailbox. Success means submission accepted.

        The original is fetched from the server, never from the read cache. A ``None`` subject and an empty body
        make the server derive "FW: <subject>" and quote the original; ``Item.forward`` is not used because it
        calls ``send()`` without ``copy_to_folder`` (no ``SavedItemFolderId``). The empty body takes the original's
        type (``HTMLBody`` for an HTML original), otherwise a ``Text`` ``NewBodyContent`` would flatten the quote.
        """
        message_id = validate_message_id(message_id)
        recipients = validate_recipients(to)

        with self._write_session("forward") as session:
            original = self._fetch_for_write("forward", session, message_id, only_fields=["body"])
            sent = self._inner_read("forward", session, lambda account: account.sent)

            def attempt(account: Account) -> None:
                empty = HTMLBody("") if isinstance(original.body, HTMLBody) else Body("")
                request = original.create_forward(None, empty, [Mailbox(email_address=a) for a in recipients])
                request.author = self._shared_mailbox()
                request.send(copy_to_folder=sent)

            self._submit_write("forward", session, attempt)
        return {"status": SUBMITTED, "message_id": message_id, "to": recipients}

    # ---- move, delete and tag

    def _reconcile_move(self, op: str, session: EwsWriteSession, message_id: str, target_id: str | None) -> str | None:
        """After an Unknown move: the item id if the SAME id is now in the target folder, else None.

        The item is re-read from the server (single attempt, ``_last_fetch`` bypassed). A failed read, a spent
        deadline, an item that is gone (EWS ids usually change on a move) or one still in the source all leave the
        outcome Unknown; the caller then re-raises the original error.
        """
        try:
            item = self._fetch_for_write(
                op, session, message_id, only_fields=["parent_folder_id"], before_mutation=False
            )
        except ExchangeError as exc:
            logger.warning(
                "Exchange write reconciliation read failed: op=%s exc_type=%s outcome=%s",
                op,
                type(exc).__name__,
                exc.OUTCOME,
            )
            return None
        return (item.id or message_id) if target_id and _parent_folder_id(item) == target_id else None

    def _move_message(self, op: str, message_id: str, folder: str) -> tuple[str | None, bool]:
        """Move a message; returns ``(message_id after the move, already_done)``. Inputs are validated.

        The target is resolved and the item read first, each a single attempt on the write session (the
        write-session twin of ``_folder``: same rules, Not applied errors). An item already in the target is
        ``already_done`` and nothing is written. ``msg.move(to_folder=...)`` is the one submission.
        """
        with self._write_session(op) as session:
            target = self._inner_read(op, session, lambda account: _resolve_write_folder(account, folder))
            item = self._fetch_for_write(op, session, message_id, only_fields=["parent_folder_id"])
            if _parent_folder_id(item) == target.id:
                return message_id, True

            def attempt(account: Account) -> None:
                item.move(to_folder=target)

            try:
                self._submit_write(op, session, attempt)
            except UnknownOutcomeError:
                found = self._reconcile_move(op, session, message_id, target.id)
                if found is None:
                    raise
                return found, False
            return item.id, False

    def move(self, message_id: str, target_folder: str) -> dict[str, Any]:
        """Move a message to ``target_folder`` (a well-known name, display name or folder id).

        Returns ``{"status": "moved", "message_id": <new id>, "target_folder": <as given>}``; a message that
        already is in the target returns the same dict plus ``"already_done": True`` with its current id and
        issues no write. After an unknown outcome the same id is re-read: found in the target is success,
        anything else stays Unknown (EWS ids usually change on a move, so a lost reply often ends Unknown).
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

        Soft is ``msg.move(to_folder=account.trash)``, never ``soft_delete()``: a message already in Deleted
        Items returns ``already_done`` without any write, and an unknown outcome never escalates to a permanent
        delete. Hard is ``msg.delete()`` (DeleteType HardDelete) after a pre-read that checks the mailbox and the id;
        a not-found on the delete itself stays Unknown (a missing item is not proof that this call deleted it). Retention or hold may preserve a
        permanently deleted item. Returns ``{"status": "deleted", "soft": bool, "message_id": id}`` (soft: the
        id after the move) plus ``"already_done": True`` when nothing had to be done.
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

        with self._write_session("delete") as session:
            # The pre-read puts a permanent delete under the mailbox check and turns a missing id into Not applied.
            self._fetch_for_write("delete", session, message_id, only_fields=["parent_folder_id"])
            self._submit_write("delete", session, lambda account: Message(account=account, id=message_id).delete())
        return {"status": DELETED, "soft": False, "message_id": message_id}

    def tag(self, message_id: str, category: str) -> dict[str, Any]:
        """Add one category to a message, keeping the existing ones (additive union, order preserved).

        The category is compared case-insensitively. Present already: ``already_done`` and no save. Otherwise
        the full union is saved with ``update_fields=["categories"]`` and ``conflict_resolution=NEVER_OVERWRITE``,
        so a stale changekey raises ``ErrorIrresolvableConflict``. The first conflict triggers one reread and
        recompute; a second raises ``CategoryConflictError`` (Not applied). The whole attempt (read, then save) is
        one submission for the write owner, so the recompute counts against the same 3 submissions and the same
        deadline. A read that fails with a body-proven ``ErrorServerBusy`` and a server back-off is retried like
        the save (nothing was changed); any other read failure is Not applied and final. After an Unknown save
        there is no reconciliation. Returns ``{"status": "tagged", "message_id": id, "categories": [...]}`` plus
        ``"already_done": True``.
        """
        message_id = validate_message_id(message_id)
        category = validate_category(category)

        with self._write_session("tag") as session:
            base = _ews_write_classifier("tag", session.evidence)
            conflicted = False
            reading = True  # the read half of the attempt; set False once the save starts

            def classify(exc: BaseException) -> Retry | Stop:
                nonlocal conflicted
                if isinstance(exc, ews.ErrorIrresolvableConflict) and not conflicted:
                    conflicted = True
                    return Retry(0.0, on_exhausted=map_ews_write_exception(exc, "tag"))
                # The read maps its failures to Not applied before they get here, so a busy read arrives as a
                # WriteRateLimitedError; retry it only when the reply body proved a busy server and gave a wait.
                last = session.evidence.last
                if (
                    reading
                    and isinstance(exc, WriteRateLimitedError)
                    and last is not None
                    and last.proves_retryable_busy
                ):
                    return Retry(last.back_off_ms / 1000.0)
                return base(exc)

            def attempt(account: Account) -> tuple[list[str], bool]:
                nonlocal reading
                reading = True
                item = self._fetch_for_write("tag", session, message_id, only_fields=["categories"])
                existing = list(item.categories or [])
                if category.casefold() in {c.casefold() for c in existing}:
                    return existing, True
                union = [*existing, category]
                item.categories = union
                reading = False
                item.save(update_fields=["categories"], conflict_resolution=NEVER_OVERWRITE)
                return union, False

            categories, already = self._submit_write("tag", session, attempt, classify)
        result: dict[str, Any] = {"status": TAGGED, "message_id": message_id, "categories": categories}
        if already:
            result["already_done"] = True
        return result

    def _inbox(self) -> Any:
        """Resolve the Inbox with typed errors.

        ``account.inbox`` is lazy and hits the server. Every enabled mailbox has an Inbox, so a
        "folder not found" while resolving it means the mailbox is denied or absent, not that a
        folder is missing: report mailbox access instead of a misleading missing-folder error.
        """

        def resolve() -> Any:
            try:
                return self._account.inbox
            except (ews.ErrorFolderNotFound, ews.ErrorItemNotFound) as exc:
                # Raised as DelegateAccessError directly (bypassing map_ews_exception), so log it here:
                # DEBUG, since a hidden/absent Inbox is a routine, expected access outcome, not an operator alert.
                logger.debug(
                    "Exchange read mapping: transport=ews op=inbox exc_type=%s mapped=%s",
                    type(exc).__name__,
                    DelegateAccessError.__name__,
                )
                raise DelegateAccessError from None

        return self._call(resolve)

    def _mail_folders(self) -> list[Any]:
        """Mail folders only: ``walk()`` also yields Calendar, Contacts, Tasks and other non-mail folders."""
        folders = self._call(lambda: list(self._account.msg_folder_root.walk()))
        return [f for f in folders if _is_mail_folder(f)]

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
        found = _match_mail_folder(self._mail_folders(), name)
        if found is None:
            raise FolderNotFoundError
        return found

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
