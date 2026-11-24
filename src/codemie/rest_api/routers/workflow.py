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

import asyncio
import base64
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any, Dict, List

from sqlalchemy.exc import SQLAlchemyError

from fastapi import APIRouter, status, Depends, Query, BackgroundTasks, Request, UploadFile
from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer
from codemie.rest_api.dependencies.core import require_core
from codemie.rest_api.models.workflow_generator import WorkflowGeneratorRequest, WorkflowGeneratorResponse
from codemie.service.llm_service.utils import set_llm_context
from codemie.service.workflow_generator_service import WorkflowGeneratorService

from codemie.configs import config, logger
from codemie.configs.customer_config import customer_config
from codemie.core.ability import Ability, Action
from codemie.rest_api.models.files import WriteFileResponse
from codemie.service.workflow_icon_service import WORKFLOW_ICON_MAX_BYTES, WorkflowIconService
from codemie.core.constants import MermaidMimeType
from codemie.service.file_service.mermaid_service import (
    MERMAID_INVALID_DIAGRAM_HELP,
    MERMAID_INVALID_DIAGRAM_MESSAGE,
    MERMAID_NOT_CONFIGURED_DETAILS,
    MERMAID_NOT_CONFIGURED_HELP,
    MERMAID_NOT_CONFIGURED_MESSAGE,
    MERMAID_UNAVAILABLE_DETAILS,
    MERMAID_UNAVAILABLE_HELP,
    MERMAID_UNAVAILABLE_MESSAGE,
    MermaidDiagramGenerationError,
    MermaidNotConfiguredError,
    MermaidServiceUnavailableError,
)
from codemie.core.exceptions import (
    ExtendedHTTPException,
    MissingPlaceholderVariablesException,
    NotFoundException,
    ValidationException,
    WorkflowGenerationError,
)
from codemie.core.models import BaseResponse, BaseResponseWithData, CreatedByUser, EvaluationResponse
from codemie.core.search_ranking import rank_by_search
from codemie.core.workflow_models import (
    CreateWorkflowRequest,
    UpdateWorkflowRequest,
    ValidateNewWorkflowRequest,
    WorkflowConfig,
    WorkflowConfigTemplate,
    WorkflowEvaluationRequest,
    WorkflowListResponse,
    WorkflowErrorFormat,
)
from codemie.core.workflow_models.workflow_config import WorkflowMode
from codemie.rest_api.models.guardrail import GuardrailAssignmentItem, GuardrailEntity
from codemie.rest_api.models.mcp_restore import DisabledMcpResponse, message_with_disabled
from codemie.rest_api.routers.assistant._model_selection import validate_model_selection
from codemie.rest_api.routers.utils import raise_access_denied, raise_forbidden, run_in_thread_pool, raise_not_found
from codemie.rest_api.security.authentication import authenticate, project_access_check
from codemie.rest_api.security.user import User
from codemie.rest_api.models.workflow_generator import (
    WorkflowRefineRequest,
    WorkflowRefineResponse,
)
from codemie.service.monitoring.workflow_monitoring_service import WorkflowMonitoringService
from codemie.service.guardrail.guardrail_service import GuardrailService
from codemie.service.workflow_config import WorkflowConfigIndexService
from codemie.service.workflow_config.workflow_config_index_service import ExcludeSelfModifier, WorkflowScope
from codemie.service.assistant.category_service import category_service
from codemie.service.workflow_service import WorkflowService
from codemie.service.workflow_evaluation_service import WorkflowEvaluationService
from codemie.rest_api.models.workflow_version import (
    DEFAULT_VERSIONS_PER_PAGE,
    MAX_VERSIONS_PER_PAGE,
    WorkflowRollbackRequest,
    WorkflowVersionHistoryResponse,
)
from codemie.service.workflow import save_validation
from codemie.service.workflow.save_validation import WORKFLOW_CONFIGURATION_ERROR, SaveValidationResult
from codemie.service.workflow.workflow_version_service import WorkflowVersionService
from codemie.workflows.custom_node_info import CustomNodeInfoService
from codemie.core.workflow_models import CustomNodeSchemaResponse
from codemie.workflows.workflow import WorkflowExecutor

router = APIRouter(
    tags=["Workflow"],
    prefix="/v1",
    dependencies=[Depends(require_core)],
)
workflow_service = WorkflowService()
workflow_monitoring_service = WorkflowMonitoringService()

WORKFLOW_STARTED_BG_MSG = "Workflow has been triggered in the background"
WORKFLOW_ERROR_FORMAT_DESCRIPTION = "Error format: 'string' or 'json'"


WORKFLOW_DATABASE_ERROR = "Workflow database is unavailable"
WORKFLOW_DATABASE_DETAILS = "The workflow operation could not be completed. Please try again later."
_DATABASE_RESPONSE = {503: {"description": "Workflow database is unavailable; no database details are returned."}}


@contextmanager
def _database_errors() -> Iterator[None]:
    """Map database failures without logging SQL, connection strings, or bind values."""
    try:
        yield
    except SQLAlchemyError as exc:
        logger.error("Workflow database operation failed (%s)", type(exc).__name__)
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=WORKFLOW_DATABASE_ERROR,
            details=WORKFLOW_DATABASE_DETAILS,
            help="",
        ) from exc


@contextmanager
def _wrap_validation_errors(error_format: WorkflowErrorFormat) -> Iterator[None]:
    """Preserve typed HTTP errors and map database and configuration errors separately."""
    with _database_errors():
        try:
            yield
        except (SQLAlchemyError, ValidationException, NotFoundException, ExtendedHTTPException):
            raise
        except Exception as e:
            details = (
                e.args[0]
                if error_format == WorkflowErrorFormat.JSON and e.args and isinstance(e.args[0], dict)
                else str(e).strip()
            )
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST, message=WORKFLOW_CONFIGURATION_ERROR, details=details, help=""
            ) from e


@contextmanager
def _guardrail_errors() -> Iterator[None]:
    """Report a failed guardrail assignment as a 400 configuration error, the same on create and update."""
    try:
        yield
    except (SQLAlchemyError, ValidationException, NotFoundException):
        raise
    except Exception as guardrail_exc:
        details = (
            guardrail_exc.message if isinstance(guardrail_exc, ExtendedHTTPException) else str(guardrail_exc).strip()
        )
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST, message=WORKFLOW_CONFIGURATION_ERROR, details=details, help=""
        ) from guardrail_exc


def _refuse_autonomous_update(mode: WorkflowMode | None) -> None:
    if mode == WorkflowMode.AUTONOMOUS:
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Autonomous workflows are disabled",
            details="Updating workflows to autonomous mode is not allowed. Only sequential workflows can be used.",
            help="Please set the workflow mode to 'SEQUENTIAL' instead.",
        )


def _run_pre_persist_update_validation(  # noqa: PLR0913 - keyword-only builder/internal helper; a parameter object would add indirection without clarity
    workflow: WorkflowConfig,
    proposed: WorkflowConfig,
    user: User,
    error_format: WorkflowErrorFormat,
    guardrail_assignments: List[GuardrailAssignmentItem] | None,
    request_dump: dict,
) -> SaveValidationResult:
    """Autonomous-mode, guardrail and save-pipeline validation of a prepared update, without persisting.

    Sync: async handlers run it with `asyncio.to_thread`.
    """
    _refuse_autonomous_update(proposed.mode)
    with _guardrail_errors():
        GuardrailService.sync_guardrail_assignments_for_entity(
            user=user,
            entity_type=GuardrailEntity.WORKFLOW,
            entity_id=str(workflow.id),
            entity_project_name=proposed.project if proposed.project else workflow.project,
            guardrail_assignments=guardrail_assignments,
            dry_run=True,
        )
    with _wrap_validation_errors(error_format):
        return save_validation.validate(
            proposed,
            request_dump,
            user,
            error_format,
            stored_workflow=workflow,
        )


@router.get(
    "/workflows/users",
    status_code=status.HTTP_200_OK,
    response_model=list[CreatedByUser],
)
def get_workflow_users(
    user: User = Depends(authenticate),
    scope: WorkflowScope | None = None,
) -> list[CreatedByUser]:
    """
    Returns list of users who created workflows.
    Pass scope=marketplace to return only creators of globally published workflows.
    """
    result = WorkflowConfigIndexService.get_users(user=user, scope=scope)
    return result


@router.get(
    "/workflows/sub-workflow-candidates",
    status_code=status.HTTP_200_OK,
    response_model=WorkflowListResponse,
    response_model_by_alias=True,
)
def get_sub_workflow_candidates(
    user: User = Depends(authenticate),
    exclude_id: str | None = None,
    page: int = 0,
    per_page: int = 100,
):
    if not customer_config.is_feature_enabled("subWorkflow"):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Sub-workflow node is disabled",
            details="Enable the 'features:subWorkflow' flag in customer-config.",
            help="Contact your administrator to enable the sub-workflow feature.",
        )

    extra_modifiers = [ExcludeSelfModifier(exclude_id)] if exclude_id else []

    return WorkflowConfigIndexService.run(
        user=user,
        filter_by_user=False,
        page=page,
        per_page=per_page,
        minimal_response=True,
        extra_modifiers=extra_modifiers,
    )


@router.get(
    "/workflows",
    status_code=status.HTTP_200_OK,
    response_model=WorkflowListResponse,
    response_model_by_alias=True,
)
def get_workflows(
    user: User = Depends(authenticate),
    filter_by_user: bool = Query(False),
    page: int = 0,
    per_page: int = 10,
    filters: str | None = None,
    minimal_response: bool = True,
    scope: WorkflowScope | None = None,
):
    try:
        parsed_filters = json.loads(filters) if filters else None
    except json.JSONDecodeError:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid filters",
            details="Filters must be a valid encoded JSON object.",
            help="Please check the filters and ensure they are in the correct format. ",
        )

    return WorkflowConfigIndexService.run(
        user=user,
        filter_by_user=filter_by_user,
        page=page,
        per_page=per_page,
        filters=parsed_filters,
        minimal_response=minimal_response,
        scope=scope,
    )


@router.get(
    "/workflows/prebuilt",
    status_code=status.HTTP_200_OK,
    response_model=List[WorkflowConfigTemplate],
    response_model_by_alias=True,
    summary="Get prebuilt workflows",
    description="Retrieves a list of prebuilt workflows available in the system.",
)
def get_prebuilt_workflows(filters: str | None = None):
    all_templates = workflow_service.get_prebuilt_workflows()

    parsed_filters: Dict[str, Any] | None = None
    if filters:
        try:
            parsed_filters = json.loads(filters)
        except json.JSONDecodeError:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Invalid filters",
                details="Filters must be a valid encoded JSON object.",
                help="Please check the filters and ensure they are in the correct format.",
            )

    if parsed_filters:
        name_filter = parsed_filters.get("name") or parsed_filters.get("search")
        categories_filter = parsed_filters.get("categories")
        created_by_filter = parsed_filters.get("created_by")
        if name_filter:
            all_templates = rank_by_search(all_templates, name_filter)
        if categories_filter:
            all_templates = [t for t in all_templates if any(c in (t.categories or []) for c in categories_filter)]
        if created_by_filter:
            all_templates = [
                t
                for t in all_templates
                if t.created_by
                and (
                    getattr(t.created_by, "name", None) == created_by_filter
                    or getattr(t.created_by, "username", None) == created_by_filter
                )
            ]

    return all_templates


@router.get(
    "/workflows/prebuilt/{slug}",
    status_code=status.HTTP_200_OK,
    response_model=WorkflowConfigTemplate,
    response_model_by_alias=True,
    summary="Get prebuilt workflow template by slug",
    description="Retrieves a prebuilt workflow template by slug",
)
def get_prebuilt_workflow_by_slug(slug: str, user: User = Depends(authenticate)):
    """
    Endpoint to retrieve prebuilt workflow template by slug.

    Utilizes the `get_prebuilt_workflows` method from the `WorkflowService` to fetch and return
    a list of prebuilt workflows. This endpoint is useful for clients to discover workflows
    that are readily available for use without the need for custom creation.

    Returns:
       `WorkflowConfig` objects representing the prebuilt workflows.
    """
    try:
        prebuilt_workflows = workflow_service.get_prebuilt_workflows()
        template = next(item for item in prebuilt_workflows if item.slug == slug)
        template.project = user.current_project

        return template
    except StopIteration:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Workflow template not found",
            details=f"No workflow template found with the slug '{slug}'.",
            help="Please check the workflow slug and ensure it is correct. ",
        )


class MaterializeRequest(BaseModel):
    variables: dict[str, str]


class MaterializeSeedResponse(BaseModel):
    yaml_config: str
    description: str | None = None
    start_hint: str | None = None


@router.post(
    "/workflows/prebuilt/{slug}/materialize",
    status_code=status.HTTP_200_OK,
    response_model=MaterializeSeedResponse,
    response_model_exclude_none=True,
    summary="Materialize a prebuilt workflow template",
    description=(
        "Substitutes caller-supplied variables into a prebuilt template and "
        + "returns the materialized workflow seed."
    ),
)
def materialize_prebuilt_workflow(
    slug: str,
    request: MaterializeRequest,
    user: User = Depends(authenticate),
) -> MaterializeSeedResponse:
    try:
        result = workflow_service.materialize_template(slug, request.variables)
    except MissingPlaceholderVariablesException as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Required placeholder variables are missing or blank.",
            details={
                "error_type": "missing_placeholder_variables",
                "errors": [{"placeholder": name} for name in exc.missing_variables],
            },
            help="Provide a non-empty value for every required placeholder variable.",
        )
    except ValueError:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="The workflow template could not be materialized.",
            details={"error_type": "materialization_failed"},
            help="Check the supplied values and the template configuration.",
        )
    if result is None:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Workflow template not found",
            details=f"No workflow template found with the slug '{slug}'.",
            help="Please check the workflow slug and ensure it is correct.",
        )
    return MaterializeSeedResponse(**result)


@router.get(
    "/workflows/id/{workflow_id}",
    status_code=status.HTTP_200_OK,
    response_model=WorkflowConfig,
    response_model_by_alias=True,
)
def get_workflow(workflow_id: str, user: User = Depends(authenticate)):
    try:
        workflow_config = workflow_service.get_workflow(workflow_id, user)
    except Exception:
        raise_not_found(resource_id=workflow_id, resource_type="Workflow")

    if not Ability(user).can(Action.READ, workflow_config):
        raise_access_denied("view")

    # Enrich with guardrail assignments
    workflow_config.guardrail_assignments = GuardrailService.get_entity_guardrail_assignments(
        user,
        GuardrailEntity.WORKFLOW,
        str(workflow_config.id),
    )

    return workflow_config


def _drop_empty_contract_fields(serialized: dict[str, Any]) -> dict[str, Any]:
    """`coverage` is sent only when contracts were linked, `warnings_truncated` only when the cap cut something."""
    if serialized.get("coverage") is None:
        serialized.pop("coverage", None)
    if not serialized.get("warnings_truncated"):
        serialized.pop("warnings_truncated", None)
    return serialized


class ValidateWorkflowResponse(BaseResponse):
    """Dry-run validation response; mirrors the advisory warnings surface of the update endpoint."""

    warnings: list[dict[str, object]] = []
    coverage: dict | None = None
    # Snake-case on the wire like every key of the contract envelope, whatever the alias generator says
    warnings_truncated: int | None = Field(default=None, alias="warnings_truncated")

    @model_serializer(mode="wrap")
    # No return annotation on purpose: a declared one would replace the OpenAPI response schema.
    def _serialize(self, handler: SerializerFunctionWrapHandler):
        return _drop_empty_contract_fields(handler(self))


class WorkflowSaveResponse(ValidateWorkflowResponse):
    """Save response that can carry non-blocking notes about the saved configuration."""

    data: Any = None


def _refuse_autonomous_creation(mode: WorkflowMode | None) -> None:
    """Autonomous workflows cannot be created: 410, shared by create and the pre-save dry-run."""
    if mode == WorkflowMode.AUTONOMOUS:
        raise ExtendedHTTPException(
            code=status.HTTP_410_GONE,
            message="Autonomous workflows are disabled",
            details="Creating autonomous workflows is not allowed. Only sequential workflows can be created.",
            help="Please set the workflow mode to 'SEQUENTIAL' instead.",
        )


def _persist_and_finalize_new_workflow(  # noqa: PLR0913 - keyword-only builder/internal helper; a parameter object would add indirection without clarity
    workflow_config: WorkflowConfig,
    request: CreateWorkflowRequest,
    request_dump: dict,
    background_tasks: BackgroundTasks,
    user: User,
    error_format: WorkflowErrorFormat,
) -> dict:
    """Validate, save, and enrich a newly created workflow, raising a formatted error on failure."""
    with _wrap_validation_errors(error_format):
        if request.categories is not None:
            # config was built from the request before validation, so dedupe it too
            workflow_config.categories = request.categories = category_service.validate_category_ids(request.categories)
        result = save_validation.validate(workflow_config, request_dump, user, error_format)
        with _guardrail_errors():
            GuardrailService.sync_guardrail_assignments_for_entity(
                user=user,
                entity_type=GuardrailEntity.WORKFLOW,
                entity_id=str(workflow_config.id),
                entity_project_name=workflow_config.project,
                guardrail_assignments=request.guardrail_assignments,
                dry_run=True,
            )
        workflow_config = workflow_service.create_workflow(workflow_config, user)

        with _guardrail_errors():
            GuardrailService.sync_guardrail_assignments_for_entity(
                user=user,
                entity_type=GuardrailEntity.WORKFLOW,
                entity_id=str(workflow_config.id),
                entity_project_name=workflow_config.project,
                guardrail_assignments=request.guardrail_assignments,
            )

        background_tasks.add_task(run_in_thread_pool, update_workflow_schema, workflow_config, user)

        # Enrich with guardrail assignments
        workflow_config.guardrail_assignments = GuardrailService.get_entity_guardrail_assignments(
            user,
            GuardrailEntity.WORKFLOW,
            str(workflow_config.id),
        )
        return {
            "message": "Workflow created successfully",
            "data": workflow_config,
            "warnings": result.warnings,
            "coverage": result.coverage,
            "warnings_truncated": result.warnings_truncated or None,
        }


@router.post("/workflows/icon", response_model=WriteFileResponse, dependencies=[Depends(authenticate)])
def upload_workflow_icon(file: UploadFile) -> WriteFileResponse:
    """Store an uploaded workflow icon image and return the file URL to save as the workflow's icon_url."""
    WorkflowIconService.check_filename(file.filename or "", file.content_type or "")
    # Read one byte past the limit so an oversize upload is detected without buffering all of it.
    data = file.file.read(WORKFLOW_ICON_MAX_BYTES + 1)
    if len(data) > WORKFLOW_ICON_MAX_BYTES:
        raise ExtendedHTTPException(
            code=400,
            message="Unsupported icon file size",
            details="Maximum icon size is 0.5 MB",
        )
    stored = WorkflowIconService.store(data, file.content_type or "")
    return WriteFileResponse(file_url=stored.to_encoded_url())


@router.post(
    "/workflows",
    status_code=status.HTTP_200_OK,
    response_model=WorkflowSaveResponse,
    response_model_by_alias=True,
    responses=_DATABASE_RESPONSE,
)
def create_workflow(
    request: CreateWorkflowRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(authenticate),
    error_format: WorkflowErrorFormat = Query(
        WorkflowErrorFormat.STRING, description=WORKFLOW_ERROR_FORMAT_DESCRIPTION
    ),
):
    with _database_errors():
        _refuse_autonomous_creation(request.mode)
        request.icon_url = WorkflowConfig.validate_icon_url(request.icon_url, user)
        request_dump = request.model_dump()
        # A yaml_config parse failure is deferred to the executor, which reports it path-aware inside validate
        workflow_config = save_validation.prepare(request_dump, user, defer_parse_failure=True)

        # Prevent creation of autonomous workflows
        _refuse_autonomous_creation(workflow_config.mode)
        project_access_check(user, request.project)

        return _persist_and_finalize_new_workflow(
            workflow_config, request, request_dump, background_tasks, user, error_format
        )


@router.post(
    "/workflows/validate",
    status_code=status.HTTP_200_OK,
    response_model=ValidateWorkflowResponse,
    response_model_by_alias=True,
    responses=_DATABASE_RESPONSE,
)
def validate_new_workflow(
    request: ValidateNewWorkflowRequest,
    user: User = Depends(authenticate),
    error_format: WorkflowErrorFormat = Query(
        WorkflowErrorFormat.STRING, description=WORKFLOW_ERROR_FORMAT_DESCRIPTION
    ),
) -> ValidateWorkflowResponse:
    """Dry-run the create-time validation of a workflow that is not saved yet. Nothing is persisted.

    Call it on an explicit user action or as a preview right before saving. It is not meant for a per-keystroke
    debounce: every call lists MCP tools and compiles the workflow graph.

    A clean result guarantees that, in the same environment, creating the same configuration is not rejected by
    contract validation. It does not cover the category, guardrail, autonomous-mode and access checks of create.
    `name` and `description` may be omitted. Only membership of the project is required, with no write check.
    """
    # Access and the autonomous refusal come first, so a refused caller triggers no lookups
    with _database_errors():
        project_access_check(user, request.project)
        _refuse_autonomous_creation(request.mode)

        dump = request.model_dump()
        # Missing values become "" when the WorkflowConfig is built
        request_dump = {**dump, "name": dump["name"] or "", "description": dump["description"] or ""}
        with _wrap_validation_errors(error_format):
            # A yaml_config parse failure is deferred to the executor, which reports it path-aware inside validate
            candidate = save_validation.prepare(request_dump, user, defer_parse_failure=True)
            result = save_validation.validate(candidate, request_dump, user, error_format)
        return ValidateWorkflowResponse(
            message="Workflow configuration is valid",
            warnings=result.warnings,
            coverage=result.coverage,
            warnings_truncated=result.warnings_truncated or None,
        )


@router.put(
    "/workflows/{workflow_id}",
    status_code=status.HTTP_200_OK,
    response_model=WorkflowSaveResponse,
    response_model_by_alias=True,
    responses=_DATABASE_RESPONSE,
)
async def update_workflow(
    workflow_id: str,
    request: UpdateWorkflowRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(authenticate),
    error_format: WorkflowErrorFormat = Query(
        WorkflowErrorFormat.STRING, description=WORKFLOW_ERROR_FORMAT_DESCRIPTION
    ),
):
    with _database_errors():
        try:
            workflow = workflow_service.get_workflow(workflow_id=workflow_id)
        except SQLAlchemyError:
            raise
        except Exception:
            raise_not_found(resource_id=workflow_id, resource_type="Workflow")

        project_access_check(user, request.project)

        if not Ability(user).can(Action.WRITE, workflow):
            raise_access_denied("update")

        _refuse_autonomous_update(request.mode)
        if request.icon_url != workflow.icon_url:
            request.icon_url = WorkflowConfig.validate_icon_url(request.icon_url, user)
        request_dump = request.model_dump()
        updated_config = await asyncio.to_thread(save_validation.prepare, request_dump, user, False, workflow)
        if request.categories is None:
            # WorkflowConfig coerces None to []; keep None so an omitted field preserves stored categories
            updated_config.categories = None

        updated_config.id = workflow_id
        logger.debug(f"Update workflow. Request: {request}")
        result = await asyncio.to_thread(
            _run_pre_persist_update_validation,
            workflow,
            updated_config,
            user,
            error_format,
            request.guardrail_assignments,
            request_dump,
        )
        try:
            if request.categories is not None:
                updated_config.categories = request.categories = category_service.validate_category_ids(
                    request.categories
                )
            updated_workflow = workflow_service.update_workflow(workflow, updated_config, user)

            GuardrailService.sync_guardrail_assignments_for_entity(
                user=user,
                entity_type=GuardrailEntity.WORKFLOW,
                entity_id=str(updated_workflow.id),
                entity_project_name=updated_workflow.project,
                guardrail_assignments=request.guardrail_assignments,
            )

            background_tasks.add_task(run_in_thread_pool, update_workflow_schema, updated_workflow, user)

            updated_workflow.guardrail_assignments = GuardrailService.get_entity_guardrail_assignments(
                user,
                GuardrailEntity.WORKFLOW,
                str(updated_workflow.id),
            )
            return {
                "message": "Workflow updated successfully",
                "data": updated_workflow,
                "warnings": result.warnings,
                "coverage": result.coverage,
                "warnings_truncated": result.warnings_truncated or None,
            }
        except (ValidationException, NotFoundException):
            raise
        except SQLAlchemyError:
            raise
        except Exception as e:
            formatted_exception = e.message if isinstance(e, ExtendedHTTPException) else str(e).strip()
            details = (
                e.args[0]
                if error_format == WorkflowErrorFormat.JSON and e.args and isinstance(e.args[0], dict)
                else formatted_exception
            )
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message=WORKFLOW_CONFIGURATION_ERROR,
                details=details,
                help="",
            ) from e


@router.post(
    "/workflows/{workflow_id}/validate",
    status_code=status.HTTP_200_OK,
    response_model=ValidateWorkflowResponse,
    response_model_by_alias=True,
    responses=_DATABASE_RESPONSE,
)
async def validate_workflow(
    workflow_id: str,
    request: UpdateWorkflowRequest,
    user: User = Depends(authenticate),
    error_format: WorkflowErrorFormat = Query(  # noqa: B008 - FastAPI dependency declaration
        WorkflowErrorFormat.STRING, description=WORKFLOW_ERROR_FORMAT_DESCRIPTION
    ),
) -> ValidateWorkflowResponse:
    with _database_errors():
        try:
            workflow = workflow_service.get_workflow(workflow_id=workflow_id)
        except KeyError as e:
            logger.error(str(e).strip())
            raise_not_found(resource_id=workflow_id, resource_type="Workflow")

        project_access_check(user, request.project)

        if not Ability(user).can(Action.WRITE, workflow):
            raise_access_denied("validate")

        _refuse_autonomous_update(request.mode)
        request_dump = request.model_dump()
        proposed = await asyncio.to_thread(save_validation.prepare, request_dump, user, False, workflow)
        proposed.id = workflow_id

        result = await asyncio.to_thread(
            _run_pre_persist_update_validation,
            workflow,
            proposed,
            user,
            error_format,
            request.guardrail_assignments,
            request_dump,
        )
        return ValidateWorkflowResponse(
            message="Workflow configuration is valid",
            warnings=result.warnings,
            coverage=result.coverage,
            warnings_truncated=result.warnings_truncated or None,
        )


@router.delete(
    "/workflows/{workflow_id}",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    response_model_by_alias=True,
)
def delete_workflow(workflow_id: str, user: User = Depends(authenticate)):
    try:
        workflow = workflow_service.get_workflow(workflow_id=workflow_id)
    except KeyError as e:
        logger.error(str(e).strip())
        raise_not_found(resource_id=workflow_id, resource_type="Workflow")

    if not Ability(user).can(Action.DELETE, workflow):
        raise_access_denied("delete")

    workflow_service.delete_workflow(workflow, user)

    GuardrailService.remove_guardrail_assignments_for_entity(GuardrailEntity.WORKFLOW, str(workflow.id))

    return BaseResponse(message="Specified workflow removed")


@router.post(
    "/workflows/diagram",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponseWithData,
)
def create_workflow_diagram(request: CreateWorkflowRequest, user: User = Depends(authenticate)):
    try:
        workflow_config = WorkflowConfig(**request.model_dump())
        diagram = WorkflowExecutor.validate_workflow_and_draw(
            workflow_config=workflow_config, user=user, error_format=WorkflowErrorFormat.STRING
        )
        b64_diagram = base64.b64encode(diagram).decode("utf-8") if diagram else None
    except MermaidNotConfiguredError as e:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=MERMAID_NOT_CONFIGURED_MESSAGE,
            details=MERMAID_NOT_CONFIGURED_DETAILS,
            help=MERMAID_NOT_CONFIGURED_HELP,
            expected=True,
        ) from e
    except MermaidServiceUnavailableError as e:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=MERMAID_UNAVAILABLE_MESSAGE,
            details=MERMAID_UNAVAILABLE_DETAILS,
            help=MERMAID_UNAVAILABLE_HELP,
        ) from e
    except MermaidDiagramGenerationError as e:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=MERMAID_INVALID_DIAGRAM_MESSAGE,
            details=str(e).strip(),
            help=MERMAID_INVALID_DIAGRAM_HELP,
        ) from e
    except Exception as e:
        formatted_exception = str(e).strip()
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=WORKFLOW_CONFIGURATION_ERROR,
            details=f"{formatted_exception}",
            help="",
        ) from e

    if diagram is None:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message="Unable to generate workflow diagram",
            details="Mermaid is not available.",
            help="Try again later",
        )

    return BaseResponseWithData(
        data=f"data:{MermaidMimeType.SVG.value};base64,{b64_diagram}",
        message="Workflow diagram generated successfully",
    )


@router.post(
    "/workflows/{workflow_id}/evaluate",
    status_code=status.HTTP_200_OK,
    response_model=EvaluationResponse,
)
def evaluate_workflow(
    workflow_id: str,
    request: WorkflowEvaluationRequest,
    raw_request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(authenticate),
):
    """
    Evaluate a workflow against a dataset of inputs from Langfuse.

    Runs every item in the dataset through the workflow and records the results as a
    named experiment in Langfuse. Eager validation returns an immediate response; the
    item-by-item execution runs in the background.
    """
    try:
        workflow_config = workflow_service.get_workflow(workflow_id, user)
    except Exception as e:
        logger.error(str(e).strip())
        raise_not_found(resource_id=workflow_id, resource_type="Workflow")

    if not Ability(user).can(Action.READ, workflow_config):
        raise_forbidden("evaluate")

    evaluation_response = WorkflowEvaluationService.evaluate_workflow(
        workflow_config=workflow_config,
        dataset_id=request.dataset_id,
        experiment_name=request.experiment_name,
        max_concurrency=request.max_concurrency,
        background_tasks=background_tasks,
        user=user,
        raw_request=raw_request,
    )
    logger.info(f"Workflow evaluation task started for experiment: {request.experiment_name}")

    return evaluation_response


def update_workflow_schema(workflow_config, user):
    try:
        workflow_schema = WorkflowExecutor.validate_workflow_and_draw(workflow_config=workflow_config, user=user)
    except Exception as e:
        # The diagram is optional: a rendering failure must never block saving the workflow
        logger.error(f"Failed to draw workflow diagram, saving without it: {str(e)}")
        workflow_schema = None
    workflow_service.save_workflow_schema(workflow_config, workflow_schema)


@router.get("/workflows/custom-nodes", response_model=list[str])
async def get_custom_nodes(user: User = Depends(authenticate)) -> list[str]:
    """Get list of all available custom node types."""
    node_ids = CustomNodeInfoService.get_node_ids()
    return node_ids


@router.get("/workflows/custom-nodes/{custom_node_id}/schema", response_model=CustomNodeSchemaResponse)
async def get_custom_node_schema(custom_node_id: str, user: User = Depends(authenticate)) -> CustomNodeSchemaResponse:
    """Get configuration schema for a specific custom node type."""
    return CustomNodeInfoService.get_node_schema(custom_node_id)


if config.WORKFLOW_GENERATION_ENABLED:

    @router.post(
        "/workflows/generate",
        status_code=status.HTTP_200_OK,
        response_model=WorkflowGeneratorResponse,
        response_model_exclude_none=True,
    )
    def generate_workflow(
        raw_request: Request,
        request: WorkflowGeneratorRequest,
        user: User = Depends(authenticate),
    ):
        """Generate a workflow configuration from a natural language description."""
        validate_model_selection(request.llm_model)
        request_id = raw_request.state.uuid
        set_llm_context(None, user.current_project, user)

        try:
            return WorkflowGeneratorService.generate(
                nl_query=request.text,
                user=user,
                llm_model=request.llm_model,
                persist=request.persist,
                guardrail_ids=request.guardrail_ids,
                request_id=request_id,
            )
        except WorkflowGenerationError as exc:
            raise ExtendedHTTPException(
                code=500,
                message=exc.message,
                details=exc.details,
                help=exc.help,
            ) from exc

    @router.post(
        "/workflows/{workflow_id}/refine",
        status_code=status.HTTP_200_OK,
        response_model=WorkflowRefineResponse,
    )
    def refine_workflow(
        workflow_id: str,
        request: WorkflowRefineRequest,
        raw_request: Request,
        user: User = Depends(authenticate),
    ):
        from codemie.configs.logger import set_logging_info
        from codemie.service.llm_service.utils import set_llm_context

        try:
            workflow = workflow_service.get_workflow(workflow_id)
        except KeyError:
            raise_not_found(resource_id=workflow_id, resource_type="Workflow")

        if not Ability(user).can(Action.WRITE, workflow):
            raise_access_denied("refine")

        validate_model_selection(request.llm_model)
        request_id = raw_request.state.uuid
        set_logging_info(uuid=request_id, user_id=user.id, username=user.username)
        set_llm_context(None, user.current_project, user)

        try:
            return WorkflowGeneratorService.refine_workflow(
                yaml_config=request.yaml_config,
                refine_prompt=request.refine_prompt,
                user=user,
                llm_model=request.llm_model,
                request_id=request_id,
            )
        except WorkflowGenerationError as exc:
            raise ExtendedHTTPException(
                code=500,
                message=exc.message,
                details=exc.details,
                help=exc.help,
            ) from exc


def _load_workflow_record(workflow_id: str) -> WorkflowConfig:
    """Load a workflow row without parsing the live yaml (needed to list/rollback a corrupt config)."""
    try:
        return WorkflowConfig.get_by_id(workflow_id)
    except KeyError:
        raise_not_found(resource_id=workflow_id, resource_type="Workflow")


@router.get(
    "/workflows/{workflow_id}/versions",
    status_code=status.HTTP_200_OK,
    response_model=WorkflowVersionHistoryResponse,
    tags=["Workflow Versioning"],
)
def get_workflow_versions(
    workflow_id: str,
    page: Annotated[int, Query(ge=0)] = 0,
    per_page: Annotated[int, Query(ge=1, le=MAX_VERSIONS_PER_PAGE)] = DEFAULT_VERSIONS_PER_PAGE,
    user: User = Depends(authenticate),
) -> WorkflowVersionHistoryResponse:
    workflow = _load_workflow_record(workflow_id)
    if not Ability(user).can(Action.WRITE, workflow):
        raise_access_denied("view")
    return WorkflowVersionService.get_version_history(workflow, page, per_page)


@router.post(
    "/workflows/{workflow_id}/versions/rollback",
    status_code=status.HTTP_200_OK,
    response_model=DisabledMcpResponse,
    tags=["Workflow Versioning"],
)
def rollback_workflow_version(
    workflow_id: str,
    request: WorkflowRollbackRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(authenticate),  # noqa: B008 - FastAPI dependency declaration
) -> DisabledMcpResponse:
    workflow = _load_workflow_record(workflow_id)
    if not Ability(user).can(Action.WRITE, workflow):
        raise_access_denied("update")
    with _database_errors():
        rolled_back, disabled_mcp = WorkflowVersionService.rollback_to_version(
            workflow=workflow,
            revision_index=request.revision_index,
            user=user,
            change_notes=request.change_notes,
        )
    background_tasks.add_task(run_in_thread_pool, update_workflow_schema, rolled_back, user)
    return DisabledMcpResponse(
        message=message_with_disabled(
            f"Workflow {workflow_id} rolled back to revision {request.revision_index}.", disabled_mcp
        ),
        disabled_mcp_servers=disabled_mcp,
    )
