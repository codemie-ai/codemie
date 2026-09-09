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

"""EPMCDME-14587: a Jira/Confluence OAuth integration is persisted under the base
`Jira`/`Confluence` credential type, carrying `auth_type=oauth` in credential_values, and must be
resolved as OAuth (not treated as a PAT/token integration)."""

from types import SimpleNamespace

from codemie.service.settings.base_settings import SearchFields  # noqa: E402
from codemie.service.settings.settings import SettingsService  # noqa: E402
from codemie_tools.base.models import CredentialTypes  # noqa: E402
from codemie_tools.core.project_management.jira.models import JiraConfig  # noqa: E402
from codemie_tools.core.vcs.github.models import GithubConfig  # noqa: E402
from codemie_tools.core.vcs.gitlab.models import GitlabConfig  # noqa: E402


def _folded_setting(credential_type, values):
    return SimpleNamespace(
        id="s1",
        alias="Team",
        credential_type=credential_type,
        credential_values=[SimpleNamespace(key=k, value=v) for k, v in values.items()],
        normalize_values=lambda: dict(values),
    )


def test_oauth_provider_folds_base_type_with_auth_type_oauth():
    jira = _folded_setting(CredentialTypes.JIRA, {"auth_type": "oauth"})
    conf = _folded_setting(CredentialTypes.CONFLUENCE, {"auth_type": "oauth"})
    gitlab = _folded_setting(CredentialTypes.GIT, {"auth_type": "oauth", "instance_url": "https://gitlab.com"})
    jira_pat = _folded_setting(CredentialTypes.JIRA, {"token": "x"})
    git_pat = _folded_setting(CredentialTypes.GIT, {"token": "x"})

    assert SettingsService._oauth_provider(jira) == "jira"
    assert SettingsService._oauth_provider(conf) == "confluence"
    assert SettingsService._oauth_provider(gitlab) == "gitlab"
    assert SettingsService._oauth_provider(jira_pat) is None
    # A plain Git (GitHub/GitLab PAT) setting is not OAuth.
    assert SettingsService._oauth_provider(git_pat) is None


def test_inject_oauth_config_values_marks_folded_jira_as_oauth():
    jira = _folded_setting(CredentialTypes.JIRA, {"auth_type": "oauth"})
    values = SettingsService._inject_oauth_config_values(jira, user_id="u1")
    assert values["auth_type"] == "oauth"
    assert values["integration_id"] == "s1"
    assert values["acting_user_id"] == "u1"
    assert values["cloud"] is True


def test_get_config_resolves_folded_jira_oauth(monkeypatch):
    setting = _folded_setting(CredentialTypes.JIRA, {"auth_type": "oauth"})
    monkeypatch.setattr(SettingsService, "retrieve_setting", classmethod(lambda cls, *a, **k: setting))
    cfg = SettingsService.get_config(JiraConfig, user_id="u1")
    assert isinstance(cfg, JiraConfig)
    assert cfg.auth_type == "oauth"
    assert cfg.integration_id == "s1"
    assert cfg.acting_user_id == "u1"


def test_pat_jira_is_not_treated_as_oauth():
    jira_pat = _folded_setting(CredentialTypes.JIRA, {"token": "x", "url": "https://jira"})
    values = SettingsService._inject_oauth_config_values(jira_pat, user_id="u1")
    assert "integration_id" not in values
    assert values.get("cloud") is not True


# --- B2: _lookup_setting resolves folded OAuth rows and disambiguates GitHub vs GitLab on Git ---


def _retrieve_stub(oauth_setting=None, pat_setting=None):
    """Fake retrieve_setting: return the oauth setting for an auth_type=oauth search, else the PAT one."""

    def _retrieve(cls, search_fields, assistant_id=None, setting_id=None):
        if search_fields.get(SearchFields.CREDENTIAL_VALUES_VALUE) == "oauth":
            return oauth_setting
        return pat_setting

    return classmethod(_retrieve)


def test_lookup_prefers_folded_oauth_for_jira(monkeypatch):
    oauth = _folded_setting(CredentialTypes.JIRA, {"auth_type": "oauth"})
    pat = _folded_setting(CredentialTypes.JIRA, {"token": "x"})
    monkeypatch.setattr(SettingsService, "retrieve_setting", _retrieve_stub(oauth_setting=oauth, pat_setting=pat))
    result = SettingsService._lookup_setting(JiraConfig, CredentialTypes.JIRA, "u1", "p", None, None, None)
    assert result is oauth


def test_lookup_prefers_folded_oauth_for_gitlab_on_git(monkeypatch):
    oauth = _folded_setting(CredentialTypes.GIT, {"auth_type": "oauth", "instance_url": "https://gitlab.com"})
    monkeypatch.setattr(SettingsService, "retrieve_setting", _retrieve_stub(oauth_setting=oauth, pat_setting=None))
    result = SettingsService._lookup_setting(GitlabConfig, CredentialTypes.GIT, "u1", "p", None, None, None)
    assert result is oauth


def test_github_never_resolves_a_gitlab_oauth_git_setting(monkeypatch):
    gitlab_oauth = _folded_setting(CredentialTypes.GIT, {"auth_type": "oauth", "instance_url": "https://gitlab.com"})
    # Simulate the GitLab-OAuth Git setting being returned for a plain Git search (shared type).
    monkeypatch.setattr(
        SettingsService,
        "retrieve_setting",
        classmethod(lambda cls, search_fields, assistant_id=None, setting_id=None: gitlab_oauth),
    )
    result = SettingsService._lookup_setting(GithubConfig, CredentialTypes.GIT, "u1", "p", None, None, None)
    assert result is None


def test_lookup_keeps_matching_provider_oauth_row_via_base_fallback(monkeypatch):
    """EPMCDME-14587: the shared-Git guard is provider-matching, not GitHub-hardcoded.

    A GitLab config keeps a GitLab-OAuth Git row even when it is reached through the base-type fallback
    (step 2) rather than the auth_type=oauth search (step 1), because the row's provider matches the one
    the config expects. Paired with test_github_never_resolves_a_gitlab_oauth_git_setting (the reject
    side), this locks the generic contract that replaced the `config_class is GithubConfig` special case.
    """
    gitlab_oauth = _folded_setting(CredentialTypes.GIT, {"auth_type": "oauth", "instance_url": "https://gitlab.com"})
    # Step 1 (auth_type=oauth search) finds nothing; step 2 (base search) returns the gitlab-oauth row.
    monkeypatch.setattr(
        SettingsService, "retrieve_setting", _retrieve_stub(oauth_setting=None, pat_setting=gitlab_oauth)
    )
    result = SettingsService._lookup_setting(GitlabConfig, CredentialTypes.GIT, "u1", "p", None, None, None)
    assert result is gitlab_oauth


def test_lookup_falls_back_to_pat_when_no_oauth(monkeypatch):
    pat = _folded_setting(CredentialTypes.JIRA, {"token": "x"})
    monkeypatch.setattr(SettingsService, "retrieve_setting", _retrieve_stub(oauth_setting=None, pat_setting=pat))
    result = SettingsService._lookup_setting(JiraConfig, CredentialTypes.JIRA, "u1", "p", None, None, None)
    assert result is pat


# --- B3: write-path helpers dispatch on the folded provider and preserve the auth_type marker ---


def _cv(key, value):
    return SimpleNamespace(key=key, value=value)


def _request(credential_type, values):
    return SimpleNamespace(
        credential_type=credential_type,
        credential_values=[_cv(k, v) for k, v in values],
    )


def test_keep_only_oauth_app_credentials_keeps_marker_and_gitlab_app_keys():
    req = _request(
        CredentialTypes.GIT,
        [
            ("auth_type", "oauth"),
            ("client_id", "cid"),
            ("client_secret", "sec"),
            ("callback_base_url", "https://host"),
            ("instance_url", "https://gitlab.com"),
            ("token", "should-be-dropped"),
        ],
    )
    SettingsService._keep_only_oauth_app_credentials(req)
    keys = {c.key for c in req.credential_values}
    assert keys == {"auth_type", "client_id", "client_secret", "callback_base_url", "instance_url"}


def test_keep_only_oauth_app_credentials_noop_for_pat():
    req = _request(CredentialTypes.GIT, [("token", "x"), ("url", "https://github.com/o/r")])
    SettingsService._keep_only_oauth_app_credentials(req)
    assert {c.key for c in req.credential_values} == {"token", "url"}


def test_oauth_settings_service_dispatches_by_provider():
    from codemie.service.gitlab_oauth.settings_service import GitLabOAuthSettingsService
    from codemie.service.jira_oauth.settings_service import JiraOAuthSettingsService
    from codemie.service.confluence_oauth.settings_service import ConfluenceOAuthSettingsService

    gitlab_req = _request(CredentialTypes.GIT, [("auth_type", "oauth")])
    jira_req = _request(CredentialTypes.JIRA, [("auth_type", "oauth")])
    conf_req = _request(CredentialTypes.CONFLUENCE, [("auth_type", "oauth")])
    pat_req = _request(CredentialTypes.JIRA, [("token", "x")])

    assert SettingsService._oauth_settings_service(gitlab_req) is GitLabOAuthSettingsService
    assert SettingsService._oauth_settings_service(jira_req) is JiraOAuthSettingsService
    assert SettingsService._oauth_settings_service(conf_req) is ConfluenceOAuthSettingsService
    assert SettingsService._oauth_settings_service(pat_req) is None
