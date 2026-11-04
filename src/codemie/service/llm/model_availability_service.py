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
from typing import TYPE_CHECKING

from codemie.configs import logger
from codemie.configs.customer_config import customer_config
from codemie.core.exceptions import ModelNotWhitelistedException, NoDefaultModelException, NotFoundException
from codemie.core.models import Application
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import (
    MODEL_AVAILABILITY_FALLBACKS_TOTAL_METRIC,
    MODEL_AVAILABILITY_VALIDATIONS_TOTAL_METRIC,
    MetricsAttributes,
)

if TYPE_CHECKING:
    from codemie.rest_api.models.assistant import Assistant

PROJECT_MODEL_OVERRIDE_FEATURE = "projectModelOverride"


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


def catalog_base_name(model: str | None) -> str | None:
    """The base_name of the catalog model a name refers to; an unknown name (a router) is returned as is.

    Project settings store deployment names, while execution looks models up by base_name (the
    ReAct check, file tools, history replay), so a model taken from project settings is converted.
    """
    if not model:
        return model
    entry = _catalog_entry(model)
    return entry.base_name if entry is not None and entry.base_name else model


def _catalog_entry(model: str):
    """The one catalog model a name refers to: by base_name first, then by deployment_name."""
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
    def validate_model_for_asset_creation(
        model_id: str,
        project_name: str,
    ) -> None:
        """
        Validate that a model is whitelisted for the project.

        Called at asset (assistant/workflow) creation time to prevent users from
        creating assets with models not in the project's whitelist.

        Backward compatibility: If a project has not been configured with allowed_models
        or default_model, validation is skipped (silent pass). This allows existing
        projects created before this feature to continue operating normally.

        Raises:
            ModelNotWhitelistedException: if model not in allowed list (only if whitelist is configured)
            ModelNotWhitelistedException: if project has empty whitelist (configured but empty)
            NoDefaultModelException: if whitelist is configured but default_model is not
            NotFoundException: if project doesn't exist
        """
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
