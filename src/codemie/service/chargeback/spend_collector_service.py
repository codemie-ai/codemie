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

from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from codemie.clients.postgres import get_async_session
from codemie.configs import config, logger
from codemie.enterprise.litellm.dependencies import get_all_keys_spending
from codemie.repository.application_repository import ApplicationRepository
from codemie.repository.project_cost_tracking_repository import ProjectCostTrackingRepository
from codemie.rest_api.models.settings import CredentialTypes, LiteLLMCredentials, Settings
from codemie.service.chargeback.spend_models import ProjectCostTracking
from codemie.service.settings.settings import SettingsService


class LiteLLMSpendCollectorService:
    """Orchestrates the daily LiteLLM spend collection cycle.

    Discovers active LiteLLM API keys, queries LiteLLM for per-key cumulative spend,
    computes budget-reset-aware daily spend deltas, and persists one row per key per day
    to the project_cost_tracking table.
    """

    def __init__(
        self,
        app_repository: ApplicationRepository,
        tracking_repository: ProjectCostTrackingRepository,
    ) -> None:
        self._app_repository = app_repository
        self._tracking_repository = tracking_repository

    async def collect(self, target_date: date | None = None) -> int:
        """Run one full spend collection cycle for target_date.

        Manages its own async session internally — no session is passed from the caller.

        Args:
            target_date: Calendar date for the snapshot. Defaults to UTC today.

        Returns:
            Count of rows inserted (duplicates for the same key+date are silently skipped).
        """
        if target_date is None:
            target_date = datetime.now(timezone.utc).date()

        logger.info(f"Starting spend collection for {target_date}")

        async with get_async_session() as session:
            # Load all non-deleted applications, filter by configured patterns/list
            all_apps = await self._app_repository.aget_all_non_deleted(session)
            matching_project_names = self._filter_project_names([app.name for app in all_apps])
            logger.debug(
                f"Found {len(all_apps)} total apps; {len(matching_project_names)} pass project filter: "
                f"{matching_project_names}"
            )

            if not matching_project_names:
                logger.info("No projects pass the configured project filter; spend collection skipped")
                return 0

            # Load all LiteLLM settings for matching projects
            settings_list = Settings.get_by_project_names(matching_project_names, CredentialTypes.LITE_LLM)
            logger.debug(f"Loaded {len(settings_list)} LiteLLM settings for {len(matching_project_names)} projects")

            # Extract api_keys per project; compute key_hash per key
            # key_details: api_key -> (project_name, key_hash)
            key_details: dict[str, tuple[str, str]] = {}
            for setting in settings_list:
                SettingsService._decrypt_credentials(setting)
                cred = SettingsService._build_credential_result(
                    setting, SettingsService.LITELLM_FIELDS, LiteLLMCredentials
                )
                if cred and cred.api_key:
                    key_hash = self._hash_key(cred.api_key)
                    key_details[cred.api_key] = (setting.project_name, key_hash)
                    logger.debug(
                        f"Resolved API key for project '{setting.project_name}' (hash prefix: {key_hash[:8]}...)"
                    )
                else:
                    logger.warning(f"No API key in LiteLLM setting {setting.id} for project {setting.project_name}")

            if not key_details:
                logger.info("No LiteLLM API keys found for matching projects; spend collection skipped")
                return 0

            logger.info(f"Querying LiteLLM spend for {len(key_details)} API key(s)")

            # Load most recent cumulative_spend baselines per key_hash
            all_key_hashes = [key_hash for _, key_hash in key_details.values()]
            prev_rows = await self._tracking_repository.get_latest_by_key_hashes(session, all_key_hashes)
            logger.debug(f"Loaded {len(prev_rows)} prior baseline rows for delta calculation")

            # Per-key: query LiteLLM spend, compute delta, build row
            rows_to_insert: list[ProjectCostTracking] = []
            for api_key, (project_name, key_hash) in key_details.items():
                # Query LiteLLM for this key's cumulative spend (sync HTTP — run off the event loop)
                logger.debug(f"Querying LiteLLM spend for project '{project_name}' (hash prefix: {key_hash[:8]}...)")
                spending_result = await asyncio.to_thread(get_all_keys_spending, [api_key])
                if not spending_result:
                    logger.warning(
                        f"No spending data from LiteLLM for key (hash prefix: {key_hash[:8]}...) "
                        f"in project {project_name}; skipping"
                    )
                    continue

                current_spend = Decimal(str(spending_result[0].get("total_spend", 0)))
                prev_row = prev_rows.get(key_hash)

                # Compute budget-reset-aware delta
                daily_spend = self._compute_delta(current_spend, prev_row)

                logger.debug(
                    f"Project '{project_name}' (hash prefix: {key_hash[:8]}...): "
                    f"cumulative={current_spend}, prev_cumulative={prev_row.cumulative_spend if prev_row else 'n/a'}, "
                    f"daily_delta={daily_spend}"
                )

                # Build tracking row
                rows_to_insert.append(
                    ProjectCostTracking(
                        id=uuid4(),
                        project_name=project_name,
                        key_hash=key_hash,
                        spend_date=target_date,
                        daily_spend=daily_spend,
                        cumulative_spend=current_spend,
                    )
                )

            skipped = len(key_details) - len(rows_to_insert)
            logger.info(f"Inserting {len(rows_to_insert)} row(s) for {target_date} (skipped: {skipped})")
            # Bulk insert; duplicates for the same key+date are silently skipped
            await self._tracking_repository.insert_entries(session, rows_to_insert)

        count = len(rows_to_insert)
        logger.info(f"Spend collection for {target_date} complete: {count} rows inserted")
        return count

    @staticmethod
    def _filter_project_names(names: list[str]) -> list[str]:
        """Return project names that pass the configured include/exclude filters.

        Filtering rules (evaluated in order):
        1. If ``LITELLM_SPEND_COLLECTOR_PROJECT_INCLUDE_PATTERN`` is non-empty, only
           names that match the pattern are kept.
        2. If ``LITELLM_SPEND_COLLECTOR_PROJECT_EXCLUDE_PATTERN`` is non-empty, names
           matching that pattern are removed.
        3. Names present in ``LITELLM_SPEND_COLLECTOR_PROJECT_EXCLUDE_LIST`` are removed.
        """
        include_pattern = config.LITELLM_SPEND_COLLECTOR_PROJECT_INCLUDE_PATTERN
        exclude_pattern = config.LITELLM_SPEND_COLLECTOR_PROJECT_EXCLUDE_PATTERN
        exclude_list: set[str] = set(config.LITELLM_SPEND_COLLECTOR_PROJECT_EXCLUDE_LIST)

        include_re = re.compile(include_pattern) if include_pattern else None
        exclude_re = re.compile(exclude_pattern) if exclude_pattern else None

        result: list[str] = []
        for name in names:
            if include_re and not include_re.match(name):
                continue
            if exclude_re and exclude_re.match(name):
                continue
            if name in exclude_list:
                continue
            result.append(name)
        return result

    def _compute_delta(
        self,
        current_spend: Decimal,
        prev_row: ProjectCostTracking | None,
    ) -> Decimal:
        """Compute budget-reset-aware daily spend delta.

        Args:
            current_spend: LiteLLM cumulative spend at snapshot time.
            prev_row: Most recent stored row for this key, or None on first run.

        Returns:
            daily_spend delta:
            - No prior row (bootstrap): returns current_spend
            - current >= prev cumulative: returns current - prev (normal delta)
            - current < prev (budget reset): returns current_spend (not zero, not negative)
        """
        if prev_row is None:
            logger.debug(f"Bootstrap run — no prior row; using current_spend={current_spend} as daily_spend")
            return current_spend

        prev_cumulative = prev_row.cumulative_spend
        if current_spend >= prev_cumulative:
            return current_spend - prev_cumulative

        # Budget reset detected: current_spend < prev_cumulative
        logger.warning(
            f"Budget reset detected for key_hash prefix {prev_row.key_hash[:8]}...: "
            f"current_spend={current_spend} < prev_cumulative={prev_cumulative}; "
            f"using current_spend as daily_spend"
        )
        return current_spend

    @staticmethod
    def _hash_key(api_key: str) -> str:
        """Return SHA-256 hex digest of api_key."""
        return hashlib.sha256(api_key.encode()).hexdigest()
