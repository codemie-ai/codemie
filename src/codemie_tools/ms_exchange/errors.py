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
"""Typed, sanitised errors for the Exchange tools.

Every class carries a FIXED user-facing sentence. Provider exception text is never
interpolated: it can contain tenant/client/correlation ids, login URLs, EWS URLs and
account names.
"""

from __future__ import annotations

import logging
import re
import ssl
from enum import StrEnum
from langchain_core.tools import ToolException

logger = logging.getLogger(__name__)


class WriteOutcome(StrEnum):
    """Outcome of a write that failed: proven not applied, or unknown (may have gone through)."""

    NOT_APPLIED = "not_applied"
    UNKNOWN = "unknown"


class ExchangeError(ToolException):
    """Base class; the message is always the class-level MESSAGE."""

    MESSAGE = "An internal error occurred while accessing Exchange."
    # Write outcome: WriteOutcome.NOT_APPLIED | WriteOutcome.UNKNOWN; None on read errors.
    OUTCOME: WriteOutcome | None = None
    # Derived from OUTCOME: an outcome of either kind is never retryable. False tells the workflow retry
    # hook not to re-run the node; read errors (OUTCOME is None) stay retryable. A subclass may still set
    # RETRYABLE explicitly to deviate from the derived value.
    RETRYABLE: bool = True

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if cls.OUTCOME is not None and not isinstance(cls.OUTCOME, WriteOutcome):
            raise TypeError(f"{cls.__name__}.OUTCOME must be a WriteOutcome member or None, got {cls.OUTCOME!r}")
        if "RETRYABLE" not in cls.__dict__:
            cls.RETRYABLE = cls.OUTCOME is None

    def __init__(self) -> None:
        super().__init__(self.MESSAGE)


class CredentialExpiredError(ExchangeError):
    MESSAGE = "The Microsoft Graph client secret has expired. Ask an administrator to issue a new secret."


class CredentialInvalidError(ExchangeError):
    MESSAGE = "The Microsoft Graph client secret is invalid. Check the configured client secret."


class TenantError(ExchangeError):
    MESSAGE = "The Microsoft Entra tenant was not found. Check the configured tenant."


class AppNotFoundError(ExchangeError):
    MESSAGE = "The Microsoft Entra application was not found. Check the configured client ID."


class TokenRejectedError(ExchangeError):
    MESSAGE = "Microsoft Graph rejected the access token. Retry the request or check the app registration."


class ConsentError(ExchangeError):
    MESSAGE = "Administrator consent is missing or was revoked for the required Microsoft Graph permissions."


class MailboxNotEnabledError(ExchangeError):
    MESSAGE = "The mailbox is not licensed or not enabled for the Microsoft Graph REST API."


class AccessDeniedError(ExchangeError):
    """Graph answers 403 ``ErrorAccessDenied`` both for missing Mail.Read consent and for an access policy."""

    MESSAGE = (
        "Access to this mailbox was denied. Check that the app registration has the Mail.Read application "
        "permission with administrator consent, and that no Application Access Policy blocks the mailbox."
    )


class MailboxNotFoundError(ExchangeError):
    MESSAGE = "The mailbox was not found."


class FolderNotFoundError(ExchangeError):
    MESSAGE = "The mail folder was not found."


class MessageNotFoundError(ExchangeError):
    MESSAGE = "The message was not found."


class UnreachableError(ExchangeError):
    MESSAGE = "The Exchange service is unreachable. Try again later."


class RateLimitedError(ExchangeError):
    MESSAGE = "The Exchange service is rate limiting requests. Try again later."


class ServiceTimeoutError(ExchangeError):
    MESSAGE = "The Exchange service did not respond in time. Try again later."


class TransientServerError(ExchangeError):
    MESSAGE = "The Exchange service reported a temporary server error. Try again later."


class TlsConfigError(ExchangeError):
    MESSAGE = (
        "The TLS certificate of the Exchange service could not be verified. "
        "Check the endpoint certificate configuration."
    )


class BadEndpointError(ExchangeError):
    MESSAGE = "The configured Exchange EWS endpoint is not usable. Check the endpoint URL."


class RedirectedError(ExchangeError):
    MESSAGE = "The Exchange endpoint redirected the request. Configure the direct EWS endpoint URL."


class EwsCredentialsRejectedError(ExchangeError):
    MESSAGE = "The Exchange service account credentials were rejected. Check the configured username and password."


class DelegateAccessError(ExchangeError):
    MESSAGE = "Delegate access to the mailbox is denied or the mailbox does not exist."


class OcrModelUnavailableError(ExchangeError):
    MESSAGE = "The requested OCR model is not available. Omit ocr_model to use the assistant's default model."


class InternalError(ExchangeError):
    MESSAGE = "An internal error occurred while accessing Exchange."


# ---------------------------------------------------------------------------------------------
# Write-path errors. Every message starts exactly "Not applied." or "Unknown." (so the wording
# survives the CodeMieTool._run rewrap), is a fixed sentence and carries safe retry advice.
# ---------------------------------------------------------------------------------------------

_UNKNOWN_ADVICE = "Check the mailbox before trying again, because repeating the request may duplicate it."


class NotAppliedError(ExchangeError):
    """The write is proven not to have changed the mailbox."""

    MESSAGE = "Not applied. The change was not made. Fix the cause and try again."
    OUTCOME = WriteOutcome.NOT_APPLIED


class UnknownOutcomeError(ExchangeError):
    """The write may or may not have changed the mailbox."""

    MESSAGE = f"Unknown. The change may or may not have been made. {_UNKNOWN_ADVICE}"
    OUTCOME = WriteOutcome.UNKNOWN


# Not applied: write twins of read classes. Each is also an instance of its read class.
class WriteCredentialExpiredError(NotAppliedError, CredentialExpiredError):
    MESSAGE = (
        "Not applied. The Microsoft Graph client secret has expired, so nothing was changed. "
        "Ask an administrator to issue a new secret, then try again."
    )


class WriteCredentialInvalidError(NotAppliedError, CredentialInvalidError):
    MESSAGE = (
        "Not applied. The Microsoft Graph client secret is invalid, so nothing was changed. "
        "Check the configured client secret, then try again."
    )


class WriteTenantError(NotAppliedError, TenantError):
    MESSAGE = (
        "Not applied. The Microsoft Entra tenant was not found, so nothing was changed. "
        "Check the configured tenant, then try again."
    )


class WriteAppNotFoundError(NotAppliedError, AppNotFoundError):
    MESSAGE = (
        "Not applied. The Microsoft Entra application was not found, so nothing was changed. "
        "Check the configured client ID, then try again."
    )


class WriteTokenRejectedError(NotAppliedError, TokenRejectedError):
    MESSAGE = (
        "Not applied. Microsoft Graph did not accept the access token, so nothing was changed. "
        "Check the app registration, then try again."
    )


class WriteConsentError(NotAppliedError, ConsentError):
    MESSAGE = (
        "Not applied. Administrator consent is missing or was revoked for the required Microsoft Graph "
        "permissions, so nothing was changed. Ask an administrator to grant consent, then try again."
    )


class WriteMailboxNotEnabledError(NotAppliedError, MailboxNotEnabledError):
    MESSAGE = (
        "Not applied. The mailbox is not licensed or not enabled for the Microsoft Graph REST API, "
        "so nothing was changed. Check the mailbox license, then try again."
    )


class WriteMailboxNotFoundError(NotAppliedError, MailboxNotFoundError):
    MESSAGE = "Not applied. The mailbox was not found, so nothing was changed. Check the mailbox, then try again."


class WriteFolderNotFoundError(NotAppliedError, FolderNotFoundError):
    # EWS hides folders the account has no rights on, so "no match" is a typo or a hidden folder: say both.
    MESSAGE = (
        "Not applied. The mail folder was not found, or this account cannot see it, so nothing was changed. "
        "Check the folder name and the delegate rights on the mailbox, then try again."
    )


class WriteMessageNotFoundError(NotAppliedError, MessageNotFoundError):
    MESSAGE = "Not applied. The message was not found, so nothing was changed. Check the message ID, then try again."


class WriteTlsConfigError(NotAppliedError, TlsConfigError):
    MESSAGE = (
        "Not applied. The TLS certificate of the Exchange service could not be verified, so nothing was "
        "changed. Check the endpoint certificate configuration, then try again."
    )


class WriteRedirectedError(NotAppliedError, RedirectedError):
    MESSAGE = (
        "Not applied. The Exchange endpoint redirected the request, so nothing was changed. "
        "Configure the direct EWS endpoint URL, then try again."
    )


class WriteEwsCredentialsRejectedError(NotAppliedError, EwsCredentialsRejectedError):
    MESSAGE = (
        "Not applied. The Exchange service account credentials were rejected, so nothing was changed. "
        "Check the configured username and password, then try again."
    )


class WriteBadEndpointError(NotAppliedError, BadEndpointError):
    """The EWS endpoint check (URL, allowlist, host rules) failed while opening a write call."""

    MESSAGE = (
        "Not applied. The configured Exchange EWS endpoint is not usable, so nothing was changed. "
        "Check the endpoint URL, then try again."
    )


class WriteUnreachableError(NotAppliedError, UnreachableError):
    """The endpoint host did not resolve while opening a write call, before any request was sent."""

    MESSAGE = "Not applied. The Exchange service is unreachable, so nothing was changed. Try again later."


# Not applied: new write-only classes.
class WriteRateLimitedError(NotAppliedError):
    MESSAGE = "Not applied. The Exchange service is rate limiting requests, so nothing was changed. Try again later."


class DeadlineExhaustedError(NotAppliedError):
    """The call's time budget ran out before a submission could start."""

    MESSAGE = (
        "Not applied. The time limit for this call was used up before the change could be sent, "
        "so nothing was changed. Try again."
    )


class SendAccessDeniedError(NotAppliedError):
    """Graph permission denial for send and forward."""

    MESSAGE = (
        "Not applied. Access to send mail from this mailbox was denied, so nothing was sent. Possible causes "
        "are that the app registration lacks the Mail.Send application permission or its administrator "
        "consent, or that mailbox access restrictions such as an Application Access Policy block the "
        "mailbox. Check these, then try again."
    )


class WriteAccessDeniedError(NotAppliedError):
    """Graph permission denial for move, tag and delete."""

    MESSAGE = (
        "Not applied. Access to change this mailbox was denied, so nothing was changed. Possible causes "
        "are that the app registration lacks the Mail.ReadWrite application permission or its "
        "administrator consent, or that mailbox access restrictions such as an Application Access Policy "
        "block the mailbox. Check these, then try again."
    )


class EwsSendAccessDeniedError(NotAppliedError):
    """EWS Send As denial (or access denial) for send and forward."""

    MESSAGE = (
        "Not applied. The Exchange service account was not allowed to send as this mailbox, so nothing was "
        "sent. Possible causes are a missing Send As right on the mailbox or mailbox access restrictions. "
        "Check the delegate rights, then try again."
    )


class EwsWriteAccessDeniedError(NotAppliedError):
    """EWS access denial for move, tag and delete."""

    MESSAGE = (
        "Not applied. The Exchange service account was not allowed to change this mailbox, so nothing was "
        "changed. Possible causes are missing delegate write rights on the mailbox or mailbox access "
        "restrictions. Check the delegate rights, then try again."
    )


class CategoryConflictError(NotAppliedError):
    MESSAGE = (
        "Not applied. The message categories were changed by someone else at the same time, so nothing was "
        "changed. Try tagging again."
    )


class InvalidWriteInputError(NotAppliedError):
    """Exchange itself rejected the request as invalid (a server-side rejection)."""

    MESSAGE = (
        "Not applied. Exchange rejected the request as invalid, so nothing was changed. "
        "Check the input values, then try again."
    )


class InvalidLocalInputError(NotAppliedError):
    """A local input check (``write_validation.py``, the ``soft`` flag) failed; nothing was sent to Exchange."""

    MESSAGE = "Not applied. The input was not valid, so nothing was sent. Check the input values, then try again."


class WriteRejectedError(NotAppliedError):
    MESSAGE = "Not applied. Exchange rejected the request before making any change. Check the request, then try again."


class PreMutationReadFailedError(NotAppliedError):
    """A read that must precede the change failed, so the change was never attempted."""

    MESSAGE = (
        "Not applied. The mailbox could not be read before the change, so nothing was changed. " "Try again later."
    )


# Unknown: a submission may have reached the mailbox.
class WriteTimeoutUnknownError(UnknownOutcomeError):
    MESSAGE = f"Unknown. The Exchange service did not respond in time, so the change may or may not have been made. {_UNKNOWN_ADVICE}"


class WriteServerErrorUnknownError(UnknownOutcomeError):
    MESSAGE = f"Unknown. The Exchange service reported a server error, so the change may or may not have been made. {_UNKNOWN_ADVICE}"


class WriteConnectionLostUnknownError(UnknownOutcomeError):
    MESSAGE = f"Unknown. The connection to the Exchange service was lost, so the change may or may not have been made. {_UNKNOWN_ADVICE}"


class WriteUnknownError(UnknownOutcomeError):
    MESSAGE = f"Unknown. An unexpected error occurred while changing the mailbox, so the change may or may not have been made. {_UNKNOWN_ADVICE}"


class WriteMessageNotFoundUnknownError(UnknownOutcomeError):
    """The old id vanished on move or delete: the write, or an earlier attempt of it, may already have succeeded."""

    MESSAGE = (
        "Unknown. The message was not found, so it may already have been moved or deleted, for example by an "
        "earlier attempt. Check the mailbox before trying again."
    )


_AADSTS_RE = re.compile(r"AADSTS(\d+)")
_AADSTS_MAP = {
    "7000222": CredentialExpiredError,
    "7000215": CredentialInvalidError,
    "90002": TenantError,
    "700016": AppNotFoundError,
}
_GRAPH_CODE_MAP = {
    (401, "InvalidAuthenticationToken"): TokenRejectedError,
    (403, "Authorization_RequestDenied"): ConsentError,
    (403, "MailboxNotEnabledForRESTAPI"): MailboxNotEnabledError,
    (403, "ErrorAccessDenied"): AccessDeniedError,
    (404, "ErrorInvalidUser"): MailboxNotFoundError,
    (404, "ResourceNotFound"): MailboxNotFoundError,
    (404, "ErrorFolderNotFound"): FolderNotFoundError,
    (404, "ErrorItemNotFound"): MessageNotFoundError,
    (400, "ErrorInvalidIdMalformed"): MessageNotFoundError,
    (404, "ErrorInvalidIdMalformed"): MessageNotFoundError,
}
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR_MIN = 500


def _is_tls_failure(exc: BaseException) -> bool:
    """True when the exception (or its cause chain) is a certificate/TLS trust failure."""
    seen = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(cur):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _is_cert_verify_failure(exc: BaseException) -> bool:
    """True only when the chain proves a certificate verification failure, not any TLS/SSL error.

    Used by the write mappers only: a non-cert SSL error (SSLEOFError, bad record MAC, a reset while
    reading the response) can happen after the server already accepted the request, so it must not be
    reported as "Not applied" — only a handshake certificate failure is proven pre-send.
    """
    seen = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(cur):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def graph_status(exc: BaseException) -> int | None:
    return getattr(exc, "response_status_code", None) or getattr(exc, "status_code", None)


def graph_code(exc: BaseException) -> str | None:
    error = getattr(exc, "error", None)
    return getattr(error, "code", None) or getattr(exc, "code", None)


def _graph_request_id(exc: BaseException) -> str | None:
    headers = getattr(exc, "response_headers", None) or {}
    for key, value in dict(headers).items():
        if str(key).lower() in ("request-id", "client-request-id"):
            return str(value)
    return None


def _exception_chain_types(exc: BaseException, limit: int = 3) -> list[str]:
    """Class names of exc and its cause/context chain, newest first. Never the exception text itself."""
    names: list[str] = []
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen and len(names) < limit:
        seen.add(id(cur))
        names.append(type(cur).__name__)
        cur = cur.__cause__ or cur.__context__
    return names


def _log_mapping(
    kind: str, exc: BaseException, mapped: ExchangeError, *, transport: str, level: int, op: str | None = None
) -> None:
    """Log a read- or write-path mapping outcome without any provider-supplied text (see module docstring).

    Only type names, status codes and request ids are logged, never ``str(exc)``: the provider message can
    carry tenant/client/correlation ids, login URLs, EWS URLs or account names. Shared by ``_log_write_mapping``
    (``kind="write"``, always carries ``op``) and ``_log_read_mapping`` (``kind="read"``, no ``op``).
    """
    logger.log(
        level,
        "Exchange %s mapping: transport=%s op=%s exc_chain=%s status=%s graph_code=%s request_id=%s "
        "mapped=%s outcome=%s",
        kind,
        transport,
        op,
        _exception_chain_types(exc),
        graph_status(exc),
        graph_code(exc),
        _graph_request_id(exc),
        type(mapped).__name__,
        mapped.OUTCOME,
    )


def _log_write_mapping(op: str, exc: BaseException, mapped: ExchangeError, *, transport: str) -> None:
    """Log a write-path mapping. WARNING when the outcome is Unknown (may have gone through), else DEBUG."""
    level = logging.WARNING if mapped.OUTCOME is WriteOutcome.UNKNOWN else logging.DEBUG
    _log_mapping("write", exc, mapped, transport=transport, level=level, op=op)


# Read-path mapped classes that mean a 5xx/throttle/TLS/internal/unreachable condition, or an admin-actionable
# misconfiguration (expired/invalid credential, unknown tenant/app, missing consent, disabled mailbox, rejected
# token): these need operator attention, so they log at WARNING. Everything else a read mapper can return
# (mailbox/folder/message not found, access/delegate denied) is a routine, expected outcome of a particular
# read - e.g. a message already deleted or a mistyped folder name - so it logs at DEBUG, matching the write
# path's "Not applied" severity and avoiding alert fatigue on every expected not-found.
_READ_WARNING_CLASSES = frozenset(
    {
        UnreachableError,
        RateLimitedError,
        ServiceTimeoutError,
        TransientServerError,
        TlsConfigError,
        InternalError,
        BadEndpointError,
        RedirectedError,
        CredentialExpiredError,
        CredentialInvalidError,
        TenantError,
        AppNotFoundError,
        TokenRejectedError,
        ConsentError,
        MailboxNotEnabledError,
        EwsCredentialsRejectedError,
    }
)


def _log_read_mapping(exc: BaseException, mapped: ExchangeError, *, transport: str) -> None:
    """Log a read-path mapping; see ``_READ_WARNING_CLASSES`` for the WARNING/DEBUG split."""
    level = logging.WARNING if type(mapped) in _READ_WARNING_CLASSES else logging.DEBUG
    _log_mapping("read", exc, mapped, transport=transport, level=level)


def map_graph_exception(exc: BaseException) -> ExchangeError:
    """Map an azure-identity / msgraph exception to a typed, sanitised error.

    An ``exc`` that is already an ``ExchangeError`` is returned unchanged and is not re-logged (it was
    logged, if at all, when it was first mapped); anything else is mapped and the mapping is logged.
    """
    if isinstance(exc, ExchangeError):
        return exc
    mapped = _map_graph_exception_raw(exc)
    _log_read_mapping(exc, mapped, transport="graph")
    return mapped


def _map_graph_exception_raw(exc: BaseException) -> ExchangeError:
    if _is_tls_failure(exc):
        return TlsConfigError()

    # Local import keeps azure-core optional at import time of this module.
    from azure.core.exceptions import ClientAuthenticationError

    if isinstance(exc, ClientAuthenticationError):
        match = _AADSTS_RE.search(str(exc))
        return _AADSTS_MAP.get(match.group(1) if match else "", InternalError)()

    status = graph_status(exc)
    if status is not None:
        mapped = _GRAPH_CODE_MAP.get((status, graph_code(exc)))
        if mapped:
            return mapped()
        if status == _HTTP_TOO_MANY_REQUESTS:
            return RateLimitedError()
        if status >= _HTTP_SERVER_ERROR_MIN:
            return UnreachableError()
        return InternalError()

    import httpx

    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return ServiceTimeoutError()
    if isinstance(exc, OSError):
        return UnreachableError()
    return InternalError()


def _map_ews_timeout(exc: BaseException) -> ExchangeError:
    """exchangelib re-raises every connection error as ErrorTimeoutExpired; the chained cause tells them apart."""
    import requests

    cause = exc.__cause__ or exc.__context__
    if isinstance(cause, (requests.exceptions.Timeout, TimeoutError)):
        return ServiceTimeoutError()
    if isinstance(cause, (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError, OSError)):
        return UnreachableError()
    # No connection-level cause: the server itself reported the timeout.
    return ServiceTimeoutError()


def map_ews_exception(exc: BaseException) -> ExchangeError:
    """Map an exchangelib exception to a typed, sanitised error.

    An ``exc`` that is already an ``ExchangeError`` is returned unchanged and is not re-logged (it was
    logged, if at all, when it was first mapped); anything else is mapped and the mapping is logged.
    """
    if isinstance(exc, ExchangeError):
        return exc
    mapped = _map_ews_exception_raw(exc)
    _log_read_mapping(exc, mapped, transport="ews")
    return mapped


def _map_ews_exception_raw(exc: BaseException) -> ExchangeError:
    if _is_tls_failure(exc):
        return TlsConfigError()

    from exchangelib import errors as ews

    if isinstance(exc, ews.ErrorTimeoutExpired):
        return _map_ews_timeout(exc)

    # Order matters: most specific first (many are TransportError subclasses).
    table = (
        (ews.RedirectError, RedirectedError),
        (ews.UnauthorizedError, EwsCredentialsRejectedError),
        ((ews.ErrorAccessDenied, ews.ErrorNonExistentMailbox), DelegateAccessError),
        ((ews.ErrorServerBusy, ews.RateLimitError), RateLimitedError),
        (ews.ErrorInternalServerTransientError, TransientServerError),
        ((ews.ErrorItemNotFound, ews.ErrorInvalidIdMalformed), MessageNotFoundError),
        (ews.ErrorFolderNotFound, FolderNotFoundError),
        (ews.TransportError, BadEndpointError),  # includes SOAPError
    )
    for types, cls in table:
        if isinstance(exc, types):
            return cls()
    if isinstance(exc, OSError):
        return UnreachableError()
    return InternalError()


# ---------------------------------------------------------------------------------------------
# Write mappings. They reuse the tables above but never the read 5xx/timeout classes: after a
# submission, timeouts, connection loss, 5xx and unexpected exceptions are Unknown.
# ---------------------------------------------------------------------------------------------

_SEND_OPS = frozenset({"send", "forward"})
_WRITE_OPS = _SEND_OPS | frozenset({"move", "tag", "delete"})
# Not-found of the old id on these ops may mean the write, or an earlier attempt of it, already succeeded.
_ID_LOSS_UNKNOWN_OPS = frozenset({"move", "delete"})
_HTTP_REQUEST_TIMEOUT = 408
_HTTP_PRECONDITION_FAILED = 412
_HTTP_FORBIDDEN = 403
_HTTP_UNAUTHORIZED = 401
_HTTP_BAD_REQUEST = 400
_HTTP_NOT_FOUND = 404

_WRITE_TWINS: dict[type[ExchangeError], type[ExchangeError]] = {
    CredentialExpiredError: WriteCredentialExpiredError,
    CredentialInvalidError: WriteCredentialInvalidError,
    TenantError: WriteTenantError,
    AppNotFoundError: WriteAppNotFoundError,
    TokenRejectedError: WriteTokenRejectedError,
    ConsentError: WriteConsentError,
    MailboxNotEnabledError: WriteMailboxNotEnabledError,
    MailboxNotFoundError: WriteMailboxNotFoundError,
    FolderNotFoundError: WriteFolderNotFoundError,
    MessageNotFoundError: WriteMessageNotFoundError,
}

# Read errors that can surface while a write call is being opened (client construction, session build), before
# any request is sent. Kept apart from _WRITE_TWINS: after a submission the same read classes (an unreachable
# host) are Unknown, so they must not be twinned as Not applied there.
_WRITE_OPEN_TWINS: dict[type[ExchangeError], type[ExchangeError]] = {
    BadEndpointError: WriteBadEndpointError,
    UnreachableError: WriteUnreachableError,
}

# Failure kinds of a submission whose outcome cannot be proven.
_KIND_TIMEOUT = "timeout"
_KIND_CONNECTION = "connection"
_KIND_SERVER = "server"
_KIND_OTHER = "other"
_UNKNOWN_BY_KIND: dict[str, type[ExchangeError]] = {
    _KIND_TIMEOUT: WriteTimeoutUnknownError,
    _KIND_CONNECTION: WriteConnectionLostUnknownError,
    _KIND_SERVER: WriteServerErrorUnknownError,
    _KIND_OTHER: WriteUnknownError,
}


def _validate_op(op: str) -> None:
    if op not in _WRITE_OPS:
        raise ValueError(f"Unknown write operation: {op!r}")


def _unknown_or_pre_read(kind: str, before_mutation: bool) -> ExchangeError:
    """An unprovable failure is Unknown, unless it hit a read that precedes the change."""
    return PreMutationReadFailedError() if before_mutation else _UNKNOWN_BY_KIND[kind]()


def _write_access_denied(op: str, *, ews_transport: bool = False) -> ExchangeError:
    if op in _SEND_OPS:
        return EwsSendAccessDeniedError() if ews_transport else SendAccessDeniedError()
    return EwsWriteAccessDeniedError() if ews_transport else WriteAccessDeniedError()


def _write_message_not_found(op: str, before_mutation: bool, *, malformed: bool = False) -> ExchangeError:
    """Not applied or Unknown for a message id that was not found.

    A missing id on move/delete is Unknown at the pre-read too: a retry after an Unknown first attempt finds the
    id already changed. Only a malformed id seen before the change proves there was nothing to change.
    """
    if op in _ID_LOSS_UNKNOWN_OPS and not (before_mutation and malformed):
        return WriteMessageNotFoundUnknownError()
    return WriteMessageNotFoundError()


def _write_twin_or_fallback(exc: ExchangeError, op: str, before_mutation: bool) -> ExchangeError:
    """An outcome-less ``ExchangeError`` (a read error) reaching a write mapper or ``_inner_read``.

    Its own write twin, when one is registered, names the same cause precisely: a cause such as an
    expired credential or a missing folder is never "maybe sent", whichever call phase surfaced it.
    ``MessageNotFoundError`` is special-cased because the right class also depends on ``op`` (a lost
    id on move/delete may mean the write already happened). Anything without a twin is Not applied
    before the mutating request, and Unknown once one may already have been submitted.
    """
    if isinstance(exc, MessageNotFoundError):
        return _write_message_not_found(op, before_mutation)
    twin = _WRITE_TWINS.get(type(exc))
    if twin is not None:
        return twin()
    return PreMutationReadFailedError() if before_mutation else WriteUnknownError()


def map_write_open_error(exc: ExchangeError) -> ExchangeError:
    """Not applied error for a read error raised while opening a write call, before anything was sent.

    An error that already carries an outcome passes through. A known configuration or reachability cause
    (bad endpoint, allowlist rejection, DNS failure) keeps its specific Not applied message; any other
    outcome-less error is the generic ``PreMutationReadFailedError``.
    """
    if exc.OUTCOME is not None:
        return exc
    twin = _WRITE_OPEN_TWINS.get(type(exc))
    return twin() if twin is not None else PreMutationReadFailedError()


def _write_conflict(op: str) -> ExchangeError:
    return CategoryConflictError() if op == "tag" else WriteRejectedError()


def _graph_status_write(status: int, code: str | None, op: str, before_mutation: bool) -> ExchangeError:
    read_cls = _GRAPH_CODE_MAP.get((status, code))
    if read_cls is AccessDeniedError:
        return _write_access_denied(op)
    if read_cls is MessageNotFoundError:
        return _write_message_not_found(op, before_mutation, malformed=code == "ErrorInvalidIdMalformed")
    if read_cls is not None:
        return _WRITE_TWINS[read_cls]()
    if status == _HTTP_TOO_MANY_REQUESTS:
        return WriteRateLimitedError()
    if status == _HTTP_PRECONDITION_FAILED:
        return _write_conflict(op)
    if status == _HTTP_FORBIDDEN:
        return _write_access_denied(op)
    if status == _HTTP_UNAUTHORIZED:
        return WriteTokenRejectedError()  # never an expiry claim: only AADSTS7000222 proves that
    if status == _HTTP_BAD_REQUEST:
        return InvalidWriteInputError()
    if status == _HTTP_REQUEST_TIMEOUT:
        return _unknown_or_pre_read(_KIND_TIMEOUT, before_mutation)
    if status >= _HTTP_SERVER_ERROR_MIN:
        return _unknown_or_pre_read(_KIND_SERVER, before_mutation)
    if status == _HTTP_NOT_FOUND and op in _ID_LOSS_UNKNOWN_OPS and not before_mutation:
        return _write_message_not_found(op, before_mutation)  # the lost first attempt may have moved/deleted it
    if status >= _HTTP_BAD_REQUEST:
        return WriteRejectedError()
    return _unknown_or_pre_read(_KIND_OTHER, before_mutation)


def map_graph_write_exception(exc: BaseException, op: str, *, before_mutation: bool = False) -> ExchangeError:
    """Map a Graph/azure-identity exception raised by a write ``op`` to a Not applied or Unknown error.

    ``op`` is one of send, forward, move, tag, delete. ``before_mutation=True`` marks a failure of a read
    that precedes the change: nothing was submitted, so unprovable failures are Not applied.

    An ``exc`` that already carries an ``OUTCOME`` (a prior write error) is returned unchanged; one that
    does not (e.g. a read error raised while building the client or during an inner read) is converted
    via ``_write_twin_or_fallback`` instead of leaving the caller with no outcome.
    """
    _validate_op(op)
    if isinstance(exc, ExchangeError):
        return exc if exc.OUTCOME is not None else _write_twin_or_fallback(exc, op, before_mutation)
    mapped = _map_graph_write_exception_raw(exc, op, before_mutation)
    _log_write_mapping(op, exc, mapped, transport="graph")
    return mapped


def _map_graph_write_exception_raw(exc: BaseException, op: str, before_mutation: bool) -> ExchangeError:
    if _is_cert_verify_failure(exc):
        return WriteTlsConfigError()

    from azure.core.exceptions import ClientAuthenticationError

    if isinstance(exc, ClientAuthenticationError):
        match = _AADSTS_RE.search(str(exc))
        read_cls = _AADSTS_MAP.get(match.group(1) if match else "")
        return _WRITE_TWINS[read_cls]() if read_cls else WriteTokenRejectedError()

    status = graph_status(exc)
    if status is not None:
        return _graph_status_write(status, graph_code(exc), op, before_mutation)

    import httpx

    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return _unknown_or_pre_read(_KIND_TIMEOUT, before_mutation)
    if isinstance(exc, (OSError, httpx.TransportError)):
        return _unknown_or_pre_read(_KIND_CONNECTION, before_mutation)
    return _unknown_or_pre_read(_KIND_OTHER, before_mutation)


def _ews_timeout_kind(exc: BaseException) -> str:
    """Same split as _map_ews_timeout: the chained cause tells a timeout from a lost connection."""
    import requests

    cause = exc.__cause__ or exc.__context__
    if isinstance(cause, (requests.exceptions.Timeout, TimeoutError)):
        return _KIND_TIMEOUT
    if isinstance(cause, (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError, OSError)):
        return _KIND_CONNECTION
    return _KIND_TIMEOUT


def map_ews_write_exception(exc: BaseException, op: str, *, before_mutation: bool = False) -> ExchangeError:
    """Map an exchangelib exception raised by a write ``op`` to a Not applied or Unknown error.

    Only ``ErrorXxx`` codes that prove a pre-mutation rejection are Not applied; busy, transient,
    timeout and every unlisted failure stay Unknown. Body-proven ServerBusy handling lives in the
    write owner, which raises ``WriteRateLimitedError`` itself.

    An ``exc`` that already carries an ``OUTCOME`` (a prior write error) is returned unchanged; one that
    does not (e.g. a read error raised while building the client or during an inner read) is converted
    via ``_write_twin_or_fallback`` instead of leaving the caller with no outcome.
    """
    _validate_op(op)
    if isinstance(exc, ExchangeError):
        return exc if exc.OUTCOME is not None else _write_twin_or_fallback(exc, op, before_mutation)
    mapped = _map_ews_write_exception_raw(exc, op, before_mutation)
    _log_write_mapping(op, exc, mapped, transport="ews")
    return mapped


def _ews_invalid_id_types() -> tuple[type[BaseException], ...]:
    """Every ``ErrorInvalidId*`` of exchangelib: a malformed or unusable id is a message that cannot be found."""
    from exchangelib import errors as ews

    return tuple(
        getattr(ews, name)
        for name in (
            "ErrorInvalidId",
            "ErrorInvalidIdEmpty",
            "ErrorInvalidIdMalformed",
            "ErrorInvalidIdMalformedEwsLegacyIdFormat",
            "ErrorInvalidIdMonikerTooLong",
            "ErrorInvalidIdNotAnItemAttachmentId",
            "ErrorInvalidIdReturnedByResolveNames",
            "ErrorInvalidIdStoreObjectIdTooLong",
            "ErrorInvalidIdTooManyAttachmentLevels",
            "ErrorInvalidIdXml",
        )
    )


def _map_ews_write_exception_raw(exc: BaseException, op: str, before_mutation: bool) -> ExchangeError:
    if _is_cert_verify_failure(exc):
        return WriteTlsConfigError()

    from exchangelib import errors as ews

    if isinstance(exc, ews.ErrorTimeoutExpired):
        return _unknown_or_pre_read(_ews_timeout_kind(exc), before_mutation)

    # Not applied: the server or transport refused before mutating (most specific first).
    for types, cls in (
        (ews.RedirectError, WriteRedirectedError),
        (ews.UnauthorizedError, WriteEwsCredentialsRejectedError),
        (ews.ErrorSendAsDenied, EwsSendAccessDeniedError),
        (ews.ErrorNonExistentMailbox, WriteMailboxNotFoundError),
        ((ews.ErrorInvalidRecipients, ews.ErrorInvalidSmtpAddress, ews.ErrorInvalidFolderId), InvalidWriteInputError),
        ((ews.ErrorMessageSizeExceeded, ews.ErrorQuotaExceeded), WriteRejectedError),
    ):
        if isinstance(exc, types):
            return cls()
    if isinstance(exc, ews.ErrorFolderNotFound):
        # Our resolver raises WriteFolderNotFoundError itself for a name it cannot match. A server "folder not
        # found" on a lookup (root, well-known, Inbox) means the mailbox hides the folder: no rights, not a typo.
        return _write_access_denied(op, ews_transport=True) if before_mutation else WriteFolderNotFoundError()
    if isinstance(exc, ews.ErrorAccessDenied):
        return _write_access_denied(op, ews_transport=True)
    if isinstance(exc, ews.ErrorIrresolvableConflict):
        return _write_conflict(op)
    if isinstance(exc, ews.ErrorItemNotFound):
        return _write_message_not_found(op, before_mutation)
    if isinstance(exc, _ews_invalid_id_types()):
        return _write_message_not_found(op, before_mutation, malformed=True)

    # Unprovable: busy, transient and server faults may have followed a processed submission.
    if isinstance(exc, (ews.ErrorServerBusy, ews.RateLimitError)):
        return WriteRateLimitedError() if before_mutation else WriteServerErrorUnknownError()
    if isinstance(
        exc,
        (ews.ErrorInternalServerTransientError, ews.ErrorInternalServerError, ews.ErrorMailboxStoreUnavailable),
    ):
        return _unknown_or_pre_read(_KIND_SERVER, before_mutation)
    if isinstance(exc, OSError):
        return _unknown_or_pre_read(_KIND_CONNECTION, before_mutation)
    return _unknown_or_pre_read(_KIND_OTHER, before_mutation)
