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

"""Tests for validate_datasource_type_for_scheduler in settings_request_validator."""

from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock, Mock, patch

from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings import CredentialValues, SettingRequest, SettingType
from codemie.service.settings.settings_request_validator import (
    DEPRECATED_CREDENTIAL_TYPES,
    UNSUPPORTED_SCHEDULER_DATASOURCE_TYPES,
    validate_credential_type_not_deprecated,
    validate_datasource_type_for_scheduler,
    validate_datasource_type_for_webhook,
    validate_git_request,
    validate_ms_teams_request,
    validate_timezone_value,
)
from codemie_tools.base.models import CredentialTypes


def _make_datasource(index_type: str, ds_id: str = "ds-123") -> Mock:
    ds = Mock()
    ds.index_type = index_type
    ds.id = ds_id
    return ds


@pytest.mark.parametrize(
    "index_type",
    [
        "knowledge_base_file",
    ],
)
def test_validate_datasource_type_for_scheduler_rejects_unsupported(index_type):
    """Unsupported datasource types must raise 422."""
    datasource = _make_datasource(index_type)

    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_datasource_type_for_scheduler(datasource)

    assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "does not support triggering by schedule" in exc_info.value.message
    assert index_type in exc_info.value.details


@pytest.mark.parametrize(
    "index_type",
    [
        "code",
        "summary",
        "chunk-summary",
        "knowledge_base_confluence",
        "knowledge_base_jira",
        "knowledge_base_xray",
        "knowledge_base_azure_devops_wiki",
        "knowledge_base_azure_devops_work_item",
        "llm_routing_google",
        # The trigger engine dispatches SharePoint reindexes and the datasource page has
        # always allowed scheduling them; only this validator used to reject them.
        "knowledge_base_sharepoint",
    ],
)
def test_validate_datasource_type_for_scheduler_accepts_supported(index_type):
    """Supported datasource types must not raise."""
    validate_datasource_type_for_scheduler(_make_datasource(index_type))  # should not raise


def test_unsupported_scheduler_datasource_types_is_frozenset():
    """Constant must be immutable (frozenset)."""
    assert isinstance(UNSUPPORTED_SCHEDULER_DATASOURCE_TYPES, frozenset)


@pytest.mark.parametrize(
    "index_type",
    [
        "knowledge_base_file",
    ],
)
def test_unsupported_scheduler_datasource_types_contains(index_type):
    """Each unsupported type must be present in the constant."""
    assert index_type in UNSUPPORTED_SCHEDULER_DATASOURCE_TYPES


def test_sharepoint_is_schedulable():
    """SharePoint must stay schedulable: the engine supports it and users depend on it."""
    assert "knowledge_base_sharepoint" not in UNSUPPORTED_SCHEDULER_DATASOURCE_TYPES


def test_validate_datasource_type_for_webhook_accepts_xwiki():
    """xWiki datasources must support webhook triggering (EPMCDME-14794)."""
    validate_datasource_type_for_webhook(_make_datasource("knowledge_base_xwiki"))  # should not raise


def _make_request_with_timezone(tz_value):
    cred = MagicMock()
    cred.key = "timezone"
    cred.value = tz_value
    req = MagicMock(spec=SettingRequest)
    req.credential_values = [cred]
    return req


def _make_request_without_timezone():
    req = MagicMock(spec=SettingRequest)
    req.credential_values = []
    return req


@pytest.mark.parametrize("tz", ["UTC", "Europe/Warsaw", "America/New_York"])
def test_validate_timezone_value_valid(tz):
    validate_timezone_value(_make_request_with_timezone(tz))


def test_validate_timezone_value_absent_is_allowed():
    validate_timezone_value(_make_request_without_timezone())


@pytest.mark.parametrize("tz", ["UTC+2", "Bad/Zone", "not_a_timezone", ""])
def test_validate_timezone_value_invalid(tz):
    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_timezone_value(_make_request_with_timezone(tz))
    assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ---------------------------------------------------------------------------
# EPMCDME-13171 — a scheduler setting must never be stored without is_enabled
# EPMCDME-14128 — and an omitted flag means "left disabled", not "enabled"
# ---------------------------------------------------------------------------


def _scheduler_request(creds):
    from codemie_tools.base.models import CredentialTypes

    return SettingRequest(
        project_name="proj",
        alias="my-schedule",
        credential_type=CredentialTypes.SCHEDULER,
        credential_values=creds,
    )


def test_normalize_is_enabled_defaults_missing_flag_to_false():
    """The form omits the key when the toggle is never touched — that means disabled."""
    from codemie.rest_api.models.settings import CredentialValues
    from codemie.service.settings.settings_request_validator import normalize_is_enabled

    request = _scheduler_request(
        [
            CredentialValues(key="resource_type", value="datasource"),
            CredentialValues(key="resource_id", value="ds-1"),
            CredentialValues(key="schedule", value="0 2 * * *"),
        ]
    )

    normalize_is_enabled(request)

    assert {c.key: c.value for c in request.credential_values}["is_enabled"] is False


def test_normalize_is_enabled_preserves_explicit_false():
    """Deliberately disabling a schedule must keep working."""
    from codemie.rest_api.models.settings import CredentialValues
    from codemie.service.settings.settings_request_validator import normalize_is_enabled

    request = _scheduler_request(
        [
            CredentialValues(key="resource_type", value="datasource"),
            CredentialValues(key="resource_id", value="ds-1"),
            CredentialValues(key="schedule", value="0 2 * * *"),
            CredentialValues(key="is_enabled", value=False),
        ]
    )

    normalize_is_enabled(request)

    values = [c for c in request.credential_values if c.key == "is_enabled"]
    assert len(values) == 1
    assert values[0].value is False


@pytest.fixture(autouse=True)
def _teams_bot_integration_enabled():
    """These tests exercise validate_ms_teams_request's own logic, not the customer-config
    feature gate — decouple them from the shared customer-config.yaml value so an unrelated
    change to that file (see CR-002) can't make them fail with a 403 instead of the code
    they actually intend to test. The one test that targets the gate itself overrides this."""
    from codemie.configs.customer_config import CustomerConfig

    original = CustomerConfig.is_feature_enabled

    def _is_feature_enabled(self, feature_name, *args, **kwargs):
        if feature_name == "teamsBotIntegration":
            return True
        return original(self, feature_name, *args, **kwargs)

    with patch.object(CustomerConfig, "is_feature_enabled", _is_feature_enabled):
        yield


def _ms_teams_request(credential_values, project_name="proj1"):
    return SettingRequest(
        project_name=project_name,
        alias="teams-integration",
        credential_type=CredentialTypes.MS_TEAMS,
        credential_values=credential_values,
    )


@patch("codemie.rest_api.models.settings.Settings.check_ms_teams_exist")
@patch("codemie.service.assistant.assistant_service.AssistantService.belongs_to_project")
def test_valid_request_passes(mock_belongs_to_project, mock_singleton):
    # Arrange
    mock_belongs_to_project.return_value = True
    mock_singleton.return_value = True
    request = _ms_teams_request([CredentialValues(key="assistant_ids", value=["a1", "a2"])])

    # Act / Assert — no exception
    validate_ms_teams_request(request, setting_type=SettingType.PROJECT)


@patch("codemie.service.settings.settings_request_validator.customer_config")
def test_rejects_when_teams_bot_feature_disabled(mock_customer_config):
    """The retired assistant_project_mapping router gated Teams on this flag; the
    replacement validator must enforce the same entitlement gate."""
    mock_customer_config.is_feature_enabled.return_value = False
    request = _ms_teams_request([CredentialValues(key="assistant_ids", value=["a1"])])

    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_ms_teams_request(request, setting_type=SettingType.PROJECT)

    assert exc_info.value.code == status.HTTP_403_FORBIDDEN
    mock_customer_config.is_feature_enabled.assert_called_once_with("teamsBotIntegration")


@patch("codemie.rest_api.models.settings.Settings.check_ms_teams_exist_for_user")
@patch("codemie.service.assistant.assistant_service.AssistantService.belongs_to_project")
def test_accepts_user_scope_with_project_name(mock_belongs_to_project, mock_singleton):
    mock_belongs_to_project.return_value = True
    mock_singleton.return_value = True
    request = _ms_teams_request([CredentialValues(key="assistant_ids", value=["a1"])])

    validate_ms_teams_request(request, setting_type=SettingType.USER, user_id="user-1")

    mock_belongs_to_project.assert_called_once_with("a1", "proj1")
    mock_singleton.assert_called_once_with("user-1", setting_id=None)


def test_rejects_user_scope_without_project_name():
    """USER-scope ms_teams requires project_name identically to PROJECT-scope."""
    request = _ms_teams_request([CredentialValues(key="assistant_ids", value=["a1"])], project_name=None)

    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_ms_teams_request(request, setting_type=SettingType.USER, user_id="user-1")
    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
    assert "project_name" in exc_info.value.message


@patch("codemie.service.assistant.assistant_service.AssistantService.belongs_to_project")
def test_rejects_assistant_not_in_project_user_scope(mock_belongs_to_project):
    mock_belongs_to_project.return_value = False
    request = _ms_teams_request([CredentialValues(key="assistant_ids", value=["not-mine"])])

    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_ms_teams_request(request, setting_type=SettingType.USER, user_id="user-1")
    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
    assert "not-mine" in exc_info.value.details


@patch("codemie.rest_api.models.settings.Settings.check_ms_teams_exist_for_user")
@patch("codemie.service.assistant.assistant_service.AssistantService.is_marketplace_assistant")
@patch("codemie.service.assistant.assistant_service.AssistantService.belongs_to_project")
def test_accepts_marketplace_assistant_outside_project_user_scope(
    mock_belongs_to_project, mock_is_marketplace, mock_singleton
):
    """Marketplace assistants (is_global=True) are allowed in assistant_ids regardless of
    their own project, mirroring how sub-assistant validation already treats them."""
    mock_belongs_to_project.return_value = False
    mock_is_marketplace.return_value = True
    mock_singleton.return_value = True
    request = _ms_teams_request([CredentialValues(key="assistant_ids", value=["marketplace-a1"])])

    validate_ms_teams_request(request, setting_type=SettingType.USER, user_id="user-1")

    mock_is_marketplace.assert_called_once_with("marketplace-a1")


@patch("codemie.rest_api.models.settings.Settings.check_ms_teams_exist_for_user")
@patch("codemie.service.assistant.assistant_service.AssistantService.is_marketplace_assistant")
@patch("codemie.service.assistant.assistant_service.AssistantService.belongs_to_project")
def test_rejects_assistant_not_in_project_and_not_marketplace(
    mock_belongs_to_project, mock_is_marketplace, mock_singleton
):
    mock_belongs_to_project.return_value = False
    mock_is_marketplace.return_value = False
    mock_singleton.return_value = True
    request = _ms_teams_request([CredentialValues(key="assistant_ids", value=["not-mine"])])

    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_ms_teams_request(request, setting_type=SettingType.USER, user_id="user-1")
    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
    assert "not-mine" in exc_info.value.details


@patch("codemie.rest_api.models.settings.Settings.check_ms_teams_exist_for_user")
@patch("codemie.service.assistant.assistant_service.AssistantService.belongs_to_project")
def test_rejects_duplicate_ms_teams_row_user_scope(mock_belongs_to_project, mock_singleton):
    mock_belongs_to_project.return_value = True
    mock_singleton.side_effect = ValueError("duplicate")
    request = _ms_teams_request([CredentialValues(key="assistant_ids", value=["a1"])])

    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_ms_teams_request(request, setting_type=SettingType.USER, user_id="user-1")
    assert exc_info.value.code == status.HTTP_409_CONFLICT


@patch("codemie.rest_api.models.settings.Settings.find_by_id")
def test_rejects_user_mismatch_on_update(mock_find_by_id):
    mock_find_by_id.return_value = SimpleNamespace(user_id="other-user", project_name="proj1")
    request = _ms_teams_request([CredentialValues(key="assistant_ids", value=["a1"])])

    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_ms_teams_request(request, setting_type=SettingType.USER, setting_id="setting-1", user_id="user-1")
    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST


@patch("codemie.rest_api.models.settings.Settings.find_by_id")
def test_rejects_project_mismatch_on_update_user_scope(mock_find_by_id):
    """USER-scope update must reject a project_name that doesn't match the existing setting's."""
    mock_find_by_id.return_value = SimpleNamespace(user_id="user-1", project_name="other-proj")
    request = _ms_teams_request([CredentialValues(key="assistant_ids", value=["a1"])], project_name="proj1")

    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_ms_teams_request(request, setting_type=SettingType.USER, setting_id="setting-1", user_id="user-1")
    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
    assert "Project mismatch" in exc_info.value.message


@patch("codemie.rest_api.models.settings.Settings.find_by_id")
def test_rejects_missing_setting_on_user_scope_update_with_404(mock_find_by_id):
    """A nonexistent USER-scope setting_id must raise a clean 404, not an unhandled KeyError."""
    mock_find_by_id.return_value = None
    request = _ms_teams_request([CredentialValues(key="assistant_ids", value=["a1"])])

    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_ms_teams_request(
            request, setting_type=SettingType.USER, setting_id="missing-setting", user_id="user-1"
        )
    assert exc_info.value.code == status.HTTP_404_NOT_FOUND
    assert "missing-setting" in exc_info.value.details


def test_rejects_missing_project_name():
    """A None project_name must fail fast with a clear error, not a confusing assistant_ids 400."""
    request = _ms_teams_request([CredentialValues(key="assistant_ids", value=["a1"])], project_name=None)

    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_ms_teams_request(request, setting_type=SettingType.PROJECT)
    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
    assert "project_name" in exc_info.value.message


def test_rejects_non_string_assistant_ids():
    """Non-string elements must be rejected before they reach set()/join() and crash with a 500."""
    request = _ms_teams_request([CredentialValues(key="assistant_ids", value=["a1", 123])])

    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_ms_teams_request(request, setting_type=SettingType.PROJECT)
    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST


def test_rejects_missing_assistant_ids():
    # Arrange
    request = _ms_teams_request([])

    # Act / Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_ms_teams_request(request, setting_type=SettingType.PROJECT)
    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST


@patch("codemie.service.assistant.assistant_service.AssistantService.belongs_to_project")
def test_rejects_assistant_not_in_project(mock_belongs_to_project):
    # Arrange
    mock_belongs_to_project.return_value = False
    request = _ms_teams_request([CredentialValues(key="assistant_ids", value=["bad-id"])])

    # Act / Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_ms_teams_request(request, setting_type=SettingType.PROJECT)
    assert "bad-id" in exc_info.value.details


@patch("codemie.rest_api.models.settings.Settings.check_ms_teams_exist")
@patch("codemie.service.assistant.assistant_service.AssistantService.belongs_to_project")
def test_rejects_duplicate_ms_teams_row(mock_belongs_to_project, mock_singleton):
    # Arrange
    mock_belongs_to_project.return_value = True
    mock_singleton.side_effect = ValueError("duplicate")
    request = _ms_teams_request([CredentialValues(key="assistant_ids", value=["a1"])])

    # Act / Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_ms_teams_request(request, setting_type=SettingType.PROJECT)
    assert exc_info.value.code == status.HTTP_409_CONFLICT


def test_rejects_empty_assistant_ids_list():
    """An empty assistant_ids list defeats the integration's purpose and must be rejected."""
    request = _ms_teams_request([CredentialValues(key="assistant_ids", value=[])])

    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_ms_teams_request(request, setting_type=SettingType.PROJECT)
    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST


def test_rejects_multiple_assistant_ids_entries():
    """A second credential_values entry keyed 'assistant_ids' must not be silently dropped."""
    request = _ms_teams_request(
        [
            CredentialValues(key="assistant_ids", value=["a1"]),
            CredentialValues(key="assistant_ids", value=["a2"]),
        ]
    )

    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_ms_teams_request(request, setting_type=SettingType.PROJECT)
    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST


@patch("codemie.service.assistant.assistant_service.AssistantService.belongs_to_project")
def test_rejects_duplicate_assistant_ids_within_list(mock_belongs_to_project):
    """The same assistant id repeated in the list must not be stored twice."""
    mock_belongs_to_project.return_value = True
    request = _ms_teams_request([CredentialValues(key="assistant_ids", value=["a1", "a1"])])

    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_ms_teams_request(request, setting_type=SettingType.PROJECT)
    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# EPMCDME-10913 — deprecated credential-type registry
# ---------------------------------------------------------------------------


def _make_request_with_credential_type(credential_type: CredentialTypes) -> SettingRequest:
    return SettingRequest(
        project_name="test_project",
        alias="test_alias",
        credential_type=credential_type,
        credential_values=[{"key": "api_key", "value": "x"}],
    )


def test_zephyr_squad_is_registered_as_deprecated():
    """ZephyrSquad must stay in the registry — that is what blocks create/update."""
    assert CredentialTypes.ZEPHYR_SQUAD in DEPRECATED_CREDENTIAL_TYPES


@pytest.mark.parametrize("credential_type", list(DEPRECATED_CREDENTIAL_TYPES))
def test_validate_credential_type_not_deprecated_rejects_registered_types(credential_type):
    """Every registered deprecated type must be rejected with 410 and a replacement hint."""
    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_credential_type_not_deprecated(_make_request_with_credential_type(credential_type))

    assert exc_info.value.code == status.HTTP_410_GONE
    assert exc_info.value.message == f"{credential_type.value} integration is deprecated"
    assert credential_type.value in exc_info.value.details
    assert exc_info.value.help == DEPRECATED_CREDENTIAL_TYPES[credential_type]


@pytest.mark.parametrize(
    "credential_type",
    [
        CredentialTypes.ZEPHYR_SCALE,
        CredentialTypes.GIT,
        CredentialTypes.JIRA,
        CredentialTypes.XRAY,
    ],
)
def test_validate_credential_type_not_deprecated_allows_active_types(credential_type):
    """Types absent from the registry must pass through untouched."""
    validate_credential_type_not_deprecated(_make_request_with_credential_type(credential_type))


def _git_request(credential_values):
    return SettingRequest(
        project_name="proj1",
        alias="git-integration",
        credential_type=CredentialTypes.GIT,
        credential_values=credential_values,
    )


def test_validate_git_request_accepts_folded_gitlab_oauth():
    """EPMCDME-14586/14587: GitLab OAuth folds into the base Git type carrying an
    auth_type=oauth marker; the Git validator must accept it (the OAuth app credentials
    are validated by the OAuth initiate/connect flow, mirroring Jira/Confluence OAuth)."""
    request = _git_request(
        [
            CredentialValues(key="auth_type", value="oauth"),
            CredentialValues(key="client_id", value="cid"),
            CredentialValues(key="client_secret", value="sec"),
            CredentialValues(key="instance_url", value="https://gitlab.com"),
        ]
    )

    # Act / Assert — no exception (previously raised "Invalid auth_type: 'oauth'")
    validate_git_request(request)


def test_validate_git_request_accepts_folded_gitlab_oauth_without_callback_base_url():
    """EPMCDME-14587: the CodeMie callback base URL is derived server-side from
    CALLBACK_API_BASE_URL and is no longer collected, so it must not be required at save time."""
    request = _git_request(
        [
            CredentialValues(key="auth_type", value="oauth"),
            CredentialValues(key="client_id", value="cid"),
            CredentialValues(key="client_secret", value="sec"),
        ]
    )

    validate_git_request(request)


@pytest.mark.parametrize("missing_key", ["client_id", "client_secret"])
def test_validate_git_request_rejects_folded_gitlab_oauth_missing_app_credential(missing_key):
    """EPMCDME-14586/14587: a folded GitLab OAuth Git integration must carry its OAuth app
    credentials (client_id, client_secret). Missing any required field is a save-time 422,
    mirroring the PAT / GitHub App validators."""
    credential_values = [
        CredentialValues(key="auth_type", value="oauth"),
        CredentialValues(key="client_id", value="cid"),
        CredentialValues(key="client_secret", value="sec"),
        CredentialValues(key="instance_url", value="https://gitlab.com"),
    ]
    credential_values = [cv for cv in credential_values if cv.key != missing_key]
    request = _git_request(credential_values)

    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_git_request(request)
    assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.parametrize("blank_key", ["client_id", "client_secret"])
def test_validate_git_request_rejects_folded_gitlab_oauth_blank_app_credential(blank_key):
    """An empty-string OAuth app credential is treated as missing (same as PAT/GitHub App)."""
    request = _git_request(
        [
            CredentialValues(key="auth_type", value="oauth"),
            CredentialValues(key="client_id", value="" if blank_key == "client_id" else "cid"),
            CredentialValues(key="client_secret", value="" if blank_key == "client_secret" else "sec"),
        ]
    )
    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_git_request(request)
    assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY


def test_validate_git_request_accepts_folded_gitlab_oauth_without_instance_url():
    """instance_url has a server-side default (gitlab.com), so it is optional at save time."""
    request = _git_request(
        [
            CredentialValues(key="auth_type", value="oauth"),
            CredentialValues(key="client_id", value="cid"),
            CredentialValues(key="client_secret", value="sec"),
        ]
    )
    validate_git_request(request)


def test_validate_git_request_still_accepts_pat():
    request = _git_request(
        [
            CredentialValues(key="auth_type", value="pat"),
            CredentialValues(key="token", value="glpat-xxx"),
        ]
    )
    validate_git_request(request)


def test_validate_git_request_rejects_unknown_auth_type():
    request = _git_request([CredentialValues(key="auth_type", value="bogus")])
    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_git_request(request)
    assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "Invalid auth_type" in exc_info.value.message
