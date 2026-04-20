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
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from codemie.clients.postgres import get_async_session
from codemie.configs import logger
from codemie.enterprise.litellm.budget_categories import derive_category_from_user_id
from codemie.enterprise.litellm.dependencies import get_all_keys_spending, get_customer_list_spending
from codemie.repository.application_repository import ApplicationRepository
from codemie.repository.budget_repository import budget_repository
from codemie.repository.cost_center_repository import cost_center_repository
from codemie.repository.project_spend_tracking_repository import ProjectSpendTrackingRepository
from codemie.rest_api.models.settings import CredentialTypes, LiteLLMCredentials, Settings
from codemie.service.budget.budget_models import Budget
from codemie.service.spend_tracking.spend_models import ProjectSpendTracking
from codemie.service.settings.settings import SettingsService


class InvalidSpendSnapshotError(ValueError):
    """Raised when a computed spend snapshot violates business invariants."""


class LiteLLMSpendCollectorService:
    """Orchestrates the daily LiteLLM spend collection cycle.

    Discovers active LiteLLM API keys, queries LiteLLM for per-key cumulative spend,
    computes budget-reset-aware daily spend deltas, and persists one row per key per day
    to the project_spend_tracking table.

    Also collects customer-list-based budget spend for personal projects via the
    /customer/list endpoint, producing budget rows alongside the key-based rows.
    """

    def __init__(
        self,
        app_repository: ApplicationRepository,
        tracking_repository: ProjectSpendTrackingRepository,
    ) -> None:
        self._app_repository = app_repository
        self._tracking_repository = tracking_repository

    _SPEND_PRECISION = Decimal("0.000000001")

    async def collect(self, target_date: date | datetime | None = None) -> int:
        """Run one full spend collection cycle for the target snapshot timestamp.

        Manages its own async session internally — no session is passed from the caller.
        Runs key-based collection first, then budget-based collection.

        Args:
            target_date: Snapshot date/datetime. Defaults to current UTC time.

        Returns:
            Total count of rows inserted across both collection paths.
        """
        target_snapshot_at = self._resolve_snapshot_at(target_date)
        logger.info(f"Starting spend collection for {target_snapshot_at.isoformat(timespec='milliseconds')}")

        key_count = await self._collect_key_based(target_snapshot_at)
        budget_count = await self._collect_budget_based(target_snapshot_at)

        total = key_count + budget_count
        logger.info(
            f"Spend collection for {target_snapshot_at} complete: "
            f"{key_count} key rows + {budget_count} budget rows = {total} total"
        )
        return total

    async def _collect_key_based(self, target_snapshot_at: datetime) -> int:
        """Run the key-based spend collection path.

        Loads active applications, decrypts API keys, queries LiteLLM per-key spend,
        and writes key rows into project_spend_tracking.

        Args:
            target_snapshot_at: Snapshot timestamp.

        Returns:
            Count of rows inserted.
        """
        async with get_async_session() as session:
            all_apps = await self._app_repository.aget_all_non_deleted(session)
            apps_by_name = {app.name: app for app in all_apps}
            cost_center_ids = [
                app.cost_center_id for app in all_apps if isinstance(getattr(app, "cost_center_id", None), UUID)
            ]
            cost_center_map = await cost_center_repository.aget_by_ids(session, cost_center_ids)

            project_names = [app.name for app in all_apps]
            if not project_names:
                logger.info("No projects found; key-based spend collection skipped")
                return 0

            settings_list = Settings.get_by_project_names(project_names, CredentialTypes.LITE_LLM)
            logger.debug(f"Loaded {len(settings_list)} LiteLLM settings for {len(project_names)} projects")

            key_details = self._build_key_details(settings_list)
            if not key_details:
                logger.info("No LiteLLM API keys found; key-based spend collection skipped")
                return 0

            logger.info(f"Querying LiteLLM spend for {len(key_details)} API key(s)")

            all_key_hashes = [key_hash for _, key_hash in key_details.values()]
            prev_rows = await self._tracking_repository.get_latest_before_by_key_hashes(
                session,
                all_key_hashes,
                target_snapshot_at,
            )
            logger.debug(f"Loaded {len(prev_rows)} prior key baseline rows for delta calculation")

            budgets_map = await budget_repository.get_all_keyed_by_id(session)
            logger.debug(f"Loaded {len(budgets_map)} budget(s) for reset detection")

            rows_to_insert: list[ProjectSpendTracking] = []
            for api_key, (project_name, key_hash) in key_details.items():
                row = await self._build_key_spend_row(
                    api_key,
                    project_name,
                    key_hash,
                    apps_by_name,
                    cost_center_map,
                    prev_rows,
                    target_snapshot_at,
                    budgets_map,
                )
                if row is not None:
                    rows_to_insert.append(row)

            skipped = len(key_details) - len(rows_to_insert)
            logger.info(f"Inserting {len(rows_to_insert)} key row(s) for {target_snapshot_at} (skipped: {skipped})")
            await self._tracking_repository.insert_key_entries(session, rows_to_insert)

        return len(rows_to_insert)

    def _build_key_details(self, settings_list: list) -> dict[str, tuple[str, str]]:
        """Build a mapping of api_key -> (project_name, key_hash) from decrypted LiteLLM settings.

        Args:
            settings_list: List of Settings objects for LiteLLM credentials.

        Returns:
            Dict mapping raw API key to (project_name, key_hash).
        """
        key_details: dict[str, tuple[str, str]] = {}
        for setting in settings_list:
            SettingsService._decrypt_credentials(setting)
            cred = SettingsService._build_credential_result(setting, SettingsService.LITELLM_FIELDS, LiteLLMCredentials)
            if cred and cred.api_key:
                key_hash = self._hash_key(cred.api_key)
                key_details[cred.api_key] = (setting.project_name, key_hash)
                logger.debug(f"Resolved API key for project '{setting.project_name}' (hash prefix: {key_hash[:8]}...)")
            else:
                logger.warning(f"No API key in LiteLLM setting {setting.id} for project {setting.project_name}")
        return key_details

    async def _build_key_spend_row(
        self,
        api_key: str,
        project_name: str,
        key_hash: str,
        apps_by_name: dict,
        cost_center_map: dict,
        prev_rows: dict[str, ProjectSpendTracking],
        target_snapshot_at: datetime,
        budgets_map: dict[str, Budget] | None = None,
    ) -> ProjectSpendTracking | None:
        """Fetch LiteLLM spend for one API key and build a tracking row, or return None to skip.

        Args:
            api_key: Raw LiteLLM API key.
            project_name: Project name associated with the key.
            key_hash: SHA-256 hex digest of the API key.
            apps_by_name: Application objects indexed by name, for cost center lookup.
            cost_center_map: Cost center objects indexed by ID.
            prev_rows: Most recent stored rows per key_hash, for delta calculation.
            target_snapshot_at: Snapshot timestamp.
            budgets_map: All budget rows keyed by budget_id, for reset detection.

        Returns:
            A ProjectSpendTracking row ready for insertion, or None if the entry should be skipped.
        """
        project = apps_by_name.get(project_name)
        project_cost_center_id = getattr(project, "cost_center_id", None)
        cost_center = cost_center_map.get(project_cost_center_id) if isinstance(project_cost_center_id, UUID) else None

        logger.debug(f"Querying LiteLLM spend for project '{project_name}' (hash prefix: {key_hash[:8]}...)")
        spending_result = await asyncio.to_thread(get_all_keys_spending, [api_key])
        if not spending_result:
            logger.warning(
                f"No spending data from LiteLLM for key (hash prefix: {key_hash[:8]}...) "
                f"in project {project_name}; skipping"
            )
            return None

        spending_payload = spending_result[0]
        current_budget_period_spend = self._extract_budget_period_spend(spending_payload)
        budget_id = spending_payload.get("budget_id")
        budget = budgets_map.get(budget_id) if (budgets_map and budget_id) else None
        prev_row = prev_rows.get(key_hash)

        try:
            daily_spend, cumulative_spend = self._compute_spend_snapshot(
                current_budget_period_spend=current_budget_period_spend,
                prev_row=prev_row,
                snapshot_at=target_snapshot_at,
                budget=budget,
            )
        except InvalidSpendSnapshotError as exc:
            logger.warning(f"Skipping invalid spend snapshot for project {project_name!r}: {exc}")
            return None

        logger.debug(
            f"Project '{project_name}' (hash prefix: {key_hash[:8]}...): "
            f"budget_period_spend={current_budget_period_spend}, "
            f"lifetime_cumulative={cumulative_spend}, "
            f"prev_budget_period={prev_row.budget_period_spend if prev_row else 'n/a'}, "
            f"daily_delta={daily_spend}"
        )

        if daily_spend == Decimal("0"):
            logger.debug(f"Project '{project_name}' (hash prefix: {key_hash[:8]}...) has zero delta; skipping snapshot")
            return None

        return ProjectSpendTracking(
            id=uuid4(),
            project_name=project_name,
            cost_center_id=cost_center.id if cost_center else None,
            cost_center_name=cost_center.name if cost_center else None,
            key_hash=key_hash,
            spend_date=target_snapshot_at,
            daily_spend=daily_spend,
            cumulative_spend=cumulative_spend,
            budget_period_spend=current_budget_period_spend,
            budget_id=spending_payload.get("budget_id"),
            budget_category=budget.budget_category if budget else None,
            spend_subject_type="key",
        )

    async def _collect_budget_based(self, target_snapshot_at: datetime) -> int:
        """Run the budget-based spend collection path using /customer/list.

        Fetches all customer budget entries from LiteLLM, normalizes user_id into
        project_name + budget_id, computes reset-aware deltas, and writes budget rows
        into project_spend_tracking.

        Args:
            target_snapshot_at: Snapshot timestamp.

        Returns:
            Count of rows inserted.
        """
        customer_entries = await asyncio.to_thread(get_customer_list_spending)
        if not customer_entries:
            logger.info("No customer budget entries from LiteLLM; budget-based spend collection skipped")
            return 0

        logger.info(f"Processing {len(customer_entries)} customer budget entries")

        async with get_async_session() as session:
            project_budget_category_triples = [
                (
                    self._normalize_project_name(entry.user_id, entry.budget_id),
                    entry.budget_id,
                    derive_category_from_user_id(entry.user_id).value,
                )
                for entry in customer_entries
            ]
            prev_rows = await self._tracking_repository.get_latest_before_by_project_budget_categories(
                session,
                project_budget_category_triples,
                target_snapshot_at,
            )
            logger.debug(f"Loaded {len(prev_rows)} prior budget baseline rows for delta calculation")

            budgets_map = await budget_repository.get_all_keyed_by_id(session)
            logger.debug(f"Loaded {len(budgets_map)} budget(s) for reset detection")

            rows_to_insert: list[ProjectSpendTracking] = []
            for entry in customer_entries:
                project_name = self._normalize_project_name(entry.user_id, entry.budget_id)
                budget_category = derive_category_from_user_id(entry.user_id).value
                current_budget_period_spend = self._quantize_spend(Decimal(str(entry.spend)))
                budget = budgets_map.get(entry.budget_id)
                prev_row = prev_rows.get((project_name, entry.budget_id, budget_category))

                try:
                    daily_spend, cumulative_spend = self._compute_spend_snapshot(
                        current_budget_period_spend=current_budget_period_spend,
                        prev_row=prev_row,
                        snapshot_at=target_snapshot_at,
                        budget=budget,
                    )
                except InvalidSpendSnapshotError as exc:
                    logger.warning(
                        f"Skipping invalid budget snapshot for project {project_name!r} "
                        f"budget_id={entry.budget_id!r}: {exc}"
                    )
                    continue

                logger.debug(
                    f"Budget project '{project_name}' budget_id={entry.budget_id!r}: "
                    f"budget_period_spend={current_budget_period_spend}, "
                    f"daily_delta={daily_spend}"
                )

                if daily_spend == Decimal("0"):
                    logger.debug(
                        f"Budget project '{project_name}' budget_id={entry.budget_id!r} "
                        f"has zero delta; skipping snapshot"
                    )
                    continue

                rows_to_insert.append(
                    ProjectSpendTracking(
                        id=uuid4(),
                        project_name=project_name,
                        cost_center_id=None,
                        cost_center_name=None,
                        key_hash=None,
                        spend_date=target_snapshot_at,
                        daily_spend=daily_spend,
                        cumulative_spend=cumulative_spend,
                        budget_period_spend=current_budget_period_spend,
                        budget_id=entry.budget_id,
                        budget_category=budget_category,
                        spend_subject_type="budget",
                    )
                )

            skipped = len(customer_entries) - len(rows_to_insert)
            logger.info(f"Inserting {len(rows_to_insert)} budget row(s) for {target_snapshot_at} (skipped: {skipped})")
            await self._tracking_repository.insert_budget_entries(session, rows_to_insert)

        return len(rows_to_insert)

    @staticmethod
    def _normalize_project_name(user_id: str, budget_id: str) -> str:
        """Derive canonical project_name (email) from LiteLLM customer user_id.

        Uses the stable ``_codemie_{category.value}`` suffixes defined by
        ``build_user_id()`` — independent of the operator-configurable budget_id.

        Examples:
            'alice@example.com', 'default'         -> 'alice@example.com'
            'alice@example.com_codemie_cli', *any* -> 'alice@example.com'
            'alice@example.com_codemie_premium_models', *any* -> 'alice@example.com'
        """
        from codemie.enterprise.litellm.budget_categories import BudgetCategory

        for category in BudgetCategory:
            if category == BudgetCategory.PLATFORM:
                continue
            suffix = f"_codemie_{category.value}"
            if user_id.endswith(suffix):
                return user_id[: -len(suffix)]
        return user_id

    def _compute_spend_snapshot(
        self,
        current_budget_period_spend: Decimal,
        prev_row: ProjectSpendTracking | None,
        snapshot_at: datetime,
        budget: Budget | None = None,
    ) -> tuple[Decimal, Decimal]:
        """Compute budget-reset-aware delta and lifetime cumulative spend.

        Args:
            current_budget_period_spend: LiteLLM spend for the current budget window.
            prev_row: Most recent stored row for this subject before the current snapshot.
            snapshot_at: Current snapshot timestamp.
            budget: Budget row from the budgets table, used for explicit reset detection.

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

        if self._did_budget_reset(prev_row, budget, snapshot_at):
            logger.debug(
                f"Budget reset detected for project {prev_row.project_name!r} via budget table; "
                f"using current period spend as daily delta"
            )
            daily_spend = current_budget_period_spend
        elif current_budget_period_spend >= prev_budget_period_spend:
            daily_spend = current_budget_period_spend - prev_budget_period_spend
        else:
            logger.warning(
                f"Budget-period spend decreased for project {prev_row.project_name!r}: "
                f"current={current_budget_period_spend} < prev={prev_budget_period_spend}; treating as reset"
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
    def _parse_budget_duration_to_delta(duration: str | None) -> timedelta | None:
        """Convert a LiteLLM budget_duration string to a timedelta.

        Handles named durations (daily, weekly, monthly, yearly) and
        numeric-day patterns like ``7d`` or ``30d``.

        Args:
            duration: Budget duration string, e.g. ``"30d"``, ``"monthly"``.

        Returns:
            Corresponding timedelta, or None if the string is unrecognised.
        """
        if not duration:
            return None
        duration = duration.strip().lower()
        _named: dict[str, timedelta] = {
            "daily": timedelta(days=1),
            "weekly": timedelta(weeks=1),
            "monthly": timedelta(days=30),
            "yearly": timedelta(days=365),
        }
        if duration in _named:
            return _named[duration]
        m = re.match(r"^(\d+)d$", duration)
        if m:
            return timedelta(days=int(m.group(1)))
        return None

    @staticmethod
    def _did_budget_reset(
        prev_row: ProjectSpendTracking,
        budget: Budget | None,
        snapshot_at: datetime,
    ) -> bool:
        """Return True if a budget period reset occurred between prev_row and snapshot_at.

        Uses ``budget.budget_reset_at`` (next scheduled reset, from LiteLLM) and
        ``budget.budget_duration`` to compute the most-recent past reset instant
        (``last_reset = next_reset - duration``).  A reset is detected when that
        instant falls strictly after the previous snapshot and at or before now.

        Args:
            prev_row: Most recently stored tracking row for this subject.
            budget: Budget record from the budgets table, or None.
            snapshot_at: Current snapshot timestamp.

        Returns:
            True if a budget reset occurred in the window (prev_row.spend_date, snapshot_at].
        """
        if budget is None or not budget.budget_reset_at or not budget.budget_duration:
            return False

        next_reset = LiteLLMSpendCollectorService._parse_optional_datetime(budget.budget_reset_at)
        if next_reset is None:
            return False

        duration_delta = LiteLLMSpendCollectorService._parse_budget_duration_to_delta(budget.budget_duration)
        if duration_delta is None:
            return False

        last_reset = next_reset - duration_delta

        prev_spend_date = prev_row.spend_date
        if prev_spend_date.tzinfo is None:
            prev_spend_date = prev_spend_date.replace(tzinfo=timezone.utc)
        snap = snapshot_at if snapshot_at.tzinfo is not None else snapshot_at.replace(tzinfo=timezone.utc)

        return prev_spend_date < last_reset <= snap

    @staticmethod
    def _extract_budget_period_spend(spending_payload: dict) -> Decimal:
        """Extract current budget-period spend from normalized or raw LiteLLM payloads."""
        if "total_spend" in spending_payload:
            return Decimal(str(spending_payload.get("total_spend", 0)))

        info = spending_payload.get("info") or {}
        return Decimal(str(info.get("spend", 0)))

    @staticmethod
    def _extract_optional_decimal(payload: dict, key: str) -> Decimal | None:
        """Extract an optional Decimal field from a payload dict."""
        value = payload.get(key)
        if value is None:
            return None
        return Decimal(str(value))

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

    @staticmethod
    def _resolve_snapshot_at(target_date: date | datetime | None) -> datetime:
        """Resolve target_date argument to a timezone-aware datetime."""
        if target_date is None:
            return datetime.now(timezone.utc)
        if isinstance(target_date, datetime):
            return target_date if target_date.tzinfo is not None else target_date.replace(tzinfo=timezone.utc)
        return datetime.combine(target_date, time.min, tzinfo=timezone.utc)
