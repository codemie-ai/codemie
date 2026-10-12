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
"""Tests for the Graph-backed Exchange client (SDK fully faked, fixture-only credentials)."""

import datetime as dt
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie_tools.ms_exchange import graph_client as gc
from codemie_tools.ms_exchange.errors import FolderNotFoundError, InternalError, RateLimitedError
from codemie_tools.ms_exchange.models import ExchangeAuthType, ExchangeToolConfig

PREFER = 'outlook.body-content-type="text"'


class FakeGraphError(Exception):
    def __init__(self, status, code=None, text="secret tenant-id https://login.example"):
        super().__init__(text)
        self.response_status_code = status
        self.error = SimpleNamespace(code=code)


def _config():
    return ExchangeToolConfig(
        auth_type=ExchangeAuthType.OAUTH_AZURE,
        mailbox="user@example.test",
        oauth_tenant_id="tenant-fixture",
        oauth_client_id="client-fixture",
        oauth_client_secret="secret-fixture",
    )


def _msg(**kw):
    base = {
        "id": "m1",
        "subject": "Hi",
        "received_date_time": None,
        "is_read": False,
        "has_attachments": True,
        "from_": SimpleNamespace(email_address=SimpleNamespace(name="A", address="a@example.test")),
        "to_recipients": [SimpleNamespace(email_address=SimpleNamespace(name="B", address="b@example.test"))],
        "cc_recipients": [],
        "body": SimpleNamespace(content="plain body"),
    }
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def graph():
    g = MagicMock()
    user = g.users.by_user_id.return_value
    user.mail_folders.get = AsyncMock(
        return_value=SimpleNamespace(
            value=[SimpleNamespace(id="f1", display_name="Inbox", total_item_count=5, unread_item_count=2)]
        )
    )
    folder_msgs = user.mail_folders.by_mail_folder_id.return_value.messages
    folder_msgs.get = AsyncMock(return_value=SimpleNamespace(value=[_msg()]))
    item = user.messages.by_message_id.return_value
    item.get = AsyncMock(return_value=_msg())
    item.attachments.get = AsyncMock(
        return_value=SimpleNamespace(
            value=[
                SimpleNamespace(
                    id="a1",
                    name="f.txt",
                    content_type="text/plain",
                    size=3,
                    odata_type="#microsoft.graph.fileAttachment",
                )
            ]
        )
    )
    item.attachments.by_attachment_id.return_value.get = AsyncMock(return_value=SimpleNamespace(content_bytes=b"abc"))
    return g


@pytest.fixture
def client(graph):
    with patch.object(gc, "build_graph_client", return_value=graph):
        yield gc.GraphExchangeClient(_config())


def test_build_credential_is_synchronous_with_explicit_timeouts():
    with patch.object(gc, "ClientSecretCredential") as cred_cls:
        gc.build_credential(_config())

    kwargs = cred_cls.call_args.kwargs
    assert kwargs["tenant_id"] == "tenant-fixture"
    assert kwargs["connection_timeout"] == gc.TOKEN_TIMEOUT_SECONDS
    assert kwargs["read_timeout"] == gc.TOKEN_TIMEOUT_SECONDS
    assert kwargs["retry_total"] == gc.TOKEN_RETRY_TOTAL
    # Two attempts must stay inside the call bound, otherwise wait_for cannot help.
    assert 2 * 2 * gc.TOKEN_TIMEOUT_SECONDS < gc.REQUEST_TIMEOUT_SECONDS


def test_graph_client_disables_kiota_retry_handler():
    graph_client = gc.build_graph_client(gc.build_credential(_config()))

    middleware = graph_client.request_adapter._http_client._transport.pipeline._first_middleware
    handlers = {}
    while middleware is not None:
        handlers[type(middleware).__name__] = middleware
        middleware = middleware.next
    assert handlers["RetryHandler"].options.should_retry is False


def test_credential_is_built_once_and_reused_across_calls_and_retries(graph):
    credential = object()
    item = graph.users.by_user_id.return_value.messages.by_message_id.return_value
    item.get = AsyncMock(side_effect=[FakeGraphError(503), _msg(), _msg()])
    with (
        patch.object(gc, "build_credential", return_value=credential) as build_credential,
        patch.object(gc, "build_graph_client", return_value=graph) as build_client,
        patch("codemie_tools.ms_exchange.retry.time.sleep"),
    ):
        client = gc.GraphExchangeClient(_config())
        client.get_message("m1")
        client.get_message("m2")

    build_credential.assert_called_once()
    assert build_client.call_count == 3  # a fresh loop-bound client per attempt
    assert all(call.args == (credential,) for call in build_client.call_args_list)


def _cfg_of(mock):
    return mock.call_args.kwargs["request_configuration"]


def test_get_message_sends_prefer_text_header(client, graph):
    result = client.get_message("m1")
    get = graph.users.by_user_id.return_value.messages.by_message_id.return_value.get
    assert _cfg_of(get).headers.get("Prefer") == {PREFER}
    graph.users.by_user_id.assert_called_with("user@example.test")
    assert result["id"] == "m1" and result["body"] == "plain body"
    assert result["from"] == "A <a@example.test>"


def test_get_text_body_sends_prefer_header(client, graph):
    assert client.get_text_body("m1") == "plain body"
    get = graph.users.by_user_id.return_value.messages.by_message_id.return_value.get
    assert _cfg_of(get).headers.get("Prefer") == {PREFER}


def test_list_messages_orders_caps_and_does_not_mark_read(client, graph):
    out = client.list_messages("inbox", 10, 10_000)
    f = graph.users.by_user_id.return_value.mail_folders
    f.by_mail_folder_id.assert_called_with("inbox")
    get = f.by_mail_folder_id.return_value.messages.get
    qp = _cfg_of(get).query_parameters
    assert qp.orderby == ["receivedDateTime desc"]  # `id` in $orderby may be rejected by Graph
    assert qp.top == gc.MAX_PAGE and qp.skip == 10
    graph.users.by_user_id.return_value.messages.by_message_id.return_value.patch.assert_not_called()
    assert out[0]["id"] == "m1" and "body" not in out[0]


def test_list_messages_breaks_received_ties_by_id_client_side(client, graph):
    same = {"received_date_time": dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc)}
    folder_msgs = graph.users.by_user_id.return_value.mail_folders.by_mail_folder_id.return_value.messages
    folder_msgs.get = AsyncMock(return_value=SimpleNamespace(value=[_msg(id="b", **same), _msg(id="a", **same)]))
    assert [m["id"] for m in client.list_messages("inbox", 0, 10)] == ["a", "b"]


def test_check_inbox_single_minimal_request_and_empty_ok(client, graph):
    msgs = graph.users.by_user_id.return_value.mail_folders.by_mail_folder_id.return_value.messages
    msgs.get = AsyncMock(return_value=SimpleNamespace(value=[]))
    client.check_inbox()
    assert msgs.get.await_count == 1
    qp = _cfg_of(msgs.get).query_parameters
    assert qp.top == 1 and qp.select == ["id"]


def test_list_folders_shape(client):
    assert client.list_folders() == [{"id": "f1", "name": "Inbox", "total_count": 5, "unread_count": 2}]


def test_attachments_shape_and_bytes(client):
    assert client.list_attachments("m1") == [
        {"id": "a1", "name": "f.txt", "content_type": "text/plain", "size": 3, "is_file": True}
    ]
    assert client.get_attachment_bytes("m1", "a1") == b"abc"


def test_errors_are_mapped_and_sanitised(client, graph):
    item = graph.users.by_user_id.return_value.messages.by_message_id.return_value
    item.get = AsyncMock(side_effect=FakeGraphError(404, "ErrorFolderNotFound"))
    with pytest.raises(FolderNotFoundError) as ei:
        client.get_message("m1")
    assert "tenant" not in str(ei.value) and "login" not in str(ei.value)
    item.get = AsyncMock(side_effect=FakeGraphError(400, "Weird"))
    with pytest.raises(InternalError):
        client.get_message("m1")


def test_transient_errors_are_retried(client, graph):
    item = graph.users.by_user_id.return_value.messages.by_message_id.return_value
    item.get = AsyncMock(side_effect=[FakeGraphError(503), _msg()])
    with patch("codemie_tools.ms_exchange.retry.time.sleep") as sleep:
        assert client.get_message("m1")["id"] == "m1"
    sleep.assert_called_once()


def test_retry_exhaustion_maps_429(client, graph):
    item = graph.users.by_user_id.return_value.messages.by_message_id.return_value
    item.get = AsyncMock(side_effect=FakeGraphError(429))
    with patch("codemie_tools.ms_exchange.retry.time.sleep"), pytest.raises(RateLimitedError):
        client.get_message("m1")


def test_bridge_without_running_loop():
    async def coro():
        return 7

    assert gc.run_sync(coro) == 7


def test_bridge_with_running_loop():
    async def coro():
        return 8

    async def outer():
        return gc.run_sync(coro)

    assert asyncio.run(outer()) == 8


def test_build_graph_client_authenticates_with_the_given_credential():
    credential = object()
    with (
        patch.object(gc, "AzureIdentityAuthenticationProvider") as auth,
        patch.object(gc, "GraphServiceClient") as svc,
    ):
        result = gc.build_graph_client(credential)
    auth.assert_called_once_with(credential, scopes=gc.GRAPH_SCOPES)
    assert result is svc.return_value


def _folder(fid, name, children=0):
    return SimpleNamespace(
        id=fid, display_name=name, total_item_count=1, unread_item_count=0, child_folder_count=children
    )


def test_read_sends_text_prefer_header_and_check_inbox_too(client, graph):
    client.list_messages("inbox", 0, 5)
    get = graph.users.by_user_id.return_value.mail_folders.by_mail_folder_id.return_value.messages.get
    assert _cfg_of(get).headers.get("Prefer") == {PREFER}


def test_list_folders_pages_requests_top_250_and_walks_children(client, graph):
    mf = graph.users.by_user_id.return_value.mail_folders
    mf.get = AsyncMock(return_value=SimpleNamespace(value=[_folder("f1", "Inbox", 1)], odata_next_link="NEXT"))
    mf.with_url.return_value.get = AsyncMock(
        return_value=SimpleNamespace(value=[_folder("f2", "Sent")], odata_next_link=None)
    )
    mf.by_mail_folder_id.return_value.child_folders.get = AsyncMock(
        return_value=SimpleNamespace(value=[_folder("f3", "Sub")], odata_next_link=None)
    )
    out = client.list_folders()
    assert [f["id"] for f in out] == ["f1", "f3", "f2"]
    assert _cfg_of(mf.get).query_parameters.top == 250
    mf.with_url.assert_called_with("NEXT")
    mf.by_mail_folder_id.assert_called_with("f1")


def test_list_folders_none_response_is_empty(client, graph):
    graph.users.by_user_id.return_value.mail_folders.get = AsyncMock(return_value=None)
    assert client.list_folders() == []


def test_list_attachments_follows_next_link(client, graph):
    atts = graph.users.by_user_id.return_value.messages.by_message_id.return_value.attachments
    a = lambda i: SimpleNamespace(id=i, name=i, content_type="t", size=1, odata_type=None)  # noqa: E731
    atts.get = AsyncMock(return_value=SimpleNamespace(value=[a("a1")], odata_next_link="N"))
    atts.with_url.return_value.get = AsyncMock(return_value=SimpleNamespace(value=[a("a2")], odata_next_link=None))
    assert [x["id"] for x in client.list_attachments("m1")] == ["a1", "a2"]
    atts.with_url.assert_called_with("N")


def test_none_attachment_response_is_empty(client, graph):
    atts = graph.users.by_user_id.return_value.messages.by_message_id.return_value.attachments
    atts.get = AsyncMock(return_value=None)
    assert client.list_attachments("m1") == []


def test_folder_display_name_resolved_to_id(client, graph):
    mf = graph.users.by_user_id.return_value.mail_folders
    mf.get = AsyncMock(return_value=SimpleNamespace(value=[_folder("id-sent", "Sent Items")], odata_next_link=None))
    client.list_messages("Sent Items", 0, 5)
    mf.by_mail_folder_id.assert_called_with("id-sent")


def test_unknown_folder_name_is_folder_not_found(client, graph):
    with pytest.raises(FolderNotFoundError):
        client.list_messages("No Such Folder", 0, 5)


@pytest.mark.parametrize("folder", ["", "   "])
def test_blank_folder_is_rejected(client, folder):
    with pytest.raises(FolderNotFoundError):
        client.list_messages(folder, 0, 5)


def test_client_is_built_per_call_so_fresh_loops_are_safe(graph):
    with patch.object(gc, "build_graph_client", return_value=graph) as build:
        c = gc.GraphExchangeClient(_config())
        assert build.call_count == 0
        c.list_folders()
        c.list_folders()
    assert build.call_count == 2
