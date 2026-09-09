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

"""Who may drive a per-user OAuth flow against a shared integration.

A PROJECT integration is usable by any member of the owning project — connecting is a member
action, not an administrative one — while a USER integration belongs to its owner alone.
"""

from types import SimpleNamespace

import pytest
from codemie_tools.base.models import CredentialTypes

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings import SettingType
from codemie.service.oauth.authorization import authorize_setting_access, load_authorized_setting
from codemie.service.oauth.folded_credentials import OAUTH_PROVIDER_GITLAB


def _oauth_values():
    # EPMCDME-14586/14587: OAuth settings are the base type marked by auth_type=oauth.
    return [SimpleNamespace(key="auth_type", value="oauth")]


def _user(uid="u1", projects=(), admin_projects=(), is_admin_or_maintainer=False):
    return SimpleNamespace(
        id=uid,
        username=uid,
        project_names=list(projects),
        admin_project_names=list(admin_projects),
        is_admin_or_maintainer=is_admin_or_maintainer,
    )


def _project_setting(project="acme", owner="owner", credential_type=CredentialTypes.GIT):
    return SimpleNamespace(
        id="s1",
        setting_type=SettingType.PROJECT,
        project_name=project,
        user_id=owner,
        credential_type=credential_type,
        credential_values=_oauth_values(),
    )


def _user_setting(owner="owner", credential_type=CredentialTypes.GIT):
    return SimpleNamespace(
        id="s1",
        setting_type=SettingType.USER,
        project_name=None,
        user_id=owner,
        credential_type=credential_type,
        credential_values=_oauth_values(),
    )


def test_project_member_may_connect():
    authorize_setting_access(_project_setting(), _user(projects=["acme"]))


def test_project_admin_may_connect():
    authorize_setting_access(_project_setting(), _user(admin_projects=["acme"]))


def test_non_member_is_refused():
    with pytest.raises(ExtendedHTTPException):
        authorize_setting_access(_project_setting(), _user(projects=["other"]))


def test_user_setting_owner_may_connect():
    authorize_setting_access(_user_setting(owner="u1"), _user("u1"))


def test_user_setting_is_refused_to_everyone_else():
    with pytest.raises(ExtendedHTTPException):
        authorize_setting_access(_user_setting(owner="someone-else"), _user("u1", projects=["acme"]))


def _load(monkeypatch, setting, user, expected_provider=OAUTH_PROVIDER_GITLAB):
    monkeypatch.setattr(
        "codemie.rest_api.models.settings.Settings.find_by_id",
        staticmethod(lambda sid: setting),
    )
    return load_authorized_setting("s1", user, expected_provider, "GitLab")


def test_load_returns_the_setting_for_an_authorized_caller(monkeypatch):
    setting = _project_setting()
    assert _load(monkeypatch, setting, _user(projects=["acme"])) is setting


def test_load_reports_a_missing_integration(monkeypatch):
    with pytest.raises(ExtendedHTTPException) as exc:
        _load(monkeypatch, None, _user(projects=["acme"]))
    assert exc.value.code == 404


def test_load_checks_access_before_looking_at_the_credential_type(monkeypatch):
    """A wrong-type 400 would confirm the integration exists to a caller with no access to it."""
    setting = _project_setting(credential_type=CredentialTypes.JIRA)
    with pytest.raises(ExtendedHTTPException) as exc:
        _load(monkeypatch, setting, _user(projects=["other"]))
    assert exc.value.code == 403


def test_load_rejects_an_integration_of_the_wrong_provider(monkeypatch):
    setting = _project_setting(credential_type=CredentialTypes.JIRA)
    with pytest.raises(ExtendedHTTPException) as exc:
        _load(monkeypatch, setting, _user(projects=["acme"]))
    assert exc.value.code == 400
