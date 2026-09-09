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

"""get_config resolves a shared Jira OAuth integration to a per-user oauth config.

The per-user connect gate is applied later, once, after tool assembly (oauth_connect_gate)
rather than inside get_config, so get_config resolves a config regardless of whether the
acting user has connected yet.
"""

from types import SimpleNamespace

from codemie.service.settings.settings import SettingsService  # noqa: E402
from codemie_tools.base.models import CredentialTypes  # noqa: E402
from codemie_tools.core.project_management.jira.models import JiraConfig  # noqa: E402


def _jira_setting():
    return SimpleNamespace(
        id="s1",
        alias="Team Jira",
        credential_type=CredentialTypes.JIRA,
        credential_values=[SimpleNamespace(key="auth_type", value="oauth")],
        normalize_values=lambda: {"url": "", "auth_type": "oauth"},
    )


def test_resolves_oauth_config_when_no_token_row(monkeypatch):
    monkeypatch.setattr(SettingsService, "retrieve_setting", classmethod(lambda cls, *a, **k: _jira_setting()))
    cfg = SettingsService.get_config(JiraConfig, user_id="u1")
    assert isinstance(cfg, JiraConfig)
    assert cfg.auth_type == "oauth"
    assert cfg.acting_user_id == "u1"
    assert cfg.integration_id == "s1"
