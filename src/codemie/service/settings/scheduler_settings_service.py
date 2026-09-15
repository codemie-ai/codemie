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

from typing import Dict, List, Optional
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from croniter import croniter
from apscheduler.triggers.cron import CronTrigger
from fastapi import status
from sqlalchemy.orm.attributes import flag_modified

from codemie.configs import logger
from codemie.configs.config import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings import CredentialValues, Settings, SettingType
from codemie.service.settings.base_settings import BaseSettingsService, SearchFields
from codemie_tools.base.models import CredentialTypes

# Constants for scheduler resource types
RESOURCE_TYPE_DATASOURCE = "datasource"

# Shared error message for invalid cron expressions
INVALID_CRON_EXPRESSION_MESSAGE = "Invalid cron expression"

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
        timezone: Optional[str] = None,
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
                timezone=timezone,
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
        timezone: Optional[str] = None,
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
                    existing_schedule, cron_expression, is_enabled, resource_name, timezone=timezone
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
                    timezone=timezone,
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
    def _update_schedule_values(
        schedule: Settings,
        cron_expression: str,
        is_enabled: bool,
        resource_name: str,
        timezone: Optional[str] = None,
    ):
        # Update credential values
        for cred in schedule.credential_values:
            if cred.key == "schedule":
                cred.value = cron_expression
            elif cred.key == "is_enabled":
                cred.value = is_enabled
            elif cred.key == "timezone" and timezone is not None:
                cred.value = timezone

        # Add timezone credential if provided and not already present
        if timezone is not None and not any(c.key == "timezone" for c in schedule.credential_values):
            schedule.credential_values.append(CredentialValues(key="timezone", value=timezone))

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
        timezone: Optional[str] = None,
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
            CredentialValues(key="is_enabled", value=is_enabled),
        ]
        if timezone is not None:
            credential_values.append(CredentialValues(key="timezone", value=timezone))

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
    def get_scheduler_settings_for_datasources(user_id: str, datasource_ids: List[str]) -> Dict[str, dict]:
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

        # Build mapping of resource_id -> {cron_expression, timezone} (only for index router schedules)
        schedule_map = {}
        for setting in all_settings:
            resource_id = setting.credential("resource_id")
            schedule = setting.credential("schedule")
            is_enabled = setting.credential("is_enabled")
            timezone = setting.credential("timezone")
            has_index_router_alias = setting.alias and setting.alias.startswith(DATASOURCE_SCHEDULE_ALIAS_PREFIX)

            if resource_id in datasource_ids and is_enabled and has_index_router_alias:
                schedule_map[resource_id] = {"cron_expression": schedule, "timezone": timezone or config.TIMEZONE}

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

    @staticmethod
    def delete_integrations_by_resource(resource_id: str, project_name: str, credential_type: CredentialTypes) -> int:
        """
        Delete all settings of the given credential_type linked to a specific resource.

        Args:
            resource_id: ID of the resource (datasource, assistant, or workflow)
            project_name: Name of the project the resource belongs to
            credential_type: Type of integration to delete (e.g. SCHEDULER, WEBHOOK)

        Returns:
            Number of deleted integrations

        Raises:
            Exception: Propagates any storage errors to the caller for handling
        """
        matched_settings = Settings.find_by_resource_id(project_name, credential_type, resource_id)

        deleted_count = 0
        for setting in matched_settings:
            setting.delete()
            deleted_count += 1
            logger.info(f"Deleted {credential_type.value} integration '{setting.alias}' for resource {resource_id}")

        if not deleted_count:
            logger.debug(f"No {credential_type.value} integrations found for resource {resource_id}")

        return deleted_count

    @staticmethod
    def _get_cred_value(setting, key):
        return next((cv.value for cv in (setting.credential_values or []) if cv.key == key), None)

    @staticmethod
    def _build_scheduler_item(setting, last_run=None):
        from codemie.rest_api.models.scheduler_run import (
            LastRunRef,
            ProjectRef,
            ResourceRef,
            ScheduleInfo,
            SchedulerListItem,
        )

        _gcv = SchedulerSettingsService._get_cred_value
        cron_expr = _gcv(setting, "schedule") or ""
        tz_name = _gcv(setting, "timezone") or "UTC"
        resource_type = _gcv(setting, "resource_type") or ""
        resource_id = _gcv(setting, "resource_id") or ""
        resource_name = _gcv(setting, "resource_name") or ""
        is_enabled = _gcv(setting, "is_enabled") or False

        description = cron_expr
        try:
            from get_pretty_cron import prettify_cron

            description = prettify_cron(cron_expr)
        except Exception:
            pass

        next_run_at = None
        if cron_expr:
            try:
                from zoneinfo import ZoneInfo
                from croniter import croniter
                from datetime import datetime

                tz = ZoneInfo(tz_name)
                itr = croniter(cron_expr, datetime.now(tz))
                next_run_at = itr.get_next(datetime).isoformat()
            except Exception:
                pass

        last_run_item = None
        if last_run:
            started = last_run["started_at"]
            last_run_item = LastRunRef(
                id=last_run["id"],
                status=last_run["status"],
                startedAt=started.isoformat() if hasattr(started, "isoformat") else str(started),
            )

        return SchedulerListItem(
            id=setting.id,
            name=setting.alias or resource_name or setting.id,
            resource=ResourceRef(id=resource_id, name=resource_name, type=resource_type.capitalize()),
            project=ProjectRef(id=setting.project_name, name=setting.project_name),
            schedule=ScheduleInfo(cron=cron_expr, description=description, timezone=tz_name, nextRunAt=next_run_at),
            isEnabled=bool(is_enabled),
            lastRun=last_run_item,
        )

    @staticmethod
    def _build_list_filters(project_id, resource_type, resource_id, search, status, last_run_status):
        conditions = ["s.credential_type = 'SCHEDULER'"]
        params: dict = {}

        if project_id:
            conditions.append("s.project_name = :project_id")
            params["project_id"] = project_id
        if resource_type:
            conditions.append(
                "EXISTS (SELECT 1 FROM jsonb_array_elements(s.credential_values::jsonb) cv "
                "WHERE cv->>'key' = 'resource_type' AND lower(cv->>'value') = lower(:resource_type))"
            )
            params["resource_type"] = resource_type
        if resource_id:
            conditions.append(
                "EXISTS (SELECT 1 FROM jsonb_array_elements(s.credential_values::jsonb) cv "
                "WHERE cv->>'key' = 'resource_id' AND cv->>'value' = :resource_id)"
            )
            params["resource_id"] = resource_id
        if search:
            conditions.append(
                "(s.alias ILIKE :search OR EXISTS ("
                "SELECT 1 FROM jsonb_array_elements(s.credential_values::jsonb) cv "
                "WHERE cv->>'key' = 'resource_name' AND cv->>'value' ILIKE :search))"
            )
            params["search"] = f"%{search}%"
        if status == "enabled":
            conditions.append(
                "EXISTS (SELECT 1 FROM jsonb_array_elements(s.credential_values::jsonb) cv "
                "WHERE cv->>'key' = 'is_enabled' AND (cv->>'value')::boolean = true)"
            )
        elif status == "disabled":
            conditions.append(
                "NOT EXISTS (SELECT 1 FROM jsonb_array_elements(s.credential_values::jsonb) cv "
                "WHERE cv->>'key' = 'is_enabled' AND (cv->>'value')::boolean = true)"
            )
        if last_run_status == "never":
            conditions.append("lr.id IS NULL")
        elif last_run_status:
            conditions.append("lr.status = :last_run_status")
            params["last_run_status"] = last_run_status

        return conditions, params

    @staticmethod
    def list_schedulers(
        page=0,
        per_page=10,
        search=None,
        resource_type=None,
        project_id=None,
        resource_id=None,
        status=None,
        last_run_status=None,
    ):
        import math

        from sqlalchemy import text
        from sqlmodel import Session, select

        from codemie.rest_api.models.base import PaginationData
        from codemie.rest_api.models.scheduler_run import SchedulersPaginatedResponse

        conditions, params = SchedulerSettingsService._build_list_filters(
            project_id, resource_type, resource_id, search, status, last_run_status
        )

        last_run_join = (
            "LEFT JOIN LATERAL ("
            "  SELECT id, status, started_at FROM codemie.scheduler_runs"
            "  WHERE scheduler_id = s.id"
            "  ORDER BY started_at DESC LIMIT 1"
            ") lr ON true"
        )

        where = "WHERE " + " AND ".join(conditions)
        row_params = {**params, "limit": per_page, "offset": page * per_page}

        count_sql = text(f"SELECT COUNT(*) FROM codemie.settings s {last_run_join} {where}")
        rows_sql = text(
            f"SELECT s.id, lr.id AS lr_id, lr.status AS lr_status, lr.started_at AS lr_started_at "
            f"FROM codemie.settings s {last_run_join} {where} "
            f"ORDER BY s.id LIMIT :limit OFFSET :offset"
        )

        with Settings.get_engine().connect() as conn:
            total = conn.execute(count_sql.bindparams(**params)).scalar_one()
            rows = conn.execute(rows_sql.bindparams(**row_params)).mappings().all()

        ids = [row["id"] for row in rows]
        if ids:
            with Session(Settings.get_engine()) as sess:
                settings_list = sess.exec(select(Settings).where(Settings.id.in_(ids))).all()
            settings_map = {s.id: s for s in settings_list}
        else:
            settings_map = {}

        items = []
        for row in rows:
            setting = settings_map.get(row["id"])
            if setting is None:
                continue
            last_run = None
            if row.get("lr_id"):
                last_run = {"id": row["lr_id"], "status": row["lr_status"], "started_at": row["lr_started_at"]}
            items.append(SchedulerSettingsService._build_scheduler_item(setting, last_run))

        total_int = int(total)
        return SchedulersPaginatedResponse(
            items=items,
            pagination=PaginationData(
                page=page,
                per_page=per_page,
                total=total_int,
                pages=math.ceil(total_int / per_page) if per_page else 0,
            ),
        )

    _filter_options_cache: dict = {}
    _FILTER_OPTIONS_TTL = 60  # seconds

    @staticmethod
    def _fetch_all_scheduler_settings():
        """Return all Settings with credential_type = SCHEDULER via Elasticsearch."""
        return Settings.get_all(credential_type=CredentialTypes.SCHEDULER)

    @staticmethod
    def _fetch_datasource_names(ids: list[str]) -> dict[str, str]:
        try:
            from codemie.rest_api.models.index import IndexInfo

            return {ds.id: ds.repo_name for ds in IndexInfo.get_by_ids(ids)}
        except Exception:
            return {}

    @staticmethod
    def _fetch_assistant_names(ids: list[str], user) -> dict[str, str]:
        try:
            from codemie.rest_api.models.assistant import Assistant

            if user is not None:
                items = Assistant.get_by_ids(user, ids)
            else:
                items = Assistant.get_by_ids_no_permission_check(ids)
            return {a.id: a.name for a in items}
        except Exception:
            return {}

    @staticmethod
    def _fetch_workflow_names(ids: list[str]) -> dict[str, str]:
        try:
            from codemie.core.workflow_models.workflow_config import WorkflowConfig

            return {wf.id: wf.name for wf in WorkflowConfig.get_by_ids(ids)}
        except Exception:
            return {}

    @staticmethod
    def _build_resource_name_map(settings, user=None) -> dict[str, str]:
        """Batch-fetch canonical resource names by type to avoid N+1 lookups."""
        from collections import defaultdict

        ids_by_type: dict[str, list[str]] = defaultdict(list)
        _gcv = SchedulerSettingsService._get_cred_value

        for setting in settings:
            rid = _gcv(setting, "resource_id") or ""
            rtype = (_gcv(setting, "resource_type") or "").lower()
            if rid and rtype:
                ids_by_type[rtype].append(rid)

        _svc = SchedulerSettingsService
        name_map: dict[str, str] = {}
        name_map.update(_svc._fetch_datasource_names(list(set(ids_by_type.get("datasource", [])))))
        name_map.update(_svc._fetch_assistant_names(list(set(ids_by_type.get("assistant", []))), user))
        name_map.update(_svc._fetch_workflow_names(list(set(ids_by_type.get("workflow", [])))))
        return name_map

    @staticmethod
    def get_filter_options(user=None):
        """Return unique resources and projects that have at least one scheduler, sorted by name."""
        import time

        from codemie.rest_api.models.scheduler_run import ProjectRef, ResourceRef, SchedulerFilterOptions

        now = time.monotonic()
        cached = SchedulerSettingsService._filter_options_cache
        if cached.get("expires_at", 0) > now:
            return cached["value"]

        _gcv = SchedulerSettingsService._get_cred_value
        settings = SchedulerSettingsService._fetch_all_scheduler_settings()
        resource_name_map = SchedulerSettingsService._build_resource_name_map(settings, user=user)

        seen_resources: dict[str, ResourceRef] = {}
        seen_projects: dict[str, ProjectRef] = {}

        for setting in settings:
            resource_id = _gcv(setting, "resource_id") or ""
            resource_type = (_gcv(setting, "resource_type") or "").capitalize()
            project_id = setting.project_name or ""

            # Canonical name from the resource model; fall back to stored credential then alias
            resource_name = resource_name_map.get(resource_id) or _gcv(setting, "resource_name") or ""

            if resource_id and resource_name and resource_id not in seen_resources:
                seen_resources[resource_id] = ResourceRef(id=resource_id, name=resource_name, type=resource_type)
            if project_id and project_id not in seen_projects:
                seen_projects[project_id] = ProjectRef(id=project_id, name=project_id)

        resources = sorted(seen_resources.values(), key=lambda r: r.name.lower())
        projects = sorted(seen_projects.values(), key=lambda p: p.name.lower())
        result = SchedulerFilterOptions(resources=resources, projects=projects)
        SchedulerSettingsService._filter_options_cache = {
            "value": result,
            "expires_at": now + SchedulerSettingsService._FILTER_OPTIONS_TTL,
        }
        return result

    @staticmethod
    def patch_is_enabled(scheduler_id: str, is_enabled: bool):
        from codemie.rest_api.routers.utils import raise_not_found

        setting = Settings.get_by_id(scheduler_id)
        if setting is None:
            raise_not_found(scheduler_id, "Scheduler")

        for cv in setting.credential_values or []:
            if cv.key == "is_enabled":
                cv.value = is_enabled
                break
        else:
            setting.credential_values.append(CredentialValues(key="is_enabled", value=is_enabled))

        flag_modified(setting, "credential_values")
        setting.update()
        return SchedulerSettingsService._build_scheduler_item(setting)


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
            message=f"{INVALID_CRON_EXPRESSION_MESSAGE}: {cron_expr}",
            details=str(e),
            help="Use standard cron format: 'minute hour day month day_of_week' (e.g., '0 2 * * *' for 2 AM daily)",
        ) from e

    # Validate against APScheduler's CronTrigger — catches constraints croniter
    # does not enforce (e.g. day_of_week > 6)
    try:
        minute, hour, day_of_month, month, day_of_week = cron_expr.split()
        CronTrigger(minute=minute, hour=hour, day=day_of_month, month=month, day_of_week=day_of_week)
    except ValueError as e:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=f"{INVALID_CRON_EXPRESSION_MESSAGE}: {cron_expr}",
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


def validate_timezone_string(timezone_str: Optional[str]) -> None:
    if timezone_str is None:
        return
    try:
        ZoneInfo(timezone_str)
    except (KeyError, ValueError):
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Invalid timezone",
            details=f"'{timezone_str}' is not a recognised IANA timezone name.",
            help="Provide an IANA timezone name such as 'Europe/Warsaw', 'America/New_York', or 'UTC'.",
        )
