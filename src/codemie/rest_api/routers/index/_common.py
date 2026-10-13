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

from typing import Optional

from elasticsearch.exceptions import NotFoundError
from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import (
    Application,
)
from codemie.datasource.exceptions import ConnectionException
from codemie.datasource.loader.git_loader import GitBatchLoader
from codemie.rest_api.models.index import (
    IndexInfo,
    KnowledgeBaseIndexInfo,
)
from codemie.rest_api.security.user import User
from codemie.service.settings.settings import SettingsService


# Error message constants
CHECK_PERMISSIONS_MESSAGE = "Please check your user permissions or contact an administrator for assistance."
ACCESS_DENIED_MESSAGE = "Access denied"


INDEX_NOT_FOUND_MESSAGE = "Index not found"
INDEX_NOT_FOUND_HELP = (
    "Please verify the index name and project name. If you believe this index should exist, "
    "check your project configuration or contact support."
)
APPLICATION_NOT_FOUND_MESSAGE = "Application not found"
APPLICATION_NOT_FOUND_HELP = (
    "Please verify the application name and ensure it exists. If you believe this is an error, contact support."
)
INVALID_INPUT_PARAMETERS_HELP = "Ensure that the submitted parameters are correct"
INCORRECT_DATASOURCE_SETUP_MESSAGE = "Datasource setup is incorrect"
EDIT_SUCCESSFUL = "Edit successful"


# Project change related constants
INDEX_EXISTS_MESSAGE = "Index already exists"
INDEX_EXISTS_HELP = "Please choose a different name for your index or use the existing index."
PROJECT_CHANGE_LOG_MESSAGE = "Project association changed for index"


def _update_datasource_scheduler(
    user_id: str,
    index_info: IndexInfo,
    cron_expression: str,
    timezone: Optional[str] = None,
) -> None:
    """
    Update or create scheduler settings for a datasource.
    If cron_expression is empty string, deletes existing schedule.

    Args:
        user_id: ID of the user
        index_info: IndexInfo object for the datasource
        cron_expression: Cron expression for scheduling, or empty string to remove schedule
        timezone: Optional IANA timezone name (e.g. 'Europe/Warsaw')
    """
    from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

    SchedulerSettingsService.handle_schedule(
        user_id=user_id,
        project_name=index_info.project_name,
        resource_id=index_info.id,
        resource_name=index_info.repo_name,
        cron_expression=cron_expression,
        timezone=timezone,
    )


def _index_unique_check(project_name, repo_name):
    if IndexInfo.filter_by_project_and_repo(project_name=project_name, repo_name=repo_name):
        raise ExtendedHTTPException(
            code=status.HTTP_409_CONFLICT,
            message=INDEX_EXISTS_MESSAGE,
            details=f"An index with the name '{repo_name}' already exists in the project '{project_name}'.",
            help=INDEX_EXISTS_HELP,
        )


def _kb_demo_user_check(user):
    """Check if user is demo user and has already indexed a repository"""
    if user.is_demo_user:
        existing_repos = KnowledgeBaseIndexInfo.get_by_user_id(user_id=user.id)

        if len(existing_repos) >= 1:
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message="Repository limit reached",
                details="Demo users are allowed to index only one repository. You have already reached this limit.",
                help="To index additional repositories, please upgrade your account "
                "or remove your existing indexed repository.",
            )


def _validate_git_credentials(user_id: str, project_name: str, repo_link: str, setting_id: str) -> None:
    """
    Validates git credentials by attempting to retrieve them.

    Args:
        user_id: The user ID
        project_name: The project name
        repo_link: The repository link
        setting_id: The git integration setting ID

    Raises:
        ExtendedHTTPException: If credentials are invalid or missing required fields
    """
    if not setting_id:
        try:
            GitBatchLoader.test_public_access(repo_link)
        except ConnectionException:
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="Repository Not Publicly Accessible",
                details="The repository URL is not accessible without authentication. "
                "Please select a Git integration to provide credentials.",
                help="Go to Settings > Integrations and add a Git integration, "
                "then select it when creating the datasource.",
            )
        return

    try:
        SettingsService.get_git_creds(
            user_id=user_id,
            project_name=project_name,
            repo_link=repo_link,
            setting_id=setting_id,
        )
    except Exception as e:
        error_message = str(e)
        if "Field required" in error_message and "token" in error_message:
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="Invalid Git Integration Configuration",
                details="The selected git integration is missing a required token. "
                "Please ensure your git integration has a valid access token configured, not just a token alias.",
                help="Go to Settings > Integrations and add a valid token to the git integration.",
            ) from e
        else:
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="Git Integration Error",
                details=f"Failed to validate git credentials: {error_message}",
                help="Please check your git integration settings.",
            ) from e


def _validate_project_change(new_project_name: str, current_project_name: str, repo_name: str, user: User):
    """Validates project change request and raises appropriate exceptions if invalid.

    Args:
        new_project_name: Target project name
        current_project_name: Current project name
        repo_name: Repository/datasource name
        user: Current user making the request

    Raises:
        ExtendedHTTPException: If project change validation fails
    """
    if not new_project_name or new_project_name == current_project_name:
        return

    # Verify target project exists
    try:
        Application.get_by_id(new_project_name)
    except (NotFoundError, KeyError) as e:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=APPLICATION_NOT_FOUND_MESSAGE,
            details=f"The application with name '{new_project_name}' could not be found in the system.",
            help=APPLICATION_NOT_FOUND_HELP,
        ) from e

    # Check if user has access to the target project
    if not user.has_access_to_application(new_project_name):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=ACCESS_DENIED_MESSAGE,
            details=f"You don't have permission for the project '{new_project_name}'.",
            help=CHECK_PERMISSIONS_MESSAGE,
        )

    # Check if there's an existing index with same name in target project
    existing_index = IndexInfo.filter_by_project_and_repo(project_name=new_project_name, repo_name=repo_name)
    if existing_index:
        raise ExtendedHTTPException(
            code=status.HTTP_409_CONFLICT,
            message=INDEX_EXISTS_MESSAGE,
            details=f"An index with the name '{repo_name}' already exists in the project '{new_project_name}'.",
            help=INDEX_EXISTS_HELP,
        )
