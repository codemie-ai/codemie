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

import re
import ssl
from langchain_core.tools import ToolException


class ExchangeError(ToolException):
    """Base class; the message is always the class-level MESSAGE."""

    MESSAGE = "An internal error occurred while accessing Exchange."

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


def graph_status(exc: BaseException) -> int | None:
    return getattr(exc, "response_status_code", None) or getattr(exc, "status_code", None)


def graph_code(exc: BaseException) -> str | None:
    error = getattr(exc, "error", None)
    return getattr(error, "code", None) or getattr(exc, "code", None)


def map_graph_exception(exc: BaseException) -> ExchangeError:
    """Map an azure-identity / msgraph exception to a typed, sanitised error."""
    if isinstance(exc, ExchangeError):
        return exc
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
    """Map an exchangelib exception to a typed, sanitised error."""
    if isinstance(exc, ExchangeError):
        return exc
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
