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
"""Spike: pin the third-party library facts the Exchange write spec leaves open.

These tests are executable documentation. They touch no project code, run fully offline
(every HTTP transport is faked), and exist so a dependency bump that invalidates one of
the spec's assumptions fails here instead of in production. Any assumption that did not
hold is recorded in the owning test's docstring starting with ``DEVIATION:``.

Pinned versions at the time of writing: exchangelib 5.6.0, azure-identity 1.24.0,
msgraph-sdk 1.63.0, microsoft-kiota-abstractions/http 1.14.1.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import Mock

import httpx
import pytest
from azure.identity import ClientSecretCredential
from exchangelib import DELEGATE, NTLM, Account, Configuration, Credentials, FailFast, Mailbox, Version
from exchangelib.errors import EWSError, ErrorAccessDenied, ErrorIrresolvableConflict, ErrorSendAsDenied
from exchangelib.items import SEND_AND_SAVE_COPY, SEND_TO_NONE, ForwardItem, Message
from exchangelib.properties import DistinguishedFolderId, ItemId
from exchangelib.protocol import CachingProtocol, Protocol
from exchangelib.services import CreateItem, SendItem
from exchangelib.util import create_element, set_xml_value, xml_to_str
from exchangelib.version import EXCHANGE_2016
from kiota_abstractions.authentication.anonymous_authentication_provider import AnonymousAuthenticationProvider
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.method import Method
from kiota_abstractions.request_information import RequestInformation
from kiota_authentication_azure.azure_identity_authentication_provider import AzureIdentityAuthenticationProvider
from kiota_http.middleware.options import RetryHandlerOption
from msgraph import GraphRequestAdapter, GraphServiceClient
from msgraph.generated.models.message import Message as GraphMessage
from msgraph_core import GraphClientFactory
from requests.adapters import HTTPAdapter

from codemie_tools.ms_exchange.ews_client import build_ews_write_session
from codemie_tools.ms_exchange.models import ExchangeAuthType, ExchangeToolConfig
from codemie_tools.ms_exchange.write_retry import Deadline

GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]
MAILBOX = "shared@example.test"
MESSAGE_ID = "AAMkAGm1"
ETAG = 'W/"CQAAABYAAAD1"'


# --------------------------------------------------------------------------------------
# (a) msgraph-sdk / kiota: does a message PATCH carry an If-Match header?
# --------------------------------------------------------------------------------------


def _graph_client(handler) -> tuple[GraphServiceClient, list[httpx.Request]]:
    """A GraphServiceClient whose only non-fake part is the kiota middleware stack."""
    seen: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    http_client = GraphClientFactory.create_with_default_middleware(
        client=httpx.AsyncClient(transport=httpx.MockTransport(_record)),
        options={RetryHandlerOption.get_key(): RetryHandlerOption(should_retry=False)},
    )
    adapter = GraphRequestAdapter(AnonymousAuthenticationProvider(), http_client)
    return GraphServiceClient(request_adapter=adapter), seen


def _message_builder(client: GraphServiceClient):
    return client.users.by_user_id(MAILBOX).messages.by_message_id(MESSAGE_ID)


def test_graph_patch_request_configuration_accepts_an_if_match_header():
    """CONFIRMED: RequestConfiguration.headers carries If-Match into the RequestInformation."""
    client, _ = _graph_client(lambda request: httpx.Response(200))
    config = RequestConfiguration()
    config.headers.try_add("If-Match", ETAG)

    request_info = _message_builder(client).to_patch_request_information(
        GraphMessage(categories=["Done"]), request_configuration=config
    )

    assert request_info.headers.try_get("If-Match") == {ETAG}


def test_kiota_http_stack_sends_the_if_match_header_on_the_wire():
    """CONFIRMED client-side only: kiota's middleware forwards If-Match verbatim to httpx.

    Graph's *server-side* enforcement of If-Match on /messages/{id} cannot be verified
    offline: nothing in the installed stack validates, strips or interprets the header, so
    a 412 can only be observed live. T10/T11 must treat the concurrency guarantee for the
    Graph transport as unverified until a live check is run.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps({"id": MESSAGE_ID, "categories": ["Done"]}),
        )

    client, seen = _graph_client(handler)
    config = RequestConfiguration()
    config.headers.try_add("If-Match", ETAG)

    asyncio.run(_message_builder(client).patch(GraphMessage(categories=["Done"]), request_configuration=config))

    assert len(seen) == 1
    assert seen[0].method == "PATCH"
    assert seen[0].headers["If-Match"] == ETAG


# --------------------------------------------------------------------------------------
# (b) azure-identity: is the client-credentials token cached across two get_token calls?
# --------------------------------------------------------------------------------------


AUTHORITY = "https://login.microsoftonline.com/tenant-fixture"
TOKEN_ENDPOINT = f"{AUTHORITY}/oauth2/v2.0/token"
_DISCOVERY_DOC = {
    "authorization_endpoint": f"{AUTHORITY}/oauth2/v2.0/authorize",
    "token_endpoint": TOKEN_ENDPOINT,
    "device_authorization_endpoint": f"{AUTHORITY}/oauth2/v2.0/devicecode",
    "issuer": f"{AUTHORITY}/v2.0",
}
_TOKEN_DOC = {
    "access_token": "token-fixture",
    "token_type": "Bearer",
    "expires_in": 3600,
    "ext_expires_in": 3600,
}


def _json_response(payload: dict) -> Mock:
    body = json.dumps(payload)
    response = Mock(status_code=200, headers={"content-type": "application/json"}, content_type="application/json")
    response.text = lambda encoding=None: body
    response.body = lambda: body.encode("utf-8")
    return response


class RecordingTransport:
    """A synchronous azure-core transport that never touches the network."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    def send(self, request, **kwargs):  # noqa: ARG002 - transport protocol
        self.requests.append(f"{request.method} {request.url}")
        if "/token" in request.url and request.method == "POST":
            return _json_response(_TOKEN_DOC)
        return _json_response(_DISCOVERY_DOC)

    @property
    def token_requests(self) -> list[str]:
        return [r for r in self.requests if r.startswith("POST") and "/token" in r]

    def open(self) -> None: ...

    def close(self) -> None: ...

    def sleep(self, duration) -> None: ...

    def __enter__(self) -> "RecordingTransport":
        return self

    def __exit__(self, *args) -> None: ...


def _patch_request_information() -> RequestInformation:
    """A fully-resolved PATCH RequestInformation, as the adapter hands it to the auth provider."""
    request_info = RequestInformation(Method.PATCH)
    request_info.url = f"https://graph.microsoft.com/v1.0/users/{MAILBOX}/messages/{MESSAGE_ID}"
    return request_info


def _credential(transport: RecordingTransport) -> ClientSecretCredential:
    return ClientSecretCredential(
        tenant_id="tenant-fixture",
        client_id="client-fixture",
        client_secret="secret-fixture",
        transport=transport,
        disable_instance_discovery=True,
        connection_timeout=10,
        read_timeout=10,
        retry_total=1,
    )


def test_client_secret_credential_reuses_the_cached_token_on_a_second_get_token():
    """CONFIRMED: two identical get_token calls hit the token endpoint exactly once."""
    transport = RecordingTransport()
    credential = _credential(transport)

    first = credential.get_token(*GRAPH_SCOPES)
    second = credential.get_token(*GRAPH_SCOPES)

    assert first.token == second.token
    assert len(transport.token_requests) == 1


def test_kiota_auth_provider_reuses_the_token_the_owner_already_acquired():
    """DEVIATION: it does NOT. The provider asks for a CAE token, which MSAL caches separately.

    ``AzureIdentityAccessTokenProvider.__init__`` defaults ``is_cae_enabled=True`` and calls
    ``credential.get_token(*scopes, claims=None, enable_cae=True)``. A plain
    ``credential.get_token(scope)`` (enable_cae=False) stores a non-CAE entry, so the
    provider's first request misses the cache and issues a *second* token request. The spec's
    AC9 plan -- "acquire the token explicitly under the deadline, the write then reuses the
    cached token" -- therefore does not hold as written: the write would do its own,
    uninterruptible token acquisition outside the deadline.

    Fix for the owner task (T5/T6, and the Graph write path in T9/T10/T11): build the auth
    provider with the CAE flag matching the pre-warm call, i.e. pre-warm with
    ``credential.get_token(*GRAPH_SCOPES, enable_cae=True)``. This test pins that the
    CAE-matched pre-warm *is* reused (one token request total).
    """
    transport = RecordingTransport()
    credential = _credential(transport)
    provider = AzureIdentityAuthenticationProvider(credential, scopes=GRAPH_SCOPES)

    # Naive pre-warm, exactly as the spec describes it.
    credential.get_token(*GRAPH_SCOPES)
    assert len(transport.token_requests) == 1

    asyncio.run(provider.authenticate_request(_patch_request_information()))

    # The provider's CAE request is not served from the non-CAE cache entry.
    assert len(transport.token_requests) == 2

    # A CAE-matched pre-warm is reused: no further token request.
    credential.get_token(*GRAPH_SCOPES, enable_cae=True)
    request_info = _patch_request_information()
    asyncio.run(provider.authenticate_request(request_info))

    assert len(transport.token_requests) == 2
    assert request_info.headers.try_get("Authorization") == {"Bearer token-fixture"}


# --------------------------------------------------------------------------------------
# (c) exchangelib 5.6.0
# --------------------------------------------------------------------------------------


def _version() -> Version:
    return Version(EXCHANGE_2016)


@pytest.fixture(autouse=True)
def _no_protocol_cache_leak():
    """Accounts built below register a cached Protocol; drop it so tests stay independent."""
    yield
    Protocol.clear_cache()


def _account() -> Account:
    """A real Account that performs no I/O: explicit endpoint, auth_type and version."""
    config = Configuration(
        service_endpoint="https://ews.example.test/EWS/Exchange.asmx",
        credentials=Credentials("svc", "pw"),
        auth_type=NTLM,
        version=_version(),
    )
    return Account(primary_smtp_address=MAILBOX, config=config, autodiscover=False, access_type=DELEGATE)


def _sent_folder() -> DistinguishedFolderId:
    """Stand-in for ``account.sent``; resolving the real folder would need a server round trip."""
    return DistinguishedFolderId(id="sentitems", mailbox=Mailbox(email_address=MAILBOX))


def _message_with_id(account: Account) -> Message:
    message = Message(account=account, subject="Original subject")
    message._id = ItemId(id=MESSAGE_ID, changekey="CK1")
    return message


def test_build_ews_write_session_pins_failfast_explicitly_not_via_the_library_default():
    """CONFIRMED: ``build_ews_write_session`` passes ``retry_policy=FailFast()`` to its private
    ``Configuration`` itself; the write session's protocol does not depend on a bare ``Configuration``
    defaulting to ``FailFast`` (a library change there would not be caught by relying on the default)."""
    config = ExchangeToolConfig(
        auth_type=ExchangeAuthType.NTLM,
        mailbox=MAILBOX,
        ews_url="https://93.184.215.14/EWS/Exchange.asmx",  # a literal IP: no DNS lookup needed offline
        service_account_username="svc-fixture",
        service_account_password="pw-fixture",
    )

    session = build_ews_write_session(config, Deadline())
    try:
        assert isinstance(session.account.protocol.config.retry_policy, FailFast)
    finally:
        session.close()
        Protocol.clear_cache()


def test_send_as_denied_and_irresolvable_conflict_are_distinct_from_the_generic_access_denied_error():
    """CONFIRMED: both are their own ``ResponseMessageError``/``EWSError`` subclasses, not aliases of
    ``ErrorAccessDenied`` (which the mapper also handles, via a separate branch). If a future exchangelib
    version folded either of these into ``ErrorAccessDenied``, the mapper's dedicated ``isinstance`` checks
    for them in ``_map_ews_write_exception_raw`` would stop firing and both would silently fall through to
    the generic access-denied/rejected mapping instead -- merely importing the names would not catch that.
    """
    assert ErrorSendAsDenied is not ErrorIrresolvableConflict
    assert issubclass(ErrorSendAsDenied, EWSError) and issubclass(ErrorIrresolvableConflict, EWSError)
    assert not issubclass(ErrorSendAsDenied, ErrorAccessDenied)
    assert not issubclass(ErrorIrresolvableConflict, ErrorAccessDenied)


def test_type_call_bypasses_the_caching_protocol_metaclass():
    """CONFIRMED: type.__call__ returns a fresh, uncached Protocol instance per call."""

    class PrivateProtocol(Protocol):
        pass

    config = Configuration(service_endpoint="https://ews.example.test/EWS/Exchange.asmx")

    first = type.__call__(PrivateProtocol, config=config)
    second = type.__call__(PrivateProtocol, config=config)

    assert first is not second
    with pytest.raises(KeyError):
        CachingProtocol.__getitem__(PrivateProtocol, config)
    # The metaclass path does cache, which is exactly what the private protocol must avoid.
    assert PrivateProtocol(config=config) is PrivateProtocol(config=config)
    del PrivateProtocol[config]


def test_http_adapter_cls_is_honored_by_get_adapter():
    """CONFIRMED: get_adapter() instantiates the subclass's HTTP_ADAPTER_CLS."""

    class DeadlineAdapter(HTTPAdapter):
        pass

    class PrivateProtocol(Protocol):
        HTTP_ADAPTER_CLS = DeadlineAdapter

    assert isinstance(PrivateProtocol.get_adapter(), DeadlineAdapter)


def test_create_forward_with_no_subject_and_empty_body_serializes():
    """CONFIRMED: ``create_forward(None, "", ...)`` serializes; no "FW: " fallback is needed.

    ``set_xml_value`` omits the ``None`` subject, so the request carries no ``<t:Subject>`` and
    the server derives "FW: <original subject>". The empty body still produces an explicit
    ``<t:NewBodyContent BodyType="Text"></t:NewBodyContent>``. T6 can use the spec's preferred
    path and drop the "FW: " + original-subject fallback.
    """
    forward = _message_with_id(_account()).create_forward(None, "", [Mailbox(email_address="to@example.test")])
    assert isinstance(forward, ForwardItem)

    items = create_element("m:Items")
    set_xml_value(items, forward, version=_version())
    xml = xml_to_str(items)

    assert "ForwardItem" in xml
    assert "<t:Subject>" not in xml
    assert '<t:NewBodyContent BodyType="Text"></t:NewBodyContent>' in xml
    assert "to@example.test" in xml


def test_forward_item_exposes_an_author_field():
    """CONFIRMED: ForwardItem has an ``author`` field, so the From can be stamped before send."""
    assert "author" in {f.name for f in ForwardItem.FIELDS}

    forward = _message_with_id(_account()).create_forward(None, "", [Mailbox(email_address="to@example.test")])
    forward.author = Mailbox(email_address=MAILBOX)
    xml = xml_to_str(set_xml_value(create_element("m:Items"), forward, version=_version()))

    assert MAILBOX in xml


def test_forward_send_passes_copy_to_folder_through_to_create_item(monkeypatch):
    """CONFIRMED: ForwardItem.send(copy_to_folder=...) reaches CreateItem as ``folder``."""
    captured = {}

    def fake_get(self, items, folder, message_disposition, send_meeting_invitations):
        captured.update(
            folder=folder, message_disposition=message_disposition, send_meeting_invitations=send_meeting_invitations
        )
        return None

    monkeypatch.setattr(CreateItem, "get", fake_get)
    forward = _message_with_id(_account()).create_forward(None, "", [Mailbox(email_address="to@example.test")])
    sent_folder = _sent_folder()

    forward.send(copy_to_folder=sent_folder)

    assert captured["folder"] is sent_folder
    assert captured["message_disposition"] == SEND_AND_SAVE_COPY
    assert captured["send_meeting_invitations"] == SEND_TO_NONE


def test_create_item_payload_emits_saved_item_folder_id_for_the_copy_folder():
    """CONFIRMED: the forward payload carries m:SavedItemFolderId for copy_to_folder."""
    account = _account()
    forward = _message_with_id(account).create_forward(None, "", [Mailbox(email_address="to@example.test")])

    payload = CreateItem(account=account).get_payload(
        items=[forward],
        folder=_sent_folder(),
        message_disposition=SEND_AND_SAVE_COPY,
        send_meeting_invitations=SEND_TO_NONE,
    )
    xml = xml_to_str(payload)

    assert "SavedItemFolderId" in xml
    assert 'MessageDisposition="SendAndSaveCopy"' in xml


def test_send_item_payload_emits_saved_item_folder_id_for_the_copy_folder():
    """CONFIRMED: the plain-send payload carries m:SavedItemFolderId for copy_to_folder."""
    account = _account()
    message = _message_with_id(account)

    payload = SendItem(account=account).get_payload(items=[message], saved_item_folder=_sent_folder())
    xml = xml_to_str(payload)

    assert "SavedItemFolderId" in xml
