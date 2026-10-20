# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Provider adapter identity calls - regression guard for the Bearer auth header.

A redaction artifact once left ``Authorization: ******`` here, which 401'd every
identity call and left Atlassian ``cloud_id`` empty. These tests pin the header.
"""

import httpx
import pytest

from codemie.service.oauth import provider_adapters
from codemie.service.oauth.flow_engine import CallbackContext, OAuthCallbackError, OAuthTokenPayload
from codemie.service.oauth.provider_adapters import (
    GitLabOAuthProviderAdapter,
    JiraOAuthProviderAdapter,
)


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _RecordingClient:
    """Fake httpx.Client that records request headers and answers by URL."""

    last_headers = None

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, headers=None):
        _RecordingClient.last_headers = headers
        if "accessible-resources" in url:
            # Includes `scopes` so _select_site can positively confirm the Jira product; site
            # selection now fails closed without per-site scope evidence (CR-010).
            return _Resp(
                [
                    {
                        "id": "cid1",
                        "url": "https://acme.atlassian.net",
                        "name": "Acme",
                        "scopes": ["read:jira-work"],
                    }
                ]
            )
        if url.endswith("/me"):
            return _Resp({"name": "Jane", "account_id": "acc-9", "email": "jane@acme.io"})
        # GitLab /user
        return _Resp({"username": "jane", "id": 7, "email": "jane@acme.io"})


def test_atlassian_fetch_cloud_sends_bearer_token(monkeypatch):
    monkeypatch.setattr(provider_adapters.httpx, "Client", _RecordingClient)
    cloud_id, site_url, site_name = provider_adapters._AtlassianOAuthProviderAdapterBase._fetch_cloud("AT123", "jira")
    assert cloud_id == "cid1"
    assert site_url == "https://acme.atlassian.net"
    assert _RecordingClient.last_headers["Authorization"] == "Bearer AT123"


def test_gitlab_fetch_user_info_sends_bearer_token(monkeypatch):
    monkeypatch.setattr(provider_adapters.httpx, "Client", _RecordingClient)
    username, gitlab_id, email = GitLabOAuthProviderAdapter._fetch_user_info("https://gitlab.com", "GLAT")
    assert username == "jane"
    assert gitlab_id == "7"
    assert _RecordingClient.last_headers["Authorization"] == "Bearer GLAT"


def test_atlassian_finalize_populates_cloud_id(monkeypatch):
    monkeypatch.setattr(provider_adapters.httpx, "Client", _RecordingClient)
    adapter = JiraOAuthProviderAdapter()
    payload = OAuthTokenPayload(access_token="AT", refresh_token="RT", expires_in=3600, scopes="read:jira-work")
    token_data, result_kwargs = adapter.finalize_callback_payload(
        CallbackContext(token_payload=payload, state_data={}, provider="jira")
    )
    # cloud_id must be captured for the tool runtime to reach the Atlassian gateway.
    assert token_data["cloud_id"] == "cid1"
    assert result_kwargs["cloud_id"] == "cid1"
    assert token_data["access_token"] == "AT"


class _FailingClient(_RecordingClient):
    """Fake httpx.Client whose accessible-resources answer is unusable."""

    resources = []
    raise_on_get = None

    def get(self, url, headers=None):
        if "accessible-resources" in url:
            if _FailingClient.raise_on_get is not None:
                raise _FailingClient.raise_on_get
            return _Resp(_FailingClient.resources)
        return super().get(url, headers=headers)


@pytest.mark.parametrize(
    ("resources", "error"),
    [
        ([], None),
        ([{"url": "https://acme.atlassian.net"}], None),
        ("not-a-list", None),
        (None, httpx.HTTPError("boom")),
    ],
    ids=["no_sites", "entry_without_cloud_id", "malformed_payload", "http_error"],
)
def test_atlassian_finalize_fails_when_no_usable_site_is_returned(monkeypatch, resources, error):
    """Without a cloud_id the stored token cannot reach the Atlassian gateway, so it is not a success."""
    _FailingClient.resources = resources
    _FailingClient.raise_on_get = error
    monkeypatch.setattr(provider_adapters.httpx, "Client", _FailingClient)
    adapter = JiraOAuthProviderAdapter()
    payload = OAuthTokenPayload(access_token="AT", refresh_token="RT", expires_in=3600, scopes="read:jira-work")

    with pytest.raises(OAuthCallbackError):
        adapter.finalize_callback_payload(CallbackContext(token_payload=payload, state_data={}, provider="jira"))


def test_atlassian_revoke_makes_no_provider_call_and_records_the_disconnect(monkeypatch):
    """Atlassian exposes no revocation endpoint, so disconnect must not call out -- but must be traceable."""
    monkeypatch.setattr(
        provider_adapters.httpx,
        "Client",
        lambda *a, **k: pytest.fail("Atlassian disconnect must not call the provider"),
    )
    logged: list[str] = []
    monkeypatch.setattr(provider_adapters.logger, "info", logged.append)

    JiraOAuthProviderAdapter.revoke_token(user_id="u1", integration_id="s1")

    # The grant outlives the vault record here, so the identifiers must reach the log for auditing.
    assert len(logged) == 1
    assert "u1" in logged[0] and "s1" in logged[0]


# --- EPMCDME-13527: build_state_data must emit only non-secret flow context ---

from codemie.service.oauth.provider_adapters import _AtlassianOAuthProviderAdapterBase  # noqa: E402


def test_gitlab_build_state_data_has_no_secrets():
    ctx = GitLabOAuthProviderAdapter.build_state_data(
        user_id="u",
        integration_id="s1",
        provider="gitlab",
        redirect_uri="https://cb",
        client_id="cid",
        client_secret="sec",
        code_verifier="v",
        context={"instance_url": "https://gitlab.com"},
    )
    assert "client_id" not in ctx and "client_secret" not in ctx
    assert "code_verifier" not in ctx
    assert ctx["provider"] == "gitlab"
    assert ctx["instance_url"] == "https://gitlab.com"


def test_atlassian_build_state_data_has_no_secrets():
    ctx = _AtlassianOAuthProviderAdapterBase.build_state_data(
        user_id="u",
        integration_id="s1",
        provider="jira",
        redirect_uri="https://cb",
        client_id="cid",
        client_secret="sec",
        code_verifier="v",
        context={},
    )
    assert "client_id" not in ctx and "client_secret" not in ctx
    assert "code_verifier" not in ctx
    assert ctx["provider"] == "jira"


# --- EPMCDME-13527 / CR-001: Atlassian site selection must not pick arbitrarily ---

from codemie.service.oauth.provider_adapters import _AtlassianOAuthProviderAdapterBase as _Atlassian  # noqa: E402


def test_select_site_single_no_scopes_is_rejected():
    # CR-010: without per-site scope evidence confirming the product we fail closed rather than
    # binding to a site we cannot verify — even when it is the only accessible one.
    with pytest.raises(OAuthCallbackError):
        _Atlassian._select_site([{"id": "c1", "url": "u", "name": "n"}], "jira")


def test_select_site_filters_by_product_scope():
    resources = [
        {"id": "c1", "scopes": ["read:confluence-content.all"]},
        {"id": "c2", "scopes": ["read:jira-work"]},
    ]
    assert _Atlassian._select_site(resources, "jira")["id"] == "c2"
    assert _Atlassian._select_site(resources, "confluence")["id"] == "c1"


def test_select_site_multiple_matching_is_ambiguous():
    resources = [
        {"id": "c1", "scopes": ["read:jira-work"]},
        {"id": "c2", "scopes": ["read:jira-user"]},
    ]
    with pytest.raises(OAuthCallbackError):
        _Atlassian._select_site(resources, "jira")


def test_select_site_when_no_site_grants_the_product():
    resources = [{"id": "c1", "scopes": ["read:confluence-content.all"]}]
    with pytest.raises(OAuthCallbackError):
        _Atlassian._select_site(resources, "jira")


def test_select_site_empty_raises():
    with pytest.raises(OAuthCallbackError):
        _Atlassian._select_site([], "jira")


def test_select_site_multiple_without_scopes_is_ambiguous():
    resources = [{"id": "c1"}, {"id": "c2"}]
    with pytest.raises(OAuthCallbackError):
        _Atlassian._select_site(resources, "jira")


# --- EPMCDME-13527 / CR-U05: token exchange reuses the enterprise OAuth2 exchange helper ---


class _TokenExchangeClient(_RecordingClient):
    """Fake httpx.Client that answers the token endpoint POST for the enterprise exchange helper."""

    last_post = None

    def post(self, url, *, data=None, headers=None, auth=None):
        _TokenExchangeClient.last_post = {"url": url, "data": data, "auth": auth}
        return _Resp({"access_token": "AT", "refresh_token": "RT", "expires_in": 3600, "scope": "api read_user"})


@pytest.mark.enterprise
def test_gitlab_exchange_maps_enterprise_token_data(monkeypatch):
    monkeypatch.setattr(provider_adapters.httpx, "Client", _TokenExchangeClient)
    payload = GitLabOAuthProviderAdapter.exchange_code_for_tokens(
        code="c",
        code_verifier="v",
        client_id="cid",
        client_secret="sec",
        redirect_uri="https://cb",
        state_data={"instance_url": "https://gitlab.com"},
    )
    assert payload.access_token == "AT"
    assert payload.refresh_token == "RT"
    assert 0 < payload.expires_in <= 3600
    assert "api" in payload.scopes
    # confidential client secret is sent in the POST body, and the RFC-8707 resource is omitted.
    assert _TokenExchangeClient.last_post["data"]["client_secret"] == "sec"
    assert "resource" not in _TokenExchangeClient.last_post["data"]


@pytest.mark.enterprise
def test_atlassian_exchange_maps_enterprise_token_data(monkeypatch):
    monkeypatch.setattr(provider_adapters.httpx, "Client", _TokenExchangeClient)
    payload = JiraOAuthProviderAdapter.exchange_code_for_tokens(
        code="c",
        code_verifier="v",
        client_id="cid",
        client_secret="sec",
        redirect_uri="https://cb",
        state_data={"provider": "jira"},
    )
    assert payload.access_token == "AT"
    assert payload.refresh_token == "RT"
    assert "resource" not in _TokenExchangeClient.last_post["data"]
