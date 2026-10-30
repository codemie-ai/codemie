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
"""EWS write plumbing: private protocol, deadline adapter, transport evidence and the write owner wiring.

No network: ``requests.adapters.HTTPAdapter.send`` is replaced by a scripted fake server, so the real
exchangelib stack (session pool, NTLM auth object, FailFast policy, SOAP parsing) runs end to end.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable
from unittest import mock

import pytest
import requests
from lxml import etree
from exchangelib import NTLM, Configuration, Credentials, FailFast
from exchangelib import errors as ews
from exchangelib.items import ForwardItem, Message
from exchangelib.protocol import Protocol
from exchangelib.util import to_xml
from requests.adapters import HTTPAdapter

from codemie_tools.ms_exchange import ews_client as ec
from codemie_tools.ms_exchange import ews_write_session as ews_session
from codemie_tools.ms_exchange.errors import (
    BadEndpointError,
    CategoryConflictError,
    DeadlineExhaustedError,
    EwsSendAccessDeniedError,
    EwsWriteAccessDeniedError,
    ExchangeError,
    InvalidLocalInputError,
    PreMutationReadFailedError,
    UnknownOutcomeError,
    UnreachableError,
    WriteConnectionLostUnknownError,
    WriteFolderNotFoundError,
    WriteMessageNotFoundError,
    WriteMessageNotFoundUnknownError,
    WriteRateLimitedError,
    WriteBadEndpointError,
    WriteTimeoutUnknownError,
    WriteUnreachableError,
)
from codemie_tools.ms_exchange.models import ExchangeAuthType, ExchangeToolConfig
from codemie_tools.ms_exchange.write_retry import Retry, Stop

EWS_URL = "https://ews.example.test/EWS/Exchange.asmx"
PUBLIC_IP = "93.184.215.14"
SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
MNS = "http://schemas.microsoft.com/exchange/services/2006/messages"
TNS = "http://schemas.microsoft.com/exchange/services/2006/types"
ENS = "http://schemas.microsoft.com/exchange/services/2006/errors"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Public DNS, no allowlist, and no cached Protocol leaking between tests."""
    monkeypatch.delenv("EXCHANGE_EWS_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr("socket.getaddrinfo", lambda host, port, *a, **kw: [(None, None, None, "", (PUBLIC_IP, 0))])
    Protocol.clear_cache()
    yield
    Protocol.clear_cache()


def _config(url: str = EWS_URL) -> ExchangeToolConfig:
    return ExchangeToolConfig(
        auth_type=ExchangeAuthType.NTLM,
        mailbox="shared@example.test",
        ews_url=url,
        service_account_username="svc-fixture",
        service_account_password="pw-fixture",
    )


# ---- fakes ---------------------------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _envelope(body: str, header: str = "") -> bytes:
    version = (
        f'<h:ServerVersionInfo xmlns:h="{TNS}" MajorVersion="15" MinorVersion="1" '
        f'MajorBuildNumber="2507" MinorBuildNumber="0" Version="V2016_06_24"/>'
    )
    return (
        f'<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="{SOAP}">'
        f"<s:Header>{version}{header}</s:Header><s:Body>{body}</s:Body></s:Envelope>"
    ).encode()


PROBE_OK = _envelope(
    f'<m:ConvertIdResponse xmlns:m="{MNS}"><m:ResponseMessages>'
    '<m:ConvertIdResponseMessage ResponseClass="Error"><m:MessageText>x</m:MessageText>'
    "<m:ResponseCode>ErrorInvalidIdMalformed</m:ResponseCode><m:DescriptiveLinkKey>0</m:DescriptiveLinkKey>"
    "</m:ConvertIdResponseMessage></m:ResponseMessages></m:ConvertIdResponse>"
)

GET_ROOT_OK = _envelope(
    f'<m:GetFolderResponse xmlns:m="{MNS}" xmlns:t="{TNS}"><m:ResponseMessages>'
    '<m:GetFolderResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode><m:Folders>'
    '<t:Folder><t:FolderId Id="ROOT" ChangeKey="RK"/><t:DisplayName>Top of Information Store</t:DisplayName>'
    "</t:Folder></m:Folders></m:GetFolderResponseMessage></m:ResponseMessages></m:GetFolderResponse>"
)


def _folder_ok(folder_id: str, name: str) -> bytes:
    return _envelope(
        f'<m:GetFolderResponse xmlns:m="{MNS}" xmlns:t="{TNS}"><m:ResponseMessages>'
        '<m:GetFolderResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode><m:Folders>'
        f'<t:Folder><t:FolderId Id="{folder_id}" ChangeKey="K"/><t:DisplayName>{name}</t:DisplayName>'
        "</t:Folder></m:Folders></m:GetFolderResponseMessage></m:ResponseMessages></m:GetFolderResponse>"
    )


GET_SENT_OK = _folder_ok("SENT", "Sent Items")

# The ids the fake server gives the main folders of the configured mailbox, by distinguished name.
MAILBOX_FOLDER_IDS = {
    "msgfolderroot": "MSGROOT",
    "inbox": "INBOX",
    "sentitems": "SENT",
    "deleteditems": "TRASH",
    "drafts": "DRAFTS",
    "junkemail": "JUNK",
}

GET_ITEM_OK = _envelope(
    f'<m:GetItemResponse xmlns:m="{MNS}" xmlns:t="{TNS}"><m:ResponseMessages>'
    '<m:GetItemResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode><m:Items>'
    '<t:Message><t:ItemId Id="AAA" ChangeKey="CK1"/><t:Subject>Hi</t:Subject>'
    '<t:ParentFolderId Id="INBOX" ChangeKey="PK"/></t:Message>'
    "</m:Items></m:GetItemResponseMessage></m:ResponseMessages></m:GetItemResponse>"
)

GET_ITEM_NOT_FOUND = _envelope(
    f'<m:GetItemResponse xmlns:m="{MNS}"><m:ResponseMessages>'
    '<m:GetItemResponseMessage ResponseClass="Error"><m:MessageText>gone</m:MessageText>'
    "<m:ResponseCode>ErrorItemNotFound</m:ResponseCode><m:DescriptiveLinkKey>0</m:DescriptiveLinkKey>"
    "</m:GetItemResponseMessage></m:ResponseMessages></m:GetItemResponse>"
)


def _fault_server_busy(back_off_ms: int | str | None) -> bytes:
    message_xml = (
        ""
        if back_off_ms is None
        else f'<t:MessageXml xmlns:t="{TNS}"><t:Value Name="BackOffMilliseconds">{back_off_ms}</t:Value></t:MessageXml>'
    )
    return (
        f'<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="{SOAP}"><s:Body><s:Fault>'
        f'<faultcode xmlns:a="{TNS}">a:ErrorServerBusy</faultcode><faultstring>busy</faultstring>'
        f'<detail><e:ResponseCode xmlns:e="{ENS}">ErrorServerBusy</e:ResponseCode>'
        f'<e:Message xmlns:e="{ENS}">busy</e:Message>{message_xml}</detail></s:Fault></s:Body></s:Envelope>'
    ).encode()


@dataclass
class Reply:
    """One scripted HTTP reply. ``elapse`` advances the fake clock while the request is 'in flight'."""

    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    elapse: float = 0.0
    raises: BaseException | None = None


def _is_housekeeping(body: bytes) -> bool:
    return b"ConvertId" in body or b"GetFolder" in body


def _requested_folders(body: bytes) -> list[tuple[str, str]]:
    """``(element name, id)`` of each folder a GetFolder request asks for, in order."""
    ids = to_xml(body).find(f".//{{{MNS}}}FolderIds")
    return [(child.tag.rsplit("}", 1)[-1], child.get("Id")) for child in ids]


@dataclass
class Sent:
    url: str
    timeout: object
    body: bytes
    remaining_at_send: float | None = None


class FakeServer:
    """Replaces ``HTTPAdapter.send``.

    The ConvertId version probe, the GetFolder root lookup that ``account.fetch`` performs first and the Sent Items
    folder lookup (``sent_folder_reply``) are always answered; every other request consumes the next scripted reply.
    """

    def __init__(self, clock: FakeClock, *replies: Reply) -> None:
        self.clock = clock
        self.replies = list(replies)
        self.sent: list[Sent] = []
        self.probes = 0
        self.sent_folder_reply = Reply(body=GET_SENT_OK)  # the answer to the Sent Items folder lookup
        # Answers to GetFolder of a distinguished folder, by its id ("inbox", "deleteditems", ...).
        self.folder_replies: dict[str, Reply] = {"deleteditems": Reply(body=_folder_ok("TRASH", "Deleted Items"))}
        self.known_folders: dict[str, str] = {}  # folder id -> its <t:Folder> XML, learned from FindFolder replies
        self.folder_error_codes: dict[str, str] = {}  # folder id -> the ResponseCode GetFolder answers for it
        self.walk_raises: BaseException | None = None  # raised by any GetFolder that asks for a folder by id

    @property
    def requests(self) -> list[Sent]:
        """The scripted requests: everything except the version probe and the root folder lookup."""
        return [s for s in self.sent if not _is_housekeeping(s.body)]

    def _learn_folders(self, content: bytes) -> None:
        for folder in to_xml(content).iter(f"{{{TNS}}}Folder"):
            folder_id = folder.find(f"{{{TNS}}}FolderId").get("Id")
            self.known_folders[folder_id] = etree.tostring(folder).decode()

    def _folder_reply(self, body: bytes) -> Reply:
        """GetFolder: one scripted answer for a lone distinguished folder, one message per id for the rest.

        exchangelib resolves all distinguished folders in one GetFolder (the server here has none of them), then
        FindFolder, then GetFolder by id for what FindFolder returned: those answer from ``known_folders``.
        """
        requested = _requested_folders(body)
        if {folder_id for _, folder_id in requested} == set(MAILBOX_FOLDER_IDS):
            return self._mailbox_folders_reply(requested)
        if self.walk_raises is not None and any(kind == "FolderId" for kind, _ in requested):
            raise self.walk_raises
        scripted = self.known_folders.keys() | self.folder_error_codes.keys()
        if len(requested) == 1 and requested[0][1] not in scripted:
            return self._single_folder_reply(body)
        messages = "".join(self._folder_message(kind, folder_id) for kind, folder_id in requested)
        return Reply(
            body=_envelope(
                f'<m:GetFolderResponse xmlns:m="{MNS}" xmlns:t="{TNS}"><m:ResponseMessages>{messages}'
                "</m:ResponseMessages></m:GetFolderResponse>"
            )
        )

    def _mailbox_folders_reply(self, requested: list[tuple[str, str]]) -> Reply:
        """The batched GetFolder of the mailbox check: the main folders of the configured mailbox, by name."""
        messages = "".join(
            '<m:GetFolderResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode><m:Folders>'
            f'<t:Folder><t:FolderId Id="{MAILBOX_FOLDER_IDS[name]}" ChangeKey="K"/></t:Folder>'
            "</m:Folders></m:GetFolderResponseMessage>"
            for _, name in requested
        )
        return Reply(
            body=_envelope(
                f'<m:GetFolderResponse xmlns:m="{MNS}" xmlns:t="{TNS}"><m:ResponseMessages>{messages}'
                "</m:ResponseMessages></m:GetFolderResponse>"
            )
        )

    def _single_folder_reply(self, body: bytes) -> Reply:
        if b'Id="sentitems"' in body:
            return self.sent_folder_reply
        for name, reply in self.folder_replies.items():
            if f'Id="{name}"'.encode() in body:
                return reply
        return Reply(body=GET_ROOT_OK)

    def _folder_message(self, kind: str, folder_id: str) -> str:
        if kind == "FolderId" and folder_id in self.known_folders:
            return (
                '<m:GetFolderResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode>'
                f"<m:Folders>{self.known_folders[folder_id]}</m:Folders></m:GetFolderResponseMessage>"
            )
        code = self.folder_error_codes.get(folder_id, "ErrorFolderNotFound")
        return (
            '<m:GetFolderResponseMessage ResponseClass="Error"><m:MessageText>none</m:MessageText>'
            f"<m:ResponseCode>{code}</m:ResponseCode><m:DescriptiveLinkKey>0</m:DescriptiveLinkKey>"
            "</m:GetFolderResponseMessage>"
        )

    def send(self, adapter, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        body = request.body or b""
        self.sent.append(Sent(url=request.url, timeout=timeout, body=body))
        if b"ConvertId" in body:
            self.probes += 1
            reply = Reply(body=PROBE_OK)
        elif b"GetFolder" in body:
            reply = self._folder_reply(body)
        else:
            assert self.replies, "the fake server ran out of scripted replies: an unexpected extra submission"
            reply = self.replies.pop(0)
        self.clock.now += reply.elapse
        if reply.raises is not None:
            raise reply.raises
        if b"FindFolder" in body:
            self._learn_folders(reply.body)
        response = requests.Response()
        response.status_code = reply.status
        response._content = reply.body
        response._content_consumed = True
        response.headers.update(reply.headers)
        response.url = request.url
        response.request = request
        return response


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def server_for(monkeypatch, clock) -> Callable[..., FakeServer]:
    def install(*replies: Reply) -> FakeServer:
        server = FakeServer(clock, *replies)
        monkeypatch.setattr(HTTPAdapter, "send", lambda self, request, **kw: server.send(self, request, **kw))
        return server

    return install


@pytest.fixture(autouse=True)
def _read_retry_must_not_run(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("call_with_retry (the read retry policy) must not run inside a write call")

    monkeypatch.setattr(ec, "call_with_retry", forbidden)


def _client(clock: FakeClock, rand: Callable[[float, float], float] = lambda a, b: 0.0) -> ec.EwsExchangeClient:
    return ec.EwsExchangeClient(_config(), clock=clock, sleep=clock.sleep, rand=rand)


def _fetch(account):
    return list(account.fetch(ids=[("AAA", None)]))


# ---- private protocol ------------------------------------------------------------------------------------


def test_write_session_has_its_own_failfast_protocol_with_a_single_session_pool(server_for, clock):
    server_for()
    client = _client(clock)
    shared = client._account.protocol

    with client._write_session("send") as session:
        private = session.account.protocol

        assert private is not shared
        assert isinstance(private, Protocol)
        assert isinstance(private.config.retry_policy, FailFast)
        assert private.config.max_connections is None
        assert private.max_connections == 1
        assert private.auth_type == NTLM
        assert private.config is not shared.config


def test_private_protocol_is_not_registered_in_the_protocol_cache(server_for, clock):
    server_for()
    client = _client(clock)
    client._account  # noqa: B018 - build the read account, so the shared protocol is already cached
    cached_before = dict(ec.Protocol._protocol_cache)

    with client._write_session("send") as session:
        assert all(protocol is not session.account.protocol for protocol, _ in ec.Protocol._protocol_cache.values())

    assert set(ec.Protocol._protocol_cache) == set(cached_before)


def test_shared_cached_protocol_timeout_and_adapter_are_unchanged_by_a_write(server_for, clock):
    server = server_for(Reply(body=GET_ITEM_OK))
    client = _client(clock)
    shared = client._account.protocol
    timeout_before, adapter_before = shared.TIMEOUT, shared.HTTP_ADAPTER_CLS
    instance_attrs_before = dict(vars(shared))

    with client._write_session("send") as session:
        client._fetch_for_write("send", session, "AAA", only_fields=["parent_folder_id"])

    assert shared.TIMEOUT == timeout_before == ec.REQUEST_TIMEOUT_SECONDS
    assert shared.HTTP_ADAPTER_CLS is adapter_before is HTTPAdapter
    assert type(shared).HTTP_ADAPTER_CLS is HTTPAdapter
    assert vars(shared) == instance_attrs_before
    assert shared.session_pool_size == 0  # nothing was ever sent through the shared protocol
    assert server.requests, "the write call did send its read through the private protocol"


def test_building_a_write_session_does_not_touch_build_ews_client_read_behaviour(server_for, clock):
    server_for()
    client = _client(clock)
    assert client._account.protocol.TIMEOUT == ec.REQUEST_TIMEOUT_SECONDS

    with client._write_session("move"):
        pass

    assert client._account.protocol.TIMEOUT == ec.REQUEST_TIMEOUT_SECONDS


def test_http_adapter_cls_accepts_the_kwargs_get_adapter_passes(server_for, clock):
    server_for()
    client = _client(clock)

    with client._write_session("send") as session:
        adapter = type(session.account.protocol).get_adapter()

        assert isinstance(adapter, ews_session.DeadlineAdapter)
        assert isinstance(adapter, HTTPAdapter)


def test_the_version_probe_runs_once_when_the_session_opens_and_counts_in_the_deadline(server_for, clock):
    server = server_for(Reply(body=GET_ITEM_OK))
    client = _client(clock)

    with client._write_session("send") as session:
        assert server.probes == 1
        assert session.account.version.api_version == "Exchange2016"
        client._fetch_for_write("send", session, "AAA", only_fields=["parent_folder_id"])

    assert server.probes == 1  # the probe is not repeated by later calls
    assert len(server.requests) == 1


def test_a_failed_version_probe_is_not_applied(monkeypatch, clock):
    def refuse(self, request, **kwargs):
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(HTTPAdapter, "send", refuse)
    client = _client(clock)

    with pytest.raises(PreMutationReadFailedError) as raised:
        with client._write_session("send"):
            pytest.fail("the session must not open")

    assert raised.value.OUTCOME == "not_applied"


def test_a_failed_endpoint_check_when_opening_the_session_is_not_applied(monkeypatch, clock):
    client = _client(clock)
    monkeypatch.setattr(ec, "_endpoint", lambda raw: (_ for _ in ()).throw(BadEndpointError()))

    with pytest.raises(WriteBadEndpointError) as raised:
        with client._write_session("send"):
            pytest.fail("the session must not open")

    assert raised.value.OUTCOME == "not_applied"
    assert "endpoint is not usable" in str(raised.value)
    assert "could not be read" not in str(raised.value)


def test_a_dns_failure_when_opening_the_session_keeps_its_own_not_applied_message(monkeypatch, clock):
    client = _client(clock)
    monkeypatch.setattr(ec, "_endpoint", lambda raw: (_ for _ in ()).throw(UnreachableError()))

    with pytest.raises(WriteUnreachableError) as raised:
        with client._write_session("send"):
            pytest.fail("the session must not open")

    assert raised.value.OUTCOME == "not_applied"
    assert "unreachable" in str(raised.value)


def _resolver(calls: list[str], ip: str = PUBLIC_IP):
    def getaddrinfo(host, port, *args, **kwargs):
        calls.append(host)
        return [(None, None, None, "", (ip, 0))]

    return getaddrinfo


def test_one_write_resolves_the_endpoint_once(monkeypatch, server_for, clock):
    server_for(Reply(body=CREATE_ITEM_OK))
    calls: list[str] = []
    monkeypatch.setattr("socket.getaddrinfo", _resolver(calls))

    _send(_client(clock))

    assert calls == ["ews.example.test"]


def test_building_the_client_resolves_nothing(monkeypatch, clock):
    calls: list[str] = []
    monkeypatch.setattr("socket.getaddrinfo", _resolver(calls))

    _client(clock)

    assert calls == []


def test_a_write_to_an_internal_host_is_not_applied(monkeypatch, server_for, clock):
    server = server_for(Reply(body=CREATE_ITEM_OK))
    monkeypatch.setattr("socket.getaddrinfo", _resolver([], "10.0.0.5"))

    with pytest.raises(WriteBadEndpointError) as raised:
        _send(_client(clock))

    assert raised.value.OUTCOME == "not_applied"
    assert server.requests == []


def test_a_write_to_an_unresolvable_host_is_not_applied(monkeypatch, server_for, clock):
    server = server_for(Reply(body=CREATE_ITEM_OK))

    def unresolvable(host, port, *args, **kwargs):
        raise OSError("no such host")

    monkeypatch.setattr("socket.getaddrinfo", unresolvable)

    with pytest.raises(WriteUnreachableError) as raised:
        _send(_client(clock))

    assert raised.value.OUTCOME == "not_applied"
    assert server.requests == []


def test_the_private_protocol_is_closed_at_the_end_of_the_call(server_for, clock):
    server_for(Reply(body=GET_ITEM_OK))
    client = _client(clock)

    with client._write_session("send") as session:
        client._fetch_for_write("send", session, "AAA", only_fields=["parent_folder_id"])
        private = session.account.protocol
        assert private.session_pool_size > 0

    assert private._session_pool.empty()


def test_the_private_protocol_is_closed_when_the_call_fails(server_for, clock):
    server_for()
    client = _client(clock)

    with pytest.raises(RuntimeError):
        with client._write_session("send") as session:
            private = session.account.protocol
            raise RuntimeError("boom")

    assert private._session_pool.empty()


def _fail_first_close(monkeypatch) -> None:
    """Make the next ``Protocol.close()`` call raise, then restore real behaviour for every later call.

    A plain permanent patch would also break the per-test ``Protocol.clear_cache()`` teardown (which
    closes every cached shared protocol); only the private write-session protocol's own close must fail.
    """
    original = Protocol.close
    state = {"raised": False}

    def flaky(self):
        if not state["raised"]:
            state["raised"] = True
            raise RuntimeError("close boom")
        return original(self)

    monkeypatch.setattr(Protocol, "close", flaky)


def test_ews_write_session_close_swallows_a_cleanup_failure(server_for, clock, monkeypatch):
    """ISSUE B: a failure while releasing the protocol must not escape ``close()`` and override the outcome."""
    server_for()
    client = _client(clock)
    _fail_first_close(monkeypatch)

    with client._write_session("send"):
        pass  # close() runs in the context manager's finally, below; a RuntimeError here would fail the test


# ---- deadline adapter ------------------------------------------------------------------------------------


def test_request_timeouts_shrink_with_the_remaining_deadline(server_for, clock):
    server = server_for(
        Reply(body=GET_ITEM_OK, elapse=40),
        Reply(body=GET_ITEM_OK, elapse=30),
        Reply(body=GET_ITEM_OK),
    )
    client = _client(clock)

    with client._write_session("send") as session:
        for _ in range(3):
            client._inner_read("send", session, _fetch)

    assert [s.timeout for s in server.requests] == [(60.0, 60.0), (50.0, 50.0), (20.0, 20.0)]


def test_the_probe_request_is_clamped_too(server_for, clock):
    server = server_for()
    client = _client(clock)

    with client._write_session("send"):
        pass

    assert server.sent[0].timeout == (60.0, 60.0)


def test_an_oversized_requested_timeout_is_clamped_to_the_attempt_cap(server_for, clock):
    server = server_for(Reply(body=GET_ITEM_OK))
    client = _client(clock)

    with client._write_session("send") as session:
        # exchangelib asks for protocol.TIMEOUT; a service-level timeout must not exceed the cap either
        session.account.protocol.TIMEOUT = 500
        _fetch(session.account)

    assert server.requests[0].timeout == (60.0, 60.0)


def test_a_tuple_timeout_is_clamped_per_element(server_for, clock):
    clock.now = 1000.0
    server = server_for()
    client = _client(clock)

    with client._write_session("send") as session:
        clock.now += 85  # 5 s remain
        adapter = type(session.account.protocol).get_adapter()
        request = requests.Request("POST", EWS_URL, data=b"<x/>").prepare()
        # the scripted server has no replies; the call is answered by the fake as a non-probe request
        server.replies.append(Reply(body=GET_ITEM_OK))
        adapter.send(request, timeout=(10, 100))

    assert server.requests[-1].timeout == (5.0, 5.0)


def test_nothing_is_sent_when_no_time_remains(server_for, clock):
    server = server_for(Reply(body=GET_ITEM_OK))
    client = _client(clock)

    with client._write_session("send") as session:
        clock.now += 90
        with pytest.raises(DeadlineExhaustedError):
            _fetch(session.account)

    assert server.requests == []


def _logged_errors(caplog) -> list[logging.LogRecord]:
    """ERROR records only: exchangelib's ``post_ratelimited`` logs the whole SOAP request at that level."""
    return [record for record in caplog.records if record.levelno >= logging.ERROR]


def test_a_spent_deadline_is_refused_before_exchangelib_can_log_the_request(server_for, clock, caplog):
    server = server_for(Reply(body=GET_ITEM_OK))
    client = _client(clock)

    with client._write_session("send") as session:
        clock.now += 90
        with caplog.at_level(logging.DEBUG), pytest.raises(DeadlineExhaustedError):
            _fetch(session.account)

    assert server.requests == []
    assert _logged_errors(caplog) == []  # the session check raises outside post_ratelimited's catch-all


@pytest.mark.parametrize("path", ["inner_read", "submit"])
def test_a_request_refused_by_the_adapter_is_not_applied_and_not_logged(server_for, clock, caplog, monkeypatch, path):
    """What the session check cannot see: a deadline that passes after it, such as an NTLM handshake step."""
    server = server_for(Reply(body=GET_ITEM_OK))
    client = _client(clock)

    def late(account):
        clock.now += 90  # the deadline passes after the guards of _inner_read / run_write
        return _fetch(account)

    with client._write_session("send") as session:
        monkeypatch.setattr(type(session.account.protocol), "get_session", Protocol.get_session)
        with caplog.at_level(logging.DEBUG), pytest.raises(DeadlineExhaustedError):
            if path == "inner_read":
                client._inner_read("send", session, late)
            else:
                client._submit_write("send", session, late)

    assert server.requests == []
    assert session.evidence.deadline_refused
    assert _logged_errors(caplog) == []  # a ConnectionError subclass, which exchangelib does not log at ERROR


def test_a_refused_request_in_a_reconciliation_read_stays_unknown(server_for, clock, monkeypatch):
    server_for(Reply(body=GET_ITEM_OK))
    client = _client(clock)

    def late(account):
        clock.now += 90
        return _fetch(account)

    with client._write_session("move") as session:
        monkeypatch.setattr(type(session.account.protocol), "get_session", Protocol.get_session)
        with pytest.raises(UnknownOutcomeError):
            client._inner_read("move", session, late, before_mutation=False)


def test_the_deadline_is_per_session_not_shared_between_concurrent_calls(server_for, clock):
    server_for(Reply(body=GET_ITEM_OK))
    other_clock = FakeClock()
    first, second = _client(clock), _client(other_clock)

    with first._write_session("send") as a, second._write_session("send") as b:
        clock.now += 90  # only the first call's deadline is spent
        assert a.deadline.expired() and not b.deadline.expired()
        with pytest.raises(DeadlineExhaustedError):
            _fetch(a.account)
        assert len(_fetch(b.account)) == 1


# ---- evidence --------------------------------------------------------------------------------------------


BUSY_500_WITH_BACKOFF = Reply(status=500, body=_fault_server_busy(7000))


def test_evidence_records_status_and_body_proof_of_each_attempt(server_for, clock):
    server_for(BUSY_500_WITH_BACKOFF)
    client = _client(clock)

    with client._write_session("send") as session:
        session.evidence.reset()
        with pytest.raises(ews.ErrorServerBusy):
            _fetch(session.account)

        last = session.evidence.last
        assert (last.status, last.server_busy, last.back_off_ms) == (500, True, 7000)


def test_each_new_request_starts_a_fresh_record_so_a_stale_one_is_never_used(server_for, clock):
    server_for(
        Reply(body=GET_ITEM_OK),
        Reply(raises=requests.exceptions.ConnectionError("reset")),
    )
    client = _client(clock)

    with client._write_session("send") as session:
        _fetch(session.account)
        assert session.evidence.last.status == 200
        with pytest.raises(ews.ErrorTimeoutExpired):
            _fetch(session.account)

        assert session.evidence.last.status is None
        assert session.evidence.last.server_busy is False


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param(_fault_server_busy(7000), (True, 7000), id="fault-with-backoff"),
        pytest.param(_fault_server_busy(0), (True, 0), id="zero-backoff-is-provided"),
        pytest.param(_fault_server_busy(None), (True, None), id="busy-without-backoff"),
        pytest.param(_fault_server_busy("soon"), (True, None), id="non-numeric-backoff"),
        pytest.param(_fault_server_busy(-5), (True, None), id="negative-backoff"),
        pytest.param(GET_ITEM_OK, (False, None), id="ordinary-success"),
        pytest.param(b"<html>Server Error in '/EWS' Application</html>", (False, None), id="html-error-page"),
        pytest.param(b"not xml at all ErrorServerBusy BackOffMilliseconds 5000", (False, None), id="text-only"),
        pytest.param(b"", (False, None), id="empty"),
        pytest.param(
            f'<a xmlns="{TNS}"><Comment>ErrorServerBusy</Comment><Value Name="BackOffMilliseconds">9</Value></a>'.encode(),
            (False, None),
            id="words-outside-a-response-code",
        ),
    ],
)
def test_server_busy_evidence_reads_the_soap_body(body, expected):
    assert ews_session.server_busy_evidence(body) == expected


# ---- classifier, driven through the real exchangelib stack -----------------------------------------------


def _run_send(client, session, attempt=_fetch):
    return client._submit_write("send", session, attempt)


def test_body_proven_server_busy_with_backoff_is_retried_after_the_server_wait(server_for, clock):
    server = server_for(BUSY_500_WITH_BACKOFF, Reply(body=GET_ITEM_OK))
    client = _client(clock)

    with client._write_session("send") as session:
        result = _run_send(client, session)

    assert len(result) == 1
    assert clock.sleeps == [7.0]  # the server's wait, never the library's 10 s RETRY_WAIT
    assert len(server.requests) == 2


def test_a_zero_backoff_is_still_a_server_hint(server_for, clock):
    server_for(Reply(status=500, body=_fault_server_busy(0)), Reply(body=GET_ITEM_OK))
    client = _client(clock, rand=lambda a, b: pytest.fail("a server hint must not use jitter"))

    with client._write_session("send") as session:
        _run_send(client, session)

    assert clock.sleeps == [0.0]


def test_the_server_wait_is_honoured_exactly_even_when_long(server_for, clock):
    server_for(Reply(status=500, body=_fault_server_busy(45000)), Reply(body=GET_ITEM_OK))
    client = _client(clock)

    with client._write_session("send") as session:
        _run_send(client, session)

    assert clock.sleeps == [45.0]


def test_a_server_wait_that_does_not_fit_the_deadline_stops_not_applied(server_for, clock):
    server = server_for(Reply(status=500, body=_fault_server_busy(120000)))
    client = _client(clock)

    with client._write_session("send") as session:
        with pytest.raises(WriteRateLimitedError):
            _run_send(client, session)

    assert clock.sleeps == []
    assert len(server.requests) == 1


def test_at_most_three_submissions_are_made_then_rate_limited(server_for, clock):
    server = server_for(*[BUSY_500_WITH_BACKOFF] * 3)
    client = _client(clock)

    with client._write_session("send") as session:
        with pytest.raises(WriteRateLimitedError):
            _run_send(client, session)

    assert len(server.requests) == 3
    assert clock.sleeps == [7.0, 7.0]


def _item_level_server_busy(back_off_ms):
    """An HTTP 200 reply whose *item-level* ``ResponseCode`` (not a SOAP ``s:Fault``) is ``ErrorServerBusy``.

    Real EWS traffic carries ``ErrorServerBusy`` this way too, not only as a 500 SOAP fault (``_SOAP_STATUSES``
    treats both 200 and 500 as bodies worth parsing). exchangelib's ``GetItem`` service never raises this
    per-item error itself: it comes back as an exception *value* inside the result list, so ``_raise_if_error``
    below raises it, exactly as ``EwsExchangeClient._fetch_for_write`` does for a real read.
    """
    message_xml = (
        ""
        if back_off_ms is None
        else f'<m:MessageXml><t:Value Name="BackOffMilliseconds">{back_off_ms}</t:Value></m:MessageXml>'
    )
    return _envelope(
        f'<m:GetItemResponse xmlns:m="{MNS}" xmlns:t="{TNS}"><m:ResponseMessages>'
        '<m:GetItemResponseMessage ResponseClass="Error"><m:MessageText>busy</m:MessageText>'
        f"<m:ResponseCode>ErrorServerBusy</m:ResponseCode>{message_xml}"
        "</m:GetItemResponseMessage></m:ResponseMessages></m:GetItemResponse>"
    )


def _raise_if_error(account):
    items = list(account.fetch(ids=[("AAA", None)]))
    if isinstance(items[0], BaseException):
        raise items[0]
    return items[0]


def test_item_level_server_busy_over_http_200_is_retried_with_the_server_back_off(server_for, clock):
    server = server_for(Reply(status=200, body=_item_level_server_busy(7000)), Reply(body=GET_ITEM_OK))
    client = _client(clock)

    with client._write_session("send") as session:
        result = _run_send(client, session, _raise_if_error)

    assert result.id == "AAA"
    assert clock.sleeps == [7.0]  # the server's wait, parsed from a plain HTTP 200 body
    assert len(server.requests) == 2


def test_item_level_server_busy_over_http_200_without_backoff_is_unknown_and_not_retried(server_for, clock):
    server = server_for(Reply(status=200, body=_item_level_server_busy(None)))
    client = _client(clock)

    with client._write_session("send") as session:
        with pytest.raises(UnknownOutcomeError) as raised:
            _run_send(client, session, _raise_if_error)

    assert str(raised.value).startswith("Unknown.")
    assert clock.sleeps == []
    assert len(server.requests) == 1


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(Reply(status=503, body=b"<html>unavailable</html>"), id="http-503"),
        pytest.param(
            Reply(status=503, body=_fault_server_busy(7000)),
            id="http-503-even-with-a-busy-body",
        ),
        pytest.param(
            Reply(status=500, body=b"<html>Server Error in '/EWS' Application.</html>"),
            id="http-500-server-error-in-ews-application",
        ),
        pytest.param(
            Reply(status=502, body=b"<html>bad gateway</html>", headers={"Connection": "close"}),
            id="non-200-with-connection-close",
        ),
        pytest.param(
            Reply(status=504, body=_fault_server_busy(7000), headers={"Connection": "close"}),
            id="gateway-timeout-with-connection-close-and-a-busy-body",
        ),
        pytest.param(Reply(status=500, body=_fault_server_busy(None)), id="server-busy-without-backoff"),
        pytest.param(Reply(status=500, body=_fault_server_busy("soon")), id="server-busy-with-garbage-backoff"),
        pytest.param(
            Reply(status=302, headers={"Location": "/ews/genericerrorpage.htm?aspxerrorpath=/ews/exchange.asmx"}),
            id="internal-server-transient-error",
        ),
    ],
)
def test_synthesized_or_unproven_busy_is_unknown_with_no_resubmission_and_no_wait(server_for, clock, reply):
    server = server_for(reply)
    client = _client(clock)

    with client._write_session("send") as session:
        with pytest.raises(UnknownOutcomeError) as raised:
            _run_send(client, session)

    assert str(raised.value).startswith("Unknown.")
    assert len(server.requests) == 1
    assert clock.sleeps == []  # no 10 s RETRY_WAIT, no local jitter


def test_a_connection_error_is_unknown_with_no_resubmission(server_for, clock):
    server = server_for(Reply(raises=requests.exceptions.ConnectionError("reset")))
    client = _client(clock)

    with client._write_session("send") as session:
        with pytest.raises(UnknownOutcomeError):
            _run_send(client, session)

    assert len(server.requests) == 1
    assert clock.sleeps == []
    assert not session.evidence.deadline_refused  # a real connection error is never taken for a refusal


def test_a_read_timeout_is_unknown_with_no_resubmission(server_for, clock):
    server = server_for(Reply(raises=requests.exceptions.ReadTimeout("slow")))
    client = _client(clock)

    with client._write_session("send") as session:
        with pytest.raises(WriteTimeoutUnknownError):
            _run_send(client, session)

    assert len(server.requests) == 1


def test_an_unexpected_exception_during_a_submission_is_wrapped_and_not_resubmitted(server_for, clock):
    server_for()
    client = _client(clock)
    calls = []

    def attempt(account):
        calls.append(1)
        raise KeyError("boom")

    with client._write_session("send") as session:
        with pytest.raises(UnknownOutcomeError):
            _run_send(client, session, attempt)

    assert calls == [1]


def test_classifier_retries_only_when_the_last_request_proves_busy_and_backoff():
    evidence = ec.TransportEvidence()
    classify = ec._ews_write_classifier("send", evidence)
    busy = ews.ErrorServerBusy("x", back_off=10)  # what exchangelib synthesizes: never a hint

    assert isinstance(classify(busy), Stop)  # no request recorded at all

    attempt = evidence.begin()
    attempt.status, attempt.server_busy, attempt.back_off_ms = 500, True, 2500
    decision = classify(busy)
    assert decision == Retry(2.5)

    attempt.back_off_ms = None
    assert isinstance(classify(busy), Stop)


@pytest.mark.parametrize("status", [200, 500])
def test_classifier_accepts_the_two_statuses_exchangelib_parses_as_soap(status):
    evidence = ec.TransportEvidence()
    attempt = evidence.begin()
    attempt.status, attempt.server_busy, attempt.back_off_ms = status, True, 1000

    assert ec._ews_write_classifier("send", evidence)(ews.ErrorServerBusy("x")) == Retry(1.0)


@pytest.mark.parametrize("status", [None, 302, 401, 502, 503, 504])
def test_classifier_rejects_other_statuses_even_with_a_busy_body(status):
    evidence = ec.TransportEvidence()
    attempt = evidence.begin()
    attempt.status, attempt.server_busy, attempt.back_off_ms = status, True, 1000

    assert isinstance(ec._ews_write_classifier("send", evidence)(ews.ErrorServerBusy("x")), Stop)


@pytest.mark.parametrize(
    "exc",
    [
        ews.ErrorInternalServerTransientError("x"),
        ews.ErrorTimeoutExpired("x"),
        ews.RateLimitError("x", wait=1),
        ews.ErrorInternalServerError("x"),
        ConnectionError("x"),
        ValueError("x"),
    ],
)
def test_classifier_never_retries_anything_but_a_proven_server_busy(exc):
    evidence = ec.TransportEvidence()
    attempt = evidence.begin()
    attempt.status, attempt.server_busy, attempt.back_off_ms = 500, True, 1000  # busy evidence is not enough alone

    decision = ec._ews_write_classifier("send", evidence)(exc)

    assert isinstance(decision, Stop)
    assert isinstance(decision.error, ExchangeError)
    assert decision.error.OUTCOME in {"not_applied", "unknown"}


def test_classifier_maps_a_proven_pre_mutation_rejection_to_not_applied():
    decision = ec._ews_write_classifier("send", ec.TransportEvidence())(ews.ErrorSendAsDenied("x"))

    assert isinstance(decision, Stop)
    assert decision.error.OUTCOME == "not_applied"


def test_classifier_reports_a_request_the_adapter_refused_as_not_applied():
    evidence = ec.TransportEvidence()
    evidence.begin().refused = True

    # exchangelib re-raises the adapter's ConnectionError as ErrorTimeoutExpired, which is Unknown on its own
    decision = ec._ews_write_classifier("send", evidence)(ews.ErrorTimeoutExpired("Reraised from _DeadlineRefusal"))

    assert isinstance(decision, Stop)
    assert isinstance(decision.error, DeadlineExhaustedError)
    assert decision.error.OUTCOME == "not_applied"


def test_classifier_keeps_a_timeout_unknown_when_nothing_was_refused():
    evidence = ec.TransportEvidence()
    evidence.begin()

    decision = ec._ews_write_classifier("send", evidence)(ews.ErrorTimeoutExpired("x"))

    assert isinstance(decision, Stop)
    assert decision.error.OUTCOME == "unknown"


def test_classifier_passes_an_existing_write_error_through():
    error = DeadlineExhaustedError()

    assert ec._ews_write_classifier("send", ec.TransportEvidence())(error) == Stop(error)


# ---- reads inside a write call ---------------------------------------------------------------------------


def test_inner_read_is_a_single_attempt_and_never_uses_the_read_retry(server_for, clock):
    server = server_for(Reply(status=503, body=b"<html>unavailable</html>"))
    client = _client(clock)

    with client._write_session("move") as session:
        with pytest.raises(WriteRateLimitedError):  # a pre-mutation busy read is Not applied
            client._inner_read("move", session, _fetch)

    assert len(server.requests) == 1
    assert clock.sleeps == []


def test_inner_read_failure_before_the_change_is_not_applied(server_for, clock):
    server_for(Reply(raises=requests.exceptions.ConnectionError("reset")))
    client = _client(clock)

    with client._write_session("move") as session:
        with pytest.raises(PreMutationReadFailedError):
            client._inner_read("move", session, _fetch)


def test_inner_read_failure_after_the_change_stays_unknown(server_for, clock):
    server_for(Reply(status=503, body=b"<html>unavailable</html>"))
    client = _client(clock)

    with client._write_session("move") as session:
        with pytest.raises(UnknownOutcomeError):
            client._inner_read("move", session, _fetch, before_mutation=False)


def test_inner_read_with_a_spent_deadline_is_not_applied_before_the_change_and_unknown_after(server_for, clock):
    server = server_for(Reply(body=GET_ITEM_OK))
    client = _client(clock)

    with client._write_session("move") as session:
        clock.now += 91
        with pytest.raises(DeadlineExhaustedError):
            client._inner_read("move", session, _fetch)
        with pytest.raises(WriteTimeoutUnknownError):
            client._inner_read("move", session, _fetch, before_mutation=False)

    assert server.requests == []


def test_a_deadline_hit_by_the_adapter_during_a_reconciliation_read_stays_unknown(server_for, clock):
    server_for(Reply(body=GET_ITEM_OK))
    client = _client(clock)

    def read(account):
        clock.now += 91  # the deadline ends after the pre-check but before the request is sent
        return _fetch(account)

    with client._write_session("move") as session:
        with pytest.raises(UnknownOutcomeError):
            client._inner_read("move", session, read, before_mutation=False)


def test_fetch_for_write_bypasses_the_last_fetch_cache(server_for, clock):
    server = server_for(Reply(body=GET_ITEM_OK))
    client = _client(clock)
    client._last_fetch = ("AAA", object())  # a stale cached item for the same id

    with client._write_session("move") as session:
        item = client._fetch_for_write("move", session, "AAA", only_fields=["parent_folder_id"])

    assert item.id == "AAA"
    assert len(server.requests) == 1
    assert client._last_fetch[0] == "AAA" and client._last_fetch[1] is not item  # and does not refresh it


def test_fetch_for_write_not_found_is_not_applied_before_the_change(server_for, clock):
    server_for(Reply(body=GET_ITEM_NOT_FOUND))
    client = _client(clock)

    with client._write_session("forward") as session:
        with pytest.raises(WriteMessageNotFoundError):
            client._fetch_for_write("forward", session, "AAA", only_fields=["body"])


def test_fetch_for_write_not_found_of_the_old_id_after_a_move_is_unknown(server_for, clock):
    server_for(Reply(body=GET_ITEM_NOT_FOUND))
    client = _client(clock)

    with client._write_session("move") as session:
        with pytest.raises(WriteMessageNotFoundUnknownError):
            client._fetch_for_write("move", session, "AAA", only_fields=["parent_folder_id"], before_mutation=False)


def test_reads_and_the_submission_share_one_deadline(server_for, clock):
    server = server_for(
        Reply(body=GET_ITEM_OK, elapse=40),  # pre-read
        Reply(body=GET_ITEM_OK, elapse=40),  # submission
        Reply(body=GET_ITEM_OK, elapse=40),  # reconciliation read: only 10 s left, still sent
    )
    client = _client(clock)
    started = clock.now

    with client._write_session("move") as session:
        client._inner_read("move", session, _fetch)
        _run_send(client, session)
        client._inner_read("move", session, _fetch, before_mutation=False)

    # The clamped (connect, read) timeout DeadlineAdapter.send actually computed and handed to the transport
    # for each request, derived from the shared Deadline's remaining time, not from the script's own elapses.
    assert [s.timeout for s in server.requests] == [(60.0, 60.0), (50.0, 50.0), (10.0, 10.0)]
    # The documented overrun ceiling: the last in-flight attempt is bounded by its own clamped timeout, so the
    # whole call can never run past deadline + one more request timeout, regardless of how the script spends it.
    assert clock.now - started <= 90 + 60


def test_no_submission_starts_after_the_deadline(server_for, clock):
    server = server_for(Reply(body=GET_ITEM_OK, elapse=95))
    client = _client(clock)

    with client._write_session("send") as session:
        client._inner_read("send", session, _fetch)
        with pytest.raises(DeadlineExhaustedError):
            _run_send(client, session)

    assert len(server.requests) == 1


# ---- client wiring ---------------------------------------------------------------------------------------


def test_client_still_builds_from_config_only():
    client = ec.EwsExchangeClient(_config())

    assert client._account.protocol.TIMEOUT == ec.REQUEST_TIMEOUT_SECONDS


def test_private_configuration_does_not_leak_credentials_into_errors(server_for, clock):
    server_for(Reply(raises=requests.exceptions.ConnectionError("pw-fixture svc-fixture")))
    client = _client(clock)

    with client._write_session("send") as session:
        with pytest.raises(UnknownOutcomeError) as raised:
            _run_send(client, session)

    assert "pw-fixture" not in str(raised.value) and "svc-fixture" not in str(raised.value)


def test_write_configuration_is_built_without_max_connections_or_a_shared_object(server_for, clock):
    server_for()
    client = _client(clock)

    with client._write_session("send") as session:
        config = session.account.protocol.config

        assert isinstance(config, Configuration)
        assert config.max_connections is None
        assert isinstance(config.credentials, Credentials)


# ---- send and forward -------------------------------------------------------------------------------------

CREATE_ITEM_OK = _envelope(
    f'<m:CreateItemResponse xmlns:m="{MNS}"><m:ResponseMessages>'
    '<m:CreateItemResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode><m:Items/>'
    "</m:CreateItemResponseMessage></m:ResponseMessages></m:CreateItemResponse>"
)


def _create_item_error(code: str) -> bytes:
    return _envelope(
        f'<m:CreateItemResponse xmlns:m="{MNS}"><m:ResponseMessages>'
        f'<m:CreateItemResponseMessage ResponseClass="Error"><m:MessageText>no</m:MessageText>'
        f"<m:ResponseCode>{code}</m:ResponseCode><m:DescriptiveLinkKey>0</m:DescriptiveLinkKey>"
        "</m:CreateItemResponseMessage></m:ResponseMessages></m:CreateItemResponse>"
    )


# The Sent Items folder resolved for the shared mailbox is serialised as its distinguished folder id.
SAVED_TO_SHARED_SENT_ITEMS = (
    '<m:SavedItemFolderId><t:DistinguishedFolderId Id="sentitems"><t:Mailbox>'
    "<t:EmailAddress>shared@example.test</t:EmailAddress>"
)


def _send(client: ec.EwsExchangeClient, **overrides):
    args = {"to": ["a@example.test", "b@example.test"], "subject": "Hello", "body": "Plain body"}
    return client.send(**{**args, **overrides})


def _forward(client: ec.EwsExchangeClient, **overrides):
    args = {"message_id": "AAA", "to": ["a@example.test", "b@example.test"]}
    return client.forward(**{**args, **overrides})


def _create_item_xml(server: FakeServer) -> str:
    bodies = [s.body.decode() for s in server.requests if b"CreateItem" in s.body]
    assert len(bodies) == 1, "exactly one mutating request"
    return bodies[0]


def test_send_submits_one_create_item_as_the_shared_mailbox_and_saves_the_copy_to_sent_items(server_for, clock):
    server = server_for(Reply(body=CREATE_ITEM_OK))

    result = _send(_client(clock))

    assert result == {"status": "submitted", "to": ["a@example.test", "b@example.test"], "subject": "Hello"}
    xml = _create_item_xml(server)
    assert 'MessageDisposition="SendAndSaveCopy"' in xml
    assert SAVED_TO_SHARED_SENT_ITEMS in xml
    assert "<t:From><t:Mailbox><t:EmailAddress>shared@example.test</t:EmailAddress>" in xml
    assert "<t:Subject>Hello</t:Subject>" in xml
    assert '<t:Body BodyType="Text">Plain body</t:Body>' in xml
    for address in ("a@example.test", "b@example.test"):
        assert f"<t:EmailAddress>{address}</t:EmailAddress>" in xml
    assert clock.sleeps == []


def test_send_html_body_goes_out_as_an_html_body_unsanitised(server_for, clock):
    server = server_for(Reply(body=CREATE_ITEM_OK))

    _send(_client(clock), body="<b>bold</b><script>x()</script>", body_type="html")

    [element] = to_xml(_create_item_xml(server).encode()).iter(f"{{{TNS}}}Body")
    assert element.get("BodyType") == "HTML"
    assert element.text == "<b>bold</b><script>x()</script>"  # as given: no sanitising, no escaping loss


@pytest.mark.parametrize(
    "kwargs",
    [
        {"to": []},
        {"to": ["not-an-address"]},
        {"body_type": "markdown"},
        {"subject": None},
        {"body": 5},
    ],
)
def test_send_with_invalid_input_is_not_applied_before_any_request(server_for, clock, kwargs):
    server = server_for()

    with pytest.raises(InvalidLocalInputError):
        _send(_client(clock), **kwargs)

    assert server.sent == []


def test_send_sets_author_and_copy_to_folder_on_the_request_object(server_for, clock, monkeypatch):
    server_for(Reply(body=CREATE_ITEM_OK))
    seen = []
    original = Message.send

    def spy(self, *args, **kwargs):
        seen.append((self.author, kwargs.get("copy_to_folder"), self.account.sent))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Message, "send", spy)

    _send(_client(clock))

    [(author, copy_to_folder, account_sent)] = seen
    assert author.email_address == "shared@example.test"
    assert copy_to_folder is account_sent
    assert copy_to_folder.id == "SENT"


def test_send_not_resolving_the_sent_folder_is_not_applied_and_nothing_is_submitted(server_for, clock):
    server = server_for()
    server.sent_folder_reply = Reply(raises=requests.exceptions.ConnectionError("reset"))

    with pytest.raises(PreMutationReadFailedError):
        _send(_client(clock))

    assert server.requests == []  # no CreateItem: fake state unchanged
    assert clock.sleeps == []


def test_send_send_as_denied_is_not_applied(server_for, clock):
    server = server_for(Reply(body=_create_item_error("ErrorSendAsDenied")))

    with pytest.raises(EwsSendAccessDeniedError) as raised:
        _send(_client(clock))

    assert raised.value.OUTCOME == "not_applied"
    assert len(server.requests) == 1


def test_send_connection_loss_after_the_submission_is_unknown_and_never_resubmitted(server_for, clock):
    server = server_for(Reply(raises=requests.exceptions.ConnectionError("reset")))

    with pytest.raises(UnknownOutcomeError) as raised:
        _send(_client(clock))

    assert str(raised.value).startswith("Unknown.")
    assert len(_create_item_requests(server)) == 1
    assert clock.sleeps == []


def _create_item_requests(server: FakeServer) -> list[Sent]:
    return [s for s in server.requests if b"CreateItem" in s.body]


def test_send_retries_only_a_body_proven_server_busy_and_then_still_sends_once(server_for, clock):
    server = server_for(BUSY_500_WITH_BACKOFF, Reply(body=CREATE_ITEM_OK))

    result = _send(_client(clock))

    assert result["status"] == "submitted"
    assert len(_create_item_requests(server)) == 2  # the first was refused by the server before processing
    assert clock.sleeps == [7.0]


def test_send_closes_the_private_protocol(server_for, clock):
    server_for(Reply(body=CREATE_ITEM_OK))
    client = _client(clock)
    closed = []
    original = ec.EwsWriteSession.close

    def spy(self):
        closed.append(self.account.protocol)
        return original(self)

    ec.EwsWriteSession.close = spy
    try:
        _send(client)
    finally:
        ec.EwsWriteSession.close = original

    assert len(closed) == 1


def test_a_close_failure_after_a_successful_send_does_not_change_the_result(server_for, clock, monkeypatch):
    """ISSUE B: cleanup must never replace or mask an already-decided outcome (here, success)."""
    server_for(Reply(body=CREATE_ITEM_OK))
    _fail_first_close(monkeypatch)

    result = _send(_client(clock))

    assert result == {"status": "submitted", "to": ["a@example.test", "b@example.test"], "subject": "Hello"}


def test_a_close_failure_after_an_unknown_submission_failure_still_raises_the_unknown_error(
    server_for, clock, monkeypatch
):
    """ISSUE B: cleanup must never replace or mask an already-decided outcome (here, Unknown)."""
    server_for(Reply(raises=requests.exceptions.ConnectionError("reset")))
    _fail_first_close(monkeypatch)

    with pytest.raises(UnknownOutcomeError) as raised:
        _send(_client(clock))

    assert str(raised.value).startswith("Unknown.")


def test_forward_submits_one_forward_item_without_subject_as_the_shared_mailbox_with_the_copy_in_sent_items(
    server_for, clock
):
    server = server_for(Reply(body=GET_ITEM_OK), Reply(body=CREATE_ITEM_OK))

    result = _forward(_client(clock))

    assert result == {"status": "submitted", "message_id": "AAA", "to": ["a@example.test", "b@example.test"]}
    xml = _create_item_xml(server)
    assert "<t:ForwardItem>" in xml
    assert 'MessageDisposition="SendAndSaveCopy"' in xml
    assert SAVED_TO_SHARED_SENT_ITEMS in xml
    assert '<t:ReferenceItemId Id="AAA" ChangeKey="CK1"' in xml
    assert "<t:From><t:Mailbox><t:EmailAddress>shared@example.test</t:EmailAddress>" in xml
    assert "<t:Subject>" not in xml  # the server derives "FW: <original subject>"
    for address in ("a@example.test", "b@example.test"):
        assert f"<t:EmailAddress>{address}</t:EmailAddress>" in xml
    assert clock.sleeps == []


def _get_item_with_body(body_type: str) -> Reply:
    return Reply(
        body=_envelope(
            f'<m:GetItemResponse xmlns:m="{MNS}" xmlns:t="{TNS}"><m:ResponseMessages>'
            '<m:GetItemResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode><m:Items>'
            '<t:Message><t:ItemId Id="AAA" ChangeKey="CK1"/><t:Subject>Hi</t:Subject>'
            f'<t:ParentFolderId Id="INBOX" ChangeKey="PK"/>'
            f'<t:Body BodyType="{body_type}">&lt;b&gt;hi&lt;/b&gt;</t:Body></t:Message>'
            "</m:Items></m:GetItemResponseMessage></m:ResponseMessages></m:GetItemResponse>"
        )
    )


@pytest.mark.parametrize(
    ("original_body_type", "expected_body_type"), [("HTML", "HTML"), ("Text", "Text")], ids=["html", "text"]
)
def test_forward_new_body_content_takes_the_body_type_of_the_original(
    server_for, clock, original_body_type, expected_body_type
):
    server = server_for(_get_item_with_body(original_body_type), Reply(body=CREATE_ITEM_OK))

    _forward(_client(clock))

    assert f'<t:NewBodyContent BodyType="{expected_body_type}">' in _create_item_xml(server)


def test_forward_sets_author_and_copy_to_folder_on_the_request_object(server_for, clock, monkeypatch):
    server_for(Reply(body=GET_ITEM_OK), Reply(body=CREATE_ITEM_OK))
    seen = []
    original = ForwardItem.send

    def spy(self, *args, **kwargs):
        seen.append((self.author, kwargs.get("copy_to_folder"), self.account.sent))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ForwardItem, "send", spy)

    _forward(_client(clock))

    [(author, copy_to_folder, account_sent)] = seen
    assert author.email_address == "shared@example.test"
    assert copy_to_folder is account_sent
    assert copy_to_folder.id == "SENT"


def test_forward_fetches_the_original_from_the_server_ignoring_the_read_cache(server_for, clock):
    server = server_for(Reply(body=GET_ITEM_OK), Reply(body=CREATE_ITEM_OK))
    client = _client(clock)
    stale = object()
    client._last_fetch = ("AAA", stale)

    _forward(client)

    assert any(b"GetItem" in s.body for s in server.requests)
    assert client._last_fetch == ("AAA", stale)


def test_forward_of_a_missing_message_is_not_applied_and_nothing_is_submitted(server_for, clock):
    server = server_for(Reply(body=GET_ITEM_NOT_FOUND))

    with pytest.raises(WriteMessageNotFoundError):
        _forward(_client(clock))

    assert _create_item_requests(server) == []


@pytest.mark.parametrize("kwargs", [{"message_id": "  "}, {"message_id": None}, {"to": []}, {"to": ["x"]}])
def test_forward_with_invalid_input_is_not_applied_before_any_request(server_for, clock, kwargs):
    server = server_for()

    with pytest.raises(InvalidLocalInputError):
        _forward(_client(clock), **kwargs)

    assert server.sent == []


def test_forward_not_resolving_the_sent_folder_is_not_applied_and_nothing_is_submitted(server_for, clock):
    server = server_for(Reply(body=GET_ITEM_OK))
    server.sent_folder_reply = Reply(raises=requests.exceptions.ConnectionError("reset"))

    with pytest.raises(PreMutationReadFailedError):
        _forward(_client(clock))

    assert _create_item_requests(server) == []


def test_forward_send_as_denied_is_not_applied(server_for, clock):
    server = server_for(Reply(body=GET_ITEM_OK), Reply(body=_create_item_error("ErrorSendAsDenied")))

    with pytest.raises(EwsSendAccessDeniedError) as raised:
        _forward(_client(clock))

    assert raised.value.OUTCOME == "not_applied"
    assert len(_create_item_requests(server)) == 1


def test_forward_connection_loss_after_the_submission_is_unknown_and_never_resubmitted(server_for, clock):
    server = server_for(Reply(body=GET_ITEM_OK), Reply(raises=requests.exceptions.ConnectionError("reset")))

    with pytest.raises(UnknownOutcomeError):
        _forward(_client(clock))

    assert len(_create_item_requests(server)) == 1
    assert clock.sleeps == []


def test_forward_retries_only_a_body_proven_server_busy_and_then_forwards_once(server_for, clock):
    server = server_for(Reply(body=GET_ITEM_OK), BUSY_500_WITH_BACKOFF, Reply(body=CREATE_ITEM_OK))

    result = _forward(_client(clock))

    assert result["status"] == "submitted"
    assert len(_create_item_requests(server)) == 2
    assert len([s for s in server.requests if b"GetItem" in s.body]) == 1  # the original is fetched once


# ---- move, delete and tag ---------------------------------------------------------------------------------


def _get_item(
    parent: str | None = "INBOX", categories: list[str] | None = None, changekey: str = "CK1", item_id: str = "AAA"
) -> Reply:
    parent_xml = "" if parent is None else f'<t:ParentFolderId Id="{parent}" ChangeKey="PK"/>'
    categories_xml = (
        ""
        if categories is None
        else "<t:Categories>" + "".join(f"<t:String>{c}</t:String>" for c in categories) + "</t:Categories>"
    )
    return Reply(
        body=_envelope(
            f'<m:GetItemResponse xmlns:m="{MNS}" xmlns:t="{TNS}"><m:ResponseMessages>'
            '<m:GetItemResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode><m:Items>'
            f'<t:Message><t:ItemId Id="{item_id}" ChangeKey="{changekey}"/><t:Subject>Hi</t:Subject>'
            f"{parent_xml}{categories_xml}</t:Message>"
            "</m:Items></m:GetItemResponseMessage></m:ResponseMessages></m:GetItemResponse>"
        )
    )


def _find_folders(*folders: tuple[str, str, str]) -> Reply:
    """A FindFolder reply: ``(id, display name, container class)`` per folder, all children of the mailbox root."""
    items = "".join(
        f'<t:Folder><t:FolderId Id="{fid}" ChangeKey="K"/><t:ParentFolderId Id="ROOT" ChangeKey="RK"/>'
        f"<t:DisplayName>{name}</t:DisplayName><t:FolderClass>{klass}</t:FolderClass></t:Folder>"
        for fid, name, klass in folders
    )
    return Reply(
        body=_envelope(
            f'<m:FindFolderResponse xmlns:m="{MNS}" xmlns:t="{TNS}"><m:ResponseMessages>'
            '<m:FindFolderResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode>'
            f'<m:RootFolder TotalItemsInView="{len(folders)}" IncludesLastItemInRange="true"><t:Folders>{items}'
            "</t:Folders></m:RootFolder></m:FindFolderResponseMessage></m:ResponseMessages></m:FindFolderResponse>"
        )
    )


MAIL = "IPF.Note"
FOLDERS = _find_folders(
    ("INBOX", "Inbox", MAIL),
    ("ARCHIVE", "Archive", MAIL),
    ("CAL", "Calendar", "IPF.Appointment"),
    ("CALARCHIVE", "Cal Archive", "IPF.Appointment"),
)


def _move_ok(new_id: str | None = "NEW1") -> Reply:
    item = f'<t:Message><t:ItemId Id="{new_id}" ChangeKey="NK"/></t:Message>' if new_id else ""
    return Reply(
        body=_envelope(
            f'<m:MoveItemResponse xmlns:m="{MNS}" xmlns:t="{TNS}"><m:ResponseMessages>'
            '<m:MoveItemResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode>'
            f"<m:Items>{item}</m:Items></m:MoveItemResponseMessage></m:ResponseMessages></m:MoveItemResponse>"
        )
    )


DELETE_ITEM_OK = Reply(
    body=_envelope(
        f'<m:DeleteItemResponse xmlns:m="{MNS}"><m:ResponseMessages>'
        '<m:DeleteItemResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode>'
        "</m:DeleteItemResponseMessage></m:ResponseMessages></m:DeleteItemResponse>"
    )
)

UPDATE_ITEM_OK = Reply(
    body=_envelope(
        f'<m:UpdateItemResponse xmlns:m="{MNS}" xmlns:t="{TNS}"><m:ResponseMessages>'
        '<m:UpdateItemResponseMessage ResponseClass="Success"><m:ResponseCode>NoError</m:ResponseCode><m:Items>'
        '<t:Message><t:ItemId Id="AAA" ChangeKey="CK2"/></t:Message></m:Items>'
        "<m:ConflictResults><t:Count>0</t:Count></m:ConflictResults>"
        "</m:UpdateItemResponseMessage></m:ResponseMessages></m:UpdateItemResponse>"
    )
)


def _error_reply(service: str, code: str) -> Reply:
    return Reply(
        body=_envelope(
            f'<m:{service}Response xmlns:m="{MNS}"><m:ResponseMessages>'
            f'<m:{service}ResponseMessage ResponseClass="Error"><m:MessageText>no</m:MessageText>'
            f"<m:ResponseCode>{code}</m:ResponseCode><m:DescriptiveLinkKey>0</m:DescriptiveLinkKey>"
            f"</m:{service}ResponseMessage></m:ResponseMessages></m:{service}Response>"
        )
    )


LOST = Reply(raises=requests.exceptions.ConnectionError("reset"))


def _requests_of(server: FakeServer, service: str) -> list[str]:
    return [s.body.decode() for s in server.requests if f"<m:{service}".encode() in s.body]


def _writes(server: FakeServer) -> list[str]:
    return [s.body.decode() for s in server.requests if any(f"<m:{n}".encode() in s.body for n in WRITE_SERVICES)]


WRITE_SERVICES = ("MoveItem", "DeleteItem", "UpdateItem", "CreateItem")


def _move(client: ec.EwsExchangeClient, **overrides):
    args = {"message_id": "AAA", "target_folder": "Archive"}
    return client.move(**{**args, **overrides})


# ---- move

MOVED_TO_ARCHIVE = {"status": "moved", "message_id": "NEW1", "target_folder": "Archive"}


def test_move_to_a_folder_by_display_name_moves_the_item_and_returns_the_new_id(server_for, clock):
    server = server_for(FOLDERS, _get_item(parent="INBOX"), _move_ok("NEW1"))

    result = _move(_client(clock))

    assert result == MOVED_TO_ARCHIVE
    [xml] = _requests_of(server, "MoveItem")
    assert '<t:FolderId Id="ARCHIVE"' in xml
    assert '<t:ItemId Id="AAA" ChangeKey="CK1"' in xml
    assert _writes(server) == [xml]
    assert clock.sleeps == []


def test_move_returns_the_target_folder_exactly_as_given(server_for, clock):
    server_for(FOLDERS, _get_item(), _move_ok())

    assert _move(_client(clock), target_folder="ARCHIVE")["target_folder"] == "ARCHIVE"


def test_move_resolves_a_folder_by_its_id(server_for, clock):
    server = server_for(FOLDERS, _get_item(), _move_ok())

    _move(_client(clock), target_folder="ARCHIVE")

    assert '<t:FolderId Id="ARCHIVE"' in _requests_of(server, "MoveItem")[0]


def test_move_never_resolves_a_non_mail_folder(server_for, clock):
    server = server_for(FOLDERS)

    with pytest.raises(WriteFolderNotFoundError) as raised:
        _move(_client(clock), target_folder="Cal Archive")

    assert raised.value.OUTCOME == "not_applied"
    assert _writes(server) == []


def test_move_to_a_well_known_folder_uses_the_distinguished_folder_of_the_mailbox(server_for, clock):
    server = server_for(_get_item(parent="INBOX"), _move_ok())

    result = _move(_client(clock), target_folder="DeletedItems")

    assert result["status"] == "moved"
    [xml] = _requests_of(server, "MoveItem")
    assert '<t:FolderId Id="TRASH"' in xml or 'DistinguishedFolderId Id="deleteditems"' in xml


def test_move_to_the_inbox_missing_on_the_server_is_a_not_applied_access_error(server_for, clock):
    server = server_for(_find_folders(("ARCHIVE", "Archive", MAIL)))  # exchangelib lists the folders first
    server.folder_replies["inbox"] = _error_reply("GetFolder", "ErrorFolderNotFound")

    with pytest.raises(EwsWriteAccessDeniedError) as raised:
        _move(_client(clock), target_folder="Inbox")

    assert raised.value.OUTCOME == "not_applied"
    assert _writes(server) == []


def test_move_to_a_well_known_folder_hidden_by_the_server_is_a_not_applied_access_error(server_for, clock):
    server = server_for(_find_folders(("ARCHIVE", "Archive", MAIL)))  # exchangelib lists the folders first
    server.folder_replies["deleteditems"] = _error_reply("GetFolder", "ErrorFolderNotFound")

    with pytest.raises(EwsWriteAccessDeniedError) as raised:
        _move(_client(clock), target_folder="DeletedItems")

    assert raised.value.OUTCOME == "not_applied"
    assert "folder name" not in str(raised.value)
    assert _writes(server) == []


def test_move_to_a_folder_the_account_cannot_see_gives_advice_covering_the_name_and_the_rights(server_for, clock):
    server = server_for(FOLDERS)  # the walk lists only the visible folders: a hidden "Target" is simply absent

    with pytest.raises(WriteFolderNotFoundError) as raised:
        _move(_client(clock), target_folder="Target")

    advice = str(raised.value)
    assert "folder name" in advice and "delegate rights" in advice
    assert raised.value.OUTCOME == "not_applied"
    assert _writes(server) == []


def test_move_to_an_unknown_folder_is_not_applied_and_does_not_read_or_write_the_message(server_for, clock):
    server = server_for(FOLDERS)

    with pytest.raises(WriteFolderNotFoundError):
        _move(_client(clock), target_folder="Nope")

    assert not any(b"GetItem" in s.body for s in server.requests)
    assert _writes(server) == []


def test_move_of_an_item_already_in_the_target_returns_already_done_without_a_write(server_for, clock):
    server = server_for(FOLDERS, _get_item(parent="ARCHIVE"))

    result = _move(_client(clock))

    assert result == {**MOVED_TO_ARCHIVE, "message_id": "AAA", "already_done": True}
    assert _writes(server) == []


def test_move_reads_the_item_from_the_server_ignoring_the_read_cache(server_for, clock):
    server = server_for(FOLDERS, _get_item(parent="INBOX"), _move_ok())
    client = _client(clock)
    stale = object()
    client._last_fetch = ("AAA", stale)

    _move(client)

    assert any(b"GetItem" in s.body for s in server.requests)
    assert client._last_fetch == ("AAA", stale)


@pytest.mark.parametrize(
    "kwargs",
    [{"message_id": " "}, {"message_id": None}, {"target_folder": "  "}, {"target_folder": None}, {"target_folder": 5}],
)
def test_move_with_invalid_input_is_not_applied_before_any_request(server_for, clock, kwargs):
    server = server_for()

    with pytest.raises(InvalidLocalInputError):
        _move(_client(clock), **kwargs)

    assert server.sent == []


def test_move_of_a_missing_item_is_unknown_and_nothing_is_written(server_for, clock):
    # An earlier move that ended Unknown may have changed the id already.
    server = server_for(FOLDERS, Reply(body=GET_ITEM_NOT_FOUND))

    with pytest.raises(WriteMessageNotFoundUnknownError) as raised:
        _move(_client(clock))

    assert raised.value.OUTCOME == "unknown"
    assert _writes(server) == []


def test_move_pre_read_connection_failure_is_not_applied_and_nothing_is_written(server_for, clock):
    server = server_for(FOLDERS, LOST)

    with pytest.raises(PreMutationReadFailedError):
        _move(_client(clock))

    assert _writes(server) == []
    assert clock.sleeps == []


def test_move_folder_listing_failure_is_not_applied_and_nothing_is_written(server_for, clock):
    server = server_for(LOST)

    with pytest.raises(PreMutationReadFailedError):
        _move(_client(clock))

    assert _writes(server) == []


def test_move_access_denied_by_the_server_is_a_not_applied_write_error(server_for, clock):
    server_for(FOLDERS, _get_item(), _error_reply("MoveItem", "ErrorAccessDenied"))

    with pytest.raises(EwsWriteAccessDeniedError) as raised:
        _move(_client(clock))

    assert raised.value.OUTCOME == "not_applied"


def test_move_lost_reply_where_the_same_id_is_now_in_the_target_is_a_success(server_for, clock):
    server = server_for(FOLDERS, _get_item(parent="INBOX"), LOST, _get_item(parent="ARCHIVE"))

    result = _move(_client(clock))

    assert result == {"status": "moved", "message_id": "AAA", "target_folder": "Archive"}
    assert len(_requests_of(server, "MoveItem")) == 1  # never replayed
    assert clock.sleeps == []


@pytest.mark.parametrize(
    "reconcile",
    [
        pytest.param(_get_item(parent="INBOX"), id="still-in-source"),
        pytest.param(Reply(body=GET_ITEM_NOT_FOUND), id="gone"),
        pytest.param(LOST, id="read-fails"),
    ],
)
def test_move_lost_reply_that_reconciliation_cannot_prove_stays_the_original_unknown(server_for, clock, reconcile):
    server = server_for(FOLDERS, _get_item(parent="INBOX"), LOST, reconcile)

    with pytest.raises(WriteConnectionLostUnknownError) as raised:
        _move(_client(clock))

    assert raised.value.OUTCOME == "unknown"
    assert len(_requests_of(server, "MoveItem")) == 1


def test_move_lost_reply_with_the_deadline_spent_stays_unknown_without_a_reconcile_read(server_for, clock):
    server = server_for(
        FOLDERS, _get_item(parent="INBOX"), Reply(raises=requests.exceptions.ConnectionError("x"), elapse=95)
    )

    with pytest.raises(UnknownOutcomeError):
        _move(_client(clock))

    assert len(_requests_of(server, "MoveItem")) == 1


def test_move_old_id_not_found_after_the_submission_is_unknown_not_not_applied(server_for, clock):
    server_for(FOLDERS, _get_item(), _error_reply("MoveItem", "ErrorItemNotFound"), Reply(body=GET_ITEM_NOT_FOUND))

    with pytest.raises(WriteMessageNotFoundUnknownError) as raised:
        _move(_client(clock))

    assert raised.value.OUTCOME == "unknown"


def test_move_reconciliation_read_is_a_fresh_server_read_and_never_a_retry_read(server_for, clock):
    server = server_for(FOLDERS, _get_item(parent="INBOX"), LOST, _get_item(parent="ARCHIVE"))

    _move(_client(clock))

    assert len([s for s in server.requests if b"GetItem" in s.body]) == 2


def test_move_retries_only_a_body_proven_server_busy_and_then_moves_once(server_for, clock):
    server = server_for(FOLDERS, _get_item(), BUSY_500_WITH_BACKOFF, _move_ok())

    result = _move(_client(clock))

    assert result == MOVED_TO_ARCHIVE
    assert len(_requests_of(server, "MoveItem")) == 2
    assert len([s for s in server.requests if b"GetItem" in s.body]) == 1
    assert clock.sleeps == [7.0]


def test_move_reply_without_an_item_id_reports_no_id_rather_than_the_old_one(server_for, clock):
    server_for(FOLDERS, _get_item(), _move_ok(None))

    assert _move(_client(clock))["message_id"] is None


# ---- delete


def _delete(client: ec.EwsExchangeClient, **overrides):
    args = {"message_id": "AAA"}
    return client.delete(**{**args, **overrides})


def test_soft_delete_is_a_move_to_deleted_items_and_never_a_delete_item(server_for, clock):
    server = server_for(_get_item(parent="INBOX"), _move_ok("NEW1"))

    result = _delete(_client(clock))

    assert result == {"status": "deleted", "soft": True, "message_id": "NEW1"}
    [xml] = _writes(server)
    assert "<m:MoveItem" in xml
    assert '<t:FolderId Id="TRASH"' in xml or 'DistinguishedFolderId Id="deleteditems"' in xml
    assert "DeleteItem" not in xml
    assert "SoftDelete" not in xml
    assert clock.sleeps == []


def test_soft_delete_of_an_item_already_in_deleted_items_makes_no_write(server_for, clock):
    server = server_for(_get_item(parent="TRASH"))

    result = _delete(_client(clock))

    assert result == {"status": "deleted", "soft": True, "message_id": "AAA", "already_done": True}
    assert _writes(server) == []


def test_soft_delete_lost_reply_where_the_same_id_is_now_in_deleted_items_is_a_success(server_for, clock):
    server = server_for(_get_item(parent="INBOX"), LOST, _get_item(parent="TRASH"))

    result = _delete(_client(clock))

    assert result == {"status": "deleted", "soft": True, "message_id": "AAA"}
    assert len(_writes(server)) == 1


@pytest.mark.parametrize(
    "reconcile",
    [
        pytest.param(_get_item(parent="INBOX"), id="still-in-source"),
        pytest.param(Reply(body=GET_ITEM_NOT_FOUND), id="gone"),
        pytest.param(LOST, id="read-fails"),
    ],
)
def test_soft_delete_unproven_lost_reply_stays_unknown_and_never_escalates_to_a_hard_delete(
    server_for, clock, reconcile
):
    server = server_for(_get_item(parent="INBOX"), LOST, reconcile)

    with pytest.raises(WriteConnectionLostUnknownError):
        _delete(_client(clock))

    assert len(_writes(server)) == 1
    assert _requests_of(server, "DeleteItem") == []


def test_soft_delete_old_id_not_found_after_the_submission_is_unknown_and_not_escalated(server_for, clock):
    server = server_for(_get_item(), _error_reply("MoveItem", "ErrorItemNotFound"), Reply(body=GET_ITEM_NOT_FOUND))

    with pytest.raises(WriteMessageNotFoundUnknownError):
        _delete(_client(clock))

    assert _requests_of(server, "DeleteItem") == []


def test_soft_delete_pre_read_failure_is_not_applied_and_writes_nothing(server_for, clock):
    server = server_for(LOST)

    with pytest.raises(PreMutationReadFailedError):
        _delete(_client(clock))

    assert _writes(server) == []


def test_soft_delete_of_a_missing_item_is_unknown(server_for, clock):
    server = server_for(Reply(body=GET_ITEM_NOT_FOUND))

    with pytest.raises(WriteMessageNotFoundUnknownError):
        _delete(_client(clock))

    assert _writes(server) == []


def test_soft_delete_deleted_items_lookup_failure_is_not_applied_and_writes_nothing(server_for, clock):
    server = server_for()
    server.folder_replies["deleteditems"] = LOST

    with pytest.raises(PreMutationReadFailedError):
        _delete(_client(clock))

    assert _writes(server) == []


def test_hard_delete_sends_one_hard_delete_item_after_one_pre_read(server_for, clock):
    server = server_for(_get_item(), DELETE_ITEM_OK)

    result = _delete(_client(clock), soft=False)

    assert result == {"status": "deleted", "soft": False, "message_id": "AAA"}
    [xml] = _writes(server)
    assert "<m:DeleteItem" in xml
    assert 'DeleteType="HardDelete"' in xml
    assert '<t:ItemId Id="AAA"' in xml
    assert len(_requests_of(server, "GetItem")) == 1
    assert "<m:MoveItem" not in xml


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(LOST, id="connection-lost"),
        pytest.param(_error_reply("DeleteItem", "ErrorItemNotFound"), id="not-found"),
        pytest.param(Reply(status=500, body=b"boom"), id="server-error"),
    ],
)
def test_hard_delete_unproven_outcome_stays_unknown_without_reconciliation_or_replay(server_for, clock, reply):
    server = server_for(_get_item(), reply)

    with pytest.raises(UnknownOutcomeError) as raised:
        _delete(_client(clock), soft=False)

    assert raised.value.OUTCOME == "unknown"
    assert len(_writes(server)) == 1
    assert len(_requests_of(server, "GetItem")) == 1  # the pre-read only: no reconciliation
    assert clock.sleeps == []


def test_hard_delete_item_not_found_after_the_pre_read_is_the_id_loss_unknown_error(server_for, clock):
    server_for(_get_item(), _error_reply("DeleteItem", "ErrorItemNotFound"))

    with pytest.raises(WriteMessageNotFoundUnknownError):
        _delete(_client(clock), soft=False)


def test_hard_delete_access_denied_is_not_applied(server_for, clock):
    server_for(_get_item(), _error_reply("DeleteItem", "ErrorAccessDenied"))

    with pytest.raises(EwsWriteAccessDeniedError) as raised:
        _delete(_client(clock), soft=False)

    assert raised.value.OUTCOME == "not_applied"


def test_hard_delete_retries_only_a_body_proven_server_busy(server_for, clock):
    server = server_for(_get_item(), BUSY_500_WITH_BACKOFF, DELETE_ITEM_OK)

    assert _delete(_client(clock), soft=False)["status"] == "deleted"
    assert len(_writes(server)) == 2
    assert clock.sleeps == [7.0]


@pytest.mark.parametrize("soft", ["false", 0, 1, None, "True"])
def test_delete_soft_must_be_a_real_bool(server_for, clock, soft):
    server = server_for()

    with pytest.raises(InvalidLocalInputError):
        _delete(_client(clock), soft=soft)

    assert server.sent == []


@pytest.mark.parametrize("message_id", [" ", None, 5])
def test_delete_with_an_invalid_message_id_is_not_applied_before_any_request(server_for, clock, message_id):
    server = server_for()

    with pytest.raises(InvalidLocalInputError):
        _delete(_client(clock), message_id=message_id)

    assert server.sent == []


# ---- tag


def _tag(client: ec.EwsExchangeClient, **overrides):
    args = {"message_id": "AAA", "category": "Blue"}
    return client.tag(**{**args, **overrides})


def test_tag_adds_the_category_keeping_the_existing_ones_in_order_with_never_overwrite(server_for, clock):
    server = server_for(_get_item(categories=["Red", "green"]), UPDATE_ITEM_OK)

    result = _tag(_client(clock))

    assert result == {"status": "tagged", "message_id": "AAA", "categories": ["Red", "green", "Blue"]}
    [xml] = _requests_of(server, "UpdateItem")
    assert 'ConflictResolution="NeverOverwrite"' in xml
    assert '<t:ItemId Id="AAA" ChangeKey="CK1"' in xml
    values = [e.text for e in to_xml(xml.encode()).iter(f"{{{TNS}}}String")]
    assert values == ["Red", "green", "Blue"]
    assert xml.count("<t:SetItemField>") == 1
    assert "Categories" in xml
    assert _writes(server) == [xml]
    assert clock.sleeps == []


def test_tag_on_an_item_without_categories_sets_just_the_new_one(server_for, clock):
    server_for(_get_item(categories=None), UPDATE_ITEM_OK)

    assert _tag(_client(clock))["categories"] == ["Blue"]


def test_tag_updates_only_the_categories_field(server_for, clock):
    server = server_for(_get_item(categories=["Red"]), UPDATE_ITEM_OK)

    _tag(_client(clock))

    [xml] = _requests_of(server, "UpdateItem")
    fields = [e.get("FieldURI") for e in to_xml(xml.encode()).iter(f"{{{TNS}}}FieldURI")]
    assert fields == ["item:Categories"]


@pytest.mark.parametrize("category", ["Blue", "blue", "BLUE"])
def test_tag_repeat_returns_already_done_without_a_save_whatever_the_case(server_for, clock, category):
    server = server_for(_get_item(categories=["Red", "Blue"]))

    result = _tag(_client(clock), category=category)

    assert result == {"status": "tagged", "message_id": "AAA", "categories": ["Red", "Blue"], "already_done": True}
    assert _writes(server) == []


def test_tag_appends_the_category_with_its_own_case(server_for, clock):
    server_for(_get_item(categories=["Red"]), UPDATE_ITEM_OK)

    assert _tag(_client(clock), category="Ünï Tag")["categories"] == ["Red", "Ünï Tag"]


def test_tag_reads_the_item_from_the_server_ignoring_the_read_cache(server_for, clock):
    server = server_for(_get_item(categories=[]), UPDATE_ITEM_OK)
    client = _client(clock)
    stale = object()
    client._last_fetch = ("AAA", stale)

    _tag(client)

    assert any(b"GetItem" in s.body for s in server.requests)
    assert client._last_fetch == ("AAA", stale)


def test_tag_stale_changekey_rereads_recomputes_and_saves_once_more(server_for, clock):
    server = server_for(
        _get_item(categories=["Red"], changekey="CK1"),
        _error_reply("UpdateItem", "ErrorIrresolvableConflict"),
        _get_item(categories=["Red", "Other"], changekey="CK9"),
        UPDATE_ITEM_OK,
    )

    result = _tag(_client(clock))

    assert result == {"status": "tagged", "message_id": "AAA", "categories": ["Red", "Other", "Blue"]}
    first, second = _requests_of(server, "UpdateItem")
    assert 'ChangeKey="CK1"' in first
    assert 'ChangeKey="CK9"' in second  # the recompute saves against the fresh changekey
    assert 'ConflictResolution="NeverOverwrite"' in second
    assert [e.text for e in to_xml(second.encode()).iter(f"{{{TNS}}}String")] == ["Red", "Other", "Blue"]
    assert clock.sleeps == [0.0]


def test_tag_recompute_that_finds_the_category_already_there_saves_nothing_more(server_for, clock):
    server = server_for(
        _get_item(categories=["Red"]),
        _error_reply("UpdateItem", "ErrorIrresolvableConflict"),
        _get_item(categories=["Red", "Blue"], changekey="CK9"),
    )

    result = _tag(_client(clock))

    assert result == {"status": "tagged", "message_id": "AAA", "categories": ["Red", "Blue"], "already_done": True}
    assert len(_requests_of(server, "UpdateItem")) == 1


def test_tag_second_conflict_is_a_category_conflict_error_not_applied(server_for, clock):
    conflict = _error_reply("UpdateItem", "ErrorIrresolvableConflict")
    server = server_for(_get_item(categories=[]), conflict, _get_item(categories=["X"], changekey="CK2"), conflict)

    with pytest.raises(CategoryConflictError) as raised:
        _tag(_client(clock))

    assert raised.value.OUTCOME == "not_applied"
    assert len(_requests_of(server, "UpdateItem")) == 2


def test_tag_recompute_counts_against_the_three_submissions(server_for, clock):
    conflict = _error_reply("UpdateItem", "ErrorIrresolvableConflict")
    server = server_for(
        _get_item(categories=[]),
        BUSY_500_WITH_BACKOFF,
        _get_item(categories=[]),  # every submission rereads the item first
        conflict,
        _get_item(categories=["X"], changekey="CK2"),
        conflict,
    )

    with pytest.raises(CategoryConflictError):
        _tag(_client(clock))

    assert len(_requests_of(server, "UpdateItem")) == 3  # busy + conflict + conflict: no fourth
    assert clock.sleeps == [7.0, 0.0]


def test_tag_read_failure_before_the_save_is_not_applied(server_for, clock):
    server = server_for(LOST)

    with pytest.raises(PreMutationReadFailedError) as raised:
        _tag(_client(clock))

    assert raised.value.OUTCOME == "not_applied"
    assert _writes(server) == []


@pytest.mark.parametrize(
    "busy",
    [BUSY_500_WITH_BACKOFF, Reply(body=_item_level_server_busy(7000))],
    ids=["soap-fault", "item-level"],
)
def test_tag_read_that_is_server_busy_with_a_back_off_waits_once_and_then_tags(server_for, clock, busy):
    server = server_for(busy, _get_item(categories=["Red"]), UPDATE_ITEM_OK)

    result = _tag(_client(clock))

    assert result == {"status": "tagged", "message_id": "AAA", "categories": ["Red", "Blue"]}
    assert clock.sleeps == [7.0]  # the server's wait, honoured exactly, before the one reread
    assert len(_requests_of(server, "GetItem")) == 2
    assert len(_requests_of(server, "UpdateItem")) == 1


@pytest.mark.parametrize(
    "busy",
    [Reply(status=500, body=_fault_server_busy(None)), Reply(body=_item_level_server_busy(None))],
    ids=["soap-fault", "item-level"],
)
def test_tag_read_that_is_server_busy_without_a_back_off_is_not_applied_and_not_waited_for(server_for, clock, busy):
    server = server_for(busy)

    with pytest.raises(WriteRateLimitedError) as raised:
        _tag(_client(clock))

    assert raised.value.OUTCOME == "not_applied"
    assert clock.sleeps == []
    assert len(_requests_of(server, "GetItem")) == 1
    assert _writes(server) == []


def test_tag_read_busy_retries_count_against_the_three_submissions(server_for, clock):
    server = server_for(*[BUSY_500_WITH_BACKOFF] * 3)

    with pytest.raises(WriteRateLimitedError) as raised:
        _tag(_client(clock))

    assert raised.value.OUTCOME == "not_applied"
    assert len(_requests_of(server, "GetItem")) == 3
    assert clock.sleeps == [7.0, 7.0]
    assert _writes(server) == []


def test_tag_busy_save_after_the_read_succeeded_is_still_retried_as_a_save(server_for, clock):
    server = server_for(_get_item(categories=[]), BUSY_500_WITH_BACKOFF, _get_item(categories=[]), UPDATE_ITEM_OK)

    assert _tag(_client(clock))["categories"] == ["Blue"]
    assert len(_requests_of(server, "UpdateItem")) == 2
    assert clock.sleeps == [7.0]


def test_tag_conflict_on_the_last_submission_is_a_category_conflict_not_rate_limiting(server_for, clock):
    server = server_for(
        BUSY_500_WITH_BACKOFF,
        BUSY_500_WITH_BACKOFF,
        _get_item(categories=[]),
        _error_reply("UpdateItem", "ErrorIrresolvableConflict"),
    )

    with pytest.raises(CategoryConflictError) as raised:
        _tag(_client(clock))

    assert raised.value.OUTCOME == "not_applied"
    assert len(_requests_of(server, "UpdateItem")) == 1  # two busy reads used two submissions
    assert clock.sleeps == [7.0, 7.0]


def test_tag_read_that_loses_the_connection_is_still_not_applied_and_not_retried(server_for, clock):
    server = server_for(LOST, _get_item(categories=[]), UPDATE_ITEM_OK)

    with pytest.raises(PreMutationReadFailedError) as raised:
        _tag(_client(clock))

    assert raised.value.OUTCOME == "not_applied"
    assert len(server.requests) - len([s for s in server.requests if _is_housekeeping(s.body)]) == 1
    assert clock.sleeps == []


def test_tag_of_a_missing_item_is_not_applied(server_for, clock):
    server = server_for(Reply(body=GET_ITEM_NOT_FOUND))

    with pytest.raises(WriteMessageNotFoundError):
        _tag(_client(clock))

    assert _writes(server) == []


def test_tag_lost_reply_after_the_save_is_unknown_with_no_reconciliation_and_no_replay(server_for, clock):
    server = server_for(_get_item(categories=["Red"]), LOST)

    with pytest.raises(WriteConnectionLostUnknownError) as raised:
        _tag(_client(clock))

    assert raised.value.OUTCOME == "unknown"
    assert len(_requests_of(server, "UpdateItem")) == 1
    assert len([s for s in server.requests if b"GetItem" in s.body]) == 1
    assert clock.sleeps == []


def test_tag_access_denied_on_the_save_is_not_applied(server_for, clock):
    server_for(_get_item(categories=[]), _error_reply("UpdateItem", "ErrorAccessDenied"))

    with pytest.raises(EwsWriteAccessDeniedError):
        _tag(_client(clock))


@pytest.mark.parametrize(
    "kwargs",
    [{"message_id": " "}, {"message_id": None}, {"category": ""}, {"category": None}, {"category": "a\nb"}],
)
def test_tag_with_invalid_input_is_not_applied_before_any_request(server_for, clock, kwargs):
    server = server_for()

    with pytest.raises(InvalidLocalInputError):
        _tag(_client(clock), **kwargs)

    assert server.sent == []


# ---- reads ask only for the fields the write needs


def _get_item_properties(server: FakeServer) -> list[set[str]]:
    """The ``FieldURI`` of every ``GetItem`` request's ``AdditionalProperties``, one set per request."""
    found = []
    for xml in _requests_of(server, "GetItem"):
        props = to_xml(xml.encode()).find(f".//{{{TNS}}}AdditionalProperties")
        found.append({e.get("FieldURI") for e in props.iter(f"{{{TNS}}}FieldURI")})
    return found


def test_tag_read_asks_only_for_the_categories_and_the_parent_folder(server_for, clock):
    server = server_for(_get_item(categories=[]), UPDATE_ITEM_OK)

    _tag(_client(clock))

    assert _get_item_properties(server) == [{"item:Categories", "item:ParentFolderId"}]


def test_forward_read_asks_only_for_the_body_and_the_parent_folder(server_for, clock):
    server = server_for(_get_item_with_body("Text"), Reply(body=CREATE_ITEM_OK))

    _forward(_client(clock))

    assert _get_item_properties(server) == [{"item:Body", "item:ParentFolderId"}]


def test_move_read_asks_only_for_the_parent_folder(server_for, clock):
    server = server_for(FOLDERS, _get_item(), _move_ok())

    _move(_client(clock))

    assert _get_item_properties(server) == [{"item:ParentFolderId"}]


def test_soft_delete_read_asks_only_for_the_parent_folder(server_for, clock):
    server = server_for(_get_item(parent="INBOX"), _move_ok("NEW1"))

    _delete(_client(clock))

    assert _get_item_properties(server) == [{"item:ParentFolderId"}]


# ---- mailbox scope: a message of another mailbox is never changed


def _folder_chain(server: FakeServer, *chain: str, root: str | None) -> None:
    """Script ``chain[0]`` -> ``chain[1]`` -> ... -> ``root`` as folders the fake server can return by id."""
    parents = [*chain[1:], root]
    for folder_id, parent in zip(chain, parents):
        parent_xml = "" if parent is None else f'<t:ParentFolderId Id="{parent}" ChangeKey="PK"/>'
        server.known_folders[folder_id] = (
            f'<t:Folder xmlns:t="{TNS}"><t:FolderId Id="{folder_id}" ChangeKey="K"/>{parent_xml}</t:Folder>'
        )


def _folder_lookups(server: FakeServer) -> list[str]:
    """Ids of the folders GetFolder was asked for by id after the item was read (the mailbox check's walk)."""
    after_read = False
    walked = []
    for sent in server.sent:
        if b"GetItem" in sent.body:
            after_read = True
        elif after_read and b"GetFolder" in sent.body:
            walked += [folder_id for kind, folder_id in _requested_folders(sent.body) if kind == "FolderId"]
    return walked


def _scenarios(parent: str) -> dict[str, tuple[Callable, list[Reply]]]:
    """Per EWS write that takes a message_id: the call and the replies up to (and including) its first mutation."""
    return {
        "tag": (_tag, [_get_item(parent=parent), UPDATE_ITEM_OK]),
        "forward": (_forward, [_get_item(parent=parent), Reply(body=CREATE_ITEM_OK)]),
        "move": (_move, [FOLDERS, _get_item(parent=parent), _move_ok()]),
        "soft-delete": (_delete, [_get_item(parent=parent), _move_ok("NEW1")]),
        "hard-delete": (lambda client: _delete(client, soft=False), [_get_item(parent=parent), DELETE_ITEM_OK]),
    }


@pytest.mark.parametrize("op", list(_scenarios("INBOX")))
def test_a_message_in_a_standard_folder_of_the_mailbox_is_changed_without_walking_folders(server_for, clock, op):
    call, replies = _scenarios("INBOX")[op]
    server = server_for(*replies)

    call(_client(clock))

    assert _folder_lookups(server) == []  # Inbox is among the folders fetched in the one batched GetFolder
    assert _writes(server) != []


@pytest.mark.parametrize("op", list(_scenarios("PROJ26")))
def test_a_message_in_a_nested_folder_is_accepted_after_walking_up_to_a_folder_of_the_mailbox(server_for, clock, op):
    call, replies = _scenarios("PROJ26")[op]
    server = server_for(*replies)
    _folder_chain(server, "PROJ26", "PROJ", root="MSGROOT")

    call(_client(clock))

    assert _folder_lookups(server) == ["PROJ26", "PROJ"]
    assert _writes(server) != []


_ID_LOSS_SCENARIOS = ("move", "soft-delete", "hard-delete")


@pytest.mark.parametrize("op", list(_scenarios("B_INBOX")))
def test_a_message_of_another_mailbox_is_refused_and_nothing_is_sent_to_change_it(server_for, clock, op):
    call, replies = _scenarios("B_INBOX")[op]
    server = server_for(*replies)
    _folder_chain(server, "B_INBOX", root="B_ROOT")  # B's Inbox sits under B's own root, which is not ours
    _folder_chain(server, "B_ROOT", root=None)

    expected = WriteMessageNotFoundUnknownError if op in _ID_LOSS_SCENARIOS else WriteMessageNotFoundError
    with pytest.raises(expected):
        call(_client(clock))

    assert _folder_lookups(server) == ["B_INBOX", "B_ROOT"]
    assert _writes(server) == []


@pytest.mark.parametrize("op", list(_scenarios("B_INBOX")))
def test_a_message_of_another_mailbox_is_reported_exactly_like_a_missing_one(server_for, clock, op):
    call, replies = _scenarios("B_INBOX")[op]
    server = server_for(*replies)
    _folder_chain(server, "B_INBOX", root=None)
    with pytest.raises(ExchangeError) as foreign:
        call(_client(clock))

    # The same call with its GetItem answered "not found" and no mutation reply.
    server_for(*[Reply(body=GET_ITEM_NOT_FOUND) if r is replies[-2] else r for r in replies[:-1]])
    with pytest.raises(ExchangeError) as missing:
        call(_client(clock))

    assert type(foreign.value) is type(missing.value)
    assert str(foreign.value) == str(missing.value)


def test_a_loop_in_the_folder_chain_is_rejected(server_for, clock):
    server = server_for(_get_item(parent="LOOP1"))
    _folder_chain(server, "LOOP1", "LOOP2", root="LOOP1")
    _folder_chain(server, "LOOP2", "LOOP1", root="LOOP2")

    with pytest.raises(WriteMessageNotFoundError):
        _tag(_client(clock))

    assert _folder_lookups(server) == ["LOOP1", "LOOP2"]
    assert _writes(server) == []


def test_a_folder_chain_deeper_than_the_limit_is_rejected(server_for, clock):
    server = server_for(_get_item(parent="D0"))
    names = [f"D{i}" for i in range(40)]
    _folder_chain(server, *names, root="MSGROOT")

    with pytest.raises(WriteMessageNotFoundError):
        _tag(_client(clock))

    assert len(_folder_lookups(server)) == ec._MAX_FOLDER_DEPTH
    assert _writes(server) == []


@pytest.mark.parametrize("code", ["ErrorAccessDenied", "ErrorFolderNotFound"])
def test_a_folder_that_cannot_be_read_partway_up_is_rejected_as_not_applied(server_for, clock, code):
    server = server_for(_get_item(parent="PROJ26"))
    _folder_chain(server, "PROJ26", root="PROJ")
    server.folder_error_codes["PROJ"] = code

    with pytest.raises(WriteMessageNotFoundError) as raised:
        _tag(_client(clock))

    assert raised.value.OUTCOME == "not_applied"
    assert _writes(server) == []


def test_a_transport_failure_during_the_walk_is_a_read_failure_not_a_not_found(server_for, clock):
    server = server_for(_get_item(parent="PROJ26"))
    server.walk_raises = requests.exceptions.ConnectionError("reset")

    with pytest.raises(PreMutationReadFailedError) as raised:
        _tag(_client(clock))

    assert not isinstance(raised.value, WriteMessageNotFoundError)
    assert raised.value.OUTCOME == "not_applied"
    assert _writes(server) == []


def test_a_retried_tag_does_not_fetch_the_mailbox_folders_or_walk_a_proven_folder_again(server_for, clock):
    server = server_for(
        _get_item(parent="PROJ26"),
        BUSY_500_WITH_BACKOFF,  # the save is busy: the whole attempt runs again
        _get_item(parent="PROJ26"),
        UPDATE_ITEM_OK,
    )
    _folder_chain(server, "PROJ26", "PROJ", root="MSGROOT")

    _tag(_client(clock))

    assert _folder_lookups(server) == ["PROJ26", "PROJ"]  # once, not once per attempt
    batches = [
        s
        for s in server.sent
        if b"GetFolder" in s.body and {i for _, i in _requested_folders(s.body)} == set(MAILBOX_FOLDER_IDS)
    ]
    assert len(batches) == 1


def test_the_rejection_is_logged_with_a_reason_and_no_message_or_folder_ids(server_for, clock):
    # ``codemie_tools`` loggers do not propagate to the root logger (see test_write_errors.py), so patch the logger.
    server = server_for(_get_item(parent="B_INBOX", item_id="SECRET_MESSAGE_ID"))
    _folder_chain(server, "B_INBOX", root=None)

    with mock.patch.object(ec, "logger") as logger:
        with pytest.raises(WriteMessageNotFoundError):
            _tag(_client(clock), message_id="SECRET_MESSAGE_ID")

    [call] = logger.warning.call_args_list
    template, *args = call.args
    rendered = template % tuple(args)
    assert "op=tag" in rendered
    assert "reason=outside_mailbox" in rendered
    assert "B_INBOX" not in rendered
    assert "SECRET_MESSAGE_ID" not in rendered


def test_hard_delete_of_a_missing_id_is_unknown(server_for, clock):
    server = server_for(Reply(body=GET_ITEM_NOT_FOUND))

    with pytest.raises(WriteMessageNotFoundUnknownError) as raised:
        _delete(_client(clock), soft=False)

    assert raised.value.OUTCOME == "unknown"
    assert _writes(server) == []


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(_error_reply("GetItem", "ErrorInvalidIdMalformed"), id="malformed"),
        pytest.param(_error_reply("GetItem", "ErrorInvalidIdEmpty"), id="empty"),
        pytest.param(_error_reply("GetItem", "ErrorInvalidIdXml"), id="xml"),
    ],
)
def test_hard_delete_of_a_malformed_id_is_not_applied_not_found(server_for, clock, reply):
    server = server_for(reply)

    with pytest.raises(WriteMessageNotFoundError) as raised:
        _delete(_client(clock), soft=False)

    assert raised.value.OUTCOME == "not_applied"
    assert _writes(server) == []
