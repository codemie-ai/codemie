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

from dataclasses import dataclass

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

PROJECT_MODEL_OVERRIDE_FEATURE = "projectModelOverride"


@dataclass
class ProjectModelConfig:
    """Configuration for models available in a project."""

    allowed_models: list[str]
    default_model: str


class ModelAvailabilityService:
    """Validates and resolves model availability against project constraints."""

    @staticmethod
    def is_model_allowed(identifiers: list[str | None], allowed_models: set[str] | list[str]) -> bool:
        """Check whether any identifier for a model (e.g. its base_name and deployment_name
        aliases) appears in a project's allowed_models whitelist.

        Pass a pre-built set as allowed_models when calling this in a loop to avoid
        rebuilding it on every call.
        """
        allowed_set = allowed_models if isinstance(allowed_models, set) else set(allowed_models)
        return any(identifier in allowed_set for identifier in identifiers if identifier)

    @staticmethod
    def pick_allowed_model(requested_model: str | None, allowed_models: list[str], default_model: str) -> str:
        """Pick the effective model from a project's whitelist.

        Returns requested_model if it is allowed; otherwise the project's default_model
        if that is allowed; otherwise the first allowed model.

        Caller must ensure allowed_models is non-empty.
        """
        if requested_model and ModelAvailabilityService.is_model_allowed([requested_model], allowed_models):
            return requested_model
        if ModelAvailabilityService.is_model_allowed([default_model], allowed_models):
            return default_model
        return allowed_models[0]

    @staticmethod
    def _get_project_models_uncached(project_name: str) -> ProjectModelConfig:
        """Fetch project's model configuration from database."""
        try:
            app = Application.get_by_id(project_name)
        except KeyError:
            raise NotFoundException(f"Project '{project_name}' not found")

        allowed_models = app.allowed_models
        default_model = app.default_model

        # Backward compatibility: project has no whitelist configured at all, so it
        # is not restricted and does not require a default_model either.
        if allowed_models is None:
            return ProjectModelConfig(allowed_models=[], default_model=default_model or "")

        if not default_model:
            raise NoDefaultModelException(
                project_name, details=f"Project '{project_name}' has no default model configured. Admin must set one."
            )

        return ProjectModelConfig(allowed_models=allowed_models, default_model=default_model)

    @staticmethod
    def get_project_models(project_name: str) -> ProjectModelConfig:
        """Fetch allowed_models and default_model for project."""
        return ModelAvailabilityService._get_project_models_uncached(project_name)

    @staticmethod
    def resolve_requested_model(
        project_name: str | None,
        requested_model: str | None,
        is_global: bool = False,
    ) -> tuple[str | None, bool]:
        """Resolve a requested model against project restrictions, degrading gracefully.

        Shared by call sites (assistant detail response, chat execution model selection)
        that should pass the requested model through unchanged when there's nothing to
        enforce - no project, feature flag off, project not found, or no whitelist
        configured - rather than raising. Callers that need hard failure semantics for a
        missing project should call get_project_models directly instead.

        Returns:
            (effective_model, was_overridden)

        Raises:
            NoDefaultModelException: whitelist is configured but default_model is not.
        """
        if not project_name or is_global:
            return requested_model, False

        if not customer_config.is_feature_enabled(PROJECT_MODEL_OVERRIDE_FEATURE):
            return requested_model, False

        try:
            config = ModelAvailabilityService.get_project_models(project_name)
        except NotFoundException:
            return requested_model, False

        if not config.allowed_models:
            return requested_model, False

        picked = ModelAvailabilityService.pick_allowed_model(
            requested_model, config.allowed_models, config.default_model
        )
        return picked, picked != requested_model

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

        try:
            app = Application.get_by_id(project_name)
        except KeyError:
            raise NotFoundException(f"Project '{project_name}' not found")

        # Backward compatibility: if neither is configured, skip validation
        if app.allowed_models is None and not app.default_model:
            return

        # Constraint check: if whitelist is configured, default must be too
        if app.allowed_models is not None and not app.default_model:
            raise NoDefaultModelException(
                project_name,
                details="Project has allowed_models whitelist but no default_model configured. "
                "Admin must set a default model.",
            )

        allowed_models = app.allowed_models

        # If project has empty whitelist, all models are blocked
        if not allowed_models:
            ModelAvailabilityService._send_validation_metric(project_name, model_id, "blocked")
            raise ModelNotWhitelistedException(
                model_id, project_name, details="Project has no allowed models. Admin must configure the whitelist."
            )

        # Check if model is in whitelist
        if model_id not in allowed_models:
            ModelAvailabilityService._send_validation_metric(project_name, model_id, "blocked")
            raise ModelNotWhitelistedException(
                model_id,
                project_name,
                details=f"Model '{model_id}' is not in the project's whitelist. "
                f"Available models: {', '.join(allowed_models)}",
            )

        ModelAvailabilityService._send_validation_metric(project_name, model_id, "allowed")
        logger.info(f"model_availability_event=validation_allowed project={project_name!r} model={model_id!r}")

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

        if is_global:
            return (requested_model or fallback_model, False)

        if not customer_config.is_feature_enabled(PROJECT_MODEL_OVERRIDE_FEATURE):
            return (requested_model or fallback_model, False)

        try:
            app = Application.get_by_id(project_name)
        except KeyError:
            raise NotFoundException(f"Project '{project_name}' not found")

        # None means the project does not restrict models. A configured project
        # default still takes precedence when the node has no selected model.
        if app.allowed_models is None:
            return (requested_model or app.default_model or fallback_model, False)

        # Constraint check: if whitelist is configured, default must be too
        if app.allowed_models is not None and not app.default_model:
            raise NoDefaultModelException(
                project_name,
                details="Project has allowed_models whitelist but no default_model configured. "
                "Admin must set a default model.",
            )

        allowed_models = app.allowed_models

        # If project has empty whitelist, all models are blocked
        if not allowed_models:
            raise ModelNotWhitelistedException(
                requested_model or fallback_model,
                project_name,
                details="Project has no allowed models. Admin must configure the whitelist.",
            )

        # An unset node model selects the project's default directly.
        if not requested_model:
            return (app.default_model, False)

        # Check if requested model is available
        if requested_model in allowed_models:
            return (requested_model, False)

        # Fallback to default model
        logger.info(
            f"model_availability_event=fallback_applied "
            f"project={project_name!r} "
            f"requested_model={requested_model!r} "
            f"fallback_model={app.default_model!r}"
        )
        BaseMonitoringService.send_count_metric(
            name=MODEL_AVAILABILITY_FALLBACKS_TOTAL_METRIC,
            attributes={
                MetricsAttributes.PROJECT: project_name,
                MetricsAttributes.LLM_MODEL: requested_model,
            },
        )

        return (app.default_model, True)
