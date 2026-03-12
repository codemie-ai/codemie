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

from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings import SettingRequest
from codemie.service.settings.scheduler_settings_service import _validate_minimum_hourly_frequency
from croniter import croniter

CRON_EXPRESSION_HELP_MESSAGE = (
    "Please provide a valid cron expression that runs at most once per hour. "
    "Examples: '0 * * * *' (hourly), '0 9 * * MON-FRI' (weekdays at 9 AM), '0 0 * * *' (daily)."
)
LITELLM_API_KEY_HELP_MESSAGE = "Please provide a valid, non-empty API key for LiteLLM integration."
GIT_AUTH_HELP_MESSAGE = (
    "Please specify authentication method using 'auth_type' field: "
    "'pat' for Personal Access Token or 'github_app' for GitHub App authentication."
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


def validate_scheduler_request(request: SettingRequest):
    """
    Validate the incoming scheduler setting request
    """
    # Extract and validate resource values
    resource_type = validate_resource_type(request)
    resource_id = validate_resource_id(request)

    # Validate that the resource belongs to the project
    validate_resource_ownership(resource_type, resource_id, request.project_name)

    # Extract and validate cron expression (schedule)
    validate_cron_expression(request)


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


def validate_resource_ownership(resource_type: str, resource_id: str, project_name: str) -> None:
    """
    Validate that the resource belongs to the specified project.

    Args:
        resource_type: The type of resource (datasource, workflow, assistant)
        resource_id: The ID of the resource
        project_name: The name of the project

    Raises:
        ExtendedHTTPException: If the resource doesn't belong to the project or validation fails
    """
    if resource_type == "datasource":
        validate_datasource_ownership(resource_id, project_name)
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


def raise_validation_error(e: Exception) -> None:
    """Helper function to raise a standardized validation error."""
    raise ExtendedHTTPException(
        code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        message="Cannot validate resource",
        details=f"An error occurred while validating the resource: {str(e)}",
        help="Please check the resource ID and try again.",
    ) from e


def validate_datasource_ownership(resource_id: str, project_name: str) -> None:
    """Validate that a datasource belongs to the specified project."""
    try:
        from codemie.service.index.index_service import IndexStatusService

        if not IndexStatusService.belongs_to_project(resource_id, project_name):
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="Datasource not found",
                details=f"The datasource with ID '{resource_id}' could not be found in '{project_name}' project.",
                help="Please ensure the datasource belongs to the specified project.",
            )
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
                    message="Invalid cron expression",
                    details="Cron expression cannot be empty.",
                    help=CRON_EXPRESSION_HELP_MESSAGE,
                )

            try:
                # Test if the cron expression is valid by creating a croniter object
                cron = croniter(cron_expression)
            except (ValueError, KeyError) as e:
                raise ExtendedHTTPException(
                    code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    message="Invalid cron expression",
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
