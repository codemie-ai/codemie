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

"""Per-user Jira OAuth token manager behavior (TMS-backed)."""

from types import SimpleNamespace

import pytest

from codemie.core.exceptions import ExtendedHTTPException, JiraAuthRequiredException
from codemie.service.jira_oauth.token_manager import JiraOAuthTokenManager
from codemie_tools.base.models import CredentialTypes


def _jira_setting():
    return SimpleNamespace(
        id="s1",
        credential_type=CredentialTypes.JIRA,
        credential_values=[SimpleNamespace(key="auth_type", value="oauth")],
    )


def test_returns_valid_token_for_acting_user(monkeypatch):
    monkeypatch.setattr(
        "codemie.service.jira_oauth.token_manager.Settings.find_by_id",
        staticmethod(lambda sid: _jira_setting()),
    )
    monkeypatch.setattr(
        "codemie.service.jira_oauth.token_manager.ToolOAuthTokenPort.get_oauth2_token_or_none",
        staticmethod(
            lambda *, user_id, integration_id: (
                SimpleNamespace(access_token="A1", provider_metadata={"cloud_id": "CID-123"})
                if (integration_id, user_id) == ("s1", "u1")
                else None
            )
        ),
    )
    mgr = JiraOAuthTokenManager()
    assert mgr.get_valid_access_token("s1", "u1") == "A1"


def test_missing_row_raises_not_connected(monkeypatch):
    monkeypatch.setattr(
        "codemie.service.jira_oauth.token_manager.Settings.find_by_id",
        staticmethod(lambda sid: _jira_setting()),
    )
    monkeypatch.setattr(
        "codemie.service.jira_oauth.token_manager.ToolOAuthTokenPort.get_oauth2_token_or_none",
        staticmethod(lambda *, user_id, integration_id: None),
    )
    mgr = JiraOAuthTokenManager()
    with pytest.raises(JiraAuthRequiredException):
        mgr.get_valid_access_token("s1", "absent")


def test_get_cloud_id_reads_provider_metadata(monkeypatch):
    monkeypatch.setattr(
        "codemie.service.jira_oauth.token_manager.ToolOAuthTokenPort.get_oauth2_token_or_none",
        staticmethod(
            lambda *, user_id, integration_id: (
                SimpleNamespace(provider_metadata={"cloud_id": "CID-123"})
                if (integration_id, user_id) == ("s1", "u1")
                else None
            )
        ),
    )
    mgr = JiraOAuthTokenManager()
    assert mgr.get_cloud_id("s1", "u1") == "CID-123"
    assert mgr.get_cloud_id("s1", "absent") == ""


def _folded_jira_oauth_setting():
    # EPMCDME-14587: OAuth persisted under the base Jira type with an auth_type=oauth marker.
    return SimpleNamespace(
        id="s1",
        credential_type=CredentialTypes.JIRA,
        credential_values=[SimpleNamespace(key="auth_type", value="oauth")],
    )


def test_accepts_folded_jira_oauth_setting(monkeypatch):
    monkeypatch.setattr(
        "codemie.service.jira_oauth.token_manager.Settings.find_by_id",
        staticmethod(lambda sid: _folded_jira_oauth_setting()),
    )
    monkeypatch.setattr(
        "codemie.service.jira_oauth.token_manager.ToolOAuthTokenPort.get_oauth2_token_or_none",
        staticmethod(
            lambda *, user_id, integration_id: (
                SimpleNamespace(access_token="A1") if (integration_id, user_id) == ("s1", "u1") else None
            )
        ),
    )
    mgr = JiraOAuthTokenManager()
    assert mgr.get_valid_access_token("s1", "u1") == "A1"


def test_rejects_non_oauth_jira_setting(monkeypatch):
    pat = SimpleNamespace(id="s1", credential_type=CredentialTypes.JIRA, credential_values=[])
    monkeypatch.setattr(
        "codemie.service.jira_oauth.token_manager.Settings.find_by_id",
        staticmethod(lambda sid: pat),
    )
    mgr = JiraOAuthTokenManager()
    with pytest.raises(ExtendedHTTPException):
        mgr.get_valid_access_token("s1", "u1")
