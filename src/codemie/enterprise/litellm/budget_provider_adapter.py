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

"""LiteLLM implementation of BudgetEnforcementProvider.

This module is the single integration boundary between core budget logic and
LiteLLM.  All LiteLLM-specific details (customer id construction, budget_id
mapping, spend-reset semantics) are confined here.

Core services MUST NOT import from codemie.enterprise.litellm directly for
budget operations — they must go through get_active_provider().
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from codemie.configs import logger
from codemie.service.budget.budget_enums import BudgetCategory, SyncStatus
from codemie.service.budget.budget_models import build_override_project_budget_id, build_shared_project_budget_id
from codemie.service.budget.provider import (
    BudgetProviderMemberState,
    BudgetProviderState,
    BudgetResetReconciliationItem,
    BudgetResetReconciliationResult,
    BudgetResetReconciliationTarget,
    BudgetRuntimeContext,
    BudgetRuntimeProviderResult,
    GlobalBudgetState,
    MemberBudgetSpendSnapshot,
    PersonalBudgetEntry,
    PersonalSpendEntry,
    ProjectBudgetSpendSnapshot,
    ProjectBudgetState,
)

if TYPE_CHECKING:
    from codemie_enterprise.litellm import LiteLLMService

    from codemie.service.budget.budget_models import Budget, ProjectMemberBudgetAssignment

_PROVIDER_NAME = "litellm"
_PROJECT_SCOPED_CUSTOMER_PREFIX = "codemie:project:"
_PROJECT_KEY_ALIAS_PREFIX = "codemie:project:"


def _metadata_value(metadata: dict[str, Any], key: str) -> Any:
    if key in metadata:
        return metadata[key]
    raw = metadata.get("raw")
    if isinstance(raw, dict):
        return raw.get(key)
    return None


def _is_project_scoped_customer_id(user_id: str | None) -> bool:
    """Return True for LiteLLM customer ids used for project-scoped member budgets."""
    return bool(user_id and user_id.startswith(_PROJECT_SCOPED_CUSTOMER_PREFIX))


def _sanitized_project_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return provider metadata safe to persist in Budget.provider_metadata."""
    sanitized = dict(metadata)
    sanitized.pop("api_key", None)
    sanitized.pop("token", None)
    sanitized.pop("key", None)
    raw = sanitized.get("raw")
    if isinstance(raw, dict):
        sanitized["raw"] = {k: v for k, v in raw.items() if k not in {"api_key", "token", "key"}}
    return sanitized


def _project_key_alias(project_name: str, budget_category: BudgetCategory) -> str:
    return f"{_PROJECT_KEY_ALIAS_PREFIX}{project_name}:category:{budget_category.value}"


def _normalize_personal_budget_identifier(user_id: str, category: BudgetCategory) -> str:
    if category == BudgetCategory.PLATFORM:
        return user_id
    suffix = f"_codemie_{category.value}"
    return user_id[: -len(suffix)] if user_id.endswith(suffix) else user_id


def _normalize_budget_reset_at(raw_value: Any, fallback: str | None) -> str | None:
    if isinstance(raw_value, str):
        return raw_value
    if raw_value is None:
        return fallback
    return raw_value.isoformat()


def _effective_project_member_budget_id(allocation: "ProjectMemberBudgetAssignment") -> str:
    effective_budget_id = getattr(allocation, "effective_budget_id", None)
    if effective_budget_id:
        return effective_budget_id

    override_budget_id = getattr(allocation, "override_budget_id", None)
    if override_budget_id:
        return override_budget_id

    shared_budget_id = getattr(allocation, "shared_budget_id", None)
    if shared_budget_id:
        return shared_budget_id

    if getattr(allocation, "allocation_mode", None) == "fixed":
        return build_override_project_budget_id(allocation.project_budget_id, allocation.user_id)
    return build_shared_project_budget_id(allocation.project_budget_id)


def _is_project_virtual_key_target(target: BudgetResetReconciliationTarget) -> bool:
    provider_ref = target.provider_budget_ref or ""
    if provider_ref.startswith(_PROJECT_KEY_ALIAS_PREFIX):
        return True

    raw_metadata = target.metadata.get("raw") if isinstance(target.metadata, dict) else None
    return isinstance(raw_metadata, dict) and isinstance(raw_metadata.get("key_alias"), str)


class LiteLLMBudgetEnforcementProvider:
    """LiteLLM-backed implementation of BudgetEnforcementProvider.

    Wraps the existing budget_helpers and LiteLLMService without changing their
    internal behaviour.  Registered at startup via
    register_budget_enforcement_provider() when LiteLLM is enabled.

    Backward-compatibility guarantees:
      - LiteLLM customer ids are built via build_user_id(username, category):
        {username} / {username}_codemie_cli / {username}_codemie_premium_models.
      - Global LiteLLM budget_ids equal the Codemie budget_id (unchanged).
      - project-scoped LiteLLM identifiers are stored only in provider_metadata
        and must never collide with the global customer id namespace.
    """

    provider_name: str = _PROVIDER_NAME

    def __init__(self, service: "LiteLLMService | None" = None) -> None:
        self._service = service

    def _get_service(self) -> "LiteLLMService | None":
        if self._service is not None:
            return self._service

        from codemie.enterprise.litellm.dependencies import get_litellm_service_or_none

        return get_litellm_service_or_none()

    @staticmethod
    def _build_personal_budget_entry(entry: Any, category: BudgetCategory) -> PersonalBudgetEntry:
        budget_table = getattr(entry, "litellm_budget_table", None)
        return PersonalBudgetEntry(
            user_identifier=_normalize_personal_budget_identifier(entry.user_id, category),
            budget_category=BudgetCategory(category.value),
            budget_id=getattr(budget_table, "budget_id", None) if budget_table else None,
            soft_budget=getattr(budget_table, "soft_budget", None) if budget_table else None,
            max_budget=getattr(budget_table, "max_budget", None) if budget_table else None,
            budget_duration=getattr(budget_table, "budget_duration", None) if budget_table else None,
            budget_reset_at=getattr(budget_table, "budget_reset_at", None) if budget_table else None,
        )

    async def _load_synced_member_allocations(
        self,
        provider_member_refs: set[str] | None,
    ) -> dict[str, "ProjectMemberBudgetAssignment"]:
        from sqlmodel import select

        from codemie.clients.postgres import get_async_session
        from codemie.service.budget.budget_models import ProjectMemberBudgetAssignment

        async with get_async_session() as session:
            stmt = select(ProjectMemberBudgetAssignment).where(
                ProjectMemberBudgetAssignment.deleted_at.is_(None),
            )
            result = await session.execute(stmt)
            allocations = list(result.scalars().all())

        ref_to_alloc: dict[str, ProjectMemberBudgetAssignment] = {}
        for alloc in allocations:
            meta = alloc.provider_metadata or {}
            ref = _metadata_value(meta, "provider_member_ref")
            if not ref:
                continue
            if provider_member_refs is not None and ref not in provider_member_refs:
                continue
            ref_to_alloc[ref] = alloc
        return ref_to_alloc

    @staticmethod
    def _collect_member_spend_snapshots(
        all_customers: list[Any],
        ref_to_alloc: dict[str, "ProjectMemberBudgetAssignment"],
        provider_member_refs: set[str] | None,
    ) -> tuple[
        list[MemberBudgetSpendSnapshot],
        dict[tuple[str, str, str], Decimal],
        dict[tuple[str, str, str], str | None],
    ]:
        member_snapshots: list[MemberBudgetSpendSnapshot] = []
        project_spend: dict[tuple[str, str, str], Decimal] = {}
        project_reset: dict[tuple[str, str, str], str | None] = {}

        for entry in all_customers:
            if provider_member_refs is not None and entry.user_id not in provider_member_refs:
                continue
            if not _is_project_scoped_customer_id(entry.user_id):
                continue

            alloc = ref_to_alloc.get(entry.user_id)
            if alloc is None:
                continue

            spend = entry.spend if isinstance(entry.spend, Decimal) else Decimal(str(entry.spend))
            budget_reset_at = _normalize_budget_reset_at(entry.budget_reset_at, alloc.budget_reset_at)
            key = (alloc.project_name, alloc.budget_category, alloc.project_budget_id)

            member_snapshots.append(
                MemberBudgetSpendSnapshot(
                    project_name=alloc.project_name,
                    budget_category=BudgetCategory(alloc.budget_category),
                    budget_id=alloc.project_budget_id,
                    user_id=alloc.user_id,
                    spend=spend,
                    budget_reset_at=budget_reset_at,
                    provider_subject_id=entry.user_id,
                )
            )
            project_spend[key] = project_spend.get(key, Decimal("0")) + spend
            project_reset.setdefault(key, budget_reset_at)

        return member_snapshots, project_spend, project_reset

    @staticmethod
    def _build_project_spend_snapshots(
        project_spend: dict[tuple[str, str, str], Decimal],
        project_reset: dict[tuple[str, str, str], str | None],
    ) -> list[ProjectBudgetSpendSnapshot]:
        return [
            ProjectBudgetSpendSnapshot(
                project_name=project_name,
                budget_category=BudgetCategory(budget_category),
                budget_id=budget_id,
                spend=spend,
                budget_reset_at=project_reset.get((project_name, budget_category, budget_id)),
            )
            for (project_name, budget_category, budget_id), spend in project_spend.items()
        ]

    async def _persist_project_api_key(
        self,
        *,
        project_name: str,
        key_alias: str,
        api_key: str | None,
    ) -> None:
        if not api_key:
            logger.debug(
                f"budget_event=project_api_key_persist_skipped component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} project_name={project_name!r} key_alias={key_alias!r} "
                f"reason=missing_api_key"
            )
            return

        from codemie.service.settings.settings import SettingsService

        await asyncio.to_thread(
            SettingsService.upsert_project_litellm_creds_by_alias,
            project_name,
            key_alias,
            api_key,
        )
        logger.debug(
            f"budget_event=project_api_key_persist_completed component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} project_name={project_name!r} key_alias={key_alias!r} "
            f"api_key_present={api_key is not None}"
        )

    async def _delete_project_api_key(self, *, project_name: str | None, key_alias: str | None) -> None:
        if not project_name or not key_alias:
            logger.debug(
                f"budget_event=project_api_key_delete_skipped component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} project_name={project_name!r} key_alias={key_alias!r} "
                f"reason=missing_project_or_alias"
            )
            return

        from codemie.service.settings.settings import SettingsService

        await asyncio.to_thread(
            SettingsService.delete_project_litellm_creds_by_alias,
            project_name,
            key_alias,
        )
        logger.debug(
            f"budget_event=project_api_key_delete_completed component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} project_name={project_name!r} key_alias={key_alias!r}"
        )

    async def _delete_project_provider_key_alias(self, *, service: "LiteLLMService", key_alias: str) -> None:
        try:
            await asyncio.to_thread(service.api_client.post, "/key/delete", data={"key_aliases": [key_alias]})
        except Exception as exc:
            logger.warning(
                f"budget_event=provider_project_key_delete_failed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} key_alias={key_alias!r} error={exc}"
            )

    @staticmethod
    def _build_project_budget_state_from_key_state(
        *,
        key_state: dict[str, Any],
        models: list[str] | None,
    ) -> BudgetProviderState | None:
        key_alias = key_state.get("key_alias")
        if not key_alias:
            return None

        return BudgetProviderState(
            provider=_PROVIDER_NAME,
            provider_budget_ref=key_alias,
            budget_reset_at=key_state.get("budget_reset_at"),
            sync_status=SyncStatus.OK,
            metadata=_sanitized_project_metadata(
                {
                    "raw": {
                        "key_hash": key_state.get("key_hash"),
                        "key_alias": key_alias,
                        "team_id": "codemie-projects",
                    },
                    "models": models or [],
                    "api_key": key_state.get("api_key"),
                }
            ),
        )

    async def _recreate_project_budget_key_alias(
        self,
        *,
        service: "LiteLLMService",
        project_name: str,
        budget_category: BudgetCategory,
        budget_id: str,
        max_budget: Decimal,
        budget_duration: str,
        budget_reset_at: str | None = None,
        models: list[str] | None,
    ) -> BudgetProviderState:
        """Recreate the project key with the canonical project/category alias."""
        key_alias = _project_key_alias(project_name, budget_category)
        logger.debug(
            f"budget_event=provider_project_key_recreate_started component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} project_name={project_name!r} budget_id={budget_id!r} "
            f"budget_category={budget_category.value!r} key_alias={key_alias!r} model_count={len(models or [])}"
        )
        await self._delete_project_provider_key_alias(service=service, key_alias=key_alias)
        await self._delete_project_api_key(project_name=project_name, key_alias=key_alias)
        key_state = await asyncio.to_thread(
            service._generate_project_key,
            key_alias=key_alias,
            project_name=project_name,
            budget_category=budget_category.value,
            project_budget_id=budget_id,
            max_budget=float(max_budget),
            budget_duration=budget_duration,
            budget_reset_at=budget_reset_at,
            models=models,
        )
        if key_state is not None and key_state.get("budget_reset_at") is None:
            refreshed = await asyncio.to_thread(service._get_project_key_by_alias, key_alias)
            if refreshed:
                key_state["budget_reset_at"] = refreshed.get("budget_reset_at")

        state = self._build_project_budget_state_from_key_state(key_state=key_state or {}, models=models)
        if state is None:
            logger.warning(
                f"budget_event=provider_project_key_recreate_failed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} project_name={project_name!r} budget_id={budget_id!r} "
                f"budget_category={budget_category.value!r} key_alias={key_alias!r} reason=missing_key_alias"
            )
            return BudgetProviderState(provider=_PROVIDER_NAME, sync_status=SyncStatus.FAILED)

        await self._persist_project_api_key(
            project_name=project_name,
            key_alias=state.provider_budget_ref or key_alias,
            api_key=key_state.get("api_key"),
        )
        logger.debug(
            f"budget_event=provider_project_key_recreate_completed component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} project_name={project_name!r} budget_id={budget_id!r} "
            f"budget_category={budget_category.value!r} key_alias={state.provider_budget_ref!r} "
            f"sync_status={state.sync_status!r} api_key_present={bool(key_state and key_state.get('api_key'))}"
        )
        return state

    async def _sync_existing_project_key_alias(
        self,
        *,
        service: "LiteLLMService",
        key_alias: str,
        project_name: str,
        budget_category: BudgetCategory,
        budget_id: str,
        max_budget: Decimal,
        budget_duration: str,
        budget_reset_at: str | None = None,
        models: list[str] | None,
    ) -> BudgetProviderState | None:
        logger.debug(
            f"budget_event=provider_project_key_lookup_started component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} project_name={project_name!r} budget_id={budget_id!r} "
            f"budget_category={budget_category.value!r} key_alias={key_alias!r}"
        )
        existing_key = await asyncio.to_thread(service._get_project_key_by_alias, key_alias)
        if existing_key is None:
            logger.debug(
                f"budget_event=provider_project_key_missing component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} project_name={project_name!r} budget_id={budget_id!r} "
                f"budget_category={budget_category.value!r} key_alias={key_alias!r}"
            )
            key_state = await asyncio.to_thread(
                service._generate_project_key,
                key_alias=key_alias,
                project_name=project_name,
                budget_category=budget_category.value,
                project_budget_id=budget_id,
                max_budget=float(max_budget),
                budget_duration=budget_duration,
                budget_reset_at=budget_reset_at,
                models=models,
            )
        elif key_hash := (existing_key.get("key_hash") or existing_key.get("token")):
            # LiteLLM's /key/list response uses "token" for the hash, not "key_hash".
            # Back-fill "key_hash" so _update_project_key can find the updatable reference.
            existing_key = {**existing_key, "key_hash": key_hash}
            logger.debug(
                f"budget_event=provider_project_key_update_started component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} project_name={project_name!r} budget_id={budget_id!r} "
                f"budget_category={budget_category.value!r} key_alias={key_alias!r} model_count={len(models or [])}"
            )
            key_state = await asyncio.to_thread(
                service._update_project_key,
                existing_key=existing_key,
                key_alias=key_alias,
                project_name=project_name,
                budget_category=budget_category.value,
                project_budget_id=budget_id,
                max_budget=float(max_budget),
                budget_duration=budget_duration,
                budget_reset_at=budget_reset_at,
                models=models,
            )
        else:
            logger.debug(
                f"budget_event=provider_project_key_sync_skipped component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} project_name={project_name!r} budget_id={budget_id!r} "
                f"budget_category={budget_category.value!r} key_alias={key_alias!r} reason=missing_key_hash"
            )
            return None

        if key_state is not None and key_state.get("budget_reset_at") is None:
            refreshed = await asyncio.to_thread(service._get_project_key_by_alias, key_alias)
            if refreshed:
                key_state["budget_reset_at"] = refreshed.get("budget_reset_at")
        if key_state is not None and key_state.get("api_key"):
            await self._persist_project_api_key(
                project_name=project_name,
                key_alias=key_state.get("key_alias") or key_alias,
                api_key=key_state.get("api_key"),
            )
        state = self._build_project_budget_state_from_key_state(key_state=key_state or {}, models=models)
        logger.debug(
            f"budget_event=provider_project_key_update_completed component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} project_name={project_name!r} budget_id={budget_id!r} "
            f"budget_category={budget_category.value!r} key_alias={key_alias!r} "
            f"sync_status={(state.sync_status if state else SyncStatus.FAILED)!r} "
            f"api_key_present={bool(key_state and key_state.get('api_key'))}"
        )
        return state

    # ── Global / user budget methods ────────────────────────────────────

    async def ensure_global_budget(
        self,
        *,
        budget_id: str,
        budget_category: BudgetCategory,
        soft_budget: float,
        max_budget: float,
        budget_duration: str,
    ) -> BudgetProviderState:
        """Idempotent: creates the LiteLLM budget if absent.

        Called from create_budget and ensure_predefined_budgets for new budgets.
        """
        _ = budget_category
        from codemie.enterprise.litellm.budget_helpers import create_budget_in_litellm

        logger.debug(
            f"budget_event=provider_global_budget_sync_started component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} operation=create budget_id={budget_id!r} "
            f"budget_category={budget_category.value!r} max_budget={max_budget!r} "
            f"soft_budget={soft_budget!r} budget_duration={budget_duration!r}"
        )
        result = await asyncio.to_thread(create_budget_in_litellm, budget_id, max_budget, soft_budget, budget_duration)
        if result is None:
            logger.debug(
                f"budget_event=provider_global_budget_sync_completed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} operation=create budget_id={budget_id!r} "
                f"sync_status={SyncStatus.FAILED!r}"
            )
            return BudgetProviderState(provider=_PROVIDER_NAME, sync_status=SyncStatus.FAILED)
        logger.debug(
            f"budget_event=provider_global_budget_sync_completed component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} operation=create budget_id={budget_id!r} "
            f"provider_budget_ref={budget_id!r} sync_status={SyncStatus.OK!r} "
            f"budget_reset_at={getattr(result, 'budget_reset_at', None)!r}"
        )
        return BudgetProviderState(
            provider=_PROVIDER_NAME,
            provider_budget_ref=budget_id,
            budget_reset_at=getattr(result, "budget_reset_at", None),
            sync_status=SyncStatus.OK,
        )

    async def update_global_budget(
        self,
        *,
        budget_id: str,
        soft_budget: float,
        max_budget: float,
        budget_duration: str,
        budget_reset_at: str | None = None,
    ) -> BudgetProviderState:
        """Update an existing LiteLLM budget."""
        from codemie.enterprise.litellm.budget_helpers import update_budget_in_litellm

        logger.debug(
            f"budget_event=provider_global_budget_sync_started component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} operation=update budget_id={budget_id!r} "
            f"max_budget={max_budget!r} soft_budget={soft_budget!r} budget_duration={budget_duration!r} "
            f"budget_reset_at={budget_reset_at!r}"
        )
        result = await asyncio.to_thread(
            update_budget_in_litellm,
            budget_id,
            max_budget,
            soft_budget,
            budget_duration,
            budget_reset_at,
        )
        if result is None:
            logger.debug(
                f"budget_event=provider_global_budget_sync_completed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} operation=update budget_id={budget_id!r} "
                f"sync_status={SyncStatus.FAILED!r}"
            )
            return BudgetProviderState(provider=_PROVIDER_NAME, sync_status=SyncStatus.FAILED)
        logger.debug(
            f"budget_event=provider_global_budget_sync_completed component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} operation=update budget_id={budget_id!r} "
            f"provider_budget_ref={budget_id!r} sync_status={SyncStatus.OK!r} "
            f"budget_reset_at={getattr(result, 'budget_reset_at', None)!r}"
        )
        return BudgetProviderState(
            provider=_PROVIDER_NAME,
            provider_budget_ref=budget_id,
            budget_reset_at=getattr(result, "budget_reset_at", None),
            sync_status=SyncStatus.OK,
        )

    async def delete_global_budget(self, *, budget_id: str) -> None:
        """No-op: LiteLLM has no budget deletion API."""
        logger.debug(
            f"budget_event=provider_global_budget_delete_skipped component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} budget_id={budget_id!r} reason=provider_has_no_delete_api"
        )

    async def assign_user_budget(
        self,
        *,
        username: str,
        budget_category: BudgetCategory,
        budget_id: str,
    ) -> None:
        """Assign a budget to a LiteLLM customer for the given category."""
        from codemie.enterprise.litellm.budget_categories import build_user_id
        from codemie.enterprise.litellm.budget_helpers import update_customer_budget_in_litellm

        litellm_user_id = build_user_id(username, budget_category)
        logger.debug(
            f"budget_event=provider_customer_budget_assignment_started component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} operation=assign username={username!r} "
            f"provider_member_ref={litellm_user_id!r} budget_category={budget_category.value!r} "
            f"budget_id={budget_id!r}"
        )
        success = await asyncio.to_thread(update_customer_budget_in_litellm, litellm_user_id, budget_id)
        if not success:
            logger.warning(
                f"budget_event=provider_customer_budget_assignment_failed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} operation=assign username={username!r} "
                f"provider_member_ref={litellm_user_id!r} budget_category={budget_category.value!r} "
                f"budget_id={budget_id!r}"
            )
            raise RuntimeError(f"Failed to assign budget {budget_id!r} for LiteLLM customer {litellm_user_id!r}")
        logger.debug(
            f"budget_event=provider_customer_budget_assignment_completed component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} operation=assign username={username!r} "
            f"provider_member_ref={litellm_user_id!r} budget_category={budget_category.value!r} "
            f"budget_id={budget_id!r}"
        )

    async def clear_user_budget(
        self,
        *,
        username: str,
        budget_category: BudgetCategory,
    ) -> None:
        """Clear (set to None) the budget assignment for a LiteLLM customer."""
        from codemie.enterprise.litellm.budget_categories import build_user_id
        from codemie.enterprise.litellm.budget_helpers import update_customer_budget_in_litellm

        litellm_user_id = build_user_id(username, budget_category)
        logger.debug(
            f"budget_event=provider_customer_budget_assignment_started component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} operation=clear username={username!r} "
            f"provider_member_ref={litellm_user_id!r} budget_category={budget_category.value!r}"
        )
        success = await asyncio.to_thread(update_customer_budget_in_litellm, litellm_user_id, None)
        if not success:
            logger.warning(
                f"budget_event=provider_customer_budget_assignment_failed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} operation=clear username={username!r} "
                f"provider_member_ref={litellm_user_id!r} budget_category={budget_category.value!r}"
            )
            raise RuntimeError(f"Failed to clear budget for LiteLLM customer {litellm_user_id!r}")
        logger.debug(
            f"budget_event=provider_customer_budget_assignment_completed component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} operation=clear username={username!r} "
            f"provider_member_ref={litellm_user_id!r} budget_category={budget_category.value!r}"
        )

    async def reset_user_budget_spending(
        self,
        *,
        username: str,
        budget_category: BudgetCategory,
        budget_id: str,
    ) -> None:
        """Reset a customer's spend counter by delete+recreate in LiteLLM."""
        from codemie.enterprise.litellm.budget_categories import build_user_id
        from codemie.enterprise.litellm.budget_helpers import reset_customer_spending_in_litellm

        litellm_user_id = build_user_id(username, budget_category)
        logger.debug(
            f"budget_event=provider_customer_spending_reset_started component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} username={username!r} provider_member_ref={litellm_user_id!r} "
            f"budget_category={budget_category.value!r} budget_id={budget_id!r}"
        )
        success = await asyncio.to_thread(reset_customer_spending_in_litellm, litellm_user_id, budget_id)
        if not success:
            logger.warning(
                f"budget_event=provider_customer_spending_reset_failed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} username={username!r} provider_member_ref={litellm_user_id!r} "
                f"budget_category={budget_category.value!r} budget_id={budget_id!r}"
            )
            raise RuntimeError(f"Failed to reset spending for LiteLLM customer {litellm_user_id!r}")
        logger.debug(
            f"budget_event=provider_customer_spending_reset_completed component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} username={username!r} provider_member_ref={litellm_user_id!r} "
            f"budget_category={budget_category.value!r} budget_id={budget_id!r}"
        )

    async def list_global_budget_states(self) -> list[GlobalBudgetState] | None:
        """Return all LiteLLM budgets as GlobalBudgetState objects.

        Returns None (not empty list) when LiteLLM is unreachable, so callers
        can distinguish 'zero budgets' from 'provider unavailable'.
        """
        from codemie.enterprise.litellm.budget_helpers import list_budgets_from_litellm

        raw = await asyncio.to_thread(list_budgets_from_litellm)
        if raw is None:
            return None
        internal_budget_ids = await self._get_internal_member_budget_ids()
        return [
            GlobalBudgetState(
                budget_id=b.budget_id,
                provider=_PROVIDER_NAME,
                soft_budget=b.soft_budget or 0.0,
                max_budget=b.max_budget or 0.0,
                budget_duration=b.budget_duration or "30d",
                budget_reset_at=getattr(b, "budget_reset_at", None),
                sync_status=SyncStatus.OK,
            )
            for b in raw
            if b.budget_id is not None and b.budget_id not in internal_budget_ids
        ]

    async def list_personal_budget_assignments(self) -> list[PersonalBudgetEntry] | None:
        """Return all LiteLLM customers as PersonalBudgetEntry objects.

        LiteLLM user_id suffixes are stripped here so core only sees plain
        email identifiers — core never sees raw LiteLLM user ids.

        Returns None when LiteLLM is unreachable.
        """
        from codemie.enterprise.litellm.budget_categories import (
            derive_category_from_user_id,
        )
        from codemie.enterprise.litellm.dependencies import get_litellm_service_or_none

        service = get_litellm_service_or_none()
        if service is None:
            logger.debug(
                "budget_event=provider_unavailable component=litellm_budget_provider provider=litellm "
                "operation=list_personal_budget_assignments"
            )
            return None

        try:
            raw_entries = await asyncio.to_thread(service.get_customer_list)
        except Exception as exc:
            logger.warning(
                f"budget_event=provider_customer_list_failed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} operation=list_personal_budget_assignments error={exc}"
            )
            return None

        entries: list[PersonalBudgetEntry] = []
        for entry in raw_entries:
            if not entry.user_id:
                continue
            if _is_project_scoped_customer_id(entry.user_id):
                continue
            category = derive_category_from_user_id(entry.user_id)
            entries.append(self._build_personal_budget_entry(entry, category))
        return entries

    async def reconcile_budget_reset_timestamps(
        self,
        *,
        targets: list[BudgetResetReconciliationTarget],
    ) -> BudgetResetReconciliationResult:
        service = self._get_service()
        if service is None:
            return BudgetResetReconciliationResult(
                items=[
                    BudgetResetReconciliationItem(
                        entity_type=target.entity_type,
                        budget_id=target.budget_id,
                        provider_budget_ref=target.provider_budget_ref,
                        provider_member_ref=target.provider_member_ref,
                        error="litellm service unavailable",
                    )
                    for target in targets
                ]
            )

        budget_targets = [target for target in targets if not _is_project_virtual_key_target(target)]
        key_targets = [target for target in targets if _is_project_virtual_key_target(target)]

        budget_map: dict[str, Any] = {}
        budget_ids = [target.provider_budget_ref for target in budget_targets if target.provider_budget_ref]
        if budget_ids:
            budget_map = await asyncio.to_thread(service.get_budget_info_map, budget_ids)

        key_map: dict[str, Any] = {}
        if key_targets:
            keys = await asyncio.to_thread(service.get_all_keys_spending)
            key_map = {key.key_alias: key for key in keys if key.key_alias}

        items: list[BudgetResetReconciliationItem] = []
        for target in targets:
            provider_ref = target.provider_budget_ref
            provider_member_ref = target.provider_member_ref

            if _is_project_virtual_key_target(target):
                provider_state = key_map.get(provider_ref) if provider_ref else None
            else:
                provider_state = budget_map.get(provider_ref) if provider_ref else None

            if provider_state is None:
                items.append(
                    BudgetResetReconciliationItem(
                        entity_type=target.entity_type,
                        budget_id=target.budget_id,
                        provider_budget_ref=provider_ref,
                        provider_member_ref=provider_member_ref,
                        error="provider entity missing during reset reconciliation",
                    )
                )
                continue

            refreshed_budget_reset_at = _normalize_budget_reset_at(
                getattr(provider_state, "budget_reset_at", None),
                target.budget_reset_at,
            )
            if not refreshed_budget_reset_at:
                items.append(
                    BudgetResetReconciliationItem(
                        entity_type=target.entity_type,
                        budget_id=target.budget_id,
                        provider_budget_ref=provider_ref,
                        provider_member_ref=provider_member_ref,
                        error="provider entity returned empty budget_reset_at",
                    )
                )
                continue

            items.append(
                BudgetResetReconciliationItem(
                    entity_type=target.entity_type,
                    budget_id=target.budget_id,
                    provider_budget_ref=provider_ref,
                    provider_member_ref=provider_member_ref,
                    refreshed_budget_reset_at=refreshed_budget_reset_at,
                )
            )

        return BudgetResetReconciliationResult(items=items)

    async def provision_global_user(self, *, user_id: str, username: str) -> None:
        """Ensure a LiteLLM customer record exists for a new Codemie user.

        Called at new-user creation time (SSO first login, admin create).
        Fail-open: callers log the exception and continue.
        """
        from codemie.enterprise.litellm.dependencies import get_litellm_service_or_none

        logger.debug(
            f"budget_event=provider_customer_provision_started component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} user_id={user_id!r} username={username!r}"
        )
        service = get_litellm_service_or_none()
        if service is None:
            logger.debug(
                f"budget_event=provider_customer_provision_skipped component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} user_id={user_id!r} username={username!r} "
                f"reason=provider_unavailable"
            )
            return
        await asyncio.to_thread(service.get_or_create_customer_with_budget, username)
        logger.debug(
            f"budget_event=provider_customer_provision_completed component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} user_id={user_id!r} username={username!r}"
        )

    # ── Project budget methods ───────────────────────────────────────────

    async def ensure_project_budget(
        self,
        *,
        project_name: str,
        budget_category: BudgetCategory,
        budget_id: str,
        max_budget: Decimal,
        budget_duration: str,
        models: list[str] | None,
        metadata: dict[str, Any] | None = None,
    ) -> BudgetProviderState:
        _ = metadata
        logger.debug(
            f"budget_event=provider_project_budget_sync_started component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} operation=create project_name={project_name!r} "
            f"budget_id={budget_id!r} budget_category={budget_category.value!r} "
            f"max_budget={max_budget!r} budget_duration={budget_duration!r} "
            f"model_count={len(models or [])}"
        )
        service = self._get_service()
        if service is None:
            logger.debug(
                f"budget_event=provider_unavailable component=litellm_budget_provider provider={_PROVIDER_NAME!r} "
                f"operation=ensure_project_budget project_name={project_name!r} budget_id={budget_id!r} "
                f"budget_category={budget_category.value!r}"
            )
            return BudgetProviderState(provider=_PROVIDER_NAME, sync_status=SyncStatus.FAILED)

        result = await asyncio.to_thread(
            service.ensure_project_budget,
            project_budget_id=budget_id,
            project_name=project_name,
            budget_category=budget_category.value,
            max_budget=float(max_budget),
            budget_duration=budget_duration,
            models=models,
        )
        if result is None:
            logger.warning(
                f"budget_event=provider_project_budget_sync_failed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} operation=create project_name={project_name!r} "
                f"budget_id={budget_id!r} budget_category={budget_category.value!r} "
                f"reason=empty_provider_result action=recreate_canonical_alias"
            )
            return await self._recreate_project_budget_key_alias(
                service=service,
                project_name=project_name,
                budget_category=budget_category,
                budget_id=budget_id,
                max_budget=max_budget,
                budget_duration=budget_duration,
                models=models,
            )

        await self._persist_project_api_key(
            project_name=project_name,
            key_alias=result.provider_budget_ref,
            api_key=getattr(result, "api_key", None),
        )

        logger.debug(
            f"budget_event=provider_project_budget_sync_completed component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} operation=create project_name={project_name!r} "
            f"budget_id={budget_id!r} budget_category={budget_category.value!r} "
            f"provider_budget_ref={result.provider_budget_ref!r} sync_status={SyncStatus.OK!r} "
            f"budget_reset_at={result.budget_reset_at!r} api_key_present={getattr(result, 'api_key', None) is not None}"
        )
        return BudgetProviderState(
            provider=_PROVIDER_NAME,
            provider_budget_ref=result.provider_budget_ref,
            budget_reset_at=result.budget_reset_at,
            sync_status=SyncStatus.OK,
            metadata=_sanitized_project_metadata(result.metadata),
        )

    async def update_project_budget(
        self,
        *,
        budget_state: BudgetProviderState,
        project_name: str,
        budget_category: BudgetCategory,
        budget_id: str,
        max_budget: Decimal,
        budget_duration: str,
        models: list[str] | None,
        metadata: dict[str, Any] | None = None,
    ) -> BudgetProviderState:
        _ = metadata
        logger.debug(
            f"budget_event=provider_project_budget_sync_started component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} operation=update project_name={project_name!r} "
            f"budget_id={budget_id!r} budget_category={budget_category.value!r} "
            f"provider_budget_ref={budget_state.provider_budget_ref!r} max_budget={max_budget!r} "
            f"budget_duration={budget_duration!r} model_count={len(models or [])}"
        )
        service = self._get_service()
        provider_budget_ref = budget_state.provider_budget_ref
        if service is None or provider_budget_ref is None:
            logger.debug(
                f"budget_event=provider_project_budget_sync_failed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} operation=update project_name={project_name!r} "
                f"budget_id={budget_id!r} budget_category={budget_category.value!r} "
                f"reason={'provider_unavailable' if service is None else 'missing_provider_budget_ref'}"
            )
            return BudgetProviderState(provider=_PROVIDER_NAME, sync_status=SyncStatus.FAILED)

        state = await self._sync_existing_project_key_alias(
            service=service,
            key_alias=provider_budget_ref,
            project_name=project_name,
            budget_category=budget_category,
            budget_id=budget_id,
            max_budget=max_budget,
            budget_duration=budget_duration,
            budget_reset_at=budget_state.budget_reset_at,
            models=models,
        )
        if state is None:
            logger.warning(
                f"budget_event=provider_project_budget_sync_failed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} operation=update project_name={project_name!r} "
                f"budget_id={budget_id!r} budget_category={budget_category.value!r} "
                f"provider_budget_ref={provider_budget_ref!r} reason=key_alias_sync_failed "
                f"action=recreate_canonical_alias"
            )
            state = await self._recreate_project_budget_key_alias(
                service=service,
                project_name=project_name,
                budget_category=budget_category,
                budget_id=budget_id,
                max_budget=max_budget,
                budget_duration=budget_duration,
                budget_reset_at=budget_state.budget_reset_at,
                models=models,
            )

        old_provider_budget_ref = provider_budget_ref
        if old_provider_budget_ref != state.provider_budget_ref:
            await self._delete_project_api_key(project_name=project_name, key_alias=old_provider_budget_ref)

        logger.debug(
            f"budget_event=provider_project_budget_sync_completed component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} operation=update project_name={project_name!r} "
            f"budget_id={budget_id!r} budget_category={budget_category.value!r} "
            f"provider_budget_ref={state.provider_budget_ref!r} sync_status={state.sync_status!r} "
            f"budget_reset_at={state.budget_reset_at!r}"
        )
        return state

    async def reset_project_budget_spend(
        self,
        *,
        budget_state: BudgetProviderState,
        project_name: str,
        budget_category: BudgetCategory,
        budget_id: str,
        changed_by: str | None = None,
        models: list[str] | None = None,
    ) -> BudgetProviderState:
        logger.debug(
            f"budget_event=provider_project_budget_reset_started component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} project_name={project_name!r} budget_id={budget_id!r} "
            f"budget_category={budget_category.value!r} provider_budget_ref={budget_state.provider_budget_ref!r} "
            f"changed_by={changed_by!r}"
        )
        service = self._get_service()
        provider_budget_ref = budget_state.provider_budget_ref
        if service is None or provider_budget_ref is None:
            logger.debug(
                f"budget_event=provider_project_budget_reset_failed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} project_name={project_name!r} budget_id={budget_id!r} "
                f"budget_category={budget_category.value!r} "
                f"reason={'provider_unavailable' if service is None else 'missing_provider_budget_ref'}"
            )
            return BudgetProviderState(provider=_PROVIDER_NAME, sync_status=SyncStatus.FAILED)

        from codemie.service.settings.settings import SettingsService

        credentials = await asyncio.to_thread(
            SettingsService.get_project_litellm_creds_by_alias,
            project_name,
            provider_budget_ref,
        )
        if credentials is None or not credentials.api_key:
            logger.warning(
                f"budget_event=provider_project_budget_reset_failed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} project_name={project_name!r} budget_id={budget_id!r} "
                f"budget_category={budget_category.value!r} provider_budget_ref={provider_budget_ref!r} "
                f"reason=missing_project_credentials"
            )
            return BudgetProviderState(provider=_PROVIDER_NAME, sync_status=SyncStatus.FAILED)

        result = await asyncio.to_thread(
            service.reset_project_budget_spend,
            provider_budget_ref=provider_budget_ref,
            api_key=credentials.api_key,
            changed_by=changed_by,
            models=models,
        )
        if result is None:
            logger.warning(
                f"budget_event=provider_project_budget_reset_failed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} project_name={project_name!r} budget_id={budget_id!r} "
                f"budget_category={budget_category.value!r} provider_budget_ref={provider_budget_ref!r} "
                f"reason=provider_reset_failed"
            )
            return BudgetProviderState(provider=_PROVIDER_NAME, sync_status=SyncStatus.FAILED)

        state = BudgetProviderState(
            provider=_PROVIDER_NAME,
            provider_budget_ref=result.provider_budget_ref,
            budget_reset_at=result.budget_reset_at,
            sync_status=SyncStatus.OK,
            metadata=_sanitized_project_metadata(result.metadata),
        )
        logger.debug(
            f"budget_event=provider_project_budget_reset_completed component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} project_name={project_name!r} budget_id={budget_id!r} "
            f"budget_category={budget_category.value!r} provider_budget_ref={state.provider_budget_ref!r} "
            f"sync_status={state.sync_status!r} budget_reset_at={state.budget_reset_at!r}"
        )
        return state

    async def delete_project_budget(
        self,
        *,
        budget_state: BudgetProviderState,
        project_name: str | None = None,
    ) -> None:
        service = self._get_service()
        if budget_state.provider_budget_ref is None:
            logger.debug(
                f"budget_event=provider_project_budget_delete_skipped component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} project_name={project_name!r} reason=missing_provider_budget_ref"
            )
            return
        if service is not None:
            await asyncio.to_thread(service.delete_project_budget, provider_budget_ref=budget_state.provider_budget_ref)
            logger.debug(
                f"budget_event=provider_project_budget_delete_completed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} project_name={project_name!r} "
                f"provider_budget_ref={budget_state.provider_budget_ref!r}"
            )
        else:
            logger.debug(
                f"budget_event=provider_unavailable component=litellm_budget_provider provider={_PROVIDER_NAME!r} "
                f"operation=delete_project_budget project_name={project_name!r} "
                f"provider_budget_ref={budget_state.provider_budget_ref!r}"
            )
        await self._delete_project_api_key(project_name=project_name, key_alias=budget_state.provider_budget_ref)

    async def get_project_budget_state_by_ref(
        self,
        *,
        provider_budget_ref: str,
    ) -> ProjectBudgetState | None:
        """Fetch the LiteLLM virtual key for the given budget ref and return its limits, or None if not found."""
        logger.debug(
            f"budget_event=provider_project_budget_lookup_started component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} provider_budget_ref={provider_budget_ref!r}"
        )
        service = self._get_service()
        if service is None:
            logger.debug(
                f"budget_event=provider_unavailable component=litellm_budget_provider provider={_PROVIDER_NAME!r} "
                f"operation=get_project_budget_state provider_budget_ref={provider_budget_ref!r}"
            )
            return None
        raw = await asyncio.to_thread(service._get_project_key_by_alias, provider_budget_ref)
        if raw is None:
            logger.debug(
                f"budget_event=provider_project_budget_lookup_missed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} provider_budget_ref={provider_budget_ref!r}"
            )
            return None
        logger.debug(
            f"budget_event=provider_project_budget_lookup_completed component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} provider_budget_ref={provider_budget_ref!r} "
            f"max_budget={(raw.get('max_budget') or 0.0)!r} soft_budget={(raw.get('soft_budget') or 0.0)!r} "
            f"budget_duration={(raw.get('budget_duration') or '30d')!r} budget_reset_at={raw.get('budget_reset_at')!r}"
        )
        return ProjectBudgetState(
            max_budget=raw.get("max_budget") or 0.0,
            soft_budget=raw.get("soft_budget") or 0.0,
            budget_duration=raw.get("budget_duration") or "30d",
            budget_reset_at=raw.get("budget_reset_at"),
            metadata={k: v for k, v in raw.items() if k not in {"api_key", "token"}},
        )

    async def sync_member_allocation(
        self,
        *,
        allocation: "ProjectMemberBudgetAssignment",
        budget: "Budget",
        effective_max_budget: float | None = None,
    ) -> BudgetProviderMemberState:
        effective_budget_id = _effective_project_member_budget_id(allocation)
        allocation_id = getattr(allocation, "id", None)
        resolved_max_budget = (
            effective_max_budget if effective_max_budget is not None else allocation.allocated_max_budget
        )
        logger.debug(
            f"budget_event=provider_member_budget_sync_started component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} project_name={allocation.project_name!r} "
            f"budget_id={allocation.project_budget_id!r} effective_budget_id={effective_budget_id!r} "
            f"budget_category={allocation.budget_category!r} allocation_id={allocation_id!r} "
            f"user_id={allocation.user_id!r} allocated_max_budget={allocation.allocated_max_budget!r} "
            f"resolved_max_budget={resolved_max_budget!r} enforcement_override={effective_max_budget is not None} "
            f"allocated_soft_budget={allocation.allocated_soft_budget!r}"
        )
        service = self._get_service()
        if service is None:
            logger.debug(
                f"budget_event=provider_member_budget_sync_failed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} project_name={allocation.project_name!r} "
                f"budget_id={allocation.project_budget_id!r} budget_category={allocation.budget_category!r} "
                f"allocation_id={allocation_id!r} user_id={allocation.user_id!r} reason=provider_unavailable"
            )
            return BudgetProviderMemberState(provider=_PROVIDER_NAME, sync_status=SyncStatus.FAILED)

        result = await asyncio.to_thread(
            service.sync_project_member_budget_assignment,
            project_budget_id=allocation.project_budget_id,
            project_name=allocation.project_name,
            budget_category=allocation.budget_category,
            user_id=allocation.user_id,
            allocated_max_budget=resolved_max_budget,
            allocated_soft_budget=allocation.allocated_soft_budget,
            budget_duration=budget.budget_duration,
            budget_reset_at=budget.budget_reset_at,
            effective_budget_id=effective_budget_id,
        )
        if result is None:
            logger.debug(
                f"budget_event=provider_member_budget_sync_failed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} project_name={allocation.project_name!r} "
                f"budget_id={allocation.project_budget_id!r} budget_category={allocation.budget_category!r} "
                f"allocation_id={allocation_id!r} user_id={allocation.user_id!r} reason=empty_provider_result"
            )
            return BudgetProviderMemberState(provider=_PROVIDER_NAME, sync_status=SyncStatus.FAILED)

        provider_budget_id = getattr(result, "budget_id", None)
        if not provider_budget_id:
            logger.debug(
                f"budget_event=provider_member_budget_sync_failed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} project_name={allocation.project_name!r} "
                f"budget_id={allocation.project_budget_id!r} budget_category={allocation.budget_category!r} "
                f"allocation_id={allocation_id!r} user_id={allocation.user_id!r} reason=missing_provider_budget_id"
            )
            return BudgetProviderMemberState(provider=_PROVIDER_NAME, sync_status=SyncStatus.FAILED)

        metadata = dict(result.metadata)
        metadata["internal_budget"] = True
        metadata["budget_scope"] = "project_member"
        metadata["provider_budget_id"] = provider_budget_id

        logger.debug(
            f"budget_event=provider_member_budget_sync_completed component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} project_name={allocation.project_name!r} "
            f"budget_id={allocation.project_budget_id!r} budget_category={allocation.budget_category!r} "
            f"allocation_id={allocation_id!r} user_id={allocation.user_id!r} "
            f"provider_member_ref={result.provider_member_ref!r} provider_budget_id={provider_budget_id!r} "
            f"budget_reset_at={result.budget_reset_at!r}"
        )
        return BudgetProviderMemberState(
            provider=_PROVIDER_NAME,
            provider_member_ref=result.provider_member_ref,
            provider_budget_id=provider_budget_id,
            budget_reset_at=result.budget_reset_at,
            sync_status=SyncStatus.OK,
            metadata=metadata,
        )

    async def _get_internal_member_budget_ids(self) -> set[str]:
        service = self._get_service()
        if service is None:
            return set()

        try:
            raw_entries = await asyncio.to_thread(service.get_customer_list)
        except Exception as exc:
            logger.warning(
                f"budget_event=provider_customer_list_failed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} operation=list_internal_member_budget_ids error={exc}"
            )
            return set()

        internal_budget_ids: set[str] = set()
        for entry in raw_entries:
            if not _is_project_scoped_customer_id(getattr(entry, "user_id", None)):
                continue
            budget_id = getattr(entry, "budget_id", None)
            if budget_id:
                internal_budget_ids.add(budget_id)
                continue
            budget_table = getattr(entry, "litellm_budget_table", None)
            nested_budget_id = getattr(budget_table, "budget_id", None) if budget_table else None
            if nested_budget_id:
                internal_budget_ids.add(nested_budget_id)
        return internal_budget_ids

    async def delete_member_allocation(self, *, allocation: "ProjectMemberBudgetAssignment") -> None:
        service = self._get_service()
        if service is None:
            logger.debug(
                f"budget_event=provider_member_budget_delete_skipped component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} project_name={allocation.project_name!r} "
                f"budget_id={allocation.project_budget_id!r} budget_category={allocation.budget_category!r} "
                f"allocation_id={allocation.id!r} user_id={allocation.user_id!r} reason=provider_unavailable"
            )
            return
        provider_metadata = allocation.provider_metadata or {}
        provider_member_ref = _metadata_value(provider_metadata, "provider_member_ref")
        await asyncio.to_thread(
            service.delete_project_member_budget_assignment,
            provider_member_ref=provider_member_ref,
        )
        logger.debug(
            f"budget_event=provider_member_budget_delete_completed component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} project_name={allocation.project_name!r} "
            f"budget_id={allocation.project_budget_id!r} budget_category={allocation.budget_category!r} "
            f"allocation_id={allocation.id!r} user_id={allocation.user_id!r} "
            f"provider_member_ref={provider_member_ref!r}"
        )

    def _resolve_runtime_core(self, *, context: BudgetRuntimeContext) -> BudgetRuntimeProviderResult:
        from codemie.service.settings.settings import SettingsService

        project_key_alias = _metadata_value(context.provider_metadata, "provider_budget_ref") or _metadata_value(
            context.provider_metadata,
            "key_alias",
        )
        project_api_key = None
        project_base_url = None
        if context.project_name and isinstance(project_key_alias, str) and project_key_alias:
            credentials = SettingsService.get_project_litellm_creds_by_alias(context.project_name, project_key_alias)
            if credentials:
                project_api_key = credentials.api_key
                project_base_url = None  # budget-managed keys always route through the global LiteLLM proxy
        logger.debug(
            f"budget_event=runtime_project_credentials_resolved component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} user_id={context.user_id!r} username={context.user_email!r} "
            f"project_name={context.project_name!r} budget_id={context.budget_id!r} "
            f"budget_category={context.budget_category.value!r} model={context.model!r} "
            f"key_alias={project_key_alias!r} api_key_present={project_api_key is not None} "
            f"base_url_present={project_base_url is not None}"
        )

        provider_member_ref = _metadata_value(context.member_provider_metadata, "provider_member_ref")
        logger.debug(
            f"budget_event=runtime_mode_selected component=litellm_budget_provider provider={_PROVIDER_NAME!r} "
            f"user_id={context.user_id!r} username={context.user_email!r} project_name={context.project_name!r} "
            f"budget_id={context.budget_id!r} budget_category={context.budget_category.value!r} "
            f"model={context.model!r} provider_member_ref={provider_member_ref!r}"
        )
        if provider_member_ref:
            logger.debug(
                f"budget_event=runtime_provider_overrides_applied component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} user_id={context.user_id!r} username={context.user_email!r} "
                f"project_name={context.project_name!r} budget_id={context.budget_id!r} "
                f"budget_category={context.budget_category.value!r} model={context.model!r} "
                f"api_key_present={project_api_key is not None} base_url_present={project_base_url is not None} "
                f"provider_member_ref={provider_member_ref!r} "
                f"headers_applied=true body_overrides_applied=true"
            )
            return BudgetRuntimeProviderResult(
                provider=_PROVIDER_NAME,
                api_key=project_api_key,
                base_url=project_base_url,
                headers={"x-litellm-customer-id": provider_member_ref},
                body_overrides={"user": provider_member_ref},
            )
        logger.debug(
            f"budget_event=runtime_provider_overrides_applied component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} user_id={context.user_id!r} username={context.user_email!r} "
            f"project_name={context.project_name!r} budget_id={context.budget_id!r} "
            f"budget_category={context.budget_category.value!r} model={context.model!r} "
            f"api_key_present={project_api_key is not None} base_url_present={project_base_url is not None} "
            f"provider_member_ref={provider_member_ref!r} "
            f"headers_applied=false body_overrides_applied=false"
        )
        return BudgetRuntimeProviderResult(
            provider=_PROVIDER_NAME,
            api_key=project_api_key,
            base_url=project_base_url,
        )

    async def resolve_runtime(self, *, context: BudgetRuntimeContext) -> BudgetRuntimeProviderResult:
        return self._resolve_runtime_core(context=context)

    def resolve_runtime_sync(self, *, context: BudgetRuntimeContext) -> BudgetRuntimeProviderResult:
        return self._resolve_runtime_core(context=context)

    async def _load_member_spend_from_litellm(
        self,
        provider_member_refs: set[str] | None = None,
    ) -> tuple[list[MemberBudgetSpendSnapshot], list[ProjectBudgetSpendSnapshot]]:
        """Load project/member spend from DB allocations and LiteLLM customer spend.

        Queries active ProjectMemberBudgetAssignment rows from the DB to build a
        provider_member_ref -> allocation map, then fetches all customer spend
        from LiteLLM's /customer/list and matches project-scoped customers.

        Project-level spend is computed by summing member spend per
        (project_name, budget_category, project_budget_id).

        Returns:
            (member_snapshots, project_snapshots) — both empty on any error.
        """
        provider_member_ref_count = len(provider_member_refs) if provider_member_refs is not None else None
        logger.debug(
            f"budget_event=spend_collection_started component=litellm_budget_provider provider={_PROVIDER_NAME!r} "
            f"scope=member provider_member_ref_count={provider_member_ref_count!r}"
        )
        service = self._get_service()
        if service is None:
            logger.debug(
                f"budget_event=provider_unavailable component=litellm_budget_provider provider={_PROVIDER_NAME!r} "
                f"operation=collect_member_spend scope=member"
            )
            return [], []

        try:
            ref_to_alloc = await self._load_synced_member_allocations(provider_member_refs)
            logger.debug(
                f"budget_event=spend_collection_allocations_loaded component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} scope=member allocation_count={len(ref_to_alloc)}"
            )
            if not ref_to_alloc:
                logger.debug(
                    f"budget_event=spend_collection_skipped component=litellm_budget_provider "
                    f"provider={_PROVIDER_NAME!r} scope=member reason=no_synced_member_allocations"
                )
                return [], []

            try:
                all_customers = await asyncio.to_thread(service.get_customer_list)
            except Exception as exc:
                logger.warning(
                    f"budget_event=spend_collection_provider_rows_failed component=litellm_budget_provider "
                    f"provider={_PROVIDER_NAME!r} scope=member error={exc}"
                )
                return [], []
            logger.debug(
                f"budget_event=spend_collection_provider_rows_loaded component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} scope=member customer_count={len(all_customers)}"
            )

            member_snapshots, project_spend, project_reset = self._collect_member_spend_snapshots(
                all_customers=all_customers,
                ref_to_alloc=ref_to_alloc,
                provider_member_refs=provider_member_refs,
            )
            project_snapshots = self._build_project_spend_snapshots(project_spend, project_reset)

            logger.info(
                f"budget_event=spend_collection_completed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} scope=member member_snapshot_count={len(member_snapshots)} "
                f"project_snapshot_count={len(project_snapshots)}"
            )
            return member_snapshots, project_snapshots

        except Exception as exc:
            logger.warning(
                f"budget_event=spend_collection_failed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} scope=member error={exc}"
            )
            return [], []

    async def _load_project_key_spend_from_litellm(self) -> list[ProjectBudgetSpendSnapshot]:
        """Load authoritative project/category spend from LiteLLM virtual keys."""
        logger.debug(
            f"budget_event=spend_collection_started component=litellm_budget_provider "
            f"provider={_PROVIDER_NAME!r} scope=project_key"
        )
        service = self._get_service()
        if service is None:
            logger.debug(
                f"budget_event=provider_unavailable component=litellm_budget_provider provider={_PROVIDER_NAME!r} "
                f"operation=collect_project_key_spend scope=project_key"
            )
            return []

        try:
            from decimal import Decimal

            from sqlmodel import select

            from codemie.clients.postgres import get_async_session
            from codemie.service.budget.budget_models import Budget, ProjectBudgetAssignment
            from codemie.service.settings.settings import SettingsService

            async with get_async_session() as session:
                stmt = (
                    select(ProjectBudgetAssignment, Budget)
                    .join(Budget, Budget.budget_id == ProjectBudgetAssignment.budget_id)
                    .where(ProjectBudgetAssignment.deleted_at.is_(None))
                    .where(Budget.deleted_at.is_(None))
                )
                result = await session.execute(stmt)
                rows = list(result.all())
            logger.debug(
                f"budget_event=spend_collection_allocations_loaded component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} scope=project_key assignment_count={len(rows)}"
            )

            snapshots: list[ProjectBudgetSpendSnapshot] = []
            for assignment, budget in rows:
                provider_metadata = budget.provider_metadata or {}
                key_alias = _metadata_value(provider_metadata, "provider_budget_ref") or _metadata_value(
                    provider_metadata,
                    "key_alias",
                )
                if not isinstance(key_alias, str) or not key_alias:
                    logger.debug(
                        f"budget_event=spend_collection_row_skipped component=litellm_budget_provider "
                        f"provider={_PROVIDER_NAME!r} scope=project_key project_name={assignment.project_name!r} "
                        f"budget_id={assignment.budget_id!r} budget_category={assignment.budget_category!r} "
                        f"reason=missing_key_alias"
                    )
                    continue

                credentials = SettingsService.get_project_litellm_creds_by_alias(assignment.project_name, key_alias)
                if not credentials:
                    logger.debug(
                        f"budget_event=spend_collection_row_skipped component=litellm_budget_provider "
                        f"provider={_PROVIDER_NAME!r} scope=project_key project_name={assignment.project_name!r} "
                        f"budget_id={assignment.budget_id!r} budget_category={assignment.budget_category!r} "
                        f"key_alias={key_alias!r} reason=missing_project_credentials"
                    )
                    continue

                key_spend = await asyncio.to_thread(service.get_key_spending_info, credentials.api_key)
                if not key_spend:
                    logger.debug(
                        f"budget_event=spend_collection_row_skipped component=litellm_budget_provider "
                        f"provider={_PROVIDER_NAME!r} scope=project_key project_name={assignment.project_name!r} "
                        f"budget_id={assignment.budget_id!r} budget_category={assignment.budget_category!r} "
                        f"key_alias={key_alias!r} reason=missing_key_spend"
                    )
                    continue

                spend = key_spend.get("total_spend", key_spend.get("spend", 0))
                snapshots.append(
                    ProjectBudgetSpendSnapshot(
                        project_name=assignment.project_name,
                        budget_category=BudgetCategory(assignment.budget_category),
                        budget_id=assignment.budget_id,
                        spend=Decimal(str(spend)),
                        budget_reset_at=key_spend.get("budget_reset_at") or budget.budget_reset_at,
                        provider_subject_id=_metadata_value(provider_metadata, "key_hash") or key_alias,
                        metadata={"key_alias": key_alias},
                    )
                )

            logger.info(
                f"budget_event=spend_collection_completed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} scope=project_key project_snapshot_count={len(snapshots)}"
            )
            return snapshots

        except Exception as exc:
            logger.warning(
                f"budget_event=spend_collection_failed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} scope=project_key error={exc}"
            )
            return []

    async def collect_project_budget_spend(self) -> list[ProjectBudgetSpendSnapshot]:
        return await self._load_project_key_spend_from_litellm()

    async def collect_member_budget_spend(self) -> list[MemberBudgetSpendSnapshot]:
        member_snapshots, _ = await self._load_member_spend_from_litellm()
        return member_snapshots

    async def collect_member_budget_spend_for_refs(
        self,
        provider_member_refs: set[str],
    ) -> list[MemberBudgetSpendSnapshot]:
        if not provider_member_refs:
            return []

        member_snapshots, _ = await self._load_member_spend_from_litellm(
            provider_member_refs=provider_member_refs,
        )
        return member_snapshots

    async def collect_personal_spend(self) -> list[PersonalSpendEntry] | None:
        """Return personal (non-project-scoped) spend entries from LiteLLM.

        Filters out project-scoped customer ids, normalizes the LiteLLM-specific
        user_id suffix to recover the plain email identifier, and pre-derives the
        budget_category so callers never need to import LiteLLM-specific logic.

        Returns None when LiteLLM is unreachable.
        """
        from codemie.enterprise.litellm.budget_categories import (
            BudgetCategory as LiteLLMCategory,
            derive_category_from_user_id,
        )
        from codemie.enterprise.litellm.dependencies import get_customer_list_spending

        entries = await asyncio.to_thread(get_customer_list_spending)
        if entries is None:
            return None

        result: list[PersonalSpendEntry] = []
        for entry in entries:
            if not entry.user_id or not entry.budget_id:
                continue
            if _is_project_scoped_customer_id(entry.user_id):
                continue

            category = derive_category_from_user_id(entry.user_id)
            if category == LiteLLMCategory.PLATFORM:
                user_identifier = entry.user_id
            else:
                suffix = f"_codemie_{category.value}"
                user_identifier = entry.user_id[: -len(suffix)] if entry.user_id.endswith(suffix) else entry.user_id

            result.append(
                PersonalSpendEntry(
                    user_identifier=user_identifier,
                    budget_id=entry.budget_id,
                    budget_category=category.value,
                    spend=entry.spend if isinstance(entry.spend, Decimal) else Decimal(str(entry.spend)),
                    budget_reset_at=getattr(entry, "budget_reset_at", None),
                )
            )
        return result
