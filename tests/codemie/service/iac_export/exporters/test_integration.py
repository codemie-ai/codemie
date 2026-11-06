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

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from codemie_tools.base.models import CredentialTypes

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings import CredentialValues, Settings, SettingType
from codemie.rest_api.security.user import User
from codemie.service.iac_export.document import to_canonical_bytes
from codemie.service.iac_export.exporters.integration import IntegrationExporter
from tests.codemie.service.iac_export.envelope.conformance import assert_conforms

GOLDEN = Path(__file__).parent / "integration_golden.json"
GET_BY_ID = "codemie.service.iac_export.exporters.integration.Settings.get_by_id"
SECRET = "sk-live-SuperSecret-9f8e7d6c5b4a"
OPENAPI = '{"openapi":"3.0.0","info":{"title":"t"}}'


def _setting(setting_type=SettingType.PROJECT, user_id="owner-1") -> Settings:
    return Settings(
        id="setting-uuid-1",
        user_id=user_id,
        created_by=None,
        project_name="demo",
        alias="My Jira",
        credential_type=CredentialTypes.JIRA,
        credential_values=[
            CredentialValues(key="url", value="https://x.example"),
            CredentialValues(key="token", value=SECRET),
            CredentialValues(key="openapi_spec", value=OPENAPI),
        ],
        setting_type=setting_type,
        is_global=False,
    )


def _user(user_id: str, is_admin: bool = False) -> User:
    user = User(id=user_id, username=user_id)
    user.is_admin = is_admin  # construction-time resolution depends on ENV; pin it
    return user


def _admin() -> User:
    return _user("admin-1", is_admin=True)


def test_project_setting_exports_env_refs_only_and_matches_golden():
    with patch(GET_BY_ID, return_value=_setting()):
        body = to_canonical_bytes(IntegrationExporter().export("setting-uuid-1", _admin()))
        again = to_canonical_bytes(IntegrationExporter().export("setting-uuid-1", _admin()))
    assert json.loads(body) == json.loads(GOLDEN.read_text())
    assert_conforms(json.loads(body))
    assert SECRET.encode() not in body
    assert b"https://x.example" not in body
    assert b"setting-uuid-1" not in body
    assert b"owner-1" not in body
    assert b"env:MY_JIRA_TOKEN" in body
    assert body == again


def test_openapi_spec_is_a_reference_like_every_other_credential():
    # The envelope has no inline value slot (every credential_values entry is a CredentialRef), so the OpenAPI
    # document is supplied through the environment on apply instead of being embedded.
    with patch(GET_BY_ID, return_value=_setting()):
        body = to_canonical_bytes(IntegrationExporter().export("s", _admin()))
    entries = {e["key"]: e["ref"] for e in json.loads(body)["spec"]["credential_values"]}
    assert entries["openapi_spec"] == "env:MY_JIRA_OPENAPI_SPEC"
    assert OPENAPI.encode() not in body


def test_project_setting_without_permission_gets_403():
    user = _user("u2")
    with patch(GET_BY_ID, return_value=_setting()):
        with pytest.raises(ExtendedHTTPException) as exc:
            IntegrationExporter().export("s", user)
    assert exc.value.code == 403


def test_user_setting_owner_can_export_and_other_gets_403():
    setting = _setting(SettingType.USER, user_id="u1")
    with patch(GET_BY_ID, return_value=setting):
        doc = IntegrationExporter().export("s", _user("u1"))
        assert doc.spec.setting_type == "user"
        with pytest.raises(ExtendedHTTPException) as exc:
            IntegrationExporter().export("s", _user("u2"))
    assert exc.value.code == 403


def test_missing_setting_gets_404():
    with patch(GET_BY_ID, side_effect=KeyError("nf")):
        with pytest.raises(ExtendedHTTPException) as exc:
            IntegrationExporter().export("x", _admin())
    assert exc.value.code == 404


def test_hidden_budget_integration_gets_403():
    with (
        patch(GET_BY_ID, return_value=_setting()),
        patch(
            "codemie.service.iac_export.exporters.integration.SettingsService.is_budget_integration_hidden_from",
            return_value=True,
        ),
    ):
        with pytest.raises(ExtendedHTTPException) as exc:
            IntegrationExporter().export("s", _admin())
    assert exc.value.code == 403


@pytest.mark.parametrize("alias", [None, "", "  "])
def test_empty_alias_is_an_export_error(alias):
    from codemie.service.iac_export.document import ExportError

    setting = _setting()
    setting.alias = alias
    with patch(GET_BY_ID, return_value=setting):
        with pytest.raises(ExportError, match="alias"):
            IntegrationExporter().export("s", _admin())


def test_missing_project_and_credential_type_are_export_errors_not_crashes():
    from codemie.service.iac_export.document import ExportError

    setting = _setting(SettingType.USER, user_id="u1")
    setting.project_name = None
    with patch(GET_BY_ID, return_value=setting):
        with pytest.raises(ExportError, match="project"):
            IntegrationExporter().export("s", _user("u1"))

    broken = _setting()
    broken.credential_type = None
    with patch(GET_BY_ID, return_value=broken):
        with pytest.raises(ExportError, match="credential type"):
            IntegrationExporter().export("s", _admin())
