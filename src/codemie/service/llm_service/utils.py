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

from typing import TYPE_CHECKING

from fastapi import status

from codemie.configs import config, logger
from codemie.core.dependecies import set_dial_credentials, set_litellm_context
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.assistant import AssistantBase
from codemie.rest_api.models.settings import LiteLLMContext
from codemie.rest_api.security.user import User
from codemie.service.budget.attribution_resolver import (
    AssetKind,
    AttributionDecision,
    AttributionInput,
    attribution_input_for_user,
    member_projects,
    resolve_attribution,
)
from codemie.service.settings.settings import SettingsService

if TYPE_CHECKING:
    from codemie.core.workflow_models.workflow_config import WorkflowConfigBase
    from codemie.rest_api.models.index import IndexInfo


def _asset_project(asset: AssistantBase | WorkflowConfigBase | IndexInfo) -> str:
    project = getattr(asset, 'project', None) or getattr(asset, 'project_name', None)
    if not project:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Asset project is not set",
            details=f"Asset {getattr(asset, 'id', None)} has no project assigned.",
        )
    return project


def _credentials_project(
    asset: AssistantBase | WorkflowConfigBase | IndexInfo | None,
    fallback_project_name: str | None,
    user: User,
) -> str | None:
    """Project whose integrations hold the user's own LiteLLM key and DIAL settings.

    Kept as before EPMCDME-16625 (matrix row 18): only budget attribution moved to the resolver, so a key the
    user keeps for the asset's project still pays exactly as it did.
    """
    if asset is None:
        return fallback_project_name
    project = _asset_project(asset)
    if not getattr(asset, 'is_global', False) or not user.email:
        return project
    if project in member_projects(user):
        return project
    default_project = getattr(user, 'default_project', None)
    return default_project if isinstance(default_project, str) and default_project else user.email


def _bills_as_asset_less(asset: object) -> bool:
    # Read without getattr: a SQLModel instance may have no private storage, and getattr then raises.
    dct = getattr(asset, "__dict__", None)
    if isinstance(dct, dict) and "_runtime_bills_as_asset_less" in dct:
        return bool(dct["_runtime_bills_as_asset_less"])
    private = getattr(asset, "__pydantic_private__", None)
    return isinstance(private, dict) and bool(private.get("_runtime_bills_as_asset_less"))


def _build_attribution_input(
    asset: AssistantBase | WorkflowConfigBase | IndexInfo | None,
    fallback_project_name: str | None,
    user: User,
) -> AttributionInput:
    if asset is None or _bills_as_asset_less(asset):
        # No asset: the default project is the target. The caller's project hint is only a last resort for users
        # without a default (EPMCDME-16625 matrix row 12).
        return attribution_input_for_user(user, kind=AssetKind.NO_ASSET, fallback_default=fallback_project_name)
    return attribution_input_for_user(
        user,
        kind=AssetKind.PROJECT_ASSET,
        asset_project=_asset_project(asset),
        is_global=bool(getattr(asset, 'is_global', False)),
    )


def _is_shared_asset_creds_override(setting: object, asset: object) -> bool:
    """Return True when a shared asset must not run on the user's non-global personal key.

    Since EPMCDME-16625 this only takes the key away; which budget then pays is the attribution walk's decision,
    not necessarily the asset's project (AR-11).
    """
    if asset is None or not config.LLM_PROXY_SHARED_ASSET_PROJECT_BUDGET_ROUTING_ENABLED:
        return False
    is_shared = getattr(asset, 'shared', None)
    if is_shared is None:
        is_shared = getattr(asset, 'project_space_visible', True)
    return bool(is_shared and not getattr(setting, 'is_global', False))


def _litellm_budgets_active() -> bool:
    """Budgets apply only when requests go through LiteLLM; other proxy modes keep the asset's project tags.

    Indexing is attributed too, even when LLM_PROXY_EMBEDDINGS_DISABLED sends its embeddings to native providers:
    the chat models it builds for summaries and documents still go through LiteLLM (EPMCDME-16625 row 13).
    """
    try:
        from codemie.enterprise.litellm.dependencies import is_litellm_enabled
    except ImportError:
        return False
    return is_litellm_enabled() and config.LLM_PROXY_MODE == "lite_llm"


def _funded_check_model(asset: object, llm_model: str | None) -> str:
    """The model whose budget category the funded check reads (CR-011): the one the request runs on.

    Indexing is checked against its embedding model, unless embeddings bypass LiteLLM: then only its chat models
    reach a budget, so the platform's default chat model stands for them.
    """
    from codemie.rest_api.models.index import IndexInfo
    from codemie.service.llm_service.llm_service import llm_service

    if model := llm_model or getattr(asset, 'llm_model_type', None):
        return model
    if not isinstance(asset, IndexInfo):
        return llm_service.default_llm_model
    if config.LLM_PROXY_EMBEDDINGS_DISABLED:
        return llm_service.default_llm_model
    return getattr(asset, 'embeddings_model', None) or llm_service.default_embedding_model


def _unfunded_project(
    project: str | None, litellm_creds: object, user: User, llm_model: str | None = None
) -> str | None:
    """Return project when its budget cannot pay for this user; the resolver then moves to the next step.

    Decided here, where attribution is decided, because agents re-create the context in other
    threads: marking it only where the model is built left analytics on copies that never saw
    the fallback (EPMCDME-15111 AC6/AC10). Own-key requests are paid by that key, not a budget.
    Raises when funding cannot be read, so the resolver counts the step as unfunded with funding unknown, exactly
    as the CLI does (AR-1). A user without an email is probed like any other: a stand-in owner must not be passed
    as able to pay unchecked (AR-3).
    """
    if litellm_creds or not project or (user.email and project == user.email):
        return None
    if not user.id:
        raise ValueError("no user id to check project funding with")
    try:
        from codemie.enterprise.litellm.dependencies import is_litellm_enabled
        from codemie.enterprise.litellm.llm_factory import (
            DirectBudgetAvailability,
            _probe_direct_project_budget_scopes,
            _resolve_direct_budget_category,
        )
    except ImportError:
        return None
    if not is_litellm_enabled() or config.LLM_PROXY_MODE != "lite_llm":
        return None
    try:
        scopes = _probe_direct_project_budget_scopes(project, user.id)
        if scopes:
            if not llm_model:
                # Model construction records the actual result when the model is not known yet.
                return None
            from codemie.service.llm_service.llm_service import llm_service

            category = _resolve_direct_budget_category(
                username=user.username,
                llm_model=llm_service.get_model_details(llm_model).base_name,
                availability=DirectBudgetAvailability(project_scopes=scopes),
            )
            if category in scopes:
                return None
    except Exception as e:
        logger.warning(f"budget scope probe failed for project={project!r} user_id={user.id!r}: {e}")
        raise
    logger.debug(
        f"budget_event=project_budget_unfunded component=llm_context user_id={user.id!r} project_name={project!r}"
    )
    return project


def _context_from_decision(
    decision: AttributionDecision, litellm_creds: object, user: User, routing_project: str | None = None
) -> LiteLLMContext:
    """Translate the resolver decision into the context analytics and the runtime read.

    The personal step is named as the CLI names it (email, else username), so a user without an email, such as a
    stand-in owner, is reported as a personal charge rather than under the asset's project.
    """
    personal_project = user.email or user.username or None
    kind = decision.fallback_kind
    if kind is not None:
        logger.info(
            f"budget_event=budget_attribution_fallback component=llm_context user_id={user.id!r} "
            f"fallback_from_project={decision.targeted_project!r} charged_scope={kind.value!r} "
            f"charged_project={decision.charged_project!r} funding_unknown={decision.funding_unknown}"
        )
    return LiteLLMContext(
        credentials=litellm_creds,
        current_project=decision.charged_project or personal_project,
        principal_email=user.email,
        personal_project=personal_project,
        budget_fallback_from=decision.targeted_project if kind else None,
        budget_fallback_kind=kind.value if kind else None,
        budget_candidates=list(decision.candidates),
        routing_project=routing_project,
        budget_funding_unknown=decision.funding_unknown,
    )


def _outcome(context: LiteLLMContext) -> tuple:
    return context.current_project, context.budget_fallback_from, context.budget_fallback_kind


def _keep_built_outcome(context: LiteLLMContext) -> LiteLLMContext:
    """Keep the payer model construction recorded when a re-decision for the same request cannot read funding.

    An agent builds its model once, with its billing headers fixed, then sets the context again before every call.
    A re-decision whose funded check failed would record a fallback the built model does not follow, so analytics
    would name a payer LiteLLM did not charge (final review AR-2).
    """
    from codemie.core.dependecies import litellm_context as current_context

    previous = current_context.get(None)
    if previous is None or not previous.budget_outcome_built:
        return context
    same_request = (previous.principal_email, previous.routing_project, previous.budget_candidates) == (
        context.principal_email,
        context.routing_project,
        context.budget_candidates,
    )
    if not same_request:
        return context
    if not context.budget_funding_unknown:
        # A readable re-decision that agrees with the build still describes the built model, so later calls of the
        # same agent keep it too.
        if _outcome(context) == _outcome(previous):
            return context.model_copy(update={"budget_outcome_built": True})
        return context
    logger.info(
        f"budget_event=built_outcome_kept component=llm_context charged_project={previous.current_project!r} "
        f"fallback_kind={previous.budget_fallback_kind!r} reason=funding_unknown"
    )
    return context.model_copy(
        update={
            "current_project": previous.current_project,
            "budget_fallback_from": previous.budget_fallback_from,
            "budget_fallback_kind": previous.budget_fallback_kind,
            "budget_funding_unknown": previous.budget_funding_unknown,
            "budget_outcome_built": True,
        }
    )


def set_llm_context(
    asset: AssistantBase | WorkflowConfigBase | IndexInfo | None,
    fallback_project_name: str | None,
    user: User,
    llm_model: str | None = None,
    credentials_asset: AssistantBase | WorkflowConfigBase | IndexInfo | None = None,
):
    """Set the LLM credentials and budget attribution for the current request.

    credentials_asset, when given, selects the own-key and DIAL credentials while asset decides only the budget:
    a marketplace workflow bills the saved assistant it runs but keeps the run's credentials (EPMCDME-16625).
    """
    from codemie.rest_api.models.settings import SettingType
    from codemie.service.settings.base_settings import SearchFields
    from codemie_tools.base.models import CredentialTypes

    attribution_input = _build_attribution_input(asset, fallback_project_name, user)
    credentials_source = credentials_asset if credentials_asset is not None else asset
    effective_project = _credentials_project(credentials_source, fallback_project_name, user)

    try:
        litellm_creds = SettingsService.get_litellm_creds(project_name=effective_project, user_id=user.id)
        if litellm_creds:
            setting = SettingsService.retrieve_setting(
                {
                    SearchFields.CREDENTIAL_TYPE: CredentialTypes.LITE_LLM,
                    SearchFields.PROJECT_NAME: effective_project,
                    SearchFields.USER_ID: user.id,
                }
            )
            # Project-scoped keys (e.g. platform/CLI budget keys) must not set litellm_context.credentials —
            # doing so would trigger USER_CREDENTIALS_BYPASS mode in llm_factory and skip override customer injection.
            if getattr(setting, "setting_type", None) == SettingType.PROJECT.value:
                litellm_creds = None
            elif _is_shared_asset_creds_override(setting, credentials_source):
                litellm_creds = None  # shared asset → force project budget when user has non-global personal key
        if not _litellm_budgets_active():
            # Without LiteLLM no budget pays, so attribution does not apply: keep the project as before
            # EPMCDME-16625, the same one whose DIAL credentials are used below.
            set_litellm_context(
                LiteLLMContext(
                    credentials=litellm_creds,
                    current_project=effective_project,
                    principal_email=user.email,
                    personal_project=user.email,
                )
            )
            set_dial_credentials(SettingsService.get_dial_creds(effective_project))
            return
        # Without a model the funded check could not tell budget categories apart and would pass a project funded
        # only for another category; the platform default is what such a request runs on (CR-011).
        model = _funded_check_model(asset, llm_model)
        decision = resolve_attribution(
            attribution_input, lambda project: _unfunded_project(project, litellm_creds, user, model) is None
        )
        litellm_context = _context_from_decision(decision, litellm_creds, user, routing_project=effective_project)
        set_litellm_context(_keep_built_outcome(litellm_context))
        dial_creds = SettingsService.get_dial_creds(effective_project)
        set_dial_credentials(dial_creds)
    except Exception as e:
        logger.warning(
            f"set_llm_context failed for project={effective_project!r} user={user.username!r} ({user.id}): {e} — "
            f"LLM calls will fall back to platform budget without project attribution"
        )
