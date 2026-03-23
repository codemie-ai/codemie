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
from datetime import date, datetime, time, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from codemie.clients.postgres import get_async_session
from codemie.configs import config, logger
from codemie.enterprise.litellm.dependencies import get_all_keys_spending
from codemie.repository.application_repository import ApplicationRepository
from codemie.repository.project_cost_tracking_repository import ProjectCostTrackingRepository
from codemie.rest_api.models.settings import CredentialTypes, LiteLLMCredentials, Settings
from codemie.service.chargeback.spend_models import ProjectCostTracking
from codemie.service.settings.settings import SettingsService


class InvalidSpendSnapshotError(ValueError):
    """Raised when a computed spend snapshot violates business invariants."""


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

    _SPEND_PRECISION = Decimal("0.000000001")

    async def collect(self, target_date: date | datetime | None = None) -> int:
        """Run one full spend collection cycle for the target snapshot timestamp.

        Manages its own async session internally — no session is passed from the caller.

        Args:
            target_date: Snapshot date/datetime. Defaults to current UTC time.

        Returns:
            Count of rows inserted (duplicates for the same key+date are silently skipped).
        """
        if target_date is None:
            target_snapshot_at = datetime.now(timezone.utc)
        elif isinstance(target_date, datetime):
            target_snapshot_at = (
                target_date if target_date.tzinfo is not None else target_date.replace(tzinfo=timezone.utc)
            )
        else:
            target_snapshot_at = datetime.combine(target_date, time.min, tzinfo=timezone.utc)

        logger.info(f"Starting spend collection for {target_snapshot_at.isoformat(timespec='milliseconds')}")

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
            prev_rows = await self._tracking_repository.get_latest_before_by_key_hashes(
                session,
                all_key_hashes,
                target_snapshot_at,
            )
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

                spending_payload = spending_result[0]
                current_budget_period_spend = self._extract_budget_period_spend(spending_payload)
                current_budget_reset_at = self._extract_budget_reset_at(spending_payload)
                prev_row = prev_rows.get(key_hash)

                # Compute budget-reset-aware delta and lifetime cumulative spend
                try:
                    daily_spend, cumulative_spend = self._compute_spend_snapshot(
                        current_budget_period_spend=current_budget_period_spend,
                        current_budget_reset_at=current_budget_reset_at,
                        prev_row=prev_row,
                        snapshot_at=target_snapshot_at,
                    )
                except InvalidSpendSnapshotError as exc:
                    logger.warning(f"Skipping invalid spend snapshot for project {project_name!r}: {exc}")
                    continue

                logger.debug(
                    f"Project '{project_name}' (hash prefix: {key_hash[:8]}...): "
                    f"budget_period_spend={current_budget_period_spend}, "
                    f"lifetime_cumulative={cumulative_spend}, "
                    f"prev_budget_period={prev_row.budget_period_spend if prev_row else 'n/a'}, "
                    f"daily_delta={daily_spend}, budget_reset_at={current_budget_reset_at}"
                )

                if daily_spend == Decimal("0"):
                    logger.debug(
                        f"Project '{project_name}' (hash prefix: {key_hash[:8]}...) has zero delta; skipping snapshot"
                    )
                    continue

                # Build tracking row
                rows_to_insert.append(
                    ProjectCostTracking(
                        id=uuid4(),
                        project_name=project_name,
                        key_hash=key_hash,
                        spend_date=target_snapshot_at,
                        daily_spend=daily_spend,
                        cumulative_spend=cumulative_spend,
                        budget_period_spend=current_budget_period_spend,
                        budget_reset_at=current_budget_reset_at,
                    )
                )

            skipped = len(key_details) - len(rows_to_insert)
            logger.info(f"Inserting {len(rows_to_insert)} row(s) for {target_snapshot_at} (skipped: {skipped})")
            await self._tracking_repository.insert_entries(session, rows_to_insert)

        count = len(rows_to_insert)
        logger.info(f"Spend collection for {target_snapshot_at} complete: {count} rows inserted")
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

    def _compute_spend_snapshot(
        self,
        current_budget_period_spend: Decimal,
        current_budget_reset_at: datetime | None,
        prev_row: ProjectCostTracking | None,
        snapshot_at: datetime,
    ) -> tuple[Decimal, Decimal]:
        """Compute budget-reset-aware delta and lifetime cumulative spend.

        Args:
            current_budget_period_spend: LiteLLM spend for the current budget window.
            current_budget_reset_at: Timestamp of the next budget reset, if provided.
            prev_row: Most recent stored row for this key before the current snapshot.
            snapshot_at: Current snapshot timestamp.

        Returns:
            Tuple of ``(daily_spend, cumulative_spend)``.
        """
        current_budget_period_spend = self._quantize_spend(current_budget_period_spend)

        if prev_row is None:
            logger.debug(
                "Bootstrap run — no prior row; using current budget-period spend as initial daily/cumulative spend"
            )
            return current_budget_period_spend, current_budget_period_spend

        prev_budget_period_spend = self._quantize_spend(prev_row.budget_period_spend)
        prev_cumulative_spend = self._quantize_spend(prev_row.cumulative_spend)
        reset_happened = self._did_budget_reset(prev_row, current_budget_reset_at, snapshot_at)

        if reset_happened:
            daily_spend = current_budget_period_spend
        elif current_budget_period_spend >= prev_budget_period_spend:
            daily_spend = current_budget_period_spend - prev_budget_period_spend
        else:
            logger.warning(
                f"Budget-period spend decreased without reset metadata change for project "
                f"{prev_row.project_name!r}: current={current_budget_period_spend} "
                f"< prev={prev_budget_period_spend}; treating as reset"
            )
            daily_spend = current_budget_period_spend

        daily_spend = self._quantize_spend(daily_spend)
        cumulative_spend = self._quantize_spend(prev_cumulative_spend + daily_spend)
        if cumulative_spend < prev_cumulative_spend:
            raise InvalidSpendSnapshotError(
                f"cumulative spend decreased: computed={cumulative_spend} < prev={prev_cumulative_spend}"
            )

        return daily_spend, cumulative_spend

    @staticmethod
    def _did_budget_reset(
        prev_row: ProjectCostTracking,
        current_budget_reset_at: datetime | None,
        snapshot_at: datetime,
    ) -> bool:
        """Return True when the previous budget window ended before this snapshot."""
        prev_budget_reset_at = prev_row.budget_reset_at
        if prev_budget_reset_at is None:
            return False

        if snapshot_at < prev_budget_reset_at:
            return False

        if current_budget_reset_at is None:
            return True

        return current_budget_reset_at != prev_budget_reset_at

    @staticmethod
    def _extract_budget_period_spend(spending_payload: dict) -> Decimal:
        """Extract current budget-period spend from normalized or raw LiteLLM payloads."""
        if "total_spend" in spending_payload:
            return Decimal(str(spending_payload.get("total_spend", 0)))

        info = spending_payload.get("info") or {}
        return Decimal(str(info.get("spend", 0)))

    @classmethod
    def _extract_budget_reset_at(cls, spending_payload: dict) -> datetime | None:
        """Extract budget reset timestamp from normalized or raw LiteLLM payloads."""
        if "budget_reset_at" in spending_payload:
            return cls._parse_optional_datetime(spending_payload.get("budget_reset_at"))

        info = spending_payload.get("info") or {}
        return cls._parse_optional_datetime(info.get("budget_reset_at"))

    @staticmethod
    def _parse_optional_datetime(value: str | None) -> datetime | None:
        """Parse ISO 8601 datetime strings returned by LiteLLM."""
        if not value:
            return None

        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    @classmethod
    def _quantize_spend(cls, value: Decimal) -> Decimal:
        """Normalize spend values to the DB precision before comparisons and persistence."""
        return value.quantize(cls._SPEND_PRECISION, rounding=ROUND_HALF_UP)

    @staticmethod
    def _hash_key(api_key: str) -> str:
        """Return SHA-256 hex digest of api_key."""
        return hashlib.sha256(api_key.encode()).hexdigest()
