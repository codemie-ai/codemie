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
"""Tests for the EWS-backed Exchange client (exchangelib fully faked, fixture-only credentials)."""

import datetime as dt
import ssl
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from exchangelib import DELEGATE, NTLM, FileAttachment, ItemAttachment
from exchangelib import errors as ews

from codemie_tools.ms_exchange import ews_client as ec
from codemie_tools.ms_exchange.client_base import MAX_PAGE
from codemie_tools.ms_exchange.errors import (
    BadEndpointError,
    DelegateAccessError,
    FolderNotFoundError,
    RedirectedError,
    TlsConfigError,
    UnreachableError,
)
from codemie_tools.ms_exchange.models import ExchangeAuthType, ExchangeToolConfig

EWS_URL = "https://ews.example.test/EWS/Exchange.asmx"
PUBLIC_IP = "93.184.215.14"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """Host names resolve to a public address and no allowlist is set, unless a test overrides it."""
    monkeypatch.delenv("EXCHANGE_EWS_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr("socket.getaddrinfo", lambda host, port, *a, **kw: [(None, None, None, "", (PUBLIC_IP, 0))])


def _config(url=EWS_URL):
    return ExchangeToolConfig(
        auth_type=ExchangeAuthType.NTLM,
        mailbox="shared@example.test",
        ews_url=url,
        service_account_username="svc-fixture",
        service_account_password="pw-fixture",
    )


class FakeQS:
    def __init__(self, items, log):
        self.items, self.log = items, log

    def all(self):
        return self

    def only(self, *fields):
        self.log["only"] = fields
        return self

    def order_by(self, *fields):
        self.log["order_by"] = fields
        return self

    def __getitem__(self, s):
        self.log["slice"] = (s.start, s.stop)
        return FakeQS(self.items, self.log)  # the server applies the window

    def __iter__(self):
        self.log["executed"] = True
        return iter(self.items)


def _mbox(name, addr):
    return SimpleNamespace(name=name, email_address=addr)


def _item(**kw):
    base = {
        "id": "m1",
        "subject": "Hi",
        "sender": _mbox("A", "a@example.test"),
        "datetime_received": dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc),
        "is_read": False,
        "has_attachments": True,
        "to_recipients": [_mbox("B", "b@example.test")],
        "cc_recipients": [],
        "text_body": "plain body",
        "body": "<p>html</p>",
        "attachments": [],
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _client(account):
    with patch.object(ec, "build_ews_client", return_value=account):
        return ec.EwsExchangeClient(_config())


def _account(items=(), log=None):
    log = {} if log is None else log
    acc = MagicMock()
    acc.inbox = FakeQS(list(items), log)
    acc.fetch.side_effect = lambda ids: iter(list(items))
    return acc


def test_build_ews_client_uses_ntlm_no_autodiscover_and_delegate():
    with patch.object(ec, "Account") as account_cls:
        ec.build_ews_client(_config())

    kwargs = account_cls.call_args.kwargs
    assert kwargs["autodiscover"] is False
    assert kwargs["access_type"] == DELEGATE
    cfg = kwargs["config"]
    assert cfg.service_endpoint == EWS_URL
    assert cfg.auth_type == NTLM
    assert cfg.credentials.username == "svc-fixture"


def test_build_ews_client_mailbox_is_distinct_from_service_account():
    with patch.object(ec, "Account") as account_cls:
        ec.build_ews_client(_config())

    kwargs = account_cls.call_args.kwargs
    assert kwargs["primary_smtp_address"] == "shared@example.test"
    assert kwargs["config"].credentials.username == "svc-fixture"
    assert kwargs["primary_smtp_address"] != kwargs["config"].credentials.username


def test_build_ews_client_bare_host_uses_server():
    with patch.object(ec, "Account") as account_cls:
        ec.build_ews_client(_config("ews.example.test"))

    assert account_cls.call_args.kwargs["config"].server == "ews.example.test"


def test_check_inbox_executes_id_only_find_item_max_one():
    log = {}
    _client(_account([], log)).check_inbox()

    assert log["only"] == ("id",)
    assert log["slice"] == (None, 1)
    assert log["executed"] is True


def test_redirect_error_maps_to_redirected():
    acc = _account()
    acc.inbox = MagicMock()
    acc.inbox.all.side_effect = ews.RedirectError("https://login.example/secret")

    with pytest.raises(RedirectedError) as info:
        _client(acc).check_inbox()

    assert "login.example" not in str(info.value)


def test_tls_trust_failure_is_not_retried():
    acc = _account()
    acc.inbox = MagicMock()
    acc.inbox.all.side_effect = ssl.SSLError("CERTIFICATE_VERIFY_FAILED host.example")

    with patch("codemie_tools.ms_exchange.retry.time.sleep") as sleep, pytest.raises(TlsConfigError):
        _client(acc).check_inbox()

    assert acc.inbox.all.call_count == 1
    sleep.assert_not_called()


def test_list_messages_window_sort_and_shape():
    log = {}
    older = _item(id="b", datetime_received=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
    tie_b = _item(id="z")
    tie_a = _item(id="a")
    acc = _account([older, tie_b, tie_a], log)

    result = _client(acc).list_messages("inbox", 5, 3)

    assert log["order_by"] == ("-datetime_received",)
    assert log["slice"] == (5, 8)
    assert [m["id"] for m in result] == ["a", "z", "b"]
    assert result[0] == {
        "id": "a",
        "subject": "Hi",
        "from": "A <a@example.test>",
        "received": "2026-01-02T03:04:05+00:00",
        "is_read": False,
        "has_attachments": True,
    }


def test_list_messages_caps_count_at_max_page():
    log = {}
    _client(_account([], log)).list_messages("inbox", 0, 10_000)

    assert log["slice"] == (0, MAX_PAGE)


def test_list_messages_unknown_folder_raises_folder_not_found():
    acc = _account()
    acc.msg_folder_root.walk.return_value = []

    with pytest.raises(FolderNotFoundError):
        _client(acc).list_messages("nope", 0, 5)


class _InboxRaises:
    """Account whose Inbox resolution fails the way exchangelib's lazy ``account.inbox`` does."""

    def __init__(self, exc):
        self._exc = exc

    @property
    def inbox(self):
        raise self._exc


_INBOX_RESOLUTION_FAILURES = [
    ews.ErrorFolderNotFound("No usable default Inbox folders"),
    ews.ErrorItemNotFound("secret-detail"),
    ews.ErrorAccessDenied("secret-detail"),
    ews.ErrorNonExistentMailbox("secret-detail"),
]


@pytest.mark.parametrize("failure", _INBOX_RESOLUTION_FAILURES, ids=lambda e: type(e).__name__)
def test_check_inbox_unresolvable_inbox_reports_mailbox_access_not_missing_folder(failure):
    with pytest.raises(DelegateAccessError) as info:
        _client(_InboxRaises(failure)).check_inbox()

    assert "secret-detail" not in str(info.value)


@pytest.mark.parametrize("failure", _INBOX_RESOLUTION_FAILURES, ids=lambda e: type(e).__name__)
def test_list_messages_unresolvable_inbox_raises_typed_error(failure):
    with pytest.raises(DelegateAccessError):
        _client(_InboxRaises(failure)).list_messages("Inbox", 0, 5)


def test_list_messages_inbox_resolution_transport_error_is_typed():
    with pytest.raises(RedirectedError):
        _client(_InboxRaises(ews.RedirectError("https://login.example/secret"))).list_messages("inbox", 0, 5)


def _folder(folder_id, name, container_class="IPF.Note", **kw):
    return SimpleNamespace(
        id=folder_id, name=name, container_class=container_class, total_count=4, unread_count=None, **kw
    )


def test_list_folders_shape():
    acc = _account()
    acc.msg_folder_root.walk.return_value = [_folder("f1", "Inbox")]

    assert _client(acc).list_folders() == [{"id": "f1", "name": "Inbox", "total_count": 4, "unread_count": 0}]


def test_list_folders_keeps_only_mail_folders():
    acc = _account()
    acc.msg_folder_root.walk.return_value = [
        _folder("f1", "Inbox"),
        _folder("f2", "Calendar", "IPF.Appointment"),
        _folder("f3", "Contacts", "IPF.Contact"),
        _folder("f4", "Tasks", "IPF.Task"),
        _folder("f5", "Generic", None),
    ]

    assert [f["name"] for f in _client(acc).list_folders()] == ["Inbox"]


def test_list_messages_resolves_name_case_insensitively():
    acc = _account()
    target = FakeQS([_item()], {})
    acc.msg_folder_root.walk.return_value = [_folder("f1", "Archive 2025", all=target.all)]

    assert _client(acc).list_messages("archive 2025", 0, 5)


def test_list_messages_ignores_non_mail_folder_with_same_name():
    acc = _account()
    acc.msg_folder_root.walk.return_value = [_folder("f2", "Calendar", "IPF.Appointment")]

    with pytest.raises(FolderNotFoundError):
        _client(acc).list_messages("calendar", 0, 5)


@pytest.mark.parametrize(
    "name,attribute",
    [
        ("sentitems", "sent"),
        ("SentItems", "sent"),
        ("deleteditems", "trash"),
        ("junkemail", "junk"),
        ("drafts", "drafts"),
    ],
)
def test_list_messages_accepts_graph_well_known_names(name, attribute):
    log = {}
    acc = _account(log=log)
    setattr(acc, attribute, FakeQS([_item()], log))

    assert _client(acc).list_messages(name, 0, 5)
    acc.msg_folder_root.walk.assert_not_called()


def test_list_messages_blank_folder_raises_folder_not_found():
    with pytest.raises(FolderNotFoundError):
        _client(_account()).list_messages("  ", 0, 5)


def test_get_message_prefers_text_body():
    result = _client(_account([_item()])).get_message("m1")

    assert result["body"] == "plain body"
    assert result["to"] == ["B <b@example.test>"]
    assert result["cc"] == []


def test_get_message_strips_html_when_no_text_body():
    item = _item(text_body=None, body="<html><style>x{}</style><p>Hello &amp; <b>bye</b></p></html>")

    assert _client(_account([item])).get_text_body("m1") == "Hello & bye"


def test_get_message_error_item_is_mapped():
    acc = _account([ews.ErrorItemNotFound("secret-id")])

    with pytest.raises(Exception) as info:
        _client(acc).get_message("m1")

    assert type(info.value).__name__ == "MessageNotFoundError"


def test_attachments_and_bytes():
    file_att = MagicMock(spec=FileAttachment)
    file_att.attachment_id = SimpleNamespace(id="a1")
    file_att.name, file_att.content_type, file_att.size, file_att.content = "f.txt", "text/plain", 3, b"abc"
    item_att = MagicMock(spec=ItemAttachment)
    item_att.attachment_id = SimpleNamespace(id="a2")
    item_att.name, item_att.content_type, item_att.size = "fwd", None, 9
    client = _client(_account([_item(attachments=[file_att, item_att])]))

    assert client.list_attachments("m1") == [
        {"id": "a1", "name": "f.txt", "content_type": "text/plain", "size": 3, "is_file": True},
        {"id": "a2", "name": "fwd", "content_type": "", "size": 9, "is_file": False},
    ]
    assert client.get_attachment_bytes("m1", "a1") == b"abc"


@pytest.mark.parametrize(
    "url", ["http://ews.example.test/EWS/Exchange.asmx", "ftp://ews.example.test", "https://", "https:///EWS"]
)
def test_build_ews_client_rejects_non_https_or_invalid_host(url):
    with patch.object(ec, "Account") as account_cls, pytest.raises(BadEndpointError):
        ec.build_ews_client(_config(url))
    account_cls.assert_not_called()


def test_build_ews_client_timeout_is_per_connection_not_global():
    from exchangelib.protocol import BaseProtocol

    before = BaseProtocol.TIMEOUT
    with patch.object(ec, "Account") as account_cls:
        account = ec.build_ews_client(_config())
    assert before == BaseProtocol.TIMEOUT
    assert account is account_cls.return_value
    assert account.protocol.TIMEOUT == ec.REQUEST_TIMEOUT_SECONDS


def test_attachment_content_fetch_is_retried_and_mapped():
    calls = {"n": 0}

    class Lazy:
        attachment_id = SimpleNamespace(id="a1")

        @property
        def content(self):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ews.ErrorInternalServerTransientError("secret")
            return b"abc"

    att = Lazy()
    with (
        patch("codemie_tools.ms_exchange.ews_client.FileAttachment", Lazy),
        patch("codemie_tools.ms_exchange.retry.time.sleep"),
    ):
        assert _client(_account([_item(attachments=[att])])).get_attachment_bytes("m1", "a1") == b"abc"
    assert calls["n"] == 2


def test_attachment_content_fetch_error_is_mapped():
    class Lazy:
        attachment_id = SimpleNamespace(id="a1")

        @property
        def content(self):
            raise ews.RedirectError("https://login.example/secret")

    with patch("codemie_tools.ms_exchange.ews_client.FileAttachment", Lazy), pytest.raises(RedirectedError):
        _client(_account([_item(attachments=[Lazy()])])).get_attachment_bytes("m1", "a1")


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost/EWS/Exchange.asmx",
        "https://LOCALHOST/EWS/Exchange.asmx",
        "localhost",
        "https://127.0.0.1/EWS/Exchange.asmx",
        "127.0.0.1",
        "https://169.254.169.254/latest/meta-data",
        "169.254.169.254",
        "https://0.0.0.0/EWS",
        "0.0.0.0",
        "https://10.1.2.3/EWS",
        "https://172.16.0.5/EWS",
        "https://172.31.255.255/EWS",
        "https://192.168.1.10/EWS",
        "192.168.1.10",
        "https://[::1]/EWS",
        "[::1]",
        "https://[::]/EWS",
        "https://[fe80::1]/EWS",
        "https://[fc00::1]/EWS",
        "https://[::ffff:127.0.0.1]/EWS",
        "https://[::ffff:10.0.0.1]/EWS",
    ],
)
def test_endpoint_rejects_internal_hosts_without_echoing_them(url):
    with pytest.raises(BadEndpointError) as exc:
        ec._endpoint(url)
    message = str(exc.value)
    assert message == BadEndpointError.MESSAGE
    for fragment in ("localhost", "127.", "169.254", "10.1", "192.168", "::", "fe80"):
        assert fragment not in message.lower()


@pytest.mark.parametrize("url", ["https://8.8.8.8/EWS", "8.8.8.8", "https://172.32.0.1/EWS", "mail.corp.example"])
def test_endpoint_still_accepts_public_hosts(url):
    assert ec._endpoint(url)


def _resolves(*ips):
    """getaddrinfo stand-in returning one entry per address, in the shape socket uses."""
    return lambda host, port, *a, **kw: [(None, None, None, "", (ip, 0)) for ip in ips]


@pytest.mark.parametrize(
    "ips",
    [
        ("127.0.0.1",),
        ("10.0.0.5",),
        ("169.254.169.254",),
        ("::1",),
        ("fe80::1",),
        ("100.64.0.1",),
        ("93.184.215.14", "10.0.0.5"),
        ("93.184.215.14", "127.0.0.1"),
        ("::ffff:169.254.169.254",),
    ],
)
def test_endpoint_rejects_names_resolving_to_internal_addresses(ips):
    with patch.object(ec.socket, "getaddrinfo", _resolves(*ips)), pytest.raises(BadEndpointError) as exc:
        ec._endpoint("https://internal-service.corp.local/EWS/Exchange.asmx")
    assert str(exc.value) == BadEndpointError.MESSAGE


def test_build_ews_client_rejects_internal_name_before_connecting():
    with (
        patch.object(ec.socket, "getaddrinfo", _resolves("10.0.0.5")),
        patch.object(ec, "Account") as account_cls,
        pytest.raises(BadEndpointError),
    ):
        ec.build_ews_client(_config("https://internal-service.corp.local/EWS/Exchange.asmx"))
    account_cls.assert_not_called()


@pytest.mark.parametrize("allowed", ["internal-service.corp.local", "INTERNAL-service.corp.local.", "10.0.0.0/8"])
def test_endpoint_accepts_allowlisted_private_name(monkeypatch, allowed):
    monkeypatch.setenv(ec.ALLOWED_HOSTS_ENV, f"other.example, {allowed}")
    with patch.object(ec.socket, "getaddrinfo", _resolves("10.0.0.5")):
        assert ec._endpoint("https://internal-service.corp.local/EWS/Exchange.asmx")


@pytest.mark.parametrize("url", ["https://10.0.0.5/EWS/Exchange.asmx", "10.0.0.5", "https://[fd00::5]/EWS"])
def test_endpoint_accepts_allowlisted_private_literal(monkeypatch, url):
    monkeypatch.setenv(ec.ALLOWED_HOSTS_ENV, "10.0.0.5,fd00::/8")
    assert ec._endpoint(url)


def test_allowlist_does_not_cover_other_private_addresses(monkeypatch):
    monkeypatch.setenv(ec.ALLOWED_HOSTS_ENV, "10.0.0.0/24")
    with patch.object(ec.socket, "getaddrinfo", _resolves("10.0.0.5", "192.168.1.1")), pytest.raises(BadEndpointError):
        ec._endpoint("https://internal-service.corp.local/EWS")


@pytest.mark.parametrize(
    ("url", "ips"),
    [
        ("https://127.0.0.1/EWS", ()),
        ("https://[fe80::1]/EWS", ()),
        ("https://localhost/EWS", ()),
        ("https://metadata.corp.local/EWS", ("169.254.169.254",)),
        ("https://loop.corp.local/EWS", ("127.0.0.1",)),
    ],
)
def test_allowlist_never_permits_loopback_or_link_local(monkeypatch, url, ips):
    monkeypatch.setenv(
        ec.ALLOWED_HOSTS_ENV, "localhost,metadata.corp.local,loop.corp.local,127.0.0.0/8,169.254.0.0/16,fe80::/10"
    )
    with patch.object(ec.socket, "getaddrinfo", _resolves(*ips)), pytest.raises(BadEndpointError):
        ec._endpoint(url)


@pytest.mark.parametrize(
    "resolver", [MagicMock(side_effect=OSError("no such host")), _resolves()], ids=["error", "empty"]
)
def test_endpoint_unresolvable_name_is_unreachable(resolver):
    with patch.object(ec.socket, "getaddrinfo", resolver), pytest.raises(UnreachableError) as exc:
        ec._endpoint("https://typo.corp.example/EWS")
    assert str(exc.value) == UnreachableError.MESSAGE


def test_endpoint_dns_timeout_is_unreachable():
    def slow(*args, **kwargs):
        time.sleep(0.5)
        return []

    with (
        patch.object(ec, "_DNS_TIMEOUT_SECONDS", 0.01),
        patch.object(ec.socket, "getaddrinfo", slow),
        pytest.raises(UnreachableError),
    ):
        ec._endpoint("https://slow.corp.example/EWS")


def test_list_messages_requests_only_summary_fields():
    log = {}
    client = _client(_account([_item()], log))
    client.list_messages("inbox", 0, 5)
    assert log["only"] == ec._SUMMARY_FIELDS


def test_summary_tolerates_non_message_item():
    assert ec._summary(SimpleNamespace(id="x"))["subject"] == ""


def test_fetch_is_reused_across_enrich_calls():
    acc = _account([_item()])
    client = _client(acc)
    client.get_text_body("m1")
    client.list_attachments("m1")
    client.get_message("m1")
    assert acc.fetch.call_count == 1
    client.get_text_body("m2")
    assert acc.fetch.call_count == 2
