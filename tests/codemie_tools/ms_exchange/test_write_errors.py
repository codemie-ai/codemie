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
"""Tests for the outcome-aware (Not applied / Unknown) write error taxonomy and mappings."""

import inspect
import logging
import re
import socket
import ssl
from unittest import mock
from unittest.mock import MagicMock

import httpx
import pytest
import requests
from azure.core.exceptions import ClientAuthenticationError
from exchangelib import errors as ews
from kiota_abstractions.api_error import APIError

from codemie_tools.ms_exchange import errors as exch

LEAK = "tenant-1234 https://login.example/x svc@corp.local msg-AAMk secret-xyz"
LEAK_FRAGMENTS = ("tenant-1234", "login.example", "svc@corp.local", "msg-AAMk", "secret-xyz", "x.example")

NOT_APPLIED = "Not applied."
UNKNOWN = "Unknown."
SEND_OPS = ("send", "forward")
WRITE_OPS = ("move", "tag", "delete")
ALL_OPS = SEND_OPS + WRITE_OPS
# "Mail.ReadWrite" must not match: only a bare Mail.Read is the read permission.
BARE_MAIL_READ = re.compile(r"Mail\.Read(?!Write)")


def _api(status, code=None):
    exc = APIError(LEAK, status, None)
    if code:
        exc.error = MagicMock(code=code)
    return exc


def _reraised_as_timeout_expired(cause):
    try:
        raise cause
    except Exception:
        try:
            raise ews.ErrorTimeoutExpired(f"Reraised from {LEAK}")
        except ews.ErrorTimeoutExpired as exc:
            return exc


def _reraised_as(outer, cause):
    """Reraise ``outer`` with ``cause`` as its implicit ``__context__``, as transport wrappers do."""
    try:
        raise cause
    except Exception:
        try:
            raise outer
        except Exception as exc:
            return exc


def _outcome_classes():
    return [
        c
        for _, c in inspect.getmembers(exch, inspect.isclass)
        if issubclass(c, exch.ExchangeError) and c.OUTCOME is not None
    ]


def _check(mapped, cls, op):
    """Shared assertions for every write mapping row."""
    assert type(mapped) is cls
    assert isinstance(mapped, exch.ExchangeError)
    assert mapped.OUTCOME == cls.OUTCOME and mapped.OUTCOME in ("not_applied", "unknown")
    assert mapped.RETRYABLE is False
    prefix = NOT_APPLIED if mapped.OUTCOME == "not_applied" else UNKNOWN
    assert str(mapped) == cls.MESSAGE and str(mapped).startswith(prefix + " ")
    for leak in LEAK_FRAGMENTS:
        assert leak not in str(mapped)
    assert not BARE_MAIL_READ.search(str(mapped))


# ---- class invariants ----


def test_exchange_error_defaults_leave_reads_retryable_and_without_outcome():
    assert exch.ExchangeError.OUTCOME is None and exch.ExchangeError.RETRYABLE is True
    for cls in (
        exch.CredentialExpiredError,
        exch.FolderNotFoundError,
        exch.MessageNotFoundError,
        exch.AccessDeniedError,
        exch.UnreachableError,
        exch.RateLimitedError,
        exch.ServiceTimeoutError,
        exch.TransientServerError,
        exch.InternalError,
    ):
        assert cls.OUTCOME is None and cls.RETRYABLE is True
        assert not cls.MESSAGE.startswith((NOT_APPLIED, UNKNOWN))


def test_outcome_is_derived_from_a_subclass_declaring_outcome_without_setting_retryable():
    class _NewUnknownWrite(exch.ExchangeError):
        MESSAGE = "Unknown. Test only."
        OUTCOME = exch.WriteOutcome.UNKNOWN

    assert _NewUnknownWrite.RETRYABLE is False

    class _NewNotAppliedWrite(exch.ExchangeError):
        MESSAGE = "Not applied. Test only."
        OUTCOME = exch.WriteOutcome.NOT_APPLIED

    assert _NewNotAppliedWrite.RETRYABLE is False


def test_outcome_class_creation_rejects_a_non_enum_outcome():
    with pytest.raises(TypeError):

        class _BadOutcomeWrite(exch.ExchangeError):
            MESSAGE = "Not applied. Test only."
            OUTCOME = "not_applied"  # a plain string, not WriteOutcome.NOT_APPLIED


def test_outcome_class_creation_allows_an_explicit_retryable_override():
    class _OverriddenRetryableWrite(exch.ExchangeError):
        MESSAGE = "Unknown. Test only."
        OUTCOME = exch.WriteOutcome.UNKNOWN
        RETRYABLE = True

    assert _OverriddenRetryableWrite.RETRYABLE is True


def test_outcome_bases():
    assert exch.NotAppliedError.OUTCOME == "not_applied" and exch.NotAppliedError.RETRYABLE is False
    assert exch.UnknownOutcomeError.OUTCOME == "unknown" and exch.UnknownOutcomeError.RETRYABLE is False
    assert issubclass(exch.NotAppliedError, exch.ExchangeError)
    assert issubclass(exch.UnknownOutcomeError, exch.ExchangeError)
    assert str(exch.NotAppliedError()).startswith(NOT_APPLIED + " ")
    assert str(exch.UnknownOutcomeError()).startswith(UNKNOWN + " ")


def test_every_outcome_class_is_non_retryable_with_matching_prefix_and_advice():
    classes = _outcome_classes()
    assert len(classes) >= 20
    for cls in classes:
        prefix = NOT_APPLIED if cls.OUTCOME == "not_applied" else UNKNOWN
        assert cls.RETRYABLE is False, cls
        assert cls.MESSAGE.startswith(prefix + " "), cls
        assert cls.MESSAGE.endswith("."), cls
        assert not BARE_MAIL_READ.search(cls.MESSAGE), cls
        assert str(cls()) == cls.MESSAGE
    assert (
        len({c.MESSAGE for c in classes if c not in (exch.NotAppliedError, exch.UnknownOutcomeError)})
        == len(classes) - 2
    )


def test_unknown_outcome_messages_warn_against_blind_replay():
    for cls in _outcome_classes():
        if cls.OUTCOME == "unknown":
            assert "may or may not" in cls.MESSAGE.lower() or "may already" in cls.MESSAGE.lower(), cls
            assert "before" in cls.MESSAGE.lower(), cls  # "check ... before trying again"


@pytest.mark.parametrize(
    "twin,read_cls",
    [
        (exch.WriteCredentialExpiredError, exch.CredentialExpiredError),
        (exch.WriteCredentialInvalidError, exch.CredentialInvalidError),
        (exch.WriteTenantError, exch.TenantError),
        (exch.WriteAppNotFoundError, exch.AppNotFoundError),
        (exch.WriteTokenRejectedError, exch.TokenRejectedError),
        (exch.WriteConsentError, exch.ConsentError),
        (exch.WriteMailboxNotEnabledError, exch.MailboxNotEnabledError),
        (exch.WriteMailboxNotFoundError, exch.MailboxNotFoundError),
        (exch.WriteFolderNotFoundError, exch.FolderNotFoundError),
        (exch.WriteMessageNotFoundError, exch.MessageNotFoundError),
        (exch.WriteTlsConfigError, exch.TlsConfigError),
        (exch.WriteRedirectedError, exch.RedirectedError),
        (exch.WriteEwsCredentialsRejectedError, exch.EwsCredentialsRejectedError),
    ],
)
def test_write_twin_is_a_non_retryable_not_applied_subclass_of_the_read_class(twin, read_cls):
    assert issubclass(twin, read_cls) and issubclass(twin, exch.NotAppliedError)
    assert twin.OUTCOME == "not_applied" and twin.RETRYABLE is False
    assert twin.MESSAGE != read_cls.MESSAGE
    # The read class is untouched.
    assert read_cls.OUTCOME is None and read_cls.RETRYABLE is True


def test_permission_messages_name_the_operation_permission_without_asserting_a_cause():
    send = exch.SendAccessDeniedError.MESSAGE
    write = exch.WriteAccessDeniedError.MESSAGE
    assert "Mail.Send" in send and "Mail.ReadWrite" not in send
    assert "Mail.ReadWrite" in write and "Mail.Send" not in write
    for msg in (send, write, exch.EwsSendAccessDeniedError.MESSAGE, exch.EwsWriteAccessDeniedError.MESSAGE):
        assert "administrator consent" in msg.lower() or "send as" in msg.lower() or "delegate" in msg.lower()
        assert "access restrictions" in msg.lower()
        assert "possible" in msg.lower() or "may" in msg.lower()
        assert msg.startswith(NOT_APPLIED + " ")
    for msg in (send, write):
        assert "administrator consent" in msg.lower()
    assert "Send As" in exch.EwsSendAccessDeniedError.MESSAGE


# ---- Graph write mapping ----

GRAPH_OP_INDEPENDENT = [
    (_api(401, "InvalidAuthenticationToken"), exch.WriteTokenRejectedError),
    (_api(401), exch.WriteTokenRejectedError),
    (_api(403, "Authorization_RequestDenied"), exch.WriteConsentError),
    (_api(403, "MailboxNotEnabledForRESTAPI"), exch.WriteMailboxNotEnabledError),
    (_api(404, "ErrorInvalidUser"), exch.WriteMailboxNotFoundError),
    (_api(404, "ResourceNotFound"), exch.WriteMailboxNotFoundError),
    (_api(404, "ErrorFolderNotFound"), exch.WriteFolderNotFoundError),
    (_api(400, "Weird"), exch.InvalidWriteInputError),
    (_api(400), exch.InvalidWriteInputError),
    (_api(409), exch.WriteRejectedError),
    (_api(429), exch.WriteRateLimitedError),
    (_api(500), exch.WriteServerErrorUnknownError),
    (_api(502), exch.WriteServerErrorUnknownError),
    (_api(503), exch.WriteServerErrorUnknownError),
    (_api(504), exch.WriteServerErrorUnknownError),
    (httpx.ReadTimeout(LEAK), exch.WriteTimeoutUnknownError),
    (TimeoutError(LEAK), exch.WriteTimeoutUnknownError),
    (ConnectionError(LEAK), exch.WriteConnectionLostUnknownError),
    (OSError(LEAK), exch.WriteConnectionLostUnknownError),
    (httpx.ConnectError(LEAK), exch.WriteConnectionLostUnknownError),
    (ValueError(LEAK), exch.WriteUnknownError),
    (RuntimeError(LEAK), exch.WriteUnknownError),
    (ssl.SSLCertVerificationError(1, LEAK), exch.WriteTlsConfigError),
    # A non-certificate SSL failure (EOF, bad record MAC) after submission is unprovable, not TLS config:
    # the server may already have accepted the request.
    (_reraised_as(httpx.ReadError(LEAK), ssl.SSLEOFError(LEAK)), exch.WriteConnectionLostUnknownError),
    (ClientAuthenticationError(f"AADSTS7000222: {LEAK} 401 Unauthorized"), exch.WriteCredentialExpiredError),
    (ClientAuthenticationError(f"AADSTS7000215: {LEAK}"), exch.WriteCredentialInvalidError),
    (ClientAuthenticationError(f"AADSTS90002: {LEAK}"), exch.WriteTenantError),
    (ClientAuthenticationError(f"AADSTS700016: {LEAK}"), exch.WriteAppNotFoundError),
    (ClientAuthenticationError(f"AADSTS999999: {LEAK} 401"), exch.WriteTokenRejectedError),
    (ClientAuthenticationError(f"no code here {LEAK}"), exch.WriteTokenRejectedError),
]


@pytest.mark.parametrize("op", ALL_OPS)
@pytest.mark.parametrize("exc,cls", GRAPH_OP_INDEPENDENT)
def test_graph_write_mapping_table(exc, cls, op):
    _check(exch.map_graph_write_exception(exc, op), cls, op)


@pytest.mark.parametrize("op", ALL_OPS)
def test_graph_generic_401_and_unknown_aadsts_never_map_to_expired_credential(op):
    for exc in (_api(401), ClientAuthenticationError("AADSTS999999 401"), ClientAuthenticationError("401")):
        assert not isinstance(exch.map_graph_write_exception(exc, op), exch.CredentialExpiredError)


@pytest.mark.parametrize(
    "op,cls,permission",
    [
        ("send", exch.SendAccessDeniedError, "Mail.Send"),
        ("forward", exch.SendAccessDeniedError, "Mail.Send"),
        ("move", exch.WriteAccessDeniedError, "Mail.ReadWrite"),
        ("tag", exch.WriteAccessDeniedError, "Mail.ReadWrite"),
        ("delete", exch.WriteAccessDeniedError, "Mail.ReadWrite"),
    ],
)
@pytest.mark.parametrize("exc", [_api(403, "ErrorAccessDenied"), _api(403), _api(403, "SomethingNew")])
def test_graph_write_access_denied_names_permission_per_operation(exc, op, cls, permission):
    mapped = exch.map_graph_write_exception(exc, op)
    _check(mapped, cls, op)
    assert permission in str(mapped)


@pytest.mark.parametrize("op", ALL_OPS)
@pytest.mark.parametrize(
    "code,status", [("ErrorItemNotFound", 404), ("ErrorInvalidIdMalformed", 400), ("ErrorInvalidIdMalformed", 404)]
)
def test_graph_message_not_found_depends_on_operation(code, status, op):
    mapped = exch.map_graph_write_exception(_api(status, code), op)
    expected = exch.WriteMessageNotFoundUnknownError if op in ("move", "delete") else exch.WriteMessageNotFoundError
    _check(mapped, expected, op)


@pytest.mark.parametrize("op", ALL_OPS)
@pytest.mark.parametrize("exc", [_api(404), _api(404, "SomethingNew")])
def test_graph_unmapped_404_is_unknown_for_move_and_delete_only(exc, op):
    mapped = exch.map_graph_write_exception(exc, op)
    expected = exch.WriteMessageNotFoundUnknownError if op in ("move", "delete") else exch.WriteRejectedError
    _check(mapped, expected, op)


@pytest.mark.parametrize("op", ["move", "delete"])
def test_graph_unmapped_404_in_a_pre_mutation_read_stays_not_applied(op):
    mapped = exch.map_graph_write_exception(_api(404), op, before_mutation=True)
    _check(mapped, exch.WriteRejectedError, op)


@pytest.mark.parametrize("op", ALL_OPS)
def test_graph_412_is_a_category_conflict_for_tag_and_a_rejection_otherwise(op):
    mapped = exch.map_graph_write_exception(_api(412, "ErrorIrresolvableConflict"), op)
    _check(mapped, exch.CategoryConflictError if op == "tag" else exch.WriteRejectedError, op)
    plain = exch.map_graph_write_exception(_api(412), op)
    _check(plain, exch.CategoryConflictError if op == "tag" else exch.WriteRejectedError, op)


def test_graph_write_passes_existing_outcome_bearing_errors_through():
    original = exch.WriteRateLimitedError()
    assert exch.map_graph_write_exception(original, "send") is original


@pytest.mark.parametrize("op", ALL_OPS)
def test_graph_write_converts_an_incoming_outcome_less_exchange_error_with_a_twin(op):
    """A raw read error (no OUTCOME) reaching the write mapper must not stay outcome-less: it is converted,
    using its registered write twin (e.g. a vanished folder is always Not applied, whichever call phase
    surfaced it), never passed through unchanged."""
    mapped = exch.map_graph_write_exception(exch.FolderNotFoundError(), op)
    _check(mapped, exch.WriteFolderNotFoundError, op)
    assert mapped.OUTCOME is not None


@pytest.mark.parametrize("op", ALL_OPS)
def test_graph_write_converts_an_incoming_outcome_less_exchange_error_without_a_twin(op):
    """No registered twin: Not applied before the mutating request, Unknown once one may have been sent."""
    not_applied = exch.map_graph_write_exception(exch.UnreachableError(), op, before_mutation=True)
    _check(not_applied, exch.PreMutationReadFailedError, op)
    unknown = exch.map_graph_write_exception(exch.UnreachableError(), op, before_mutation=False)
    _check(unknown, exch.WriteUnknownError, op)


@pytest.mark.parametrize("op", ALL_OPS)
def test_graph_write_never_uses_read_transient_classes(op):
    read_only = (exch.UnreachableError, exch.ServiceTimeoutError, exch.TransientServerError, exch.RateLimitedError)
    for exc in (_api(503), _api(429), TimeoutError(LEAK), ConnectionError(LEAK), httpx.ReadTimeout(LEAK)):
        assert not isinstance(exch.map_graph_write_exception(exc, op), read_only)


# ---- a non-certificate SSL failure is never reported as proven pre-send (TLS config) ----


@pytest.mark.parametrize("op", ALL_OPS)
def test_graph_non_cert_ssl_failure_after_submission_is_unknown_not_tls_config(op):
    exc = _reraised_as(httpx.ReadError(LEAK), ssl.SSLEOFError(LEAK))
    mapped = exch.map_graph_write_exception(exc, op)
    _check(mapped, exch.WriteConnectionLostUnknownError, op)
    assert not isinstance(mapped, exch.WriteTlsConfigError)


@pytest.mark.parametrize("op", ALL_OPS)
def test_graph_non_cert_ssl_failure_before_mutation_is_not_applied_but_not_tls_config(op):
    exc = _reraised_as(httpx.ReadError(LEAK), ssl.SSLEOFError(LEAK))
    mapped = exch.map_graph_write_exception(exc, op, before_mutation=True)
    _check(mapped, exch.PreMutationReadFailedError, op)
    assert not isinstance(mapped, exch.WriteTlsConfigError)


@pytest.mark.parametrize("op", ALL_OPS)
def test_graph_cert_verification_failure_is_still_tls_config(op):
    mapped = exch.map_graph_write_exception(ssl.SSLCertVerificationError(1, LEAK), op)
    _check(mapped, exch.WriteTlsConfigError, op)


def test_graph_write_rejects_unknown_operation():
    with pytest.raises(ValueError):
        exch.map_graph_write_exception(_api(500), "rename")
    with pytest.raises(ValueError):
        exch.map_ews_write_exception(ConnectionError(LEAK), "rename")


# ---- before_mutation: a failed pre-mutation read is always Not applied ----


@pytest.mark.parametrize("op", ALL_OPS)
@pytest.mark.parametrize(
    "exc,cls",
    [
        (httpx.ReadTimeout(LEAK), exch.PreMutationReadFailedError),
        (ConnectionError(LEAK), exch.PreMutationReadFailedError),
        (_api(503), exch.PreMutationReadFailedError),
        (ValueError(LEAK), exch.PreMutationReadFailedError),
        (_reraised_as(httpx.ReadError(LEAK), ssl.SSLEOFError(LEAK)), exch.PreMutationReadFailedError),
        (_api(429), exch.WriteRateLimitedError),
        (_api(403, "ErrorAccessDenied"), None),
        (_api(404, "ErrorFolderNotFound"), exch.WriteFolderNotFoundError),
    ],
)
def test_graph_before_mutation_failures_are_not_applied(exc, cls, op):
    mapped = exch.map_graph_write_exception(exc, op, before_mutation=True)
    assert mapped.OUTCOME == "not_applied" and mapped.RETRYABLE is False
    if cls is not None:
        _check(mapped, cls, op)


@pytest.mark.parametrize("op", ALL_OPS)
@pytest.mark.parametrize(
    "exc,cls",
    [
        (ews.ErrorTimeoutExpired(LEAK), exch.PreMutationReadFailedError),
        (ews.ErrorServerBusy(LEAK), exch.WriteRateLimitedError),
        (ews.ErrorInternalServerTransientError(LEAK), exch.PreMutationReadFailedError),
        (ConnectionError(LEAK), exch.PreMutationReadFailedError),
        (ews.RateLimitError(LEAK, wait=30), exch.WriteRateLimitedError),
        (_reraised_as_timeout_expired(requests.exceptions.SSLError(LEAK)), exch.PreMutationReadFailedError),
    ],
)
def test_ews_before_mutation_failures_are_not_applied(exc, cls, op):
    _check(exch.map_ews_write_exception(exc, op, before_mutation=True), cls, op)


@pytest.mark.parametrize("op", ALL_OPS)
@pytest.mark.parametrize(
    "exc,mapper",
    [
        (_api(404, "ErrorItemNotFound"), exch.map_graph_write_exception),
        (ews.ErrorItemNotFound(LEAK), exch.map_ews_write_exception),
    ],
)
def test_missing_message_before_mutation_is_unknown_on_move_and_delete(exc, mapper, op):
    # A retry after an Unknown move/delete finds the old id gone: that may be the earlier attempt's success.
    expected = exch.WriteMessageNotFoundUnknownError if op in ("move", "delete") else exch.WriteMessageNotFoundError
    _check(mapper(exc, op, before_mutation=True), expected, op)


@pytest.mark.parametrize("op", ALL_OPS)
@pytest.mark.parametrize("status", [400, 404])
def test_graph_malformed_id_before_mutation_is_not_applied_not_found(status, op):
    mapped = exch.map_graph_write_exception(_api(status, "ErrorInvalidIdMalformed"), op, before_mutation=True)
    _check(mapped, exch.WriteMessageNotFoundError, op)


# ---- EWS write mapping ----

EWS_OP_INDEPENDENT = [
    (ews.UnauthorizedError(LEAK), exch.WriteEwsCredentialsRejectedError),
    (ews.RedirectError("https://x.example/ews"), exch.WriteRedirectedError),
    (ews.ErrorSendAsDenied(LEAK), exch.EwsSendAccessDeniedError),
    (ews.ErrorNonExistentMailbox(LEAK), exch.WriteMailboxNotFoundError),
    (ews.ErrorFolderNotFound(LEAK), exch.WriteFolderNotFoundError),
    (ews.ErrorInvalidRecipients(LEAK), exch.InvalidWriteInputError),
    (ews.ErrorInvalidSmtpAddress(LEAK), exch.InvalidWriteInputError),
    (ews.ErrorMessageSizeExceeded(LEAK), exch.WriteRejectedError),
    (ews.ErrorQuotaExceeded(LEAK), exch.WriteRejectedError),
    (ews.ErrorServerBusy(LEAK), exch.WriteServerErrorUnknownError),
    (ews.RateLimitError(LEAK, wait=30), exch.WriteServerErrorUnknownError),
    (ews.ErrorInternalServerTransientError(LEAK), exch.WriteServerErrorUnknownError),
    (ews.ErrorInternalServerError(LEAK), exch.WriteServerErrorUnknownError),
    (ews.ErrorMailboxStoreUnavailable(LEAK), exch.WriteServerErrorUnknownError),
    (ews.ErrorTimeoutExpired(LEAK), exch.WriteTimeoutUnknownError),
    (_reraised_as_timeout_expired(requests.exceptions.ReadTimeout(LEAK)), exch.WriteTimeoutUnknownError),
    (_reraised_as_timeout_expired(requests.exceptions.ConnectTimeout(LEAK)), exch.WriteTimeoutUnknownError),
    (_reraised_as_timeout_expired(socket.timeout(LEAK)), exch.WriteTimeoutUnknownError),
    (
        _reraised_as_timeout_expired(requests.exceptions.ConnectionError(LEAK)),
        exch.WriteConnectionLostUnknownError,
    ),
    (
        _reraised_as_timeout_expired(requests.exceptions.ChunkedEncodingError(LEAK)),
        exch.WriteConnectionLostUnknownError,
    ),
    (_reraised_as_timeout_expired(ConnectionResetError(LEAK)), exch.WriteConnectionLostUnknownError),
    (ConnectionError(LEAK), exch.WriteConnectionLostUnknownError),
    (ews.SOAPError(LEAK), exch.WriteUnknownError),
    (ews.TransportError(LEAK), exch.WriteUnknownError),
    (ews.ErrorItemSave(LEAK), exch.WriteUnknownError),
    (ValueError(LEAK), exch.WriteUnknownError),
    (ssl.SSLCertVerificationError(1, LEAK), exch.WriteTlsConfigError),
    # A non-certificate SSL failure (EOF, bad record MAC) after submission is unprovable, not TLS config:
    # the server may already have accepted the request. exchangelib re-raises it as ErrorTimeoutExpired.
    (_reraised_as_timeout_expired(requests.exceptions.SSLError(LEAK)), exch.WriteConnectionLostUnknownError),
    (
        _reraised_as(requests.exceptions.ConnectionError(LEAK), ssl.SSLEOFError(LEAK)),
        exch.WriteConnectionLostUnknownError,
    ),
]


@pytest.mark.parametrize("op", ALL_OPS)
@pytest.mark.parametrize("exc,cls", EWS_OP_INDEPENDENT)
def test_ews_write_mapping_table(exc, cls, op):
    _check(exch.map_ews_write_exception(exc, op), cls, op)


@pytest.mark.parametrize(
    "op,cls",
    [
        ("send", exch.EwsSendAccessDeniedError),
        ("forward", exch.EwsSendAccessDeniedError),
        ("move", exch.EwsWriteAccessDeniedError),
        ("tag", exch.EwsWriteAccessDeniedError),
        ("delete", exch.EwsWriteAccessDeniedError),
    ],
)
def test_ews_access_denied_is_write_aware_per_operation(op, cls):
    mapped = exch.map_ews_write_exception(ews.ErrorAccessDenied(LEAK), op)
    _check(mapped, cls, op)
    assert not isinstance(mapped, exch.DelegateAccessError)


@pytest.mark.parametrize(
    "op,cls",
    [
        ("send", exch.EwsSendAccessDeniedError),
        ("forward", exch.EwsSendAccessDeniedError),
        ("move", exch.EwsWriteAccessDeniedError),
        ("tag", exch.EwsWriteAccessDeniedError),
        ("delete", exch.EwsWriteAccessDeniedError),
    ],
)
def test_ews_folder_not_found_on_a_lookup_means_no_access_not_a_wrong_folder_name(op, cls):
    mapped = exch.map_ews_write_exception(ews.ErrorFolderNotFound(LEAK), op, before_mutation=True)
    _check(mapped, cls, op)
    assert not isinstance(mapped, exch.FolderNotFoundError)


@pytest.mark.parametrize("op", ALL_OPS)
@pytest.mark.parametrize("exc", [ews.ErrorItemNotFound(LEAK), ews.ErrorInvalidIdMalformed(LEAK)])
def test_ews_message_not_found_depends_on_operation(exc, op):
    mapped = exch.map_ews_write_exception(exc, op)
    expected = exch.WriteMessageNotFoundUnknownError if op in ("move", "delete") else exch.WriteMessageNotFoundError
    _check(mapped, expected, op)


@pytest.mark.parametrize("op", ALL_OPS)
def test_ews_irresolvable_conflict_is_category_conflict_for_tag(op):
    mapped = exch.map_ews_write_exception(ews.ErrorIrresolvableConflict(LEAK), op)
    _check(mapped, exch.CategoryConflictError if op == "tag" else exch.WriteRejectedError, op)


def test_ews_write_passes_existing_outcome_bearing_errors_through():
    original = exch.CategoryConflictError()
    assert exch.map_ews_write_exception(original, "tag") is original


@pytest.mark.parametrize("op", ALL_OPS)
def test_ews_write_converts_an_incoming_outcome_less_exchange_error_with_a_twin(op):
    mapped = exch.map_ews_write_exception(exch.MailboxNotFoundError(), op)
    _check(mapped, exch.WriteMailboxNotFoundError, op)
    assert mapped.OUTCOME is not None


@pytest.mark.parametrize("op", ALL_OPS)
def test_ews_write_converts_an_incoming_outcome_less_exchange_error_without_a_twin(op):
    not_applied = exch.map_ews_write_exception(exch.UnreachableError(), op, before_mutation=True)
    _check(not_applied, exch.PreMutationReadFailedError, op)
    unknown = exch.map_ews_write_exception(exch.UnreachableError(), op, before_mutation=False)
    _check(unknown, exch.WriteUnknownError, op)


@pytest.mark.parametrize("op", ALL_OPS)
def test_write_mapper_message_not_found_twin_still_depends_on_op(op):
    """MessageNotFoundError's twin depends on ``op`` (a lost id on move/delete may mean success)."""
    expected = exch.WriteMessageNotFoundUnknownError if op in ("move", "delete") else exch.WriteMessageNotFoundError
    _check(exch.map_graph_write_exception(exch.MessageNotFoundError(), op), expected, op)
    _check(exch.map_ews_write_exception(exch.MessageNotFoundError(), op), expected, op)


@pytest.mark.parametrize("op", ALL_OPS)
def test_ews_write_never_uses_read_transient_classes(op):
    read_only = (
        exch.UnreachableError,
        exch.ServiceTimeoutError,
        exch.TransientServerError,
        exch.RateLimitedError,
        exch.DelegateAccessError,
        exch.BadEndpointError,
    )
    for exc in (
        ews.ErrorServerBusy(LEAK),
        ews.ErrorInternalServerTransientError(LEAK),
        ews.ErrorTimeoutExpired(LEAK),
        ews.ErrorAccessDenied(LEAK),
        ews.TransportError(LEAK),
        ConnectionError(LEAK),
    ):
        assert not isinstance(exch.map_ews_write_exception(exc, op), read_only)


# ---- a non-certificate SSL failure is never reported as proven pre-send (TLS config) ----


@pytest.mark.parametrize("op", ALL_OPS)
def test_ews_non_cert_ssl_failure_after_submission_is_unknown_not_tls_config(op):
    exc = _reraised_as_timeout_expired(requests.exceptions.SSLError(LEAK))
    mapped = exch.map_ews_write_exception(exc, op)
    _check(mapped, exch.WriteConnectionLostUnknownError, op)
    assert not isinstance(mapped, exch.WriteTlsConfigError)


@pytest.mark.parametrize("op", ALL_OPS)
def test_ews_non_cert_ssl_failure_before_mutation_is_not_applied_but_not_tls_config(op):
    exc = _reraised_as_timeout_expired(requests.exceptions.SSLError(LEAK))
    mapped = exch.map_ews_write_exception(exc, op, before_mutation=True)
    _check(mapped, exch.PreMutationReadFailedError, op)
    assert not isinstance(mapped, exch.WriteTlsConfigError)


@pytest.mark.parametrize("op", ALL_OPS)
def test_ews_cert_verification_failure_is_still_tls_config(op):
    mapped = exch.map_ews_write_exception(ssl.SSLCertVerificationError(1, LEAK), op)
    _check(mapped, exch.WriteTlsConfigError, op)


def test_read_mappings_are_unchanged_by_the_write_mappings():
    assert type(exch.map_graph_exception(_api(503))) is exch.UnreachableError
    assert type(exch.map_graph_exception(_api(429))) is exch.RateLimitedError
    assert type(exch.map_ews_exception(ews.ErrorAccessDenied(LEAK))) is exch.DelegateAccessError
    assert type(exch.map_ews_exception(ews.ErrorTimeoutExpired(LEAK))) is exch.ServiceTimeoutError
    # Reads are always safe to retry: a non-cert SSL failure still maps to TlsConfigError, unlike writes.
    assert type(exch.map_graph_exception(ssl.SSLEOFError(LEAK))) is exch.TlsConfigError
    assert type(exch.map_ews_exception(ssl.SSLEOFError(LEAK))) is exch.TlsConfigError


# ---- write-path mapping is logged without the provider's exception text ----
#
# ``codemie_tools.ms_exchange.errors.logger`` is a descendant of the ``codemie_tools`` logger, which
# dictConfig sets to ``propagate: False`` (see src/codemie/configs/logger.py and the precedent at
# tests/codemie_tools/sharepoint/test_tools.py::TestAuditLogging), so pytest's ``caplog`` (attached to
# the root logger) never sees these records. The module logger is patched directly instead.


class TestWriteMappingLogging:
    @staticmethod
    def _logged_messages(mock_logger):
        return [call.args for call in mock_logger.log.call_args_list]

    @mock.patch("codemie_tools.ms_exchange.errors.logger")
    def test_graph_unknown_outcome_is_logged_as_warning_without_the_secret(self, mock_logger):
        exc = _api(503)
        mapped = exch.map_graph_write_exception(exc, "send")

        assert isinstance(mapped, exch.WriteServerErrorUnknownError)
        [(level, template, *args)] = self._logged_messages(mock_logger)
        assert level == logging.WARNING
        rendered = template % tuple(args)
        assert "op=send" in rendered
        assert "mapped=WriteServerErrorUnknownError" in rendered
        assert "outcome=unknown" in rendered
        for fragment in LEAK_FRAGMENTS:
            assert fragment not in rendered

    @mock.patch("codemie_tools.ms_exchange.errors.logger")
    def test_ews_unknown_outcome_is_logged_as_warning_without_the_secret(self, mock_logger):
        exc = ews.ErrorInternalServerError(LEAK)
        mapped = exch.map_ews_write_exception(exc, "move")

        assert isinstance(mapped, exch.WriteServerErrorUnknownError)
        [(level, template, *args)] = self._logged_messages(mock_logger)
        assert level == logging.WARNING
        rendered = template % tuple(args)
        assert "op=move" in rendered
        assert "mapped=WriteServerErrorUnknownError" in rendered
        assert "outcome=unknown" in rendered
        for fragment in LEAK_FRAGMENTS:
            assert fragment not in rendered

    @mock.patch("codemie_tools.ms_exchange.errors.logger")
    def test_not_applied_outcome_is_logged_below_warning(self, mock_logger):
        exc = _api(400)
        mapped = exch.map_graph_write_exception(exc, "tag")

        assert isinstance(mapped, exch.InvalidWriteInputError)
        [(level, *_rest)] = self._logged_messages(mock_logger)
        assert level < logging.WARNING

    @mock.patch("codemie_tools.ms_exchange.errors.logger")
    def test_passthrough_of_an_already_mapped_error_is_not_logged_again(self, mock_logger):
        """An exc that already carries an OUTCOME is returned unchanged, with no new mapping to log."""
        already_mapped = exch.WriteRateLimitedError()
        mapped = exch.map_graph_write_exception(already_mapped, "move")

        assert mapped is already_mapped
        mock_logger.log.assert_not_called()


@pytest.mark.parametrize(
    "name",
    [
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
    ],
)
def test_every_ews_invalid_id_error_is_a_message_that_cannot_be_found(name):
    exc = getattr(ews, name)(LEAK)

    before = exch.map_ews_write_exception(exc, "delete", before_mutation=True)
    after = exch.map_ews_write_exception(exc, "delete", before_mutation=False)

    assert type(before) is exch.WriteMessageNotFoundError  # nothing was sent yet: Not applied
    assert type(after) is exch.WriteMessageNotFoundUnknownError  # after the delete it may already have gone through
    assert LEAK not in str(before)
