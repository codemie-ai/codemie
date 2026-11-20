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

from dataclasses import dataclass, field
from enum import StrEnum
from collections.abc import Iterable
from typing import TYPE_CHECKING
from uuid import UUID


from codemie.configs import logger
from codemie.configs.customer_config import customer_config
from codemie.core.exceptions import ModelNotWhitelistedException, NoDefaultModelException, NotFoundException
from codemie.core.models import Application
from codemie.rest_api.models.user_model_override import UserModelOverride
from codemie.service.llm.user_override_cache import load_user_override
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import (
    MODEL_AVAILABILITY_FALLBACKS_TOTAL_METRIC,
    MODEL_AVAILABILITY_VALIDATIONS_TOTAL_METRIC,
    MetricsAttributes,
)

if TYPE_CHECKING:
    from codemie.rest_api.models.assistant import Assistant

PROJECT_MODEL_OVERRIDE_FEATURE = "projectModelOverride"
USER_MODEL_OVERRIDE_FEATURE = "userModelOverride"


class PolicyMode(StrEnum):
    UNRESTRICTED = "unrestricted"
    DENY_ALL = "deny_all"
    WHITELIST = "whitelist"


def model_aliases(model: str) -> set[str]:
    """The names a model can be referred to by: the given name plus the base_name and deployment_name
    of the one catalog model it names. An unknown name (a router, a test value) is only itself.

    The name picks a single catalog entry, by base_name first and by deployment_name otherwise, so a
    name that also appears on another entry never pulls in that entry's names.
    """
    entry = _catalog_entry(model)
    if entry is None:
        return {model}
    return {model} | {name for name in (entry.base_name, entry.deployment_name) if name}


def model_in_override(allowed_models: Iterable[str], *names: str | None) -> bool:
    """Whether a model, named by any of the given names, is in a user override's model list.

    The one rule behind every "does the override allow this model" question (resolution, asset creation,
    the OCR model and the model and router lists). Callers pass the names the model can be referred to by:
    model_aliases() for a name taken from a request, base_name and deployment_name for a catalog entry,
    base_name alone for a router.
    """
    allowed = set(allowed_models)
    return any(name in allowed for name in names if name)


def catalog_base_name(model: str | None) -> str | None:
    """The base_name of the catalog model a name refers to; an unknown name (a router) is returned as is.

    Project settings store deployment names, while execution looks models up by base_name (the
    ReAct check, file tools, history replay), so a model taken from project settings is converted.
    """
    if not model:
        return model
    entry = _catalog_entry(model)
    return entry.base_name if entry is not None and entry.base_name else model


def is_known_model(model: str) -> bool:
    """Whether a name refers to a catalog model (by base_name or deployment_name) or an enabled router
    that is not disabled at platform level.

    This is also the liveness check: a model removed from the catalog, or disabled by an administrator
    in platform model management, after it was saved in an override or a project is not known. The
    catalog keeps a disabled model with status DISABLED, so presence alone is not enough.
    """
    from codemie.service.llm_service.llm_service import llm_service

    # One catalog read per check: the resolution chain calls this at every tier, and each read re-merges
    # the persisted model status under a lock.
    catalog = llm_service.get_all_llm_model_info()
    known = _catalog_entry(model, catalog) is not None or any(
        r.base_name == model and r.enabled for r in llm_service.get_llm_routers(catalog)
    )
    return known and not llm_service.is_disabled_for_execution(model, catalog)


def _is_live_for_override(model: str, tier: str) -> bool:
    """is_known_model, logging the fallback tier a disabled model is skipped at."""
    if is_known_model(model):
        return True
    logger.info(f"user_model_override=model_not_live tier={tier} model={model!r}; skipping to the next fallback tier")
    return False


def _catalog_entry(model: str, catalog: list | None = None):
    """The one catalog model a name refers to: by base_name first, then by deployment_name.

    A caller that already holds the catalog passes it, so it is not read again.
    """
    if catalog is None:
        from codemie.service.llm_service.llm_service import llm_service

        catalog = llm_service.get_all_llm_model_info()
    return next((m for m in catalog if m.base_name == model), None) or next(
        (m for m in catalog if m.deployment_name == model), None
    )


@dataclass
class ProjectModelPolicy:
    """The single project model rule: unrestricted, deny-all or whitelist."""

    mode: PolicyMode
    allowed_models: list[str] = field(default_factory=list)
    default_model: str | None = None
    project_name: str | None = None

    @classmethod
    def for_project(
        cls,
        project: Application | str | None,
        is_global: bool = False,
        *,
        strict: bool = False,
        require_default: bool = True,
    ) -> "ProjectModelPolicy":
        """Build the policy. With strict=True a project id that does not exist raises NotFoundException.

        A populated whitelist without a default_model raises NoDefaultModelException unless
        require_default=False, which suits callers that only check membership and never substitute.
        """
        if project is None or is_global or not customer_config.is_feature_enabled(PROJECT_MODEL_OVERRIDE_FEATURE):
            return cls(mode=PolicyMode.UNRESTRICTED)

        name = project if isinstance(project, str) else getattr(project, "name", None) or getattr(project, "id", None)
        if isinstance(project, str):
            try:
                project = Application.get_by_id(project)
            except KeyError:
                if strict:
                    raise NotFoundException(f"Project '{project}' not found")
                return cls(mode=PolicyMode.UNRESTRICTED, project_name=name)

        return cls.for_application(project, name=name, require_default=require_default)

    @classmethod
    def for_application(
        cls, project: Application, *, name: str | None = None, require_default: bool = True
    ) -> "ProjectModelPolicy":
        """Build the policy from a loaded project's whitelist, without the feature-flag check.

        For callers that decide on the flag themselves (the model list filters in llm_service).
        """
        name = name or getattr(project, "name", None) or getattr(project, "id", None)
        allowed, default = project.allowed_models, project.default_model

        if allowed is None:
            return cls(mode=PolicyMode.UNRESTRICTED, default_model=default, project_name=name)
        if not allowed:
            return cls(mode=PolicyMode.DENY_ALL, default_model=default, project_name=name)
        if not default and require_default:
            raise NoDefaultModelException(
                name,
                details="Project has allowed_models whitelist but no default_model configured. "
                "Admin must set a default model.",
            )
        return cls(mode=PolicyMode.WHITELIST, allowed_models=list(allowed), default_model=default, project_name=name)

    def choose(self, candidates: list[str | None], *, context: str = "", report: bool = True) -> str | None:
        """Return the first allowed candidate, else the default, else the first allowed model.

        A candidate is returned under the name it was given. The default and the first allowed model
        come from project settings, which store deployment names, so they are returned as base names.

        A substitution is logged and counted unless report=False, which a caller sets when the same
        resolution is reported elsewhere in the request.
        """
        present = [c for c in candidates if c]
        if self.mode == PolicyMode.UNRESTRICTED:
            return present[0] if present else catalog_base_name(self.default_model)
        if self.mode == PolicyMode.DENY_ALL:
            raise ModelNotWhitelistedException(
                present[0] if present else "",
                self.project_name,
                details="Project has no allowed models. Admin must configure the whitelist.",
            )

        chosen = next((c for c in present if self.allows(c)), None)
        if chosen is None:
            chosen = catalog_base_name(self._fallback_model())
        if report and present and chosen != present[0] and not self.allows(present[0]):
            self._report_fallback(present[0], chosen, context)
        return chosen

    def _fallback_model(self) -> str:
        """The project default when the whitelist allows it, else the first allowed model."""
        if self.default_model and self.allows(self.default_model):
            return self.default_model
        return self.allowed_models[0]

    def allows(self, *identifiers: str | None) -> bool:
        """Whether a model, named by any of its identifiers, is allowed.

        A whitelist entry matches a model by its base_name or its deployment_name: the project
        whitelist stores deployment names for chat models, while requests and assistants carry
        base names. Each identifier is expanded to both names before the membership check.
        """
        if self.mode == PolicyMode.UNRESTRICTED:
            return True
        if self.mode == PolicyMode.DENY_ALL:
            return False
        allowed = set(self.allowed_models)
        return any(alias in allowed for identifier in identifiers if identifier for alias in model_aliases(identifier))

    def check(self, model: str) -> None:
        """Reject a model outside the whitelist. Never substitutes."""
        if self.mode == PolicyMode.UNRESTRICTED:
            return
        if self.mode == PolicyMode.DENY_ALL:
            ModelAvailabilityService._send_validation_metric(self.project_name, model, "blocked")
            raise ModelNotWhitelistedException(
                model, self.project_name, details="Project has no allowed models. Admin must configure the whitelist."
            )
        if not self.allows(model):
            ModelAvailabilityService._send_validation_metric(self.project_name, model, "blocked")
            raise ModelNotWhitelistedException(
                model,
                self.project_name,
                details=f"Model '{model}' is not in the project's whitelist. "
                f"Available models: {', '.join(self.allowed_models)}",
            )
        ModelAvailabilityService._send_validation_metric(self.project_name, model, "allowed")
        logger.info(f"model_availability_event=validation_allowed project={self.project_name!r} model={model!r}")

    def _report_fallback(self, requested: str, chosen: str, context: str) -> None:
        logger.info(
            f"model_availability_event=fallback_applied project={self.project_name!r} "
            f"requested_model={requested!r} fallback_model={chosen!r} context={context!r}"
        )
        BaseMonitoringService.send_count_metric(
            name=MODEL_AVAILABILITY_FALLBACKS_TOTAL_METRIC,
            attributes={
                MetricsAttributes.PROJECT: self.project_name,
                MetricsAttributes.LLM_MODEL: requested,
            },
        )


class ModelAvailabilityService:
    """Validates and resolves model availability against project constraints."""

    @staticmethod
    def resolve_user_override_model(
        requested_model: str | None,
        user_id: str | UUID | None,
        project_name: str | None,
        is_global: bool = False,
        *,
        report: bool = True,
    ) -> str | None:
        """Resolve a model through the user's override, or None when the override does not apply.

        A substitution is logged and counted unless report=False, which a caller sets when the
        resolution is display-only or the same resolution is reported elsewhere in the request
        (the same contract as ProjectModelPolicy.choose).

        Applies only when the userModelOverride feature is on and the user has an override row for
        the project. Once an override row exists it is the user's whole model set - the project
        whitelist is never consulted, so a user an override excludes a model for cannot reach that
        model through the project whitelist either (see EPMCDME-15082 review). Precedence: the
        requested model if the override allows it; else the override's default_model if allowed;
        else the project's default (the override still excludes the requested model); else the
        platform default model. At every tier a model disabled at platform level is skipped to the
        next tier rather than returned (EPMCDME-15082 Context: a disabled model is a hard stop
        that moves resolution on, not an error). A project that denies all models rejects the
        request with ModelNotWhitelistedException. None means "no override applies - use the
        project-level logic": no override row exists.
        """
        if is_global or not user_id or not project_name:
            logger.info(
                f"user_model_override=skipped reason=is_global_or_missing_ids "
                f"is_global={is_global} user_id={user_id!r} project_name={project_name!r}"
            )
            return None
        if not customer_config.is_feature_enabled(USER_MODEL_OVERRIDE_FEATURE):
            logger.info("user_model_override=skipped reason=feature_disabled")
            return None

        override = load_user_override(user_id, project_name)
        if not override:
            logger.info(
                f"user_model_override=none_found user_id={user_id!r} project_name={project_name!r} "
                f"requested_model={requested_model!r}"
            )
            return None
        resolved = ModelAvailabilityService._pick_from_override(override, requested_model)
        if resolved is None:
            project_default = ProjectModelPolicy.for_project(project_name, require_default=False).choose(
                [], context="user_override_default"
            )
            if project_default and _is_live_for_override(project_default, "project_default"):
                resolved = project_default
        if resolved is None:
            from codemie.service.llm_service.llm_service import llm_service

            # The last tier: default_llm_model already prefers an enabled platform default.
            resolved = llm_service.default_llm_model
        logger.info(
            f"user_model_override=applied user_id={user_id!r} project_name={project_name!r} "
            f"requested_model={requested_model!r} allowed_models={override.allowed_models!r} "
            f"default_model={override.default_model!r} resolved_model={resolved!r}"
        )
        if report and requested_model and resolved != requested_model:
            ModelAvailabilityService._report_override_fallback(project_name, requested_model, resolved)
        return resolved

    @staticmethod
    def _report_override_fallback(project_name: str, requested_model: str, resolved_model: str) -> None:
        """Log and count a substitution made by a user override, on the counter the project policy uses.

        Until the user is told (EPMCDME-16924) this is the only signal that a user's run was substituted.
        The fallback_source label tells these apart from the project policy's own substitutions.
        """
        logger.info(
            f"model_availability_event=fallback_applied project={project_name!r} "
            f"requested_model={requested_model!r} fallback_model={resolved_model!r} context='user_override'"
        )
        BaseMonitoringService.send_count_metric(
            name=MODEL_AVAILABILITY_FALLBACKS_TOTAL_METRIC,
            attributes={
                MetricsAttributes.PROJECT: project_name,
                MetricsAttributes.LLM_MODEL: requested_model,
                MetricsAttributes.FALLBACK_SOURCE: "user_override",
            },
        )

    @staticmethod
    def _pick_from_override(override: UserModelOverride, requested_model: str | None) -> str | None:
        """Once an override row exists it is the user's whole model set: the requested model if
        allowed, else the override's default_model if allowed. The project whitelist is deliberately
        not consulted here - see resolve_user_override_model.

        The requested model keeps the name it was given. The override's default_model comes from
        override settings, which may hold a deployment name, so it is returned as a base name, like
        ProjectModelPolicy.choose does for the project's settings. Either model is skipped when it has
        been disabled at platform level since the override was saved. None means the override has no
        live default to fall back to; resolve_user_override_model then uses the project default."""
        if (
            requested_model
            and model_in_override(override.allowed_models, *model_aliases(requested_model))
            and _is_live_for_override(requested_model, "requested")
        ):
            return requested_model
        default_model = override.default_model
        if (
            default_model
            and model_in_override(override.allowed_models, *model_aliases(default_model))
            and _is_live_for_override(default_model, "user_override_default")
        ):
            return catalog_base_name(default_model)
        return None

    @staticmethod
    def user_override_allows_model(model: str, user_id: str | UUID | None, project_name: str | None) -> bool | None:
        """Whether the user's override allows the model, for callers that reject instead of falling back.

        None means no override applies (feature off, missing ids, or no row) - use the project-level check.
        """
        if not user_id or not project_name or not customer_config.is_feature_enabled(USER_MODEL_OVERRIDE_FEATURE):
            return None
        override = load_user_override(user_id, project_name)
        if not override:
            return None
        return model_in_override(override.allowed_models, *model_aliases(model))

    @staticmethod
    def validate_model_for_asset_creation(
        model_id: str,
        project_name: str,
        user_id: str | UUID | None = None,
    ) -> None:
        """
        Validate that a model is whitelisted for the project, honoring the requesting
        user's model override first.

        Called at asset (assistant/workflow) creation time to prevent users from
        creating assets with models not in the project's whitelist.

        Tier 1: when `user_id` is given, the userModelOverride feature is on, and the user
        has an override row for the project, the override alone decides - mirroring execution,
        where it is the user's whole model set: a model it lists passes regardless of the
        project whitelist, and a model it leaves out is rejected even if the project allows it.
        Tier 2 (the only tier when no user_id is given, or the user has no override): enforce
        the project's allowed_models whitelist, as before.

        Backward compatibility: If a project has not been configured with allowed_models
        or default_model, validation is skipped (silent pass). This allows existing
        projects created before this feature to continue operating normally.

        Raises:
            ModelNotWhitelistedException: if the user's override leaves the model out
            ModelNotWhitelistedException: if model not in allowed list (only if whitelist is configured)
            ModelNotWhitelistedException: if project has empty whitelist (configured but empty)
            NoDefaultModelException: if whitelist is configured but default_model is not
            NotFoundException: if project doesn't exist
        """
        override_allows = ModelAvailabilityService.user_override_allows_model(model_id, user_id, project_name)
        if override_allows:
            ModelAvailabilityService._send_validation_metric(project_name, model_id, "allowed")
            logger.info(
                f"model_availability_event=validation_allowed_by_user_override "
                f"project={project_name!r} model={model_id!r} user_id={user_id!r}"
            )
            return
        if override_allows is False:
            ModelAvailabilityService._send_validation_metric(project_name, model_id, "blocked")
            logger.info(
                f"model_availability_event=validation_blocked_by_user_override "
                f"project={project_name!r} model={model_id!r} user_id={user_id!r}"
            )
            raise ModelNotWhitelistedException(
                model_id,
                project_name,
                details="Model is not in the user's model override. Please use available models.",
            )

        if not customer_config.is_feature_enabled(PROJECT_MODEL_OVERRIDE_FEATURE):
            return

        ProjectModelPolicy.for_project(project_name, strict=True).check(model_id)

    @staticmethod
    def _send_validation_metric(project_name: str, model_id: str, result: str) -> None:
        BaseMonitoringService.send_count_metric(
            name=MODEL_AVAILABILITY_VALIDATIONS_TOTAL_METRIC,
            attributes={
                MetricsAttributes.PROJECT: project_name,
                MetricsAttributes.LLM_MODEL: model_id,
                MetricsAttributes.STATUS: result,
            },
        )

    @staticmethod
    def resolve_model_for_execution(
        requested_model: str | None,
        project_name: str,
        is_global: bool = False,
        fallback_model: str = "",
    ) -> tuple[str, bool]:
        """Apply project availability rules, then replace any administratively disabled model."""
        from codemie.service.llm_service.llm_service import llm_service

        model, overridden = ModelAvailabilityService._resolve_project_model_for_execution(
            requested_model, project_name, is_global, fallback_model
        )
        resolved = llm_service.resolve_disabled_model(
            model,
            project_name,
            is_global=is_global,
            allow_deprecated=bool(requested_model) and model == requested_model,
        )
        return resolved, overridden or resolved != model

    @staticmethod
    def _resolve_project_model_for_execution(
        requested_model: str | None,
        project_name: str,
        is_global: bool = False,
        fallback_model: str = "",
    ) -> tuple[str, bool]:
        """
        Resolve actual model to use, with fallback to default.

        An unset requested model selects the project default for configured project
        workflows. Global and unconfigured projects use fallback_model instead.

        Called during workflow execution. If the requested model is not in the project's
        whitelist, falls back to the project's default model and returns True as the
        fallback flag.

        Backward compatibility: If a project has not been configured with allowed_models
        or default_model, returns the requested model as-is (no fallback).

        Returns:
            (actual_model_id, was_fallback_applied)
            - If whitelist not configured: (requested_model or fallback_model, False)
            - If requested_model is unset: (default_model, False)
            - If requested_model is in whitelist: (requested_model, False)
            - If requested_model is not in whitelist: (default_model, True)

        Raises:
            ModelNotWhitelistedException: if model not in whitelist and project has empty whitelist
            NoDefaultModelException: if whitelist is configured but default_model is not
            NotFoundException: if project doesn't exist
        """

        if is_global or not customer_config.is_feature_enabled(PROJECT_MODEL_OVERRIDE_FEATURE):
            return (requested_model or fallback_model, False)

        policy = ProjectModelPolicy.for_project(project_name, strict=True)
        # A whitelist falls to the project default, never to the caller's fallback; an unrestricted
        # project's own default still beats that fallback.
        candidates = [requested_model]
        if policy.mode == PolicyMode.UNRESTRICTED:
            candidates = [requested_model, catalog_base_name(policy.default_model), fallback_model]
        picked = policy.choose(candidates, context="workflow_execution")
        return (picked, bool(requested_model) and picked != requested_model)


def resolve_assistant_model(
    assistant: "Assistant", user_pick: str | None, *, report_fallback: bool = True
) -> str | None:
    """Return the model an assistant runs on: the user pick, else its own model, under its project's policy.

    A global assistant, a missing project and a project without a whitelist are unrestricted.
    report_fallback=False resolves without logging or counting a substitution.
    """
    policy = ProjectModelPolicy.for_project(assistant.project or None, assistant.is_global)
    return policy.choose([user_pick, assistant.llm_model_type], context="assistant_execution", report=report_fallback)
