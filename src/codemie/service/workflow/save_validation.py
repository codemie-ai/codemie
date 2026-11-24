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

"""Save pipeline shared by the workflow create, update and validate entry points.

Two sync steps: `prepare` turns a request dump into a parsed, normalized candidate and `validate` runs the
save-time gates on it (MCP, marketplace, executor, advisory and contract warnings). Routers keep the entry-specific
steps (access, autonomous mode, guardrails, categories, persistence) and offload each step with `asyncio.to_thread`
when they are async.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from typing import Any, NamedTuple, cast

import yaml
from fastapi import status
from sqlalchemy.exc import SQLAlchemyError

from codemie.clients.postgres import get_session
from codemie.configs import config, logger
from codemie.configs.llm_config import LLMModel
from codemie.core.exceptions import ExtendedHTTPException, ModelNotSelectableError
from codemie.core.workflow_models import WorkflowConfig, WorkflowErrorFormat
from codemie.repository.application_repository import application_repository
from codemie.rest_api.security.user import User
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.mcp.access_control import MCPAccessControlService
from codemie.service.workflow_config.workflow_marketplace_service import WorkflowMarketplaceService
from codemie.workflows.validation.contract_issues import build_contract_issues, contract_line_finder
from codemie.workflows.validation.line_lookup import ValidationSourceContext
from codemie.workflows.validation.resources import collect_consumer_slot_integration_warnings
from codemie.workflows.validation.tool_access import collect_tool_access_owner_integration_warnings
from codemie.workflows.workflow import WorkflowExecutor

WORKFLOW_CONFIGURATION_ERROR = "Workflow Configuration error"

_marketplace_service = WorkflowMarketplaceService()


class SaveValidationResult(NamedTuple):
    warnings: list[dict[str, object]]
    coverage: dict[str, Any] | None  # None when unlinked
    warnings_truncated: int  # 0 when unlinked or nothing cut


def contract_linking_enabled() -> bool:
    """Return whether save-time contract linking is enabled on this platform.

    Validation reads ``WORKFLOW_CONTRACT_LINKING_ENABLED`` only here; the same setting is exposed to the UI as the
    ``features:workflowContracts`` runtime component in ``customer_config``.
    """
    return config.WORKFLOW_CONTRACT_LINKING_ENABLED


def assert_model_selections_selectable(
    workflow_config: WorkflowConfig, previous_config: WorkflowConfig | None = None
) -> None:
    """Reject unavailable models newly selected for workflow assistants or custom nodes.

    A stored deprecated model remains valid when its owning entry keeps the same model.
    ``get_workflow()`` hydrates assistants and custom nodes from the persisted YAML, so
    matching entries by their stable workflow id preserves that legacy selection on edit.
    """
    selection_groups = (
        (workflow_config.assistants, previous_config.assistants if previous_config else None),
        (workflow_config.custom_nodes, previous_config.custom_nodes if previous_config else None),
    )
    for selections, previous_selections in selection_groups:
        previous_models = {item.id: item.model for item in previous_selections or []}
        for selection in selections or []:
            if not selection.model:
                continue
            try:
                llm_service.assert_model_selectable(
                    selection.model, previous_model_name=previous_models.get(selection.id)
                )
            except ModelNotSelectableError as e:
                raise ExtendedHTTPException(
                    code=status.HTTP_400_BAD_REQUEST,
                    message="Model not selectable",
                    details=str(e),
                ) from e


def prepare(
    request_dump: dict[str, Any],
    user: User,
    defer_parse_failure: bool,
    previous_config: WorkflowConfig | None = None,
) -> WorkflowConfig:
    """Build the candidate config from a request dump: parse, check model selections, normalize model ids,
    rewrite `yaml_config`.

    With ``defer_parse_failure`` a parse failure is left for the executor's schema stage to report and the
    candidate is returned unparsed. Without it the failure is raised as a 400 with the raw message.
    The model-selection check runs on the parsed, not yet normalized, candidate; ``previous_config`` is the
    stored workflow on edit, so an entry that keeps its deprecated model stays valid. A rejected model is
    raised as the check's own 400 "Model not selectable".
    """
    with nullcontext() if defer_parse_failure else _configuration_errors():
        candidate = WorkflowConfig(**request_dump)
        if not _parse(candidate, defer_parse_failure):
            return candidate
        assert_model_selections_selectable(candidate, previous_config)
        normalize_workflow_model_ids(candidate, user)
        rewrite_yaml_config_from_objects(candidate)
    return candidate


def _parse(candidate: WorkflowConfig, defer_parse_failure: bool) -> bool:
    """Parse `yaml_config`; with ``defer_parse_failure`` a failure is logged and reported as False, else raised."""
    if not defer_parse_failure:
        candidate.parse_execution_config()
        return True
    # Parsing errors are deferred to the executor, which validates the original yaml_config against the JSON
    # schema and reports a precise, path-aware error. Normalizing a half-parsed config could persist it.
    try:
        candidate.parse_execution_config()
    except SQLAlchemyError:
        raise
    except Exception as e:
        logger.warning(f"Could not parse workflow yaml_config before model-id normalization: {e}")
        return False
    return True


@contextmanager
def _configuration_errors() -> Iterator[None]:
    """Raise a failure to build the candidate as a 400 with the raw message."""
    try:
        yield
    except (SQLAlchemyError, ExtendedHTTPException):
        # ExtendedHTTPException never calls super().__init__(), so re-wrapping the model-selection
        # check's 400 via str(e) would discard its message and details - re-raise it as-is.
        raise
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=WORKFLOW_CONFIGURATION_ERROR,
            details=str(e).strip(),
            help="",
        ) from e


def validate(  # noqa: PLR0913 - keyword-only builder/internal helper; a parameter object would add indirection without clarity
    candidate: WorkflowConfig,
    request_dump: dict[str, Any] | None,
    user: User,
    error_format: WorkflowErrorFormat,
    stored_workflow: WorkflowConfig | None = None,
    link_contracts: bool | None = None,
) -> SaveValidationResult:
    """Run the save-time gates on a prepared candidate.

    ``link_contracts=None`` follows the platform flag. Raises what the gates raise (MCP `ValidationException`,
    marketplace exceptions, `SQLAlchemyError`, executor `ValueError`) untouched; the routers wrap them.
    ``request_dump=None`` (rollback) gates the candidate only and skips the advisory warnings.
    """
    linked = contract_linking_enabled() if link_contracts is None else link_contracts

    _gate_mcp_servers(candidate, request_dump)
    if stored_workflow and stored_workflow.is_global:
        _marketplace_service.validate(candidate, user)

    source_context = ValidationSourceContext()
    _, report = WorkflowExecutor.validate_workflow_with_report(
        candidate, user, error_format, linked, source_context=source_context
    )

    if request_dump is None:
        return SaveValidationResult([], None, 0)

    # Flag off keeps today's rule: the owner-integration warning is for global workflows only.
    owner_warnings = linked or bool(stored_workflow and stored_workflow.is_global)
    warnings = collect_advisory_warnings(candidate, user, include_owner_integration=owner_warnings)
    coverage: dict[str, Any] | None = None
    truncated = 0
    if linked:
        try:
            issues = build_contract_issues(report, contract_line_finder(candidate, source_context))
            warnings.extend(issues.warnings)
            coverage = issues.coverage
            truncated = issues.warnings_truncated
        except Exception as e:
            logger.warning(f"Failed to shape contract issues for a validated workflow: {e}", exc_info=True)
    return SaveValidationResult(warnings, coverage, truncated)


def _gate_mcp_servers(candidate: WorkflowConfig, request_dump: dict[str, Any] | None) -> None:
    """MCP governance on the raw request and a freshly parsed copy, then strip the candidate's servers in-place."""
    if request_dump is None:
        MCPAccessControlService.validate_and_strip_workflow_mcp_servers(candidate)
        return
    # `candidate` was parsed from yaml_config, which overwrites assistants/tools and would hide a server hand-written
    # directly on the request's own fields. Check those as submitted, and a freshly parsed copy for clients
    # (UI/SDK) that send only yaml_config; a parse failure there is left to the executor to report.
    raw_from_request = WorkflowConfig(**request_dump)
    parsed_from_yaml = WorkflowConfig(**request_dump)
    try:
        parsed_from_yaml.parse_execution_config()
    except Exception as e:
        logger.warning(f"Could not parse workflow yaml_config before MCP validation: {e}")
    for checked in (raw_from_request, parsed_from_yaml):
        MCPAccessControlService.validate_workflow_mcp(checked)
    MCPAccessControlService.strip_workflow_mcp_servers(candidate)


def collect_advisory_warnings(
    workflow_config: WorkflowConfig, user: User, include_owner_integration: bool = True
) -> list[dict[str, object]]:
    """Collect the advisory warnings for a workflow that passed validation.

    ``include_owner_integration=False`` skips the script-step owner-integration warning.

    Formatting failures stay advisory; database failures propagate to the route's safe 503 boundary.
    """
    warnings: list[dict[str, object]] = []
    try:
        warnings.extend(collect_consumer_slot_integration_warnings(workflow_config, user))
    except SQLAlchemyError:
        raise
    except Exception as e:
        logger.warning(f"Failed to collect consumer slot warnings for a saved workflow: {e}")
    if include_owner_integration:
        try:
            warnings.extend(collect_tool_access_owner_integration_warnings(workflow_config))
        except SQLAlchemyError:
            raise
        except Exception as e:
            logger.warning(f"Failed to collect script step warnings for a saved workflow: {e}")
    return warnings


def _get_project_for_workflow(project_name: str):
    """
    Fetch Application for project filtering when normalizing model ids.
    We keep this local to avoid import cycles elsewhere.
    """
    if not project_name:
        return None
    with get_session() as session:
        return application_repository.get_by_name(session, project_name)


def normalize_workflow_model_ids(workflow_config: WorkflowConfig, user: User) -> None:
    """
    Normalize assistant/custom-node model ids from base_name (e.g. 'o1') to deployment_name
    (e.g. 'o1-2024-12-17') so that:
      - YAML saved to DB/storage is consistent
      - model whitelist checks work reliably
      - executions don't unexpectedly fall back
    """
    project = None if workflow_config.is_global else _get_project_for_workflow(workflow_config.project)

    # Build base_name -> deployment_name mapping using the same catalog as /v1/llm_models
    models = list(llm_service.get_allowed_chat_models(user, include_all=True, project=project))
    by_base = {
        cast(LLMModel, m).base_name: cast(LLMModel, m).deployment_name
        for m in models
        if isinstance(m, LLMModel) and getattr(m, "deployment_name", None)
    }

    for assistant in workflow_config.assistants or []:
        if assistant.model and assistant.model in by_base:
            assistant.model = by_base[assistant.model]

    for cn in workflow_config.custom_nodes or []:
        if cn.model and cn.model in by_base:
            cn.model = by_base[cn.model]


def _patch_model_ids_in_yaml(config: dict[str, Any], section: str, items: list[Any] | None) -> bool:
    """Copy normalized item.model values into the parsed YAML entry with the same id."""
    entries = {e["id"]: e for e in config.get(section) or [] if isinstance(e, dict) and "id" in e}
    changed = False
    for item in items or []:
        entry = entries.get(item.id)
        if entry is not None and "model" in entry and item.model and entry["model"] != item.model:
            entry["model"] = item.model
            changed = True
    return changed


def rewrite_yaml_config_from_objects(workflow_config: WorkflowConfig) -> None:
    """
    The UI YAML panel is driven by workflow_config.yaml_config (raw YAML string).
    We normalize models in assistants/custom_nodes, so yaml_config must carry the resulting
    deployment_name identifiers (e.g. o1-2024-12-17).

    Only those `model` values are rewritten; everything else (orphaned_states, meta_states,
    comments, key order, absent defaults) is stored exactly as sent. When no model id changed,
    yaml_config is left untouched.
    """
    original = yaml.safe_load(workflow_config.yaml_config or "")
    if not isinstance(original, dict):
        return

    assistants_changed = _patch_model_ids_in_yaml(original, "assistants", workflow_config.assistants)
    custom_nodes_changed = _patch_model_ids_in_yaml(original, "custom_nodes", workflow_config.custom_nodes)

    if assistants_changed or custom_nodes_changed:
        if workflow_config._source_yaml_config is None:
            workflow_config._source_yaml_config = workflow_config.yaml_config  # validation errors point at this text
        workflow_config.yaml_config = yaml.safe_dump(original, sort_keys=False)
