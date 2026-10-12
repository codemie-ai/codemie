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
"""Tests for the Exchange typed error taxonomy and retry helper."""

import socket
import ssl
from unittest.mock import MagicMock, patch

import httpx
import pytest
import requests
from azure.core.exceptions import ClientAuthenticationError
from exchangelib import errors as ews
from kiota_abstractions.api_error import APIError
from langchain_core.tools import ToolException

from codemie_tools.ms_exchange import errors as exch
from codemie_tools.ms_exchange.retry import MAX_RETRIES, MAX_RETRY_AFTER_SECONDS, call_with_retry

LEAK = "tenant-1234 https://login.example/x svc@corp.local msg-AAMk secret-xyz"

ALL = [
    exch.CredentialExpiredError,
    exch.CredentialInvalidError,
    exch.TenantError,
    exch.AppNotFoundError,
    exch.TokenRejectedError,
    exch.ConsentError,
    exch.MailboxNotEnabledError,
    exch.AccessDeniedError,
    exch.MailboxNotFoundError,
    exch.FolderNotFoundError,
    exch.MessageNotFoundError,
    exch.UnreachableError,
    exch.RateLimitedError,
    exch.ServiceTimeoutError,
    exch.TransientServerError,
    exch.TlsConfigError,
    exch.BadEndpointError,
    exch.RedirectedError,
    exch.EwsCredentialsRejectedError,
    exch.DelegateAccessError,
    exch.InternalError,
]


def _api(status, code=None, headers=None):
    exc = APIError(LEAK, status, headers)
    if code:
        exc.error = MagicMock(code=code)
    return exc


def test_each_class_has_distinct_fixed_sentence():
    msgs = [c.MESSAGE for c in ALL]
    assert len(set(msgs)) == len(ALL)
    for c in ALL:
        assert issubclass(c, exch.ExchangeError) and issubclass(c, ToolException)
        assert str(c()) == c.MESSAGE and c.MESSAGE.endswith(".")


def test_exact_sentences():
    assert (
        exch.CredentialExpiredError.MESSAGE
        == "The Microsoft Graph client secret has expired. Ask an administrator to issue a new secret."
    )
    assert (
        exch.CredentialInvalidError.MESSAGE
        == "The Microsoft Graph client secret is invalid. Check the configured client secret."
    )
    assert exch.TenantError.MESSAGE == "The Microsoft Entra tenant was not found. Check the configured tenant."
    assert (
        exch.AppNotFoundError.MESSAGE
        == "The Microsoft Entra application was not found. Check the configured client ID."
    )
    assert (
        exch.TokenRejectedError.MESSAGE
        == "Microsoft Graph rejected the access token. Retry the request or check the app registration."
    )
    assert (
        exch.ConsentError.MESSAGE
        == "Administrator consent is missing or was revoked for the required Microsoft Graph permissions."
    )
    assert (
        exch.MailboxNotEnabledError.MESSAGE
        == "The mailbox is not licensed or not enabled for the Microsoft Graph REST API."
    )
    assert exch.AccessDeniedError.MESSAGE == (
        "Access to this mailbox was denied. Check that the app registration has the Mail.Read application "
        "permission with administrator consent, and that no Application Access Policy blocks the mailbox."
    )
    assert exch.MailboxNotFoundError.MESSAGE == "The mailbox was not found."
    assert exch.FolderNotFoundError.MESSAGE == "The mail folder was not found."
    assert exch.MessageNotFoundError.MESSAGE == "The message was not found."
    assert exch.UnreachableError.MESSAGE == "The Exchange service is unreachable. Try again later."
    assert exch.RateLimitedError.MESSAGE == "The Exchange service is rate limiting requests. Try again later."
    assert exch.ServiceTimeoutError.MESSAGE == "The Exchange service did not respond in time. Try again later."
    assert (
        exch.TransientServerError.MESSAGE == "The Exchange service reported a temporary server error. Try again later."
    )
    assert (
        exch.TlsConfigError.MESSAGE
        == "The TLS certificate of the Exchange service could not be verified. Check the endpoint certificate configuration."
    )
    assert (
        exch.BadEndpointError.MESSAGE == "The configured Exchange EWS endpoint is not usable. Check the endpoint URL."
    )
    assert (
        exch.RedirectedError.MESSAGE
        == "The Exchange endpoint redirected the request. Configure the direct EWS endpoint URL."
    )
    assert (
        exch.EwsCredentialsRejectedError.MESSAGE
        == "The Exchange service account credentials were rejected. Check the configured username and password."
    )
    assert exch.DelegateAccessError.MESSAGE == "Delegate access to the mailbox is denied or the mailbox does not exist."
    assert exch.InternalError.MESSAGE == "An internal error occurred while accessing Exchange."


@pytest.mark.parametrize(
    "code,cls",
    [
        ("7000222", exch.CredentialExpiredError),
        ("7000215", exch.CredentialInvalidError),
        ("90002", exch.TenantError),
        ("700016", exch.AppNotFoundError),
    ],
)
def test_aadsts_keyed_on_code(code, cls):
    exc = ClientAuthenticationError(f"AADSTS{code}: {LEAK} 401 Unauthorized")
    mapped = exch.map_graph_exception(exc)
    assert type(mapped) is cls
    assert "tenant-1234" not in str(mapped)


def test_unknown_aadsts_with_401_is_not_expired():
    mapped = exch.map_graph_exception(ClientAuthenticationError("AADSTS999999 401"))
    assert type(mapped) is exch.InternalError


@pytest.mark.parametrize(
    "status,code,cls",
    [
        (401, "InvalidAuthenticationToken", exch.TokenRejectedError),
        (403, "Authorization_RequestDenied", exch.ConsentError),
        (403, "MailboxNotEnabledForRESTAPI", exch.MailboxNotEnabledError),
        (403, "ErrorAccessDenied", exch.AccessDeniedError),
        (404, "ErrorInvalidUser", exch.MailboxNotFoundError),
        (404, "ResourceNotFound", exch.MailboxNotFoundError),
        (404, "ErrorFolderNotFound", exch.FolderNotFoundError),
        (404, "ErrorItemNotFound", exch.MessageNotFoundError),
        (400, "ErrorInvalidIdMalformed", exch.MessageNotFoundError),
        (404, "ErrorInvalidIdMalformed", exch.MessageNotFoundError),
        (429, None, exch.RateLimitedError),
        (503, None, exch.UnreachableError),
        (500, None, exch.UnreachableError),
        (400, "Weird", exch.InternalError),
        (401, None, exch.InternalError),
    ],
)
def test_graph_mapping(status, code, cls):
    mapped = exch.map_graph_exception(_api(status, code))
    assert type(mapped) is cls
    assert "tenant-1234" not in str(mapped) and "msg-AAMk" not in str(mapped)


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionError(LEAK),
        OSError(LEAK),
    ],
)
def test_graph_network_errors_unreachable(exc):
    assert type(exch.map_graph_exception(exc)) is exch.UnreachableError


@pytest.mark.parametrize("exc", [TimeoutError(LEAK), httpx.ReadTimeout(LEAK)])
def test_graph_timeouts_are_not_reported_as_unreachable(exc):
    assert type(exch.map_graph_exception(exc)) is exch.ServiceTimeoutError


def test_tls_failure_maps_to_tls_config_both_transports():
    exc = ssl.SSLCertVerificationError(1, LEAK)
    assert type(exch.map_graph_exception(exc)) is exch.TlsConfigError
    assert type(exch.map_ews_exception(exc)) is exch.TlsConfigError


def test_existing_exchange_error_passes_through():
    original = exch.FolderNotFoundError()
    assert exch.map_graph_exception(original) is original
    assert exch.map_ews_exception(original) is original


@pytest.mark.parametrize(
    "exc,cls",
    [
        (ews.SOAPError(LEAK), exch.BadEndpointError),
        (ews.TransportError(LEAK), exch.BadEndpointError),
        (ews.RedirectError("https://x.example/ews"), exch.RedirectedError),
        (ews.UnauthorizedError(LEAK), exch.EwsCredentialsRejectedError),
        (ews.ErrorAccessDenied(LEAK), exch.DelegateAccessError),
        (ews.ErrorNonExistentMailbox(LEAK), exch.DelegateAccessError),
        (ews.ErrorServerBusy(LEAK), exch.RateLimitedError),
        (ews.RateLimitError(LEAK, wait=30), exch.RateLimitedError),
        (ews.ErrorInternalServerTransientError(LEAK), exch.TransientServerError),
        (ews.ErrorTimeoutExpired(LEAK), exch.ServiceTimeoutError),
        (ews.ErrorItemNotFound(LEAK), exch.MessageNotFoundError),
        (ews.ErrorInvalidIdMalformed(LEAK), exch.MessageNotFoundError),
        (ews.ErrorFolderNotFound(LEAK), exch.FolderNotFoundError),
        (ConnectionError(LEAK), exch.UnreachableError),
        (ValueError(LEAK), exch.InternalError),
    ],
)
def test_ews_mapping(exc, cls):
    mapped = exch.map_ews_exception(exc)
    assert type(mapped) is cls
    for leak in ("tenant-1234", "svc@corp.local", "x.example"):
        assert leak not in str(mapped)


def _reraised_as_timeout_expired(cause):
    """Mimic exchangelib.util.post_ratelimited, which re-raises connection errors as ErrorTimeoutExpired."""
    try:
        raise cause
    except Exception:
        try:
            raise ews.ErrorTimeoutExpired(f"Reraised from {LEAK}")
        except ews.ErrorTimeoutExpired as exc:
            return exc


@pytest.mark.parametrize(
    "cause,cls",
    [
        (requests.exceptions.ReadTimeout(LEAK), exch.ServiceTimeoutError),
        (requests.exceptions.ConnectTimeout(LEAK), exch.ServiceTimeoutError),
        (socket.timeout(LEAK), exch.ServiceTimeoutError),
        (requests.exceptions.ConnectionError(LEAK), exch.UnreachableError),
        (requests.exceptions.ChunkedEncodingError(LEAK), exch.UnreachableError),
        (ConnectionResetError(LEAK), exch.UnreachableError),
    ],
)
def test_ews_timeout_expired_is_split_by_connection_cause(cause, cls):
    mapped = exch.map_ews_exception(_reraised_as_timeout_expired(cause))
    assert type(mapped) is cls
    assert "tenant-1234" not in str(mapped)


# ---- retry ----


@pytest.fixture
def sleeps():
    with patch("codemie_tools.ms_exchange.retry.time.sleep") as s:
        yield s


def test_returns_value_without_sleep(sleeps):
    assert call_with_retry(lambda: 5) == 5
    sleeps.assert_not_called()


def test_retries_429_then_succeeds(sleeps):
    fn = MagicMock(side_effect=[_api(429), _api(503), "ok"])
    assert call_with_retry(fn) == "ok"
    assert fn.call_count == 3 and sleeps.call_count == 2


def test_exhausted_retries_raise_original(sleeps):
    fn = MagicMock(side_effect=_api(429))
    with pytest.raises(APIError):
        call_with_retry(fn)
    assert fn.call_count == MAX_RETRIES + 1


def test_retry_after_honoured_and_capped(sleeps):
    fn = MagicMock(
        side_effect=[_api(429, headers={"Retry-After": "3"}), _api(429, headers={"retry-after": "9999"}), "ok"]
    )
    call_with_retry(fn)
    assert [c.args[0] for c in sleeps.call_args_list] == [3, MAX_RETRY_AFTER_SECONDS]


def test_ews_back_off_honoured_and_capped(sleeps):
    busy = ews.ErrorServerBusy(LEAK, back_off=2)
    huge = ews.ErrorServerBusy(LEAK, back_off=10_000)
    call_with_retry(MagicMock(side_effect=[busy, huge, "ok"]))
    assert [c.args[0] for c in sleeps.call_args_list] == [2, MAX_RETRY_AFTER_SECONDS]


@pytest.mark.parametrize(
    "exc",
    [
        ews.ErrorInternalServerTransientError(LEAK),
        ews.ErrorTimeoutExpired(LEAK),
    ],
)
def test_ews_transient_retried(sleeps, exc):
    fn = MagicMock(side_effect=[exc, "ok"])
    assert call_with_retry(fn) == "ok"
    assert fn.call_count == 2


@pytest.mark.parametrize(
    "exc",
    [
        ClientAuthenticationError("AADSTS7000222"),
        ews.UnauthorizedError(LEAK),
        ssl.SSLCertVerificationError(1, LEAK),
        ews.RedirectError("https://x"),
        ews.SOAPError(LEAK),
        _api(401, "InvalidAuthenticationToken"),
        _api(403),
        _api(404),
        exch.CredentialInvalidError(),
        ValueError("x"),
    ],
)
def test_never_retried(sleeps, exc):
    fn = MagicMock(side_effect=exc)
    with pytest.raises(type(exc)):
        call_with_retry(fn)
    assert fn.call_count == 1
    sleeps.assert_not_called()
