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
import threading
from datetime import datetime, timezone
from typing import Any

from codemie.clients.postgres import get_async_session
from codemie.configs import logger
from codemie.repository.budget_repository import budget_repository
from codemie.repository.project_budget_repository import (
    project_member_budget_assignment_repository,
)
from codemie.service.budget.budget_enums import BudgetCategory, BudgetScope, SyncStatus
from codemie.service.budget.budget_models import build_override_project_budget_id, build_shared_project_budget_id
from codemie.service.budget.budget_resolution_service import _resolution_cache, budget_resolution_service
from codemie.service.budget.provider import BudgetProviderMemberState
from codemie.service.budget.provider_registry import get_active_provider

_main_event_loop: asyncio.AbstractEventLoop | None = None


def set_main_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_event_loop
    _main_event_loop = loop


_ALLOWED_SYNC_STATUSES = {SyncStatus.OK.value, SyncStatus.NOOP.value}


def _metadata_value(metadata: dict[str, Any], key: str) -> Any:
    if key in metadata:
        return metadata[key]
    raw = metadata.get("raw")
    if isinstance(raw, dict):
        return raw.get(key)
    return None


def _build_member_provider_metadata(member_state: BudgetProviderMemberState) -> dict[str, Any]:
    raw = dict(member_state.metadata or {})
    if member_state.provider_member_ref is not None:
        raw["provider_member_ref"] = member_state.provider_member_ref
    if member_state.provider_budget_id is not None:
        raw["provider_budget_id"] = member_state.provider_budget_id
    return {
        "provider": member_state.provider,
        "last_synced_at": datetime.now(tz=timezone.utc).isoformat(),
        "sync_status": member_state.sync_status,
        "raw": raw,
    }


def _effective_budget_id_for_member(
    *,
    budget_id: str,
    user_id: str,
    resolved,
    allocation: Any | None = None,
    current_provider_budget_id: str | None = None,
) -> str:
    effective_budget_id = resolved.effective_budget_id or current_provider_budget_id
    if effective_budget_id:
        return effective_budget_id

    override_budget_id = getattr(resolved, "override_budget_id", None)
    if override_budget_id:
        return override_budget_id

    shared_budget_id = getattr(resolved, "shared_budget_id", None)
    if shared_budget_id:
        return shared_budget_id

    if getattr(allocation, "allocation_mode", None) == "fixed":
        return build_override_project_budget_id(budget_id, user_id)
    return build_shared_project_budget_id(budget_id)


async def ensure_project_member_runtime_ready(
    *,
    user_id: str,
    user_email: str,
    project_name: str,
    budget_category: BudgetCategory,
) -> None:
    from codemie.service.settings.settings import SettingsService

    logger.debug(
        f"budget_event=runtime_member_sync_check_started component=project_member_runtime_sync "
        f"user_id={user_id!r} username={user_email!r} project_name={project_name!r} "
        f"budget_category={budget_category.value!r}"
    )
    async with get_async_session() as session:
        resolved = await budget_resolution_service.resolve(
            session,
            user_id=user_id,
            project_name=project_name,
            budget_category=budget_category,
        )
        if resolved.scope != BudgetScope.PROJECT:
            logger.warning(
                f"budget_event=runtime_member_sync_skipped component=project_member_runtime_sync "
                f"user_id={user_id!r} username={user_email!r} project_name={project_name!r} "
                f"budget_category={budget_category.value!r} scope={resolved.scope.value!r} reason=global_scope "
                f"hint=project_budget_not_resolved_for_user"
            )
            return

        current_provider_member_ref = _metadata_value(resolved.member_provider_metadata, "provider_member_ref")
        current_provider_budget_id = _metadata_value(resolved.member_provider_metadata, "provider_budget_id")
        expected_budget_id = resolved.effective_budget_id or current_provider_budget_id
        if (
            current_provider_member_ref
            and current_provider_budget_id
            and expected_budget_id
            and current_provider_budget_id == expected_budget_id
        ):
            logger.debug(
                f"budget_event=runtime_member_sync_skipped component=project_member_runtime_sync "
                f"user_id={user_id!r} username={user_email!r} project_name={project_name!r} "
                f"budget_id={resolved.budget_id!r} budget_category={budget_category.value!r} "
                f"provider_member_ref={current_provider_member_ref!r} "
                f"provider_budget_id={current_provider_budget_id!r} reason=provider_metadata_current"
            )
            return

        allocation = await project_member_budget_assignment_repository.get_active_by_project_category_user(
            session,
            project_name=project_name,
            budget_category=budget_category.value,
            user_id=user_id,
        )
        if allocation is None:
            logger.error(
                f"budget_event=runtime_member_sync_failed component=project_member_runtime_sync "
                f"user_id={user_id!r} username={user_email!r} project_name={project_name!r} "
                f"budget_id={resolved.budget_id!r} budget_category={budget_category.value!r} "
                f"reason=missing_allocation error=None"
            )
            raise RuntimeError(
                f"Project member allocation missing for project={project_name!r}, "
                f"budget_category={budget_category.value!r}, user_id={user_id!r}"
            )
        if resolved.budget_id is None:
            logger.error(
                f"budget_event=runtime_member_sync_failed component=project_member_runtime_sync "
                f"user_id={user_id!r} username={user_email!r} project_name={project_name!r} "
                f"budget_id={resolved.budget_id!r} budget_category={budget_category.value!r} "
                f"reason=missing_resolved_budget_id error=None"
            )
            raise RuntimeError(
                f"Resolved project budget context missing budget_id for project={project_name!r}, "
                f"budget_category={budget_category.value!r}, user_id={user_id!r}"
            )

        budget = await budget_repository.get_by_id(session, resolved.budget_id)
        if budget is None:
            logger.error(
                f"budget_event=runtime_member_sync_failed component=project_member_runtime_sync "
                f"user_id={user_id!r} username={user_email!r} project_name={project_name!r} "
                f"budget_id={resolved.budget_id!r} budget_category={budget_category.value!r} "
                f"reason=missing_budget_row error=None"
            )
            raise RuntimeError(
                f"Budget not found for budget_id={resolved.budget_id!r}, "
                f"project={project_name!r}, budget_category={budget_category.value!r}, user_id={user_id!r}"
            )

        enforce_limit = SettingsService.get_enforce_member_spend_limits(project_name)
        effective_max_budget = allocation.allocated_max_budget if enforce_limit else budget.max_budget

        effective_budget_id = _effective_budget_id_for_member(
            budget_id=resolved.budget_id,
            user_id=user_id,
            resolved=resolved,
            allocation=allocation,
            current_provider_budget_id=current_provider_budget_id,
        )
        allocation.effective_budget_id = effective_budget_id

        try:
            logger.debug(
                f"budget_event=runtime_member_sync_started component=project_member_runtime_sync "
                f"user_id={user_id!r} username={user_email!r} project_name={project_name!r} "
                f"budget_id={resolved.budget_id!r} effective_budget_id={effective_budget_id!r} "
                f"budget_category={budget_category.value!r} allocation_id={allocation.id!r}"
            )
            member_state = await get_active_provider().sync_member_allocation(
                allocation=allocation,
                budget=budget,
                effective_max_budget=effective_max_budget,
            )
        except Exception as exc:
            logger.error(
                f"budget_event=runtime_member_sync_failed component=project_member_runtime_sync "
                f"user_id={user_id!r} username={user_email!r} project_name={project_name!r} "
                f"budget_id={resolved.budget_id!r} budget_category={budget_category.value!r} "
                f"reason=provider_sync_failed error={exc}",
                exc_info=True,
            )
            raise RuntimeError(
                f"Provider member sync failed for project={project_name!r}, "
                f"budget_category={budget_category.value!r}, user_id={user_id!r}: {exc}"
            ) from exc

        sync_status = str(member_state.sync_status)
        if sync_status not in _ALLOWED_SYNC_STATUSES:
            logger.error(
                f"budget_event=runtime_member_sync_failed component=project_member_runtime_sync "
                f"user_id={user_id!r} username={user_email!r} project_name={project_name!r} "
                f"budget_id={resolved.budget_id!r} budget_category={budget_category.value!r} "
                f"reason=unexpected_sync_status sync_status={sync_status!r} error=None"
            )
            raise RuntimeError(
                f"Provider member sync returned unexpected sync_status={sync_status!r} "
                f"for project={project_name!r}, budget_category={budget_category.value!r}, user_id={user_id!r}"
            )
        if not member_state.provider_budget_id:
            logger.error(
                f"budget_event=runtime_member_sync_failed component=project_member_runtime_sync "
                f"user_id={user_id!r} username={user_email!r} project_name={project_name!r} "
                f"budget_id={resolved.budget_id!r} budget_category={budget_category.value!r} "
                f"reason=missing_provider_budget_id sync_status={sync_status!r} error=None"
            )
            raise RuntimeError(
                f"Provider member sync missing provider_budget_id for project={project_name!r}, "
                f"budget_category={budget_category.value!r}, user_id={user_id!r}"
            )

        allocation_id = allocation.id
        await project_member_budget_assignment_repository.update_provider_metadata(
            session,
            allocation_id=allocation_id,
            provider_metadata=_build_member_provider_metadata(member_state),
            sync_status=sync_status,
            budget_reset_at=member_state.budget_reset_at,
        )

        _resolution_cache.pop((project_name, budget_category.value, user_id), None)
        await session.commit()
        logger.debug(
            f"budget_event=runtime_member_sync_completed component=project_member_runtime_sync "
            f"user_id={user_id!r} username={user_email!r} project_name={project_name!r} "
            f"budget_id={resolved.budget_id!r} effective_budget_id={effective_budget_id!r} "
            f"budget_category={budget_category.value!r} allocation_id={allocation_id!r} "
            f"provider={member_state.provider!r} provider_member_ref={member_state.provider_member_ref!r} "
            f"provider_budget_id={member_state.provider_budget_id!r} sync_status={sync_status!r} "
            f"cache_invalidated=true"
        )


async def resync_project_member_allocations(project_name: str, enforce_limit: bool) -> None:
    """Re-sync max_budget for all already-synced member allocations after enforcement flag toggle.

    Skips allocations that have never been synced (no provider_member_ref) — they will
    pick up the new enforcement state lazily on their first LLM request.
    """
    provider = get_active_provider()
    async with get_async_session() as session:
        allocations = await project_member_budget_assignment_repository.get_active_by_project(
            session, project_name=project_name
        )
        updated = 0
        for allocation in allocations:
            provider_member_ref = _metadata_value(allocation.provider_metadata or {}, "provider_member_ref")
            if not provider_member_ref:
                continue
            budget = await budget_repository.get_by_id(session, allocation.project_budget_id)
            if budget is None:
                logger.warning(
                    f"budget_event=member_resync_skipped component=project_member_runtime_sync "
                    f"project_name={project_name!r} allocation_id={allocation.id!r} "
                    f"reason=budget_row_missing"
                )
                continue
            effective_max_budget = allocation.allocated_max_budget if enforce_limit else budget.max_budget
            try:
                member_state = await provider.sync_member_allocation(
                    allocation=allocation,
                    budget=budget,
                    effective_max_budget=effective_max_budget,
                )
            except Exception as exc:
                logger.warning(
                    f"budget_event=member_resync_failed component=project_member_runtime_sync "
                    f"project_name={project_name!r} user_id={allocation.user_id!r} "
                    f"allocation_id={allocation.id!r} error={exc}"
                )
                continue
            sync_status = str(member_state.sync_status)
            if sync_status not in _ALLOWED_SYNC_STATUSES:
                logger.warning(
                    f"budget_event=member_resync_unexpected_status component=project_member_runtime_sync "
                    f"project_name={project_name!r} allocation_id={allocation.id!r} sync_status={sync_status!r}"
                )
                continue
            metadata = _build_member_provider_metadata(member_state)
            await project_member_budget_assignment_repository.update_provider_metadata(
                session,
                allocation_id=allocation.id,
                provider_metadata=metadata,
                sync_status=sync_status,
                budget_reset_at=member_state.budget_reset_at,
            )
            updated += 1
        await session.commit()
    logger.info(
        f"budget_event=member_resync_completed component=project_member_runtime_sync "
        f"project_name={project_name!r} enforce_limit={enforce_limit} "
        f"total={len(allocations)} updated={updated}"
    )


def resync_project_member_allocations_sync(project_name: str, enforce_limit: bool) -> None:
    """Synchronous bridge for resync_project_member_allocations.

    Uses the same event-loop bridging pattern as ensure_project_member_runtime_ready_sync.
    """
    coro = resync_project_member_allocations(project_name=project_name, enforce_limit=enforce_limit)

    loop = _main_event_loop
    if loop is not None and loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        future.result(timeout=30)
        return

    thread_error: list[Exception] = []

    def _run_in_thread() -> None:
        try:
            asyncio.run(coro)
        except Exception as exc:
            thread_error.append(exc)

    worker = threading.Thread(
        target=_run_in_thread,
        name="project-member-resync",
        daemon=False,
    )
    worker.start()
    worker.join(timeout=30)

    if worker.is_alive():
        raise RuntimeError(f"resync_project_member_allocations timed out after 30 s for project_name={project_name!r}")
    if thread_error:
        raise thread_error[0]


def ensure_project_member_runtime_ready_sync(
    *,
    user_id: str,
    user_email: str,
    project_name: str,
    budget_category: BudgetCategory,
) -> None:
    coro = ensure_project_member_runtime_ready(
        user_id=user_id,
        user_email=user_email,
        project_name=project_name,
        budget_category=budget_category,
    )

    # Preferred path: dispatch to the main FastAPI event loop so that async DB
    # operations reuse the pool connections that are bound to that loop.  This
    # is necessary when called from a thread (e.g. asyncio.to_thread worker)
    # because asyncio.run() would create a *new* loop that cannot reuse the
    # pool's existing asyncpg connections.
    loop = _main_event_loop
    if loop is not None and loop.is_running():
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        future.result()
        return

    # Fallback (e.g. unit tests, or main loop not yet registered): run in a
    # fresh isolated thread so asyncio.run() starts with a clean loop context.
    thread_error: list[Exception] = []

    def _run_in_thread() -> None:
        try:
            asyncio.run(coro)
        except Exception as exc:  # pragma: no cover - covered via caller propagation.
            thread_error.append(exc)

    worker = threading.Thread(
        target=_run_in_thread,
        name="project-member-runtime-sync",
        daemon=False,
    )
    worker.start()
    worker.join()

    if thread_error:
        raise thread_error[0]
