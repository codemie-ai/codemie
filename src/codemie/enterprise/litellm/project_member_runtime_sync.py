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
from codemie.repository.budget_repository import budget_repository
from codemie.repository.project_budget_repository import (
    project_member_budget_assignment_repository,
)
from codemie.service.budget.budget_enums import BudgetCategory, BudgetScope, SyncStatus
from codemie.service.budget.budget_models import build_override_project_budget_id, build_shared_project_budget_id
from codemie.service.budget.budget_resolution_service import _resolution_cache, budget_resolution_service
from codemie.service.budget.provider import BudgetProviderMemberState
from codemie.service.budget.provider_registry import get_active_provider

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
    _ = user_email
    from codemie.service.settings.settings import SettingsService

    if not SettingsService.get_project_member_budget_tracking_enabled(project_name):
        return

    async with get_async_session() as session:
        resolved = await budget_resolution_service.resolve(
            session,
            user_id=user_id,
            project_name=project_name,
            budget_category=budget_category,
        )
        if resolved.scope != BudgetScope.PROJECT:
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
            return

        allocation = await project_member_budget_assignment_repository.get_active_by_project_category_user(
            session,
            project_name=project_name,
            budget_category=budget_category.value,
            user_id=user_id,
        )
        if allocation is None:
            raise RuntimeError(
                f"Project member allocation missing for project={project_name!r}, "
                f"budget_category={budget_category.value!r}, user_id={user_id!r}"
            )
        if resolved.budget_id is None:
            raise RuntimeError(
                f"Resolved project budget context missing budget_id for project={project_name!r}, "
                f"budget_category={budget_category.value!r}, user_id={user_id!r}"
            )

        budget = await budget_repository.get_by_id(session, resolved.budget_id)
        if budget is None:
            raise RuntimeError(
                f"Budget not found for budget_id={resolved.budget_id!r}, "
                f"project={project_name!r}, budget_category={budget_category.value!r}, user_id={user_id!r}"
            )

        effective_budget_id = _effective_budget_id_for_member(
            budget_id=resolved.budget_id,
            user_id=user_id,
            resolved=resolved,
            allocation=allocation,
            current_provider_budget_id=current_provider_budget_id,
        )
        allocation.effective_budget_id = effective_budget_id

        try:
            member_state = await get_active_provider().sync_member_allocation(allocation=allocation, budget=budget)
        except Exception as exc:
            raise RuntimeError(
                f"Provider member sync failed for project={project_name!r}, "
                f"budget_category={budget_category.value!r}, user_id={user_id!r}: {exc}"
            ) from exc

        sync_status = str(member_state.sync_status)
        if sync_status not in _ALLOWED_SYNC_STATUSES:
            raise RuntimeError(
                f"Provider member sync returned unexpected sync_status={sync_status!r} "
                f"for project={project_name!r}, budget_category={budget_category.value!r}, user_id={user_id!r}"
            )
        if not member_state.provider_budget_id:
            raise RuntimeError(
                f"Provider member sync missing provider_budget_id for project={project_name!r}, "
                f"budget_category={budget_category.value!r}, user_id={user_id!r}"
            )

        await project_member_budget_assignment_repository.update_provider_metadata(
            session,
            allocation_id=allocation.id,
            provider_metadata=_build_member_provider_metadata(member_state),
            sync_status=sync_status,
            budget_reset_at=member_state.budget_reset_at,
        )

        _resolution_cache.pop((project_name, budget_category.value, user_id), None)
        await session.commit()


def ensure_project_member_runtime_ready_sync(
    *,
    user_id: str,
    user_email: str,
    project_name: str,
    budget_category: BudgetCategory,
) -> None:
    _ = user_email
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(
            ensure_project_member_runtime_ready(
                user_id=user_id,
                user_email=user_email,
                project_name=project_name,
                budget_category=budget_category,
            )
        )
        return

    thread_error: list[Exception] = []

    def _run_in_thread() -> None:
        try:
            asyncio.run(
                ensure_project_member_runtime_ready(
                    user_id=user_id,
                    user_email=user_email,
                    project_name=project_name,
                    budget_category=budget_category,
                )
            )
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
