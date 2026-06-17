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

from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Optional, TypeVar

from langchain_openai import AzureChatOpenAI
from langchain_core.language_models import LanguageModelInput
from langchain_core.runnables import Runnable
from pydantic import BaseModel

from codemie.configs import logger

from .project_member_runtime_sync import ensure_project_member_runtime_ready_sync
from .runtime_budget_selection import RuntimeBudgetMode, select_runtime_budget_mode

if TYPE_CHECKING:
    from codemie.configs.llm_config import LLMModel
    from codemie.service.budget.budget_enums import BudgetCategory as CoreBudgetCategory
    from codemie.rest_api.models.settings import LiteLLMContext, LiteLLMCredentials
    from langchain_openai import AzureOpenAIEmbeddings

_BM = TypeVar("_BM", bound=BaseModel)
_DictOrPydanticClass = dict[str, Any] | type[_BM]
_DictOrPydantic = dict | _BM


def _metadata_value(metadata: dict[str, Any], key: str) -> Any:
    if key in metadata:
        return metadata[key]
    raw = metadata.get("raw")
    if isinstance(raw, dict):
        return raw.get(key)
    return None


def _anonymized_key_fingerprint(secret_value: str | None) -> str | None:
    """Return a stable non-secret fingerprint for provider keys used in logs."""
    if not secret_value:
        return None
    return sha256(secret_value.encode("utf-8")).hexdigest()[:12]


class LiteLLMChatOpenAI(AzureChatOpenAI):
    """
    Extended AzureChatOpenAI that forces 'function_calling' for all structured output.

    Overrides with_structured_output to unconditionally use method='function_calling'
    for all model providers. The LiteLLM proxy does not reliably handle the
    json_schema response_format, causing JSONDecodeError on responses with extra data.

    Attributes:
        llm_model_details: Model configuration (not a Pydantic field)
    """

    def __init__(self, llm_model_details: "LLMModel", **kwargs: Any):
        """
        Initialize LiteLLMChatOpenAI.

        Args:
            llm_model_details: Model configuration details
            **kwargs: Keyword arguments passed to AzureChatOpenAI parent class
        """
        # Initialize parent AzureChatOpenAI class first
        super().__init__(**kwargs)

        # Store llm_model_details as a regular attribute (not a Pydantic field)
        # Use object.__setattr__ to bypass Pydantic's __setattr__
        object.__setattr__(self, 'llm_model_details', llm_model_details)

    def with_structured_output(
        self,
        schema: Optional[_DictOrPydanticClass] = None,
        *,
        include_raw: bool = False,
        strict: Optional[bool] = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, _DictOrPydantic]:
        """
        Override with_structured_output to force function_calling for LiteLLM compatibility.

        The LiteLLM proxy does not reliably handle json_schema response_format,
        causing JSONDecodeError on responses with extra data. Tool-calling-based
        structured output (function_calling) works correctly with the proxy.

        Args:
            schema: Pydantic model or dict defining the output structure
            include_raw: Whether to include raw response
            strict: Whether to use strict mode
            **kwargs: Additional arguments (note: 'tools' is not supported)

        Returns:
            Runnable that produces structured output

        Raises:
            ValueError: If 'tools' kwarg is passed (incompatible with function_calling)
        """
        if "tools" in kwargs:
            raise ValueError(
                "LiteLLMChatOpenAI.with_structured_output does not support the 'tools' parameter "
                "because it forces method='function_calling'. Remove the 'tools' argument."
            )
        logger.debug(f"Using function_calling method for model: {self.llm_model_details.base_name}")
        return super().with_structured_output(
            schema, method="function_calling", include_raw=include_raw, strict=strict, **kwargs
        )


def create_litellm_chat_model(
    llm_model_details: "LLMModel",
    litellm_context: Optional["LiteLLMContext"],
    user_email: Optional[str],
    user_id: Optional[str] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    streaming: bool = True,
) -> AzureChatOpenAI:
    """
    Create LiteLLM chat model instance (enterprise business logic).

    This function creates an AzureChatOpenAI instance configured to connect
    to LiteLLM proxy. It handles:
    - Credentials from context (user-specific) or app key
    - Budget checking for non-credentialed users
    - Header generation (x-litellm-tags, etc.)
    - Model configuration

    Args:
        llm_model_details: LLM model configuration from core
        litellm_context: Optional LiteLLM context with credentials and project info
        user_email: User email for budget tracking
        temperature: Optional temperature override
        top_p: Optional top_p override
        streaming: Enable streaming responses

    Returns:
        AzureChatOpenAI instance configured for LiteLLM proxy

    Raises:
        Exception: If model creation fails
    """

    logger.debug(f"Creating LiteLLM chat model: {llm_model_details.base_name}")

    # Extract credentials from context
    creds: Optional["LiteLLMCredentials"] = litellm_context.credentials if litellm_context else None

    # Generate merged headers (business logic)
    merged_headers = _generate_litellm_headers(llm_model_details, litellm_context)

    # If the key is empty, set placeholder value
    # (empty string would cause LangChain to read from environment)
    if creds and not creds.api_key:
        creds.api_key = "empty_key"

    base_args = _build_chat_model_base_args(
        llm_model_details=llm_model_details,
        creds=creds,
        temperature=temperature,
        top_p=top_p,
        streaming=streaming,
    )

    _configure_direct_runtime_overrides(
        llm_model_details=llm_model_details,
        litellm_context=litellm_context,
        user_email=user_email,
        user_id=user_id,
        creds=creds,
        merged_headers=merged_headers,
        request_params=base_args,
    )

    # Enable streaming usage tracking
    if streaming:
        base_args['stream_usage'] = True

    # Add custom headers
    if merged_headers:
        _format_headers_for_openai_api(merged_headers)
        base_args['default_headers'] = merged_headers

    # Filter parameters based on model features
    filtered_args = _filter_litellm_params(base_args, llm_model_details)

    # Return LiteLLMChatOpenAI instance (forces function_calling for all structured output)
    return LiteLLMChatOpenAI(llm_model_details=llm_model_details, **filtered_args)


def _build_chat_model_base_args(
    *,
    llm_model_details: "LLMModel",
    creds: Optional["LiteLLMCredentials"],
    temperature: Optional[float],
    top_p: Optional[float],
    streaming: bool,
) -> dict[str, Any]:
    from codemie.configs import config

    return {
        'azure_endpoint': config.LITE_LLM_URL,
        'openai_api_version': llm_model_details.api_version or config.OPENAI_API_VERSION,
        'openai_api_key': creds.api_key if creds else config.LITE_LLM_APP_KEY,
        'openai_api_type': config.OPENAI_API_TYPE,
        'deployment_name': llm_model_details.base_name,
        'model_name': llm_model_details.base_name,
        'streaming': streaming,
        'max_retries': config.AZURE_OPENAI_MAX_RETRIES,
        'temperature': temperature,
        'top_p': top_p,
    }


def _build_embedding_model_params(
    *,
    embedding_model: str,
    creds: Optional["LiteLLMCredentials"],
) -> dict[str, Any]:
    from codemie.configs import config
    from codemie.service.llm_service.llm_service import LLMService

    return {
        'openai_api_key': creds.api_key if creds and creds.api_key else config.LITE_LLM_APP_KEY,
        "azure_endpoint": config.LITE_LLM_URL,
        "deployment": embedding_model,
        "model": embedding_model,
        "tiktoken_model_name": LLMService.BASE_NAME_GPT_41_MINI,
        "openai_api_type": config.OPENAI_API_TYPE,
        "api_version": config.OPENAI_API_VERSION,
        "max_retries": 10,
        "show_progress_bar": True,
        "check_embedding_ctx_length": False,
    }


def _has_project_runtime_overrides(
    runtime_user: str | None,
    runtime_headers: dict[str, str],
    runtime_api_key: str | None,
    runtime_base_url: str | None,
) -> bool:
    return bool(runtime_user or runtime_headers or runtime_api_key or runtime_base_url)


def _apply_project_runtime_overrides(
    *,
    merged_headers: dict[str, str],
    request_params: dict[str, Any],
    runtime_user: str | None,
    runtime_headers: dict[str, str],
    runtime_api_key: str | None,
    runtime_base_url: str | None,
) -> None:
    from codemie.configs import config

    merged_headers.update(runtime_headers)
    request_params["openai_api_key"] = runtime_api_key or config.LITE_LLM_APP_KEY
    if runtime_base_url:
        request_params["azure_endpoint"] = runtime_base_url
    if runtime_user:
        request_params["model_kwargs"] = {"user": runtime_user}


def _mirror_platform_budget_assignment(*, user_id: str | None, customer: Any | None) -> None:
    """Best-effort: mirror the PLATFORM budget assignment into user_budget_assignments.

    Called from the synchronous direct-runtime path after check_user_budget() creates
    the LiteLLM customer.  Dispatches to the main event loop without blocking so that
    the model-creation call-site is not affected if the DB write is slow or fails.

    Uses the budget_id from the actual LiteLLM customer record so that users with a
    custom budget get the correct ID mirrored.  Falls back to the configured default
    platform budget_id when the customer has no budget table attached.
    """
    if not user_id:
        return
    import asyncio

    from .project_member_runtime_sync import _main_event_loop
    from codemie.service.budget.budget_enums import BudgetCategory as CoreBudgetCategory
    from codemie.service.budget.budget_service import budget_service

    budget_table = getattr(customer, "litellm_budget_table", None) if customer is not None else None
    platform_budget_id: str | None = getattr(budget_table, "budget_id", None) if budget_table is not None else None

    if not platform_budget_id:
        from .budget_categories import BudgetCategory as LiteLLMBudgetCategory
        from .dependencies import get_category_budget_id

        platform_budget_id = get_category_budget_id(LiteLLMBudgetCategory.PLATFORM)

    if not platform_budget_id:
        return
    loop = _main_event_loop
    if loop is None or not loop.is_running():
        return
    asyncio.run_coroutine_threadsafe(
        budget_service.track_proxy_budget_assignment_for_request(
            user_id=user_id,
            category=CoreBudgetCategory.PLATFORM,
            budget_id=platform_budget_id,
        ),
        loop,
    )


def _configure_direct_runtime_overrides(
    *,
    llm_model_details: "LLMModel",
    litellm_context: Optional["LiteLLMContext"],
    user_email: Optional[str],
    user_id: Optional[str],
    creds: Optional["LiteLLMCredentials"],
    merged_headers: dict[str, str],
    request_params: dict[str, Any],
) -> None:
    if creds:
        logger.info(
            f"budget_event=runtime_mode_selected component=litellm_llm_factory "
            f"user_id={user_id!r} username={user_email!r} "
            f"mode={RuntimeBudgetMode.USER_CREDENTIALS_BYPASS.value!r} reason=own_credentials"
        )
        # Even in bypass mode, resolve project member runtime to inject end_user for
        # override-customer spending tracking. Only model_kwargs["user"] is taken from
        # project runtime; api_key/base_url are intentionally ignored — creds from
        # litellm_context take precedence.
        (project_runtime_user, _, _, _) = _resolve_direct_project_budget_runtime(
            llm_model_details=llm_model_details,
            litellm_context=litellm_context,
            user_id=user_id,
            user_email=user_email,
        )
        if project_runtime_user:
            request_params["model_kwargs"] = {"user": project_runtime_user}
        return

    (
        project_runtime_user,
        project_runtime_headers,
        project_runtime_api_key,
        project_runtime_base_url,
    ) = _resolve_direct_project_budget_runtime(
        llm_model_details=llm_model_details,
        litellm_context=litellm_context,
        user_id=user_id,
        user_email=user_email,
    )

    if _has_project_runtime_overrides(
        project_runtime_user,
        project_runtime_headers,
        project_runtime_api_key,
        project_runtime_base_url,
    ):
        _apply_project_runtime_overrides(
            merged_headers=merged_headers,
            request_params=request_params,
            runtime_user=project_runtime_user,
            runtime_headers=project_runtime_headers,
            runtime_api_key=project_runtime_api_key,
            runtime_base_url=project_runtime_base_url,
        )
        logger.info(
            f"budget_event=runtime_provider_overrides_applied component=litellm_llm_factory "
            f"user_id={user_id!r} username={user_email!r} model={llm_model_details.base_name!r} "
            f"api_key_present={project_runtime_api_key is not None} "
            f"api_key_fingerprint={_anonymized_key_fingerprint(project_runtime_api_key)!r} "
            f"base_url_present={project_runtime_base_url is not None} "
            f"headers_applied={bool(project_runtime_headers)} "
            f"provider_header_names={sorted(project_runtime_headers.keys())!r} "
            f"body_user_present={project_runtime_user is not None} "
            f"litellm_customer_key={project_runtime_user!r}"
        )
        return

    from .budget_categories import BudgetCategory as LiteLLMBudgetCategory
    from .dependencies import check_user_budget, get_category_budget_id

    platform_budget_id = (
        _get_direct_request_category_budget_id(user_id, LiteLLMBudgetCategory.PLATFORM) if user_id else None
    ) or get_category_budget_id(LiteLLMBudgetCategory.PLATFORM)
    logger.info(
        f"budget_event=runtime_mode_selected component=litellm_llm_factory "
        f"user_id={user_id!r} username={user_email!r} model={llm_model_details.base_name!r} "
        f"mode={RuntimeBudgetMode.GLOBAL_OR_PERSONAL_BUDGET.value!r} "
        f"litellm_customer_key={user_email!r}"
    )
    customer = check_user_budget(user_email=user_email, user_id=user_id, budget_id=platform_budget_id)
    request_params["model_kwargs"] = {"user": user_email}
    _mirror_platform_budget_assignment(user_id=user_id, customer=customer)


def _get_direct_request_category_budget_id(user_id: str, category: str) -> str | None:
    """Return the user's currently assigned budget id for a direct request category."""
    from sqlmodel import Session, select

    from codemie.clients.postgres import PostgresClient
    from codemie.service.budget.budget_models import UserBudgetAssignment

    with Session(PostgresClient.get_engine()) as session:
        return session.exec(
            select(UserBudgetAssignment.budget_id).where(
                UserBudgetAssignment.user_id == user_id,
                UserBudgetAssignment.category == category,
            )
        ).first()


@dataclass(slots=True)
class DirectBudgetAvailability:
    user_budget_ids: dict[str, str | None] = field(default_factory=dict)
    project_scopes: set["CoreBudgetCategory"] = field(default_factory=set)


def _get_cached_direct_project_budget_scopes(project_name: str, user_id: str) -> set["CoreBudgetCategory"] | None:
    from codemie.service.budget.budget_enums import BudgetCategory as CoreBudgetCategory
    from codemie.service.budget.budget_resolution_service import _resolution_cache

    categories = (CoreBudgetCategory.PLATFORM, CoreBudgetCategory.CLI, CoreBudgetCategory.PREMIUM_MODELS)
    if not all((project_name, category.value, user_id) in _resolution_cache for category in categories):
        return None

    scopes = {
        category for category in categories if _resolution_cache[(project_name, category.value, user_id)] is not None
    }
    logger.debug(
        f"budget_event=project_budget_availability_cache_hit component=litellm_llm_factory "
        f"user_id={user_id!r} project_name={project_name!r} project_scopes={sorted(scope.value for scope in scopes)!r}"
    )
    return scopes


def _probe_direct_project_budget_scopes(project_name: str, user_id: str) -> set["CoreBudgetCategory"]:
    from sqlalchemy import text
    from sqlmodel import Session

    from codemie.clients.postgres import PostgresClient
    from codemie.service.budget.budget_enums import BudgetCategory as CoreBudgetCategory
    from codemie.service.budget.budget_resolution_service import BudgetScope, ResolvedBudgetContext, _resolution_cache

    cached_scopes = _get_cached_direct_project_budget_scopes(project_name, user_id)
    if cached_scopes is not None:
        return cached_scopes

    categories = [
        CoreBudgetCategory.PLATFORM.value,
        CoreBudgetCategory.CLI.value,
        CoreBudgetCategory.PREMIUM_MODELS.value,
    ]
    with Session(PostgresClient.get_engine()) as session:
        result = session.execute(
            text(
                """
                SELECT pba.budget_category,
                       pba.budget_id,
                       pmba.id                     AS allocation_id,
                       pmba.effective_budget_id    AS effective_budget_id,
                       pmba.shared_budget_id       AS shared_budget_id,
                       pmba.override_budget_id     AS override_budget_id,
                       b.provider_metadata         AS budget_meta,
                       pmba.pmba_provider_metadata AS member_meta
                FROM   project_budget_assignments pba
                JOIN   project_member_budget_assignments pmba
                         ON  pmba.project_name    = pba.project_name
                         AND pmba.budget_category = pba.budget_category
                         AND pmba.user_id         = :user_id
                         AND pmba.pmba_deleted_at IS NULL
                JOIN   budgets b ON b.budget_id = pba.budget_id
                WHERE  pba.project_name = :project_name
                  AND  pba.budget_category = ANY(:categories)
                  AND  pba.deleted_at IS NULL
                """
            ),
            {"project_name": project_name, "user_id": user_id, "categories": categories},
        )
        rows = {row["budget_category"]: row for row in result.mappings().all()}

    scopes: set[CoreBudgetCategory] = set()
    for category in (CoreBudgetCategory.PLATFORM, CoreBudgetCategory.CLI, CoreBudgetCategory.PREMIUM_MODELS):
        cache_key = (project_name, category.value, user_id)
        row = rows.get(category.value)
        if row is None:
            _resolution_cache[cache_key] = None
            continue

        scopes.add(category)
        _resolution_cache[cache_key] = ResolvedBudgetContext(
            scope=BudgetScope.PROJECT,
            project_name=project_name,
            budget_category=category,
            budget_id=row["budget_id"],
            effective_budget_id=row.get("effective_budget_id"),
            shared_budget_id=row.get("shared_budget_id"),
            override_budget_id=row.get("override_budget_id"),
            member_allocation_id=row["allocation_id"],
            provider_metadata=row["budget_meta"] or {},
            member_provider_metadata=row["member_meta"] or {},
        )

    logger.info(
        f"budget_event=project_budget_availability_probed component=litellm_llm_factory "
        f"user_id={user_id!r} project_name={project_name!r} project_scopes={sorted(scope.value for scope in scopes)!r}"
    )
    return scopes


def _resolve_direct_budget_availability(project_name: str, user_id: str) -> DirectBudgetAvailability:
    from codemie.service.budget.budget_service import budget_service

    return DirectBudgetAvailability(
        user_budget_ids=budget_service.get_all_category_budget_ids_for_request_sync(user_id),
        project_scopes=_probe_direct_project_budget_scopes(project_name, user_id),
    )


def _resolve_direct_budget_category(
    *,
    user_email: str,
    llm_model: str,
    availability: DirectBudgetAvailability,
) -> "CoreBudgetCategory":
    from codemie.service.budget.budget_enums import BudgetCategory as CoreBudgetCategory

    from .budget_categories import BudgetCategory as LiteLLMBudgetCategory
    from .dependencies import get_category_budget_id, get_premium_username

    premium_budget_id = availability.user_budget_ids.get(CoreBudgetCategory.PREMIUM_MODELS.value)
    if (
        get_premium_username(user_email, llm_model) is not None
        and CoreBudgetCategory.PREMIUM_MODELS in availability.project_scopes
    ):
        logger.info(
            f"budget_event=runtime_category_selected component=litellm_llm_factory "
            f"username={user_email!r} model={llm_model!r} "
            f"budget_category={CoreBudgetCategory.PREMIUM_MODELS.value!r} reason=project_premium_scope"
        )
        return CoreBudgetCategory.PREMIUM_MODELS

    if not availability.project_scopes:
        premium_budget_id = premium_budget_id or get_category_budget_id(LiteLLMBudgetCategory.PREMIUM_MODELS)

    if (
        get_premium_username(user_email, llm_model) is not None
        and premium_budget_id
        and (not availability.project_scopes or CoreBudgetCategory.PREMIUM_MODELS in availability.project_scopes)
    ):
        logger.info(
            f"budget_event=runtime_category_selected component=litellm_llm_factory "
            f"username={user_email!r} model={llm_model!r} "
            f"budget_category={CoreBudgetCategory.PREMIUM_MODELS.value!r} reason=premium_model_no_project_scope"
        )
        return CoreBudgetCategory.PREMIUM_MODELS

    logger.info(
        f"budget_event=runtime_category_selected component=litellm_llm_factory "
        f"username={user_email!r} model={llm_model!r} "
        f"budget_category={CoreBudgetCategory.PLATFORM.value!r} reason=platform_default"
    )
    return CoreBudgetCategory.PLATFORM


def _resolve_direct_project_budget_runtime(
    *,
    llm_model_details: "LLMModel",
    litellm_context: Optional["LiteLLMContext"],
    user_id: Optional[str],
    user_email: Optional[str],
) -> tuple[str | None, dict[str, str], str | None, str | None]:
    """Resolve project budget headers for direct LiteLLM chat model usage."""
    if not litellm_context or not litellm_context.current_project or not user_id or not user_email:
        logger.info(
            f"budget_event=budget_resolution_global_fallback component=litellm_llm_factory path=sync "
            f"user_id={user_id!r} username={user_email!r} "
            f"project_name={(litellm_context.current_project if litellm_context else None)!r} "
            f"model={llm_model_details.base_name!r} reason=missing_context_or_project"
        )
        return None, {}, None, None

    from codemie.service.budget.budget_resolution_service import budget_resolution_service

    availability = _resolve_direct_budget_availability(
        litellm_context.current_project,
        user_id,
    )
    category = _resolve_direct_budget_category(
        user_email=user_email,
        llm_model=llm_model_details.base_name,
        availability=availability,
    )

    project_name = litellm_context.current_project
    logger.info(
        f"budget_event=runtime_category_selected component=litellm_llm_factory "
        f"user_id={user_id!r} username={user_email!r} project_name={project_name!r} "
        f"budget_category={category.value!r} model={llm_model_details.base_name!r}"
    )
    ensure_project_member_runtime_ready_sync(
        user_id=user_id,
        user_email=user_email,
        project_name=project_name,
        budget_category=category,
    )

    resolved = budget_resolution_service.resolve_sync(
        user_id=user_id,
        project_name=project_name,
        budget_category=category,
    )
    logger.info(
        f"budget_event=runtime_budget_resolved component=litellm_llm_factory "
        f"user_id={user_id!r} username={user_email!r} project_name={project_name!r} "
        f"budget_category={category.value!r} scope={resolved.scope.value!r} "
        f"budget_id={resolved.budget_id!r} effective_budget_id={resolved.effective_budget_id!r} "
        f"shared_budget_id={resolved.shared_budget_id!r} override_budget_id={resolved.override_budget_id!r} "
        f"allocation_id={resolved.member_allocation_id!r} model={llm_model_details.base_name!r}"
    )
    provider_result = budget_resolution_service.dispatch_runtime_sync(
        resolved, user_id=user_id, user_email=user_email, model=llm_model_details.base_name
    )
    if provider_result is None:
        logger.info(
            f"budget_event=runtime_project_budget_resolution_skipped component=litellm_llm_factory "
            f"user_id={user_id!r} username={user_email!r} project_name={project_name!r} "
            f"budget_category={category.value!r} model={llm_model_details.base_name!r} "
            f"reason=no_provider_result"
        )
        return None, {}, None, None

    selection = select_runtime_budget_mode(
        has_user_litellm_credentials=False,
        project_name=project_name,
        project_member_tracking_enabled=True,
        resolved_project_budget=True,
    )
    runtime_user = provider_result.body_overrides.get("user")
    logger.info(
        f"budget_event=runtime_mode_selected component=litellm_llm_factory "
        f"user_id={user_id!r} username={user_email!r} project_name={project_name!r} "
        f"budget_category={category.value!r} model={llm_model_details.base_name!r} "
        f"mode={selection.mode.value!r} "
        f"api_key_present={provider_result.api_key is not None} "
        f"api_key_fingerprint={_anonymized_key_fingerprint(provider_result.api_key)!r} "
        f"headers_applied={bool(provider_result.headers)} "
        f"provider_header_names={sorted(provider_result.headers.keys())!r} "
        f"litellm_customer_key={runtime_user!r}"
    )
    if selection.mode == RuntimeBudgetMode.PROJECT_BUDGET_WITH_MEMBER_TRACKING:
        if not isinstance(runtime_user, str) or not runtime_user:
            raise RuntimeError(
                f"Project member runtime selected but provider returned no runtime user for {project_name!r}"
            )
        return runtime_user, provider_result.headers, provider_result.api_key, provider_result.base_url

    return None, provider_result.headers, provider_result.api_key, provider_result.base_url


def create_litellm_embedding_model(
    embedding_model: str,
    llm_model_details: "LLMModel",
    litellm_context: Optional["LiteLLMContext"],
    user_email: Optional[str],
    user_id: Optional[str] = None,
) -> "AzureOpenAIEmbeddings":
    """
    Create LiteLLM embedding model instance (enterprise business logic).

    This function creates an AzureOpenAIEmbeddings instance configured for
    LiteLLM proxy embeddings endpoint.

    Args:
        embedding_model: Model name for embeddings
        llm_model_details: Model configuration details
        litellm_context: Optional LiteLLM context with credentials
        user_email: User email for budget tracking
        user_id: Codemie user id for project member budget tracking

    Returns:
        AzureOpenAIEmbeddings instance configured for LiteLLM proxy
    """
    from langchain_openai import AzureOpenAIEmbeddings

    logger.debug(f"Creating LiteLLM embedding model: {embedding_model}")

    # Extract credentials
    creds: Optional["LiteLLMCredentials"] = litellm_context.credentials if litellm_context else None

    # Generate headers
    merged_headers = _generate_litellm_headers(llm_model_details, litellm_context)

    embedding_params = _build_embedding_model_params(embedding_model=embedding_model, creds=creds)
    _configure_direct_runtime_overrides(
        llm_model_details=llm_model_details,
        litellm_context=litellm_context,
        user_email=user_email,
        user_id=user_id,
        creds=creds,
        merged_headers=merged_headers,
        request_params=embedding_params,
    )

    # Add headers
    if merged_headers:
        embedding_params['default_headers'] = merged_headers

    return AzureOpenAIEmbeddings(**embedding_params)


def generate_litellm_headers_from_context(
    litellm_context: Optional["LiteLLMContext"],
) -> dict[str, str]:
    """
    Generate LiteLLM context-based headers (public API for routers/services).

    Generates x-litellm-tags header based on current project in context.
    This is the public API for code that needs to add LiteLLM headers to requests.

    Args:
        litellm_context: LiteLLM context with current project info

    Returns:
        Dictionary of headers to include in requests (always includes x-litellm-tags)
    """
    from codemie.configs import config

    headers = {}

    # Determine tag value based on context
    tag_value = config.LITE_LLM_TAGS_HEADER_VALUE  # default

    if litellm_context and litellm_context.current_project:
        # Parse allowed projects from config
        allowed_projects = [
            project.strip() for project in config.LITE_LLM_PROJECTS_TO_TAGS_LIST.split(",") if project.strip()
        ]

        # Use project name if it's in allowed list
        if allowed_projects and litellm_context.current_project in allowed_projects:
            tag_value = litellm_context.current_project

    # Always add the header (either project name or default)
    headers["x-litellm-tags"] = tag_value

    return headers


def _generate_litellm_headers(
    llm_model_details: "LLMModel",
    litellm_context: Optional["LiteLLMContext"],
) -> dict[str, str]:
    """
    Generate LiteLLM-specific headers (internal - for model creation).

    Handles:
    - Static model configuration headers
    - Dynamic x-litellm-tags header based on project
    - Project-to-tags mapping

    Args:
        llm_model_details: Model configuration with optional static headers
        litellm_context: LiteLLM context with current project info

    Returns:
        Dictionary of headers to include in requests
    """
    merged_headers = {}

    # Add static headers from model configuration
    if llm_model_details.configuration and llm_model_details.configuration.client_headers:
        merged_headers.update(llm_model_details.configuration.client_headers)

    # Add context-based headers (x-litellm-tags)
    context_headers = generate_litellm_headers_from_context(litellm_context)
    if context_headers:
        merged_headers.update(context_headers)

    return merged_headers


def _format_headers_for_openai_api(default_headers: dict[str, Any]) -> None:
    """
    Format headers for OpenAI API (in-place modification).

    Special handling for anthropic_beta header which needs JSON encoding.

    Args:
        default_headers: Headers dictionary to modify in-place
    """
    for key in default_headers:
        if key == "anthropic_beta":
            default_headers[key] = json.dumps(default_headers[key])


def _filter_litellm_params(
    base_args: dict[str, Any],
    llm_model_details: "LLMModel",
) -> dict[str, Any]:
    """
    Filter LiteLLM model parameters based on model features.

    Some models don't support certain parameters (temperature, streaming, etc.).
    This function removes or adjusts parameters based on model capabilities.

    Args:
        base_args: Base model arguments
        llm_model_details: Model details with feature flags

    Returns:
        Filtered arguments dictionary
    """
    filtered = base_args.copy()

    # Filter based on model features
    if llm_model_details.features.temperature is False:
        filtered['temperature'] = 1

    if llm_model_details.features.parallel_tool_calls is False:
        filtered['disabled_params'] = {"parallel_tool_calls": None}

    if llm_model_details.features.streaming is False:
        filtered['streaming'] = False
        filtered['disable_streaming'] = True
        if 'stream_usage' in filtered:
            filtered.pop('stream_usage')

    if llm_model_details.features.max_tokens is False:
        filtered['max_tokens'] = None

    if llm_model_details.features.top_p is False:
        filtered['top_p'] = None

    return filtered


def get_litellm_chat_model(
    llm_model_details: "LLMModel",
    litellm_context: Optional["LiteLLMContext"],
    user_email: Optional[str],
    user_id: Optional[str] = None,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    streaming: bool = True,
) -> Optional["AzureChatOpenAI"]:
    """
    Get LiteLLM chat model if enterprise is enabled AND proxy mode is lite_llm.

    This is the public wrapper called from core. It provides graceful degradation:
    - Returns None if LiteLLM not enabled (core falls back to internal providers)
    - Returns None if LLM_PROXY_MODE is not "lite_llm" (use internal proxy instead)
    - Returns None on error (graceful degradation)
    - Returns model if successful

    This function is the abstraction boundary between core and enterprise.
    Core doesn't know HOW LiteLLM works, just calls this and checks for None.

    Args:
        llm_model_details: Model configuration
        litellm_context: LiteLLM context with credentials
        user_email: User email for budget tracking
        temperature: Optional temperature override
        top_p: Optional top_p override
        streaming: Enable streaming

    Returns:
        AzureChatOpenAI configured for LiteLLM, or None if not available
    """
    from .dependencies import is_litellm_enabled
    from codemie.configs import config
    from codemie.core.constants import LLMProxyMode

    # Check if LiteLLM is enabled (HAS_LITELLM + LLM_PROXY_ENABLED)
    if not is_litellm_enabled():
        return None

    # Check if we're specifically using LiteLLM mode (routing check)
    if LLMProxyMode.lite_llm != config.LLM_PROXY_MODE:
        return None

    try:
        return create_litellm_chat_model(
            llm_model_details=llm_model_details,
            litellm_context=litellm_context,
            user_email=user_email,
            user_id=user_id,
            temperature=temperature,
            top_p=top_p,
            streaming=streaming,
        )
    except Exception:
        logger.exception("Failed to create LiteLLM chat model while LiteLLM proxy is enabled")
        raise


def get_litellm_embedding_model(
    embedding_model: str,
    llm_model_details: "LLMModel",
    litellm_context: Optional["LiteLLMContext"],
    user_email: Optional[str],
    user_id: Optional[str] = None,
) -> Optional["AzureOpenAIEmbeddings"]:
    """
    Get LiteLLM embedding model if enterprise is enabled AND proxy mode is lite_llm.

    Similar to get_litellm_chat_model but for embeddings.

    Args:
        embedding_model: Model name
        llm_model_details: Model configuration
        litellm_context: LiteLLM context with credentials
        user_email: User email for budget tracking
        user_id: Codemie user id for project member budget tracking

    Returns:
        AzureOpenAIEmbeddings configured for LiteLLM, or None if not available
    """
    from .dependencies import is_litellm_enabled
    from codemie.configs import config
    from codemie.core.constants import LLMProxyMode

    # Check if LiteLLM embeddings are explicitly disabled (use native providers instead)
    if config.LLM_PROXY_EMBEDDINGS_DISABLED:
        logger.debug("LiteLLM embeddings disabled via config, falling back to native providers")
        return None

    # Check if LiteLLM is enabled (HAS_LITELLM + LLM_PROXY_ENABLED)
    if not is_litellm_enabled():
        return None

    # Check if we're specifically using LiteLLM mode (routing check)
    if LLMProxyMode.lite_llm != config.LLM_PROXY_MODE:
        return None

    try:
        return create_litellm_embedding_model(
            embedding_model=embedding_model,
            llm_model_details=llm_model_details,
            litellm_context=litellm_context,
            user_email=user_email,
            user_id=user_id,
        )
    except Exception:
        logger.exception("Failed to create LiteLLM embedding model while LiteLLM proxy is enabled")
        raise
