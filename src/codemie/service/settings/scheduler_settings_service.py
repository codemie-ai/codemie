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

"""Scheduler Settings Service for managing datasource cron scheduling."""

from typing import Dict, List
from datetime import datetime, timezone

from croniter import croniter
from fastapi import status
from sqlalchemy.orm.attributes import flag_modified

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings import CredentialValues, Settings, SettingType
from codemie.service.settings.base_settings import BaseSettingsService, SearchFields
from codemie_tools.base.models import CredentialTypes

# Constants for scheduler resource types
RESOURCE_TYPE_DATASOURCE = "datasource"

# Alias prefix for datasource schedulers created by index router
DATASOURCE_SCHEDULE_ALIAS_PREFIX = "Schedule_"


class SchedulerSettingsService(BaseSettingsService):
    """Service for managing scheduler settings for datasource reindexing."""

    @staticmethod
    def handle_schedule(
        user_id: str,
        project_name: str,
        resource_id: str,
        resource_name: str,
        cron_expression: str | None,
        resource_type: str = RESOURCE_TYPE_DATASOURCE,
    ) -> Settings | None:
        """
        Handle scheduler creation, update, or deletion based on cron_expression value.

        Args:
            user_id: ID of the user
            project_name: Name of the project
            resource_id: ID of the resource (e.g., datasource ID)
            resource_name: Name of the resource (e.g., datasource name)
            cron_expression: Cron expression for scheduling, None or empty string to delete
            resource_type: Type of resource (default: "datasource")

        Returns:
            Settings object if created/updated, None if deleted or no action taken

        Behavior:
            - If cron_expression is non-empty string: Creates or updates schedule
            - If cron_expression is None or empty string: Deletes existing schedule
        """
        if cron_expression and cron_expression.strip():
            # Create or update the schedule
            return SchedulerSettingsService.create_or_update_schedule(
                user_id=user_id,
                project_name=project_name,
                resource_type=resource_type,
                resource_id=resource_id,
                resource_name=resource_name,
                cron_expression=cron_expression,
                is_enabled=True,
            )
        else:
            # Delete the schedule if it exists (cron_expression is None or empty)
            SchedulerSettingsService.delete_schedule(
                resource_id=resource_id,
                user_id=user_id,
            )
            return None

    @staticmethod
    def create_or_update_schedule(
        user_id: str,
        project_name: str,
        resource_type: str,
        resource_id: str,
        resource_name: str,
        cron_expression: str,
        is_enabled: bool = True,
    ) -> Settings | None:
        """
        Create or update scheduler setting for automatic datasource reindexing.

        Args:
            user_id: ID of the user creating the schedule
            project_name: Name of the project
            resource_type: Type of resource (e.g., "datasource")
            resource_id: ID of the resource (e.g., index_info.id)
            resource_name: Name of the resource (e.g., index_info.repo_name)
            cron_expression: Cron expression for scheduling (e.g., "0 9 * * *")
            is_enabled: Whether the schedule is enabled

        Returns:
            Settings object if created/updated, None if cron_expression is empty/None

        Raises:
            Exception: If there's an error creating/updating the schedule
        """
        if not cron_expression:
            logger.debug(f"Skipping scheduler creation for {resource_id}: no cron_expression provided")
            return None

        try:
            # Check if schedule already exists for this resource
            existing_schedule = SchedulerSettingsService._find_schedule_by_resource_id(
                user_id=user_id, project_name=project_name, resource_id=resource_id
            )

            if existing_schedule:
                # Update existing schedule
                logger.info(f"Updating existing schedule for datasource {resource_id}")
                SchedulerSettingsService._update_schedule_values(
                    existing_schedule, cron_expression, is_enabled, resource_name
                )
                flag_modified(existing_schedule, "credential_values")
                existing_schedule.update()
                return existing_schedule
            else:
                # Create new schedule
                logger.info(f"Creating new schedule for datasource {resource_id}")
                new_schedule = SchedulerSettingsService._create_new_schedule(
                    user_id=user_id,
                    project_name=project_name,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    resource_name=resource_name,
                    cron_expression=cron_expression,
                    is_enabled=is_enabled,
                )
                new_schedule.save()
                return new_schedule

        except Exception as e:
            logger.error(f"Failed to create/update schedule for datasource {resource_id}: {e}", exc_info=True)
            raise

    @staticmethod
    def _find_schedule_by_resource_id(user_id: str, project_name: str, resource_id: str) -> Settings | None:
        """
        Find existing scheduler setting by resource_id for index router schedules only.

        Only returns schedules created by the index router (with alias starting with DATASOURCE_SCHEDULE_ALIAS_PREFIX).
        This ensures we don't update/delete schedules from other integrations.

        Args:
            user_id: ID of the user
            project_name: Name of the project
            resource_id: ID of the resource

        Returns:
            Settings object if found, None otherwise
        """
        # Query for Settings with:
        # - credential_type = SCHEDULER
        # - user_id matches
        # - project_name matches
        # - credential_values contains resource_id
        # - alias starts with DATASOURCE_SCHEDULE_ALIAS_PREFIX (index router schedules only)
        search_fields = {
            SearchFields.USER_ID: user_id,
            SearchFields.PROJECT_NAME: project_name,
            SearchFields.CREDENTIAL_TYPE: CredentialTypes.SCHEDULER,
        }

        # Get all scheduler settings for this user/project
        all_settings = Settings.get_all_by_fields(search_fields)

        # Filter by resource_id in credential_values AND alias prefix
        for setting in all_settings:
            resource_id_value = setting.credential("resource_id")
            has_index_router_alias = setting.alias and setting.alias.startswith(DATASOURCE_SCHEDULE_ALIAS_PREFIX)

            if resource_id_value == resource_id and has_index_router_alias:
                return setting

        return None

    @staticmethod
    def _update_schedule_values(schedule: Settings, cron_expression: str, is_enabled: bool, resource_name: str):
        """
        Update schedule credential values.

        Args:
            schedule: Existing Settings object
            cron_expression: New cron expression
            is_enabled: Whether the schedule is enabled
            resource_name: Name of the resource
        """
        # Update credential values
        for cred in schedule.credential_values:
            if cred.key == "schedule":
                cred.value = cron_expression
            elif cred.key == "is_enabled":
                cred.value = is_enabled

        # Update alias to reflect resource name (using index router prefix)
        schedule.alias = f"{DATASOURCE_SCHEDULE_ALIAS_PREFIX}{resource_name}"

    @staticmethod
    def _create_new_schedule(
        user_id: str,
        project_name: str,
        resource_type: str,
        resource_id: str,
        resource_name: str,
        cron_expression: str,
        is_enabled: bool,
    ) -> Settings:
        """
        Create new scheduler setting.

        Args:
            user_id: ID of the user
            project_name: Name of the project
            resource_type: Type of resource
            resource_id: ID of the resource
            resource_name: Name of the resource
            cron_expression: Cron expression
            is_enabled: Whether the schedule is enabled

        Returns:
            New Settings object (not saved to database yet)
        """
        credential_values = [
            CredentialValues(key="schedule", value=cron_expression),
            CredentialValues(key="resource_type", value=RESOURCE_TYPE_DATASOURCE),
            CredentialValues(key="resource_id", value=resource_id),
            CredentialValues(key="is_enabled", value=True),
        ]

        new_schedule = Settings(
            user_id=user_id,
            project_name=project_name,
            alias=f"{DATASOURCE_SCHEDULE_ALIAS_PREFIX}{resource_name}",
            credential_type=CredentialTypes.SCHEDULER,
            credential_values=credential_values,
            setting_type=SettingType.USER,
            is_global=False,
        )

        return new_schedule

    @staticmethod
    def get_scheduler_settings_for_datasources(user_id: str, datasource_ids: List[str]) -> Dict[str, str]:
        """
        Get cron expressions for multiple datasources from index router schedules only.

        Only includes schedules with alias starting with DATASOURCE_SCHEDULE_ALIAS_PREFIX.
        This ensures we don't return schedules from other integrations.

        Args:
            user_id: ID of the user
            datasource_ids: List of datasource IDs

        Returns:
            Dict mapping datasource_id -> cron_expression
        """
        if not datasource_ids:
            return {}

        # Query for all scheduler settings for this user
        search_fields = {
            SearchFields.USER_ID: user_id,
            SearchFields.CREDENTIAL_TYPE: CredentialTypes.SCHEDULER,
        }

        all_settings = Settings.get_all_by_fields(search_fields)

        # Build mapping of resource_id -> cron_expression (only for index router schedules)
        schedule_map = {}
        for setting in all_settings:
            resource_id = setting.credential("resource_id")
            schedule = setting.credential("schedule")
            is_enabled = setting.credential("is_enabled")
            has_index_router_alias = setting.alias and setting.alias.startswith(DATASOURCE_SCHEDULE_ALIAS_PREFIX)

            if resource_id in datasource_ids and is_enabled and has_index_router_alias:
                schedule_map[resource_id] = schedule

        return schedule_map

    @staticmethod
    def delete_schedule(resource_id: str, user_id: str) -> bool:
        """
        Delete scheduler setting for a datasource created by index router only.

        Args:
            resource_id: ID of the resource
            user_id: ID of the user

        Returns:
            True if schedule was deleted, False if not found
        """
        try:
            # Find schedule by resource_id
            search_fields = {
                SearchFields.USER_ID: user_id,
                SearchFields.CREDENTIAL_TYPE: CredentialTypes.SCHEDULER,
            }

            all_settings = Settings.get_all_by_fields(search_fields)

            # Find and delete the schedule with matching resource_id AND alias prefix
            for setting in all_settings:
                resource_id_value = setting.credential("resource_id")
                has_index_router_alias = setting.alias and setting.alias.startswith(DATASOURCE_SCHEDULE_ALIAS_PREFIX)

                if resource_id_value == resource_id and has_index_router_alias:
                    setting.delete()
                    logger.info(f"Deleted schedule for datasource {resource_id}")
                    return True

            logger.warning(f"No index router schedule found for datasource {resource_id}")
            return False

        except Exception as e:
            logger.error(f"Failed to delete schedule for datasource {resource_id}: {e}", exc_info=True)
            return False


def validate_cron_expression(cron_expr: str | None) -> None:
    """
    Validate cron expression using croniter.

    Ensures the schedule runs at most once per hour (minimum hourly frequency).
    Empty strings are allowed (they signal schedule deletion).

    Args:
        cron_expr: Cron expression to validate (e.g., "0 2 * * *"), None, or empty string to delete

    Raises:
        ExtendedHTTPException: If cron expression is invalid or runs more frequently than hourly
    """
    if cron_expr is None or (isinstance(cron_expr, str) and not cron_expr.strip()):
        return

    if not isinstance(cron_expr, str):
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Cron expression must be a string",
            details=f"Invalid cron expression type: {type(cron_expr)}",
            help="Please provide a valid cron expression.",
        )

    try:
        # Validate cron expression format
        cron = croniter(cron_expr)
    except (ValueError, KeyError) as e:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=f"Invalid cron expression: {cron_expr}",
            details=str(e),
            help="Use standard cron format: 'minute hour day month day_of_week' (e.g., '0 2 * * *' for 2 AM daily)",
        ) from e

    # Check minimum frequency (must not run more than once per hour)
    _validate_minimum_hourly_frequency(cron_expr, cron)


def _validate_minimum_hourly_frequency(cron_expr: str, cron: croniter) -> None:
    """
    Validate that cron expression runs at most once per hour.

    Args:
        cron_expr: Original cron expression string
        cron: Initialized croniter instance

    Raises:
        ExtendedHTTPException: If schedule runs more frequently than hourly
    """
    # Get next two execution times
    base_time = datetime.now(timezone.utc)
    cron_check = croniter(cron_expr, base_time)

    first_run = cron_check.get_next(datetime)
    second_run = cron_check.get_next(datetime)

    # Calculate difference in seconds
    time_diff = (second_run - first_run).total_seconds()

    # Must be at least 1 hour (3600 seconds) between runs
    if time_diff < 3600:
        minutes_between = int(time_diff / 60)
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=f"Cron expression runs too frequently: every {minutes_between} minute(s)",
            details=f"Schedule must run at most once per hour. Current schedule: '{cron_expr}'",
            help=(
                "Please use a schedule that runs hourly or less frequently "
                "(e.g., '0 * * * *' for hourly, '0 */2 * * *' for every 2 hours, '0 0 * * *' for daily)"
            ),
        )
