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

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import status

from codemie.configs.customer_config import customer_config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings import CredentialValues, Settings, SettingRequest, SettingType
from codemie.service.assistant.assistant_service import AssistantService
from codemie.service.settings.scheduler_settings_service import (
    _validate_minimum_hourly_frequency,
    INVALID_CRON_EXPRESSION_MESSAGE,
    validate_timezone_string,
)
from codemie_tools.base.models import CredentialTypes
from apscheduler.triggers.cron import CronTrigger
from croniter import croniter

if TYPE_CHECKING:
    from codemie.rest_api.models.index import IndexInfo

CRON_EXPRESSION_HELP_MESSAGE = (
    "Please provide a valid cron expression that runs at most once per hour. "
    "Examples: '0 * * * *' (hourly), '0 9 * * MON-FRI' (weekdays at 9 AM), '0 0 * * *' (daily)."
)
LITELLM_API_KEY_HELP_MESSAGE = "Please provide a valid, non-empty API key for LiteLLM integration."
GIT_AUTH_HELP_MESSAGE = (
    "Please specify authentication method using 'auth_type' field: "
    "'pat' for Personal Access Token or 'github_app' for GitHub App authentication."
)

# SharePoint is intentionally absent: the trigger engine dispatches it (see
# Cron.__schedule_datasource_job and triggers.bindings.utils.validate_datasource), and the
# datasource page has been creating SharePoint schedules all along. Only this validator,
# used by the Integration path, still rejected them.
UNSUPPORTED_SCHEDULER_DATASOURCE_TYPES: frozenset[str] = frozenset(
    [
        "knowledge_base_file",  # user-visible name: "file" — no reindex path exists
    ]
)
UNSUPPORTED_SCHEDULER_DATASOURCE_TYPES_HELP_MESSAGE = (
    "The following datasource types do not support triggering by schedule: file. "
    "Please select a datasource of a supported type "
    "(e.g., git/code, summary, chunk-summary, confluence, jira, sharepoint)."
)

UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES: frozenset[str] = frozenset(
    [
        "knowledge_base_file",  # user-visible name: "file"
        "knowledge_base_sharepoint",  # user-visible name: "sharepoint"
        "knowledge_base_xray",  # user-visible name: "xray"
        "knowledge_base_azure_devops_wiki",  # user-visible name: "azure devops wiki"
        "knowledge_base_azure_devops_work_item",  # user-visible name: "azure devops work item"
        "knowledge_base_xwiki",  # user-visible name: "xwiki"
        "platform_marketplace_assistant",  # internal platform type
    ]
)
UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES_HELP_MESSAGE = (
    "The following datasource types do not support triggering by webhook: "
    "file, sharepoint, xray, azure devops wiki, azure devops work item, xwiki. "
    "Please select a datasource of a supported type (e.g., git/code, summary, chunk-summary, confluence, jira, google)."
)


DEPRECATED_CREDENTIAL_TYPES: dict[CredentialTypes, str] = {
    CredentialTypes.ZEPHYR_SQUAD: (
        "Use an alternative test-management integration (e.g. Zephyr Scale) for new setups."
    ),
}
"""Credential types that can no longer be created or updated.

Maps the deprecated credential type to the ``help`` hint pointing users at a replacement.
Existing settings of these types stay readable and usable; only create/update is blocked.
To deprecate another integration, add one entry here — no router changes needed.
"""


def validate_credential_type_not_deprecated(request: SettingRequest) -> None:
    """
    Reject create/update of a setting whose credential type is deprecated.

    Raises:
        ExtendedHTTPException: 410 GONE if the credential type is in DEPRECATED_CREDENTIAL_TYPES
    """
    help_message = DEPRECATED_CREDENTIAL_TYPES.get(request.credential_type)
    if help_message is None:
        return

    credential_type_name = request.credential_type.value
    raise ExtendedHTTPException(
        code=status.HTTP_410_GONE,
        message=f"{credential_type_name} integration is deprecated",
        details=(
            f"New {credential_type_name} settings can no longer be created or updated. "
            "Existing configurations remain read-only."
        ),
        help=help_message,
    )


def _validate_pat_authentication(values: dict):
    """Validate PAT authentication fields."""
    if "token" not in values or not values["token"]:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="PAT authentication requires 'token'",
            details="Personal Access Token authentication requires a valid token.",
            help=(
                "Please provide a GitHub Personal Access Token. "
                "You can generate one at: https://github.com/settings/tokens"
            ),
        )

    # Ensure GitHub App fields are not provided
    if "app_id" in values or "private_key" in values:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Cannot mix authentication methods",
            details="Cannot provide GitHub App fields (app_id, private_key) when using PAT authentication",
            help="Please choose either PAT or GitHub App authentication, not both.",
        )


def _validate_github_app_required_fields(values: dict):
    """Validate required GitHub App fields."""
    if "app_id" not in values or not values["app_id"]:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="GitHub App authentication requires 'app_id'",
            details="GitHub App authentication requires a valid App ID.",
            help=("Find your App ID in GitHub App settings: Settings → Developer settings → GitHub Apps → Your App"),
        )
    if "private_key" not in values or not values["private_key"]:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="GitHub App authentication requires 'private_key'",
            details="GitHub App authentication requires a private key.",
            help=(
                "Generate and download a private key from GitHub App settings: "
                "Settings → Developer settings → GitHub Apps → Your App → Generate private key"
            ),
        )


def _validate_github_app_field_formats(values: dict):
    """Validate GitHub App field formats."""
    # Validate private key format
    private_key = values["private_key"]
    if not private_key.startswith("-----BEGIN"):
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Invalid private key format",
            details="Private key must be in PEM format (should start with '-----BEGIN').",
            help=("Please provide the private key exactly as downloaded from GitHub (including BEGIN/END markers)."),
        )

    # Validate app_id is numeric
    try:
        int(values["app_id"])
    except (ValueError, TypeError):
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Invalid app_id format",
            details="app_id must be a numeric value.",
            help="Find your numeric App ID in GitHub App settings.",
        )

    # Validate installation_id if provided (optional)
    if "installation_id" in values and values["installation_id"]:
        try:
            int(values["installation_id"])
        except (ValueError, TypeError):
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="Invalid installation_id format",
                details="installation_id must be a numeric value.",
                help=("Find your installation ID in the GitHub App installation URL, or leave empty to auto-detect."),
            )


def _validate_github_app_authentication(values: dict):
    """Validate GitHub App authentication fields."""
    # Ensure PAT token is not provided
    if "token" in values and values["token"]:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Cannot mix authentication methods",
            details="Cannot provide PAT token when using GitHub App authentication",
            help="Please choose either PAT or GitHub App authentication, not both.",
        )

    _validate_github_app_required_fields(values)
    _validate_github_app_field_formats(values)


def validate_git_request(request: SettingRequest):
    """
    Validate Git credentials (PAT or GitHub App) using auth_type field.

    Raises:
        ExtendedHTTPException: If validation fails
    """
    values = {cv.key: cv.value for cv in request.credential_values}

    # Get auth_type (defaults to "pat" for backward compatibility)
    auth_type = values.get("auth_type", "pat")

    if auth_type == "pat":
        _validate_pat_authentication(values)
    elif auth_type == "github_app":
        _validate_github_app_authentication(values)
    else:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=f"Invalid auth_type: '{auth_type}'",
            details="auth_type must be either 'pat' or 'github_app'.",
            help=GIT_AUTH_HELP_MESSAGE,
        )


def validate_scheduler_request(request: SettingRequest) -> None:
    """
    Validate the incoming scheduler setting request
    """
    # Extract and validate resource values
    resource_type = validate_resource_type(request)
    resource_id = validate_resource_id(request)

    # Validate that the resource belongs to the project; returns datasource for type checking
    datasource = validate_resource_ownership(resource_type, resource_id, request.project_name)

    # Validate datasource type is supported for scheduler triggering
    if resource_type == "datasource" and datasource is not None:
        validate_datasource_type_for_scheduler(datasource)

    # Extract and validate cron expression (schedule)
    validate_cron_expression(request)

    # Validate timezone (optional credential)
    validate_timezone_value(request)

    # Store an explicit is_enabled so the trigger engine never has to guess
    normalize_is_enabled(request)


def normalize_is_enabled(request: SettingRequest) -> None:
    """Default `is_enabled` to false when the client omits it.

    The Integration form drops the key entirely when the user never touches the toggle, so
    an omitted flag means "left disabled" — that is also how the integration list renders
    it. Defaulting to true here enabled schedules the user never enabled (EPMCDME-14128).
    The flag is still written explicitly so the trigger engine never has to guess.
    """
    if not any(cred.key == "is_enabled" for cred in request.credential_values):
        request.credential_values.append(CredentialValues(key="is_enabled", value=False))


def validate_datasource_type_for_scheduler(datasource: IndexInfo) -> None:
    """
    Validate that a datasource type supports triggering by schedule.

    Args:
        datasource: The already-fetched IndexInfo object to validate

    Raises:
        ExtendedHTTPException: If the datasource type does not support scheduler triggering
    """
    if datasource.index_type in UNSUPPORTED_SCHEDULER_DATASOURCE_TYPES:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Datasource type does not support triggering by schedule",
            details=(
                f"The datasource with ID '{datasource.id}' has type '{datasource.index_type}', "
                "which does not support schedule-based triggering."
            ),
            help=UNSUPPORTED_SCHEDULER_DATASOURCE_TYPES_HELP_MESSAGE,
        )


def validate_webhook_request(request: SettingRequest) -> None:
    """
    Validate the incoming webhook setting request.
    """
    resource_type = validate_resource_type(request)
    resource_id = validate_resource_id(request)

    datasource = validate_resource_ownership(resource_type, resource_id, request.project_name)

    if resource_type == "datasource" and datasource is not None:
        validate_datasource_type_for_webhook(datasource)


TEAMS_BOT_INTEGRATION_FEATURE = "teamsBotIntegration"


def validate_ms_teams_request(
    request: SettingRequest, setting_type: SettingType, setting_id: str | None = None
) -> None:
    """
    Validate an ms_teams project-integration request: PROJECT-scope only, every
    assistant_ids entry must belong to the target project, and at most one
    ms_teams row may exist per project.
    """
    if not customer_config.is_feature_enabled(TEAMS_BOT_INTEGRATION_FEATURE):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Feature not available",
            details="Teams Bot Integration is not enabled for this customer.",
            help="Contact your system administrator to enable the 'features:teamsBotIntegration' component.",
        )

    if setting_type != SettingType.PROJECT:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="ms_teams integrations are project-scoped only",
            details="ms_teams integrations cannot be created or updated at USER scope.",
            help="Submit this request with setting_type=PROJECT.",
        )

    if not request.project_name:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="project_name is required",
            details="ms_teams integrations require a non-empty project_name.",
            help="Provide the target project_name in the request.",
        )

    if setting_id is not None:
        existing_setting = Settings.get_by_id(setting_id)
        if existing_setting.project_name != request.project_name:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Project mismatch",
                details=f"Setting {setting_id!r} belongs to project {existing_setting.project_name!r}, "
                f"not {request.project_name!r}.",
                help="The project_name in the request body must match the setting being updated.",
            )

    assistant_ids_creds = [cv for cv in request.credential_values if cv.key == "assistant_ids"]
    if (
        len(assistant_ids_creds) != 1
        or not isinstance(assistant_ids_creds[0].value, list)
        or not assistant_ids_creds[0].value
    ):
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="assistant_ids is required",
            details="ms_teams integrations require exactly one credential_values entry "
            "with key='assistant_ids' and a non-empty list value.",
            help="Provide a single CredentialValues(key='assistant_ids', value=[<assistant_id>, ...]).",
        )

    assistant_ids = assistant_ids_creds[0].value
    if not all(isinstance(assistant_id, str) for assistant_id in assistant_ids):
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid assistant_ids",
            details="Every entry in assistant_ids must be a string assistant id.",
            help="Provide assistant_ids as a list of string assistant id values.",
        )

    if len(assistant_ids) != len(set(assistant_ids)):
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Duplicate assistant_ids",
            details="assistant_ids must not contain duplicate assistant id values.",
            help="Remove duplicate entries from assistant_ids.",
        )

    invalid_ids = [
        assistant_id
        for assistant_id in assistant_ids
        if not AssistantService.belongs_to_project(assistant_id, request.project_name)
    ]
    if invalid_ids:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid assistant_ids",
            details=f"The following assistant id(s) do not exist or do not belong to "
            f"project {request.project_name!r}: {', '.join(invalid_ids)}.",
            help="Only assistants belonging to the target project may be listed in assistant_ids.",
        )

    try:
        Settings.check_ms_teams_exist(request.project_name, setting_id=setting_id)
    except ValueError as e:
        raise ExtendedHTTPException(
            code=status.HTTP_409_CONFLICT,
            message="ms_teams integration already exists",
            details=str(e),
            help="Update or delete the existing ms_teams integration for this project instead of creating a new one.",
        ) from e


def validate_datasource_type_for_webhook(datasource: IndexInfo) -> None:
    """
    Validate that a datasource type supports triggering by webhook.

    Args:
        datasource: The already-fetched IndexInfo object to validate

    Raises:
        ExtendedHTTPException: If the datasource type does not support webhook triggering
    """
    if datasource.index_type in UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Datasource type does not support triggering by webhook",
            details=(
                f"The datasource with ID '{datasource.id}' has type '{datasource.index_type}', "
                "which does not support webhook-based triggering."
            ),
            help=UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES_HELP_MESSAGE,
        )


def validate_resource_type(request: SettingRequest) -> str:
    """
    Validate that the provided resource type is valid.

    Args:
        request: The setting request
    Raises:
        ExtendedHTTPException: If the resource type is invalid
    Returns: resource_type string
    """
    resource_type_cred = list(filter(lambda cred: cred.key == "resource_type", request.credential_values))
    if resource_type_cred:
        return resource_type_cred[0].value
    else:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Resource type is missing.",
            details="Please select a resource from the dropdown.",
            help="Please provide a valid resource type (e.g., 'datasource', 'workflow', 'assistant').",
        )


def validate_litellm_request(request: SettingRequest):
    """
    Validate the incoming LiteLLM setting request to ensure API key is not empty
    """
    # Extract and validate API key
    validate_litellm_api_key(request)


def validate_litellm_api_key(request: SettingRequest) -> str:
    """
    Validate that the provided LiteLLM API key is not empty.

    Args:
        request: The setting request
    Raises:
        ExtendedHTTPException: If the API key is empty or missing
    Returns: api_key string
    """
    api_key_cred = list(filter(lambda cred: cred.key == "api_key", request.credential_values))
    if api_key_cred:
        api_key = api_key_cred[0].value
        if not api_key or (isinstance(api_key, str) and api_key.strip() == ""):
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="LiteLLM API key cannot be empty",
                details="The API key field is required and cannot be empty or contain only whitespace.",
                help=LITELLM_API_KEY_HELP_MESSAGE,
            )
        return api_key
    else:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="LiteLLM API key is missing.",
            details="Please provide an API key for the LiteLLM integration.",
            help=LITELLM_API_KEY_HELP_MESSAGE,
        )


def validate_resource_id(request: SettingRequest) -> str:
    """
    Validate that the provided resource ID exists.

    Args:
        request: The setting request
    Raises:
        ExtendedHTTPException: If the resource is empty
    Returns: resource_id
    """
    resource_id_cred = list(filter(lambda cred: cred.key == "resource_id", request.credential_values))
    if not resource_id_cred or not resource_id_cred[0].value:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Cannot process the resource ID",
            details="An error occurred while trying to validate the resource ID: The resource ID is missing.",
            help="Please provide a valid resource ID to proceed.",
        )
    return resource_id_cred[0].value


def validate_resource_ownership(resource_type: str, resource_id: str, project_name: str) -> IndexInfo | None:
    """
    Validate that the resource belongs to the specified project.

    Args:
        resource_type: The type of resource (datasource, workflow, assistant)
        resource_id: The ID of the resource
        project_name: The name of the project

    Returns:
        IndexInfo if resource_type is "datasource", None otherwise

    Raises:
        ExtendedHTTPException: If the resource doesn't belong to the project or validation fails
    """
    if resource_type == "datasource":
        return validate_datasource_ownership(resource_id, project_name)
    elif resource_type == "workflow":
        validate_workflow_ownership(resource_id, project_name)
    elif resource_type == "assistant":
        validate_assistant_ownership(resource_id, project_name)
    else:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Invalid resource type",
            details=f"The resource type '{resource_type}' is not recognized.",
            help="Please provide a valid resource type (e.g., 'datasource', 'workflow', 'assistant').",
        )
    return None


def raise_validation_error(e: Exception) -> None:
    """Helper function to raise a standardized validation error."""
    raise ExtendedHTTPException(
        code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        message="Cannot validate resource",
        details=f"An error occurred while validating the resource: {str(e)}",
        help="Please check the resource ID and try again.",
    ) from e


def validate_datasource_ownership(resource_id: str, project_name: str) -> IndexInfo:
    """Validate that a datasource belongs to the specified project. Returns the datasource."""
    try:
        from codemie.rest_api.models.index import IndexInfo

        datasource = IndexInfo.find_by_id(resource_id)
        if not datasource or datasource.project_name != project_name:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="Datasource not found",
                details=f"The datasource with ID '{resource_id}' could not be found in '{project_name}' project.",
                help="Please ensure the datasource belongs to the specified project.",
            )
        return datasource
    except ExtendedHTTPException:
        raise
    except Exception as e:
        raise_validation_error(e)


def validate_workflow_ownership(resource_id: str, project_name: str) -> None:
    """Validate that a workflow belongs to the specified project."""
    try:
        from codemie.service.workflow_service import WorkflowService

        if not WorkflowService.belongs_to_project(resource_id, project_name):
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="Workflow not found",
                details=f"The workflow with ID '{resource_id}' could not be found in '{project_name}' project.",
                help="Please ensure the workflow belongs to the specified project.",
            )
    except ExtendedHTTPException:
        raise
    except Exception as e:
        raise_validation_error(e)


def validate_assistant_ownership(resource_id: str, project_name: str) -> None:
    """Validate that an assistant belongs to the specified project."""
    try:
        from codemie.service.assistant.assistant_service import AssistantService

        if not AssistantService.belongs_to_project(resource_id, project_name):
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="Assistant not found",
                details=f"The assistant with ID '{resource_id}' could not be found in '{project_name}' project.",
                help="Please ensure the assistant belongs to the specified project.",
            )
    except ExtendedHTTPException:
        raise
    except Exception as e:
        raise_validation_error(e)


def validate_cron_expression(request: SettingRequest) -> None:
    """
    Extract and validate the schedule value from the request.
    Validates that the schedule is a valid cron expression and runs at most once per hour.

    Args:
        request: The setting request containing credential values

    Raises:
        ExtendedHTTPException: If the schedule format is invalid, cron expression is malformed,
                               or runs more frequently than once per hour
    """
    schedule_cred = list(filter(lambda cred: cred.key == "schedule", request.credential_values))
    if schedule_cred:
        schedule = schedule_cred[0].value
        if schedule and isinstance(schedule, str):
            # Validate cron expression inline
            cron_expression = schedule.strip()
            if not cron_expression:
                raise ExtendedHTTPException(
                    code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    message=INVALID_CRON_EXPRESSION_MESSAGE,
                    details="Cron expression cannot be empty.",
                    help=CRON_EXPRESSION_HELP_MESSAGE,
                )

            try:
                # Test if the cron expression is valid by creating a croniter object
                cron = croniter(cron_expression)
            except (ValueError, KeyError) as e:
                raise ExtendedHTTPException(
                    code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    message=INVALID_CRON_EXPRESSION_MESSAGE,
                    details=f"The cron expression '{schedule}' is not valid: {str(e)}",
                    help=CRON_EXPRESSION_HELP_MESSAGE,
                ) from e

            # Validate against APScheduler's CronTrigger — catches constraints croniter
            # does not enforce (e.g. day_of_week > 6)
            try:
                minute, hour, day_of_month, month, day_of_week = cron_expression.split()
                CronTrigger(minute=minute, hour=hour, day=day_of_month, month=month, day_of_week=day_of_week)
            except ValueError as e:
                raise ExtendedHTTPException(
                    code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    message=INVALID_CRON_EXPRESSION_MESSAGE,
                    details=f"The cron expression '{schedule}' is not valid: {str(e)}",
                    help=CRON_EXPRESSION_HELP_MESSAGE,
                ) from e

            # Validate minimum hourly frequency
            _validate_minimum_hourly_frequency(cron_expression, cron)

        elif schedule is not None:  # Allow None/empty values but validate non-empty strings
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="Invalid schedule format",
                details="Schedule must be a valid cron expression string.",
                help=CRON_EXPRESSION_HELP_MESSAGE,
            )
    else:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Cron expression is missing.",
            details="Please provide a cron expression for the scheduler.",
            help=CRON_EXPRESSION_HELP_MESSAGE,
        )


def validate_timezone_value(request: SettingRequest) -> None:
    timezone_cred = next((cred for cred in request.credential_values if cred.key == "timezone"), None)
    if timezone_cred is not None and isinstance(timezone_cred.value, str):
        validate_timezone_string(timezone_cred.value)
