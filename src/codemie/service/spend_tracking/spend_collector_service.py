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

import hashlib
import re
from datetime import date, datetime, time, timedelta, timezone

from dateutil.relativedelta import relativedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from codemie.clients.postgres import get_async_session
from codemie.configs import config, logger
from codemie.repository.application_repository import ApplicationRepository
from codemie.repository.budget_repository import budget_repository
from codemie.repository.project_budget_repository import project_member_budget_assignment_repository
from codemie.repository.project_spend_tracking_repository import ProjectSpendTrackingRepository
from codemie.service.budget.budget_models import Budget
from codemie.service.budget.provider import PersonalSpendEntry, ProjectBudgetSpendSnapshot
from codemie.service.budget.provider_registry import get_active_provider
from codemie.service.spend_tracking.spend_models import ProjectSpendTracking


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

        budget_count = await self._collect_budget_based(target_snapshot_at)
        project_budget_count = await self._collect_provider_project_budgets(target_snapshot_at)

        total = budget_count + project_budget_count
        logger.info(
            f"Spend collection for {target_snapshot_at} complete: "
            f"{budget_count} budget rows + "
            f"{project_budget_count} project/member budget rows = {total} total"
        )
        return total

    async def collect_member_budget_reset_window(
        self,
        snapshot_at: datetime | None = None,
    ) -> int:
        """Collect member-budget spend only for allocations whose budgets reset within the configured window."""
        target_snapshot_at = snapshot_at or datetime.now(timezone.utc)
        window_end = target_snapshot_at + timedelta(minutes=config.LITELLM_BUDGET_RESET_WINDOW_MINUTES)
        logger.info(
            "Starting member budget reset-window collection for "
            f"{target_snapshot_at.isoformat(timespec='milliseconds')}.."
            f"{window_end.isoformat(timespec='milliseconds')}"
        )

        async with get_async_session() as session:
            eligible_allocations = await (
                project_member_budget_assignment_repository.get_allocations_resetting_within_window(
                    session=session,
                    window_start=target_snapshot_at,
                    window_end=window_end,
                )
            )
            if not eligible_allocations:
                logger.info("No member budget allocations found in reset window; skipping collection")
                return 0

            provider_member_refs = {
                row.provider_metadata.get("provider_member_ref")
                for row in eligible_allocations
                if row.provider_metadata.get("provider_member_ref")
            }
            if not provider_member_refs:
                logger.info("No provider member refs found for reset-window allocations; skipping collection")
                return 0

            member_snapshots = await get_active_provider().collect_member_budget_spend_for_refs(provider_member_refs)
            if not member_snapshots:
                logger.info("Provider returned no reset-window member spend snapshots")
                return 0

            budgets_map = await budget_repository.get_all_keyed_by_id(session)
            prev_rows = await self._tracking_repository.get_latest_before_by_member_budget_ids(
                session,
                [(s.project_name, s.budget_id, s.user_id) for s in member_snapshots],
                target_snapshot_at,
            )

            rows_to_insert: list[ProjectSpendTracking] = []
            for snapshot in member_snapshots:
                budget = budgets_map.get(snapshot.budget_id)
                prev_row = prev_rows.get((snapshot.project_name, snapshot.budget_id, snapshot.user_id))
                daily_spend, cumulative_spend = self._compute_spend_snapshot(
                    current_budget_period_spend=self._quantize_spend(snapshot.spend),
                    prev_row=prev_row,
                    snapshot_at=target_snapshot_at,
                    budget=budget,
                )
                effective_cumulative = self._quantize_spend(
                    snapshot.cumulative_spend if snapshot.cumulative_spend is not None else cumulative_spend
                )
                if daily_spend == Decimal("0"):
                    logger.debug(
                        f"Member '{snapshot.user_id}' project '{snapshot.project_name}' "
                        f"budget_id={snapshot.budget_id!r} has zero delta; skipping snapshot"
                    )
                    continue
                rows_to_insert.append(
                    ProjectSpendTracking(
                        id=uuid4(),
                        project_name=snapshot.project_name,
                        spend_date=target_snapshot_at,
                        daily_spend=daily_spend,
                        cumulative_spend=effective_cumulative,
                        budget_period_spend=self._quantize_spend(snapshot.spend),
                        budget_id=snapshot.budget_id,
                        budget_category=snapshot.budget_category.value,
                        user_id=snapshot.user_id,
                        provider_subject_id=snapshot.provider_subject_id,
                        spend_subject_type="member_budget",
                    )
                )

            await self._tracking_repository.insert_member_budget_entries(session, rows_to_insert)
            logger.info(f"Inserted {len(rows_to_insert)} member budget reset-window row(s)")
            return len(rows_to_insert)

    async def _collect_provider_project_budgets(self, target_snapshot_at: datetime) -> int:
        """Collect provider-neutral project/member budget snapshots.

        A project budget whose newest row is dated at or after ``target_snapshot_at`` was
        refreshed live before the lock was granted. That row already holds the spend, so the
        snapshot is skipped rather than counted a second time.
        """
        provider = get_active_provider()

        async with get_async_session() as session:
            await self._tracking_repository.lock_all_project_budget_refresh(session)
            project_snapshots = await provider.collect_project_budget_spend()
            member_snapshots = await provider.collect_member_budget_spend()
            if not project_snapshots and not member_snapshots:
                return 0

            budgets_map = await budget_repository.get_all_keyed_by_id(session)
            project_rows: list[ProjectSpendTracking] = []
            project_pairs = [(s.project_name, s.budget_id) for s in project_snapshots]
            project_prev = await self._tracking_repository.get_latest_before_by_project_budget_ids(
                session,
                project_pairs,
                datetime.now(timezone.utc),
                spend_subject_type="project_budget",
            )
            for snapshot in project_snapshots:
                row = self._project_budget_row(
                    snapshot,
                    project_prev.get((snapshot.project_name, snapshot.budget_id)),
                    budgets_map.get(snapshot.budget_id),
                    target_snapshot_at,
                )
                if row is not None:
                    project_rows.append(row)

            member_rows: list[ProjectSpendTracking] = []
            member_triples = [(s.project_name, s.budget_id, s.user_id) for s in member_snapshots]
            member_prev = await self._tracking_repository.get_latest_before_by_member_budget_ids(
                session,
                member_triples,
                target_snapshot_at,
            )
            for snapshot in member_snapshots:
                budget = budgets_map.get(snapshot.budget_id)
                prev_row = member_prev.get((snapshot.project_name, snapshot.budget_id, snapshot.user_id))
                daily_spend, cumulative_spend = self._compute_spend_snapshot(
                    current_budget_period_spend=self._quantize_spend(snapshot.spend),
                    prev_row=prev_row,
                    snapshot_at=target_snapshot_at,
                    budget=budget,
                )
                effective_cumulative = self._quantize_spend(
                    snapshot.cumulative_spend if snapshot.cumulative_spend is not None else cumulative_spend
                )
                if daily_spend == Decimal("0"):
                    logger.debug(
                        f"Member '{snapshot.user_id}' project '{snapshot.project_name}' "
                        f"budget_id={snapshot.budget_id!r} has zero delta; skipping snapshot"
                    )
                    continue
                member_rows.append(
                    ProjectSpendTracking(
                        id=uuid4(),
                        project_name=snapshot.project_name,
                        spend_date=target_snapshot_at,
                        daily_spend=daily_spend,
                        cumulative_spend=effective_cumulative,
                        budget_period_spend=self._quantize_spend(snapshot.spend),
                        budget_id=snapshot.budget_id,
                        budget_category=snapshot.budget_category.value,
                        user_id=snapshot.user_id,
                        provider_subject_id=snapshot.provider_subject_id,
                        spend_subject_type="member_budget",
                    )
                )

            await self._tracking_repository.insert_project_budget_entries(session, project_rows)
            await self._tracking_repository.insert_member_budget_entries(session, member_rows)
            return len(project_rows) + len(member_rows)

    def _project_budget_row(
        self,
        snapshot: ProjectBudgetSpendSnapshot,
        prev_row: ProjectSpendTracking | None,
        budget: Budget | None,
        target_snapshot_at: datetime,
    ) -> ProjectSpendTracking | None:
        """Return the project_budget row for one snapshot, or None when it must not be written."""
        if prev_row is not None and self._as_utc(prev_row.spend_date) >= target_snapshot_at:
            logger.debug(
                f"Project '{snapshot.project_name}' budget_id={snapshot.budget_id!r} "
                f"was refreshed after the snapshot time; skipping snapshot"
            )
            return None
        daily_spend, cumulative_spend = self._compute_spend_snapshot(
            current_budget_period_spend=self._quantize_spend(snapshot.spend),
            prev_row=prev_row,
            snapshot_at=target_snapshot_at,
            budget=budget,
        )
        effective_cumulative = self._quantize_spend(
            snapshot.cumulative_spend if snapshot.cumulative_spend is not None else cumulative_spend
        )
        if daily_spend == Decimal("0") and not self.is_reset_transition(
            prev_row, budget, self._quantize_spend(snapshot.spend), target_snapshot_at
        ):
            logger.debug(
                f"Project '{snapshot.project_name}' budget_id={snapshot.budget_id!r} has zero delta; skipping snapshot"
            )
            return None
        return ProjectSpendTracking(
            id=uuid4(),
            project_name=snapshot.project_name,
            spend_date=target_snapshot_at,
            daily_spend=daily_spend,
            cumulative_spend=effective_cumulative,
            budget_period_spend=self._quantize_spend(snapshot.spend),
            budget_id=snapshot.budget_id,
            budget_category=snapshot.budget_category.value,
            provider_subject_id=snapshot.provider_subject_id,
            spend_subject_type="project_budget",
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
        personal_entries = await get_active_provider().collect_personal_spend()
        if not personal_entries:
            logger.info("No personal spend entries from provider; budget-based spend collection skipped")
            return 0

        entries_with_budget_id = [e for e in personal_entries if e.budget_id]
        if not entries_with_budget_id:
            logger.info("No personal spend entries with budget_id; budget-based spend collection skipped")
            return 0

        logger.info(f"Processing {len(entries_with_budget_id)} personal spend entries")

        async with get_async_session() as session:
            budgets_map = await budget_repository.get_all_keyed_by_id(session)
            logger.debug(f"Loaded {len(budgets_map)} budget(s) for reset detection")
            deduped_entries = self._dedupe_personal_entries(entries_with_budget_id, budgets_map)
            project_category_pairs = [(entry.user_identifier, entry.budget_category) for entry in deduped_entries]

            prev_rows = await self._tracking_repository.get_latest_before_by_budget_category_ids(
                session,
                project_category_pairs,
                target_snapshot_at,
            )
            logger.debug(f"Loaded {len(prev_rows)} prior budget baseline rows for delta calculation")

            rows_to_insert: list[ProjectSpendTracking] = []
            for entry in deduped_entries:
                project_name = entry.user_identifier
                current_budget_period_spend = self._quantize_spend(entry.spend)
                budget = budgets_map.get(entry.budget_id)
                prev_row = prev_rows.get((project_name, entry.budget_category))

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
                        budget_category=entry.budget_category,
                        spend_subject_type="budget",
                    )
                )

            skipped = len(deduped_entries) - len(rows_to_insert)
            logger.info(f"Inserting {len(rows_to_insert)} budget row(s) for {target_snapshot_at} (skipped: {skipped})")
            await self._tracking_repository.insert_budget_entries(session, rows_to_insert)

        return len(rows_to_insert)

    @staticmethod
    def _dedupe_personal_entries(
        entries: list[PersonalSpendEntry],
        budgets_map: dict[str, Budget],
    ) -> list[PersonalSpendEntry]:
        """Collapse provider duplicates to one row per budget snapshot conflict key."""
        deduped: dict[tuple[str, str, str], PersonalSpendEntry] = {}
        categories_by_budget_assignment: dict[tuple[str, str], set[str]] = {}
        duplicate_count = 0

        for entry in entries:
            assignment_key = (entry.user_identifier, entry.budget_id)
            categories_by_budget_assignment.setdefault(assignment_key, set()).add(entry.budget_category)

            budget = budgets_map.get(entry.budget_id)
            resolved_category = budget.budget_category if budget else entry.budget_category
            key = (entry.user_identifier, entry.budget_id, resolved_category)
            normalized_entry = entry.model_copy(update={"budget_category": resolved_category})
            existing = deduped.get(key)

            if existing is None:
                deduped[key] = normalized_entry
                continue

            duplicate_count += 1
            if normalized_entry.spend >= existing.spend:
                deduped[key] = normalized_entry

        for (user_identifier, budget_id), categories in categories_by_budget_assignment.items():
            if len(categories) < 2:
                continue

            budget = budgets_map.get(budget_id)
            resolved_category = budget.budget_category if budget else None
            logger.warning(
                f"Personal spend provider returned multiple categories for user_identifier={user_identifier!r} "
                f"budget_id={budget_id!r}: categories={sorted(categories)!r}, "
                f"resolved_category={resolved_category!r}"
            )

        if duplicate_count:
            logger.warning(
                f"Collapsed {duplicate_count} duplicate personal spend entr"
                f"{'y' if duplicate_count == 1 else 'ies'} into {len(deduped)} unique budget snapshot keys"
            )

        return list(deduped.values())

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
                "Bootstrap run - no prior row; using current budget-period spend as initial daily/cumulative spend"
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
    def _parse_budget_duration_to_delta(duration: str | None) -> timedelta | relativedelta | None:
        """Convert a LiteLLM budget_duration string to a timedelta or relativedelta.

        Calendar-period durations (``monthly``, ``yearly``) return a
        ``relativedelta`` so that subtraction from a ``datetime`` respects the
        actual calendar boundary rather than a fixed day count.  All other
        durations return a plain ``timedelta``.

        Args:
            duration: Budget duration string, e.g. ``"30d"``, ``"monthly"``.

        Returns:
            Corresponding delta, or None if the string is unrecognised.
        """
        if not duration:
            return None
        duration = duration.strip().lower()
        _named: dict[str, timedelta | relativedelta] = {
            "daily": timedelta(days=1),
            "weekly": timedelta(weeks=1),
            "monthly": relativedelta(months=1),
            "30d": relativedelta(months=1),
            "yearly": relativedelta(years=1),
        }
        if duration in _named:
            return _named[duration]
        m = re.match(r"^(\d+)d$", duration)
        if m:
            return timedelta(days=int(m.group(1)))
        return None

    @staticmethod
    def is_reset_transition(
        prev_row: ProjectSpendTracking | None,
        budget: Budget | None,
        fresh_spend: Decimal,
        snapshot_at: datetime,
    ) -> bool:
        """Return True when the provider counter dropped from a non-zero value.

        Separates the two cases that both compute a zero delta: spend that has not moved,
        which is correctly skipped, and a counter that reset, which must be recorded or the
        previous value stays newest and reads as current spend.
        """
        if prev_row is None:
            return False
        prev_period = LiteLLMSpendCollectorService._quantize_spend(prev_row.budget_period_spend)
        if prev_period <= Decimal("0"):
            return False
        return (
            LiteLLMSpendCollectorService._did_budget_reset(prev_row, budget, snapshot_at)
            or LiteLLMSpendCollectorService._quantize_spend(fresh_spend) < prev_period
        )

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
        as_utc = LiteLLMSpendCollectorService._as_utc

        return as_utc(prev_row.spend_date) < last_reset <= as_utc(snapshot_at)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """Return ``value`` as an aware datetime, reading a naive one as UTC."""
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

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
