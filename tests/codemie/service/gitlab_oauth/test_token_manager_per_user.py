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

"""Per-user GitLab OAuth token manager behavior (TMS-backed)."""

from types import SimpleNamespace

import pytest

from codemie.core.exceptions import GitLabAuthRequiredException
from codemie.service.gitlab_oauth.token_manager import GitLabOAuthTokenManager
from codemie_tools.base.models import CredentialTypes


def _gitlab_setting():
    return SimpleNamespace(
        id="s1",
        credential_type=CredentialTypes.GIT,
        credential_values=[
            SimpleNamespace(key="auth_type", value="oauth"),
            SimpleNamespace(key="instance_url", value="https://gitlab.com"),
        ],
    )


def test_returns_valid_token_for_the_acting_user(monkeypatch):
    monkeypatch.setattr(
        "codemie.service.gitlab_oauth.token_manager.Settings.find_by_id",
        staticmethod(lambda sid: _gitlab_setting()),
    )
    monkeypatch.setattr(
        "codemie.service.gitlab_oauth.token_manager.ToolOAuthTokenPort.get_oauth2_token_or_none",
        staticmethod(
            lambda *, user_id, integration_id: (
                SimpleNamespace(access_token="A1") if (integration_id, user_id) == ("s1", "u1") else None
            )
        ),
    )
    mgr = GitLabOAuthTokenManager()
    assert mgr.get_valid_access_token("s1", "u1") == "A1"


def test_missing_row_raises_auth_required(monkeypatch):
    monkeypatch.setattr(
        "codemie.service.gitlab_oauth.token_manager.Settings.find_by_id",
        staticmethod(lambda sid: _gitlab_setting()),
    )
    monkeypatch.setattr(
        "codemie.service.gitlab_oauth.token_manager.ToolOAuthTokenPort.get_oauth2_token_or_none",
        staticmethod(lambda *, user_id, integration_id: None),
    )
    mgr = GitLabOAuthTokenManager()
    # Missing token rides the shared auth-required propagation (like Jira/Confluence) rather than
    # surfacing a generic 400, so the user gets a GitLab connect gate.
    with pytest.raises(GitLabAuthRequiredException):
        mgr.get_valid_access_token("s1", "u-absent")


def test_two_users_get_their_own_tokens(monkeypatch):
    monkeypatch.setattr(
        "codemie.service.gitlab_oauth.token_manager.Settings.find_by_id",
        staticmethod(lambda sid: _gitlab_setting()),
    )
    token_map = {
        ("s1", "u1"): SimpleNamespace(access_token="A1"),
        ("s1", "u2"): SimpleNamespace(access_token="A2"),
    }
    monkeypatch.setattr(
        "codemie.service.gitlab_oauth.token_manager.ToolOAuthTokenPort.get_oauth2_token_or_none",
        staticmethod(lambda *, user_id, integration_id: token_map.get((integration_id, user_id))),
    )
    mgr = GitLabOAuthTokenManager()
    assert mgr.get_valid_access_token("s1", "u1") == "A1"
    assert mgr.get_valid_access_token("s1", "u2") == "A2"
