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
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from codemie.service.budget.budget_enums import BudgetCategory, SyncStatus
from codemie.service.budget.budget_models import build_override_project_budget_id, build_shared_project_budget_id
from codemie.service.budget.provider import (
    BudgetProviderMemberState,
    BudgetProviderState,
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

logger = logging.getLogger(__name__)

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


class LiteLLMBudgetEnforcementProvider:
    """LiteLLM-backed implementation of BudgetEnforcementProvider.

    Wraps the existing budget_helpers and LiteLLMService without changing their
    internal behaviour.  Registered at startup via
    register_budget_enforcement_provider() when LiteLLM is enabled.

    Backward-compatibility guarantees:
      - LiteLLM customer ids are built via build_user_id(email, category)
        exactly as before:  {email} / {email}_codemie_cli /
        {email}_codemie_premium_models.
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
            return

        from codemie.service.settings.settings import SettingsService

        await asyncio.to_thread(
            SettingsService.upsert_project_litellm_creds_by_alias,
            project_name,
            key_alias,
            api_key,
        )

    async def _delete_project_api_key(self, *, project_name: str | None, key_alias: str | None) -> None:
        if not project_name or not key_alias:
            return

        from codemie.service.settings.settings import SettingsService

        await asyncio.to_thread(
            SettingsService.delete_project_litellm_creds_by_alias,
            project_name,
            key_alias,
        )

    async def _delete_project_provider_key_alias(self, *, service: "LiteLLMService", key_alias: str) -> None:
        try:
            await asyncio.to_thread(service.api_client.post, "/key/delete", data={"key_aliases": [key_alias]})
        except Exception as exc:
            logger.warning(f"Failed to delete stale LiteLLM project key alias {key_alias!r}: {exc}")

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
        soft_budget: Decimal,
        max_budget: Decimal,
        budget_duration: str,
        budget_reset_at: str | None = None,
        models: list[str] | None,
    ) -> BudgetProviderState:
        """Recreate the project key with the canonical project/category alias."""
        key_alias = _project_key_alias(project_name, budget_category)
        await self._delete_project_provider_key_alias(service=service, key_alias=key_alias)
        await self._delete_project_api_key(project_name=project_name, key_alias=key_alias)
        key_state = await asyncio.to_thread(
            service._generate_project_key,
            key_alias=key_alias,
            project_name=project_name,
            budget_category=budget_category.value,
            project_budget_id=budget_id,
            max_budget=float(max_budget),
            soft_budget=float(soft_budget),
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
            return BudgetProviderState(provider=_PROVIDER_NAME, sync_status=SyncStatus.FAILED)

        await self._persist_project_api_key(
            project_name=project_name,
            key_alias=state.provider_budget_ref or key_alias,
            api_key=key_state.get("api_key"),
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
        soft_budget: Decimal,
        max_budget: Decimal,
        budget_duration: str,
        budget_reset_at: str | None = None,
        models: list[str] | None,
    ) -> BudgetProviderState | None:
        existing_key = await asyncio.to_thread(service._get_project_key_by_alias, key_alias)
        if existing_key is None:
            key_state = await asyncio.to_thread(
                service._generate_project_key,
                key_alias=key_alias,
                project_name=project_name,
                budget_category=budget_category.value,
                project_budget_id=budget_id,
                max_budget=float(max_budget),
                soft_budget=float(soft_budget),
                budget_duration=budget_duration,
                budget_reset_at=budget_reset_at,
                models=models,
            )
        elif existing_key.get("key_hash"):
            key_state = await asyncio.to_thread(
                service._update_project_key,
                existing_key=existing_key,
                key_alias=key_alias,
                project_name=project_name,
                budget_category=budget_category.value,
                project_budget_id=budget_id,
                max_budget=float(max_budget),
                soft_budget=float(soft_budget),
                budget_duration=budget_duration,
                budget_reset_at=budget_reset_at,
                models=models,
            )
        else:
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
        return self._build_project_budget_state_from_key_state(key_state=key_state or {}, models=models)

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

        result = await asyncio.to_thread(create_budget_in_litellm, budget_id, max_budget, soft_budget, budget_duration)
        if result is None:
            return BudgetProviderState(provider=_PROVIDER_NAME, sync_status=SyncStatus.FAILED)
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
    ) -> BudgetProviderState:
        """Update an existing LiteLLM budget."""
        from codemie.enterprise.litellm.budget_helpers import update_budget_in_litellm

        result = await asyncio.to_thread(update_budget_in_litellm, budget_id, max_budget, soft_budget, budget_duration)
        if result is None:
            return BudgetProviderState(provider=_PROVIDER_NAME, sync_status=SyncStatus.FAILED)
        return BudgetProviderState(
            provider=_PROVIDER_NAME,
            provider_budget_ref=budget_id,
            budget_reset_at=getattr(result, "budget_reset_at", None),
            sync_status=SyncStatus.OK,
        )

    async def delete_global_budget(self, *, budget_id: str) -> None:
        """No-op: LiteLLM has no budget deletion API."""
        logger.debug(f"delete_global_budget: LiteLLM has no delete budget API, skipping {budget_id!r}")

    async def assign_user_budget(
        self,
        *,
        user_email: str,
        budget_category: BudgetCategory,
        budget_id: str,
    ) -> None:
        """Assign a budget to a LiteLLM customer for the given category."""
        from codemie.enterprise.litellm.budget_categories import build_user_id
        from codemie.enterprise.litellm.budget_helpers import update_customer_budget_in_litellm

        litellm_user_id = build_user_id(user_email, budget_category)
        success = await asyncio.to_thread(update_customer_budget_in_litellm, litellm_user_id, budget_id)
        if not success:
            raise RuntimeError(f"Failed to assign budget {budget_id!r} for LiteLLM customer {litellm_user_id!r}")

    async def clear_user_budget(
        self,
        *,
        user_email: str,
        budget_category: BudgetCategory,
    ) -> None:
        """Clear (set to None) the budget assignment for a LiteLLM customer."""
        from codemie.enterprise.litellm.budget_categories import build_user_id
        from codemie.enterprise.litellm.budget_helpers import update_customer_budget_in_litellm

        litellm_user_id = build_user_id(user_email, budget_category)
        success = await asyncio.to_thread(update_customer_budget_in_litellm, litellm_user_id, None)
        if not success:
            raise RuntimeError(f"Failed to clear budget for LiteLLM customer {litellm_user_id!r}")

    async def reset_user_budget_spending(
        self,
        *,
        user_email: str,
        budget_category: BudgetCategory,
        budget_id: str,
    ) -> None:
        """Reset a customer's spend counter by delete+recreate in LiteLLM."""
        from codemie.enterprise.litellm.budget_categories import build_user_id
        from codemie.enterprise.litellm.budget_helpers import reset_customer_spending_in_litellm

        litellm_user_id = build_user_id(user_email, budget_category)
        success = await asyncio.to_thread(reset_customer_spending_in_litellm, litellm_user_id, budget_id)
        if not success:
            raise RuntimeError(f"Failed to reset spending for LiteLLM customer {litellm_user_id!r}")

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
            return None

        try:
            raw_entries = await asyncio.to_thread(service.get_customer_list)
        except Exception as exc:
            logger.warning(f"list_personal_budget_assignments: get_customer_list failed: {exc}")
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

    async def provision_global_user(self, *, user_id: str, user_email: str) -> None:
        """Ensure a LiteLLM customer record exists for a new Codemie user.

        Called at new-user creation time (SSO first login, admin create).
        Fail-open: callers log the exception and continue.
        """
        _ = user_email
        from codemie.enterprise.litellm.dependencies import get_litellm_service_or_none

        service = get_litellm_service_or_none()
        if service is None:
            logger.debug(f"provision_global_user: LiteLLM not available, skipping user_id={user_id!r}")
            return
        await asyncio.to_thread(service.get_or_create_customer_with_budget, user_id)

    # ── Project budget methods ───────────────────────────────────────────

    async def ensure_project_budget(
        self,
        *,
        project_name: str,
        budget_category: BudgetCategory,
        budget_id: str,
        soft_budget: Decimal,
        max_budget: Decimal,
        budget_duration: str,
        models: list[str] | None,
        metadata: dict[str, Any] | None = None,
    ) -> BudgetProviderState:
        _ = metadata
        service = self._get_service()
        if service is None:
            return BudgetProviderState(provider=_PROVIDER_NAME, sync_status=SyncStatus.FAILED)

        result = await asyncio.to_thread(
            service.ensure_project_budget,
            project_budget_id=budget_id,
            project_name=project_name,
            budget_category=budget_category.value,
            max_budget=float(max_budget),
            soft_budget=float(soft_budget),
            budget_duration=budget_duration,
            models=models,
        )
        if result is None:
            logger.warning(
                f"Canonical LiteLLM project key alias for project={project_name!r}, "
                f"category={budget_category.value!r} could not be synced; recreating the canonical alias"
            )
            return await self._recreate_project_budget_key_alias(
                service=service,
                project_name=project_name,
                budget_category=budget_category,
                budget_id=budget_id,
                soft_budget=soft_budget,
                max_budget=max_budget,
                budget_duration=budget_duration,
                models=models,
            )

        await self._persist_project_api_key(
            project_name=project_name,
            key_alias=result.provider_budget_ref,
            api_key=getattr(result, "api_key", None),
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
        soft_budget: Decimal,
        max_budget: Decimal,
        budget_duration: str,
        models: list[str] | None,
        metadata: dict[str, Any] | None = None,
    ) -> BudgetProviderState:
        _ = metadata
        service = self._get_service()
        provider_budget_ref = budget_state.provider_budget_ref
        if service is None or provider_budget_ref is None:
            return BudgetProviderState(provider=_PROVIDER_NAME, sync_status=SyncStatus.FAILED)

        state = await self._sync_existing_project_key_alias(
            service=service,
            key_alias=provider_budget_ref,
            project_name=project_name,
            budget_category=budget_category,
            budget_id=budget_id,
            soft_budget=soft_budget,
            max_budget=max_budget,
            budget_duration=budget_duration,
            budget_reset_at=budget_state.budget_reset_at,
            models=models,
        )
        if state is None:
            logger.warning(
                f"Existing LiteLLM project key alias {provider_budget_ref!r} could not be synced; "
                "recreating the canonical alias"
            )
            state = await self._recreate_project_budget_key_alias(
                service=service,
                project_name=project_name,
                budget_category=budget_category,
                budget_id=budget_id,
                soft_budget=soft_budget,
                max_budget=max_budget,
                budget_duration=budget_duration,
                budget_reset_at=budget_state.budget_reset_at,
                models=models,
            )

        old_provider_budget_ref = provider_budget_ref
        if old_provider_budget_ref != state.provider_budget_ref:
            await self._delete_project_api_key(project_name=project_name, key_alias=old_provider_budget_ref)

        return state

    async def delete_project_budget(
        self,
        *,
        budget_state: BudgetProviderState,
        project_name: str | None = None,
    ) -> None:
        service = self._get_service()
        if budget_state.provider_budget_ref is None:
            return
        if service is not None:
            await asyncio.to_thread(service.delete_project_budget, provider_budget_ref=budget_state.provider_budget_ref)
        await self._delete_project_api_key(project_name=project_name, key_alias=budget_state.provider_budget_ref)

    async def get_project_budget_state_by_ref(
        self,
        *,
        provider_budget_ref: str,
    ) -> ProjectBudgetState | None:
        """Fetch the LiteLLM virtual key for the given budget ref and return its limits, or None if not found."""
        service = self._get_service()
        if service is None:
            return None
        raw = await asyncio.to_thread(service._get_project_key_by_alias, provider_budget_ref)
        if raw is None:
            return None
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
    ) -> BudgetProviderMemberState:
        service = self._get_service()
        if service is None:
            return BudgetProviderMemberState(provider=_PROVIDER_NAME, sync_status=SyncStatus.FAILED)

        result = await asyncio.to_thread(
            service.sync_project_member_budget_assignment,
            project_budget_id=allocation.project_budget_id,
            project_name=allocation.project_name,
            budget_category=allocation.budget_category,
            user_id=allocation.user_id,
            allocated_max_budget=allocation.allocated_max_budget,
            allocated_soft_budget=allocation.allocated_soft_budget,
            budget_duration=budget.budget_duration,
            budget_reset_at=budget.budget_reset_at,
            effective_budget_id=_effective_project_member_budget_id(allocation),
        )
        if result is None:
            return BudgetProviderMemberState(provider=_PROVIDER_NAME, sync_status=SyncStatus.FAILED)

        provider_budget_id = getattr(result, "budget_id", None)
        if not provider_budget_id:
            return BudgetProviderMemberState(provider=_PROVIDER_NAME, sync_status=SyncStatus.FAILED)

        metadata = dict(result.metadata)
        metadata["internal_budget"] = True
        metadata["budget_scope"] = "project_member"
        metadata["provider_budget_id"] = provider_budget_id

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
                f"list_global_budget_states: get_customer_list failed while filtering internal budgets: {exc}"
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
            return
        provider_metadata = allocation.provider_metadata or {}
        await asyncio.to_thread(
            service.delete_project_member_budget_assignment,
            provider_member_ref=_metadata_value(provider_metadata, "provider_member_ref"),
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
                project_base_url = credentials.url or None

        provider_member_ref = _metadata_value(context.member_provider_metadata, "provider_member_ref")
        member_tracking_enabled = bool(
            context.project_name and SettingsService.get_project_member_budget_tracking_enabled(context.project_name)
        )
        if member_tracking_enabled and provider_member_ref:
            return BudgetRuntimeProviderResult(
                provider=_PROVIDER_NAME,
                api_key=project_api_key,
                base_url=project_base_url,
                headers={"x-litellm-customer-id": provider_member_ref},
                body_overrides={"user": provider_member_ref},
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
        service = self._get_service()
        if service is None:
            return [], []

        try:
            ref_to_alloc = await self._load_synced_member_allocations(provider_member_refs)
            if not ref_to_alloc:
                logger.debug("No synced project member allocations found; skipping spend collection")
                return [], []

            try:
                all_customers = await asyncio.to_thread(service.get_customer_list)
            except Exception as exc:
                logger.warning(f"collect_spend: get_customer_list failed: {exc}")
                return [], []

            member_snapshots, project_spend, project_reset = self._collect_member_spend_snapshots(
                all_customers=all_customers,
                ref_to_alloc=ref_to_alloc,
                provider_member_refs=provider_member_refs,
            )
            project_snapshots = self._build_project_spend_snapshots(project_spend, project_reset)

            logger.info(
                f"Collected {len(member_snapshots)} member spend snapshot(s) and "
                f"{len(project_snapshots)} project budget spend snapshot(s) from LiteLLM"
            )
            return member_snapshots, project_snapshots

        except Exception as exc:
            logger.warning(f"_load_member_spend_from_litellm failed: {exc}")
            return [], []

    async def _load_project_key_spend_from_litellm(self) -> list[ProjectBudgetSpendSnapshot]:
        """Load authoritative project/category spend from LiteLLM virtual keys."""
        service = self._get_service()
        if service is None:
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

            snapshots: list[ProjectBudgetSpendSnapshot] = []
            for assignment, budget in rows:
                provider_metadata = budget.provider_metadata or {}
                key_alias = _metadata_value(provider_metadata, "provider_budget_ref") or _metadata_value(
                    provider_metadata,
                    "key_alias",
                )
                if not isinstance(key_alias, str) or not key_alias:
                    continue

                credentials = SettingsService.get_project_litellm_creds_by_alias(assignment.project_name, key_alias)
                if not credentials:
                    continue

                key_spend = await asyncio.to_thread(service.get_key_spending_info, credentials.api_key)
                if not key_spend:
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

            logger.info(f"Collected {len(snapshots)} project key spend snapshot(s) from LiteLLM")
            return snapshots

        except Exception as exc:
            logger.warning(f"_load_project_key_spend_from_litellm failed: {exc}")
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
