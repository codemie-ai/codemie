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

"""Save pipeline (EPMCDME-16504, T6): the flag helper, `prepare` and `validate`."""

from unittest.mock import MagicMock, patch

import pytest
import yaml
from sqlalchemy.exc import SQLAlchemyError

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException, ValidationException
from codemie.core.workflow_models import WorkflowConfig, WorkflowErrorFormat
from codemie.workflows.workflow import WorkflowExecutor
from codemie.service.workflow import save_validation
from codemie.service.workflow.save_validation import (
    SaveValidationResult,
    contract_linking_enabled,
    prepare,
    validate,
)

MODULE = "codemie.service.workflow.save_validation"
BAD_YAML = "assistants: [unclosed"
NEEDS_X = '{"type":"object","properties":{"x":{"type":"string"}},"required":["x"]}'
PRODUCER = '{"type":"object","properties":{"x":{"type":"string"}},"required":["x"]}'


@pytest.mark.parametrize("flag", [True, False])
def test_contract_linking_enabled_follows_config(flag):
    with patch.object(config, "WORKFLOW_CONTRACT_LINKING_ENABLED", flag):
        assert contract_linking_enabled() is flag


def _clean() -> dict:
    return {
        "assistants": [{"id": "a1", "model": "o1", "system_prompt": "do it"}],
        "states": [{"id": "s1", "assistant_id": "a1", "next": {"state_id": "end"}}],
    }


def _derived_warning() -> dict:
    """s2 removes x, then c1 reads it through a placeholder: a derived record that saves in the default mode."""
    return {
        "assistants": [{"id": "a1", "system_prompt": "do it"}],
        "states": [
            {"id": "s1", "assistant_id": "a1", "output_schema": PRODUCER, "next": {"state_id": "s2"}},
            {"id": "s2", "assistant_id": "a1", "next": {"state_id": "c1", "reset_keys_in_context_store": ["x"]}},
            {
                "id": "c1",
                "assistant_id": "a1",
                "task": "use {{ x }}",
                "resolve_dynamic_values_in_prompt": True,
                "next": {"state_id": "end"},
            },
        ],
    }


def _dump(config_dict: dict | None = None, yaml_text: str | None = None, **extra) -> dict:
    return {
        "name": "wf",
        "description": "d",
        "project": "demo",
        "mode": "Sequential",
        "yaml_config": yaml_text if yaml_text is not None else yaml.safe_dump(config_dict or _clean()),
        **extra,
    }


def _candidate(config_dict: dict | None = None) -> WorkflowConfig:
    return WorkflowConfig(**_dump(config_dict))


@pytest.fixture
def user():
    return MagicMock(id="u1")


@pytest.fixture(autouse=True)
def _no_database():
    with patch(f"{MODULE}._get_project_for_workflow", return_value=None):
        yield


def _model_catalog(*pairs):
    """An LLM catalog of (base_name, deployment_name) pairs, as llm_service hands it out."""
    from codemie.configs.llm_config import LLMModel

    models = [
        LLMModel(base_name=base, deployment_name=dep, label=base, enabled=True, provider="azure_openai")
        for base, dep in pairs
    ]
    return patch(f"{MODULE}.llm_service.get_allowed_chat_models", return_value=models)


# ---------------------------------------------------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------------------------------------------------


def test_prepare_defer_returns_an_unparsed_config_on_bad_yaml(user):
    with patch(f"{MODULE}.normalize_workflow_model_ids") as normalize:
        result = prepare(_dump(yaml_text=BAD_YAML), user, defer_parse_failure=True)

    assert isinstance(result, WorkflowConfig)
    assert result.yaml_config == BAD_YAML
    assert not result.assistants
    normalize.assert_not_called()


def test_prepare_without_defer_raises_400_on_bad_yaml(user):
    with pytest.raises(ExtendedHTTPException) as exc_info:
        prepare(_dump(yaml_text=BAD_YAML), user, defer_parse_failure=False)

    assert exc_info.value.code == 400
    assert exc_info.value.message == "Workflow Configuration error"
    assert exc_info.value.details


def test_prepare_normalizes_model_ids_and_rewrites_yaml_keeping_the_source(user):
    original = yaml.safe_dump(_clean())
    with _model_catalog(("o1", "o1-2024-12-17")):
        result = prepare(_dump(yaml_text=original), user, defer_parse_failure=True)

    assert result.assistants[0].model == "o1-2024-12-17"
    assert yaml.safe_load(result.yaml_config)["assistants"][0]["model"] == "o1-2024-12-17"
    assert result._source_yaml_config == original


def test_prepare_leaves_yaml_untouched_when_no_model_id_changes(user):
    original = yaml.safe_dump(_clean())
    with _model_catalog(("gpt-4o", "gpt-4o-2024-11-20")):
        result = prepare(_dump(yaml_text=original), user, defer_parse_failure=False)

    assert result.yaml_config == original
    assert result._source_yaml_config is None


# ---------------------------------------------------------------------------------------------------------------------
# validate: flag
# ---------------------------------------------------------------------------------------------------------------------


def test_validate_flag_on_returns_coverage_and_contract_warnings_after_the_advisory_ones(user):
    consumer_slot = {"kind": "consumer_slot", "message": "slot"}
    with (
        patch(f"{MODULE}.collect_consumer_slot_integration_warnings", return_value=[consumer_slot]),
        patch(f"{MODULE}.collect_tool_access_owner_integration_warnings", return_value=[]),
    ):
        result = validate(
            _candidate(_derived_warning()),
            _dump(_derived_warning()),
            user,
            WorkflowErrorFormat.STRING,
            link_contracts=True,
        )

    assert isinstance(result, SaveValidationResult)
    assert result.warnings[0] == consumer_slot
    contract = [w for w in result.warnings if w.get("kind") == "contract_validation"]
    assert contract
    assert result.warnings[1:] == contract
    assert result.coverage is not None
    assert result.coverage["analysed"] is True
    assert result.warnings_truncated == 0


def test_validate_flag_none_follows_the_platform_flag(user):
    with (
        patch.object(config, "WORKFLOW_CONTRACT_LINKING_ENABLED", True),
        patch.object(
            WorkflowExecutor, "validate_workflow_with_report", wraps=WorkflowExecutor.validate_workflow_with_report
        ) as run,
    ):
        result = validate(_candidate(), _dump(), user, WorkflowErrorFormat.STRING)

    assert run.call_args.args[3] is True
    assert result.coverage is not None


def test_validate_flag_off_has_no_coverage_and_never_calls_the_linker(user):
    with (
        patch.object(config, "WORKFLOW_CONTRACT_LINKING_ENABLED", False),
        patch("codemie.workflows.workflow.link_workflow_contracts") as linker,
    ):
        result = validate(
            _candidate(_derived_warning()),
            _dump(_derived_warning()),
            user,
            WorkflowErrorFormat.STRING,
        )

    linker.assert_not_called()
    assert result.coverage is None
    assert result.warnings_truncated == 0
    assert not [w for w in result.warnings if w.get("kind") == "contract_validation"]


def test_validate_reports_the_truncation_count_of_the_contract_warnings(user):
    from codemie.workflows.validation.contract_issues import ContractIssues

    issues = ContractIssues([], [{"kind": "contract_validation"}], {"analysed": True}, 7)
    with patch(f"{MODULE}.build_contract_issues", return_value=issues):
        result = validate(_candidate(), _dump(), user, WorkflowErrorFormat.STRING, link_contracts=True)

    assert result.warnings_truncated == 7
    assert result.coverage == {"analysed": True}


def test_validate_shaping_failure_is_logged_and_advisory(user):
    with patch(f"{MODULE}.build_contract_issues", side_effect=RuntimeError("boom")):
        result = validate(_candidate(), _dump(), user, WorkflowErrorFormat.STRING, link_contracts=True)

    assert result.coverage is None
    assert result.warnings_truncated == 0


def test_validate_advisory_collection_failure_never_fails_the_save(user):
    with (
        patch(f"{MODULE}.collect_consumer_slot_integration_warnings", side_effect=RuntimeError("boom")),
        patch(f"{MODULE}.collect_tool_access_owner_integration_warnings", side_effect=RuntimeError("boom")),
    ):
        result = validate(_candidate(), _dump(), user, WorkflowErrorFormat.STRING, link_contracts=False)

    assert result.warnings == []


@pytest.mark.parametrize(
    "collector", ["collect_consumer_slot_integration_warnings", "collect_tool_access_owner_integration_warnings"]
)
def test_validate_advisory_provider_database_failure_propagates(user, collector):
    failure = SQLAlchemyError("private database diagnostic")
    with patch(f"{MODULE}.{collector}", side_effect=failure), patch(f"{MODULE}._marketplace_service"):
        with pytest.raises(SQLAlchemyError) as caught:
            validate(
                _candidate(),
                _dump(),
                user,
                WorkflowErrorFormat.STRING,
                stored_workflow=MagicMock(is_global=True),
                link_contracts=False,
            )
    assert caught.value is failure


@pytest.mark.parametrize(
    ("link_contracts", "stored_global", "expect_owner_warnings"),
    [(False, False, False), (False, True, True), (True, False, True)],
)
def test_validate_owner_integration_warning_scope_follows_the_flag(
    user, link_contracts, stored_global, expect_owner_warnings
):
    stored = MagicMock(is_global=stored_global) if stored_global else None
    with (
        patch(f"{MODULE}._marketplace_service"),
        patch(f"{MODULE}.collect_consumer_slot_integration_warnings", return_value=[]),
        patch(f"{MODULE}.collect_tool_access_owner_integration_warnings", return_value=[{"kind": "owner"}]) as owner,
    ):
        result = validate(
            _candidate(),
            _dump(),
            user,
            WorkflowErrorFormat.STRING,
            stored_workflow=stored,
            link_contracts=link_contracts,
        )

    assert owner.called is expect_owner_warnings
    assert ({"kind": "owner"} in result.warnings) is expect_owner_warnings


# ---------------------------------------------------------------------------------------------------------------------
# validate: MCP gate and marketplace
# ---------------------------------------------------------------------------------------------------------------------

CUSTOM_MCP = {"name": "srv", "use_custom_config": True, "config": {"command": "npx", "args": []}}


def test_validate_restricted_mode_custom_mcp_on_the_raw_dump_raises_validation_exception(user):
    dump = _dump(_clean(), assistants=[{"id": "a1", "mcp_servers": [CUSTOM_MCP]}])
    # the candidate was parsed from yaml_config, so only the raw dump still carries the hand-written server
    with patch("codemie.service.mcp.access_control.customer_config") as customer:
        customer.is_component_enabled.side_effect = lambda name: name == "mcpCustomServersDisabled"
        with pytest.raises(ValidationException):
            validate(_candidate(), dump, user, WorkflowErrorFormat.STRING, link_contracts=False)


def test_validate_runs_the_mcp_gate_on_the_raw_dump_and_a_parsed_copy_then_strips(user):
    candidate = _candidate()
    with (
        patch(f"{MODULE}.MCPAccessControlService.validate_workflow_mcp") as gate,
        patch(f"{MODULE}.MCPAccessControlService.strip_workflow_mcp_servers") as strip,
    ):
        validate(candidate, _dump(), user, WorkflowErrorFormat.STRING, link_contracts=False)

    assert gate.call_count == 2
    strip.assert_called_once_with(candidate)


def test_validate_without_a_dump_gates_the_candidate_only_and_collects_no_warnings(user):
    candidate = _candidate()
    candidate.parse_execution_config()
    with (
        patch(f"{MODULE}.MCPAccessControlService.validate_and_strip_workflow_mcp_servers") as gate,
        patch(f"{MODULE}.collect_advisory_warnings") as advisory,
    ):
        result = validate(candidate, None, user, WorkflowErrorFormat.STRING, link_contracts=False)

    gate.assert_called_once_with(candidate)
    advisory.assert_not_called()
    assert result == SaveValidationResult([], None, 0)


def test_validate_stored_global_workflow_triggers_marketplace_validation(user):
    candidate = _candidate()
    stored = MagicMock(is_global=True)
    with patch(f"{MODULE}._marketplace_service") as marketplace:
        validate(
            candidate,
            _dump(),
            user,
            WorkflowErrorFormat.STRING,
            stored_workflow=stored,
            link_contracts=False,
        )

    marketplace.validate.assert_called_once_with(candidate, user)


@pytest.mark.parametrize("stored", [None, MagicMock(is_global=False)])
def test_validate_skips_marketplace_when_nothing_stored_is_global(user, stored):
    with patch(f"{MODULE}._marketplace_service") as marketplace:
        validate(
            _candidate(),
            _dump(),
            user,
            WorkflowErrorFormat.STRING,
            stored_workflow=stored,
            link_contracts=False,
        )

    marketplace.validate.assert_not_called()


def test_validate_marketplace_failure_propagates_untouched(user):
    with patch(f"{MODULE}._marketplace_service") as marketplace:
        marketplace.validate.side_effect = ValidationException("external assistants")
        with pytest.raises(ValidationException, match="external assistants"):
            validate(
                _candidate(),
                _dump(),
                user,
                WorkflowErrorFormat.STRING,
                stored_workflow=MagicMock(is_global=True),
                link_contracts=False,
            )


# ---------------------------------------------------------------------------------------------------------------------
# validate: executor errors propagate unwrapped
# ---------------------------------------------------------------------------------------------------------------------


def test_validate_sqlalchemy_error_from_the_executor_is_not_wrapped(user):
    boom = SQLAlchemyError("db down")
    with patch.object(WorkflowExecutor, "validate_workflow_with_report", side_effect=boom):
        with pytest.raises(SQLAlchemyError) as exc_info:
            validate(_candidate(), _dump(), user, WorkflowErrorFormat.STRING)

    assert exc_info.value is boom


def test_validate_executor_value_error_is_not_wrapped(user):
    boom = ValueError({"error_type": "x"})
    with patch.object(WorkflowExecutor, "validate_workflow_with_report", side_effect=boom):
        with pytest.raises(ValueError) as exc_info:
            validate(_candidate(), _dump(), user, WorkflowErrorFormat.JSON)

    assert exc_info.value is boom


def test_validate_passes_the_error_format_to_the_executor(user):
    with patch.object(WorkflowExecutor, "validate_workflow_with_report", return_value=(object(), MagicMock())) as run:
        validate(_candidate(), _dump(), user, WorkflowErrorFormat.JSON, link_contracts=False)

    assert run.call_args.args[2] == WorkflowErrorFormat.JSON
    assert run.call_args.args[3] is False


# ---------------------------------------------------------------------------------------------------------------------
# rollback (AR-8)
# ---------------------------------------------------------------------------------------------------------------------


def test_rollback_shaped_call_never_links_contracts_even_with_the_flag_on(user):
    workflow = _candidate(_derived_warning())
    workflow.parse_execution_config()
    with (
        patch.object(config, "WORKFLOW_CONTRACT_LINKING_ENABLED", True),
        patch("codemie.workflows.workflow.link_workflow_contracts") as linker,
    ):
        result = validate(
            workflow,
            None,
            user,
            WorkflowErrorFormat.STRING,
            stored_workflow=workflow,
            link_contracts=False,
        )

    linker.assert_not_called()
    assert result.coverage is None


def test_module_exposes_the_moved_helpers():
    for name in (
        "_get_project_for_workflow",
        "normalize_workflow_model_ids",
        "_patch_model_ids_in_yaml",
        "rewrite_yaml_config_from_objects",
        "collect_advisory_warnings",
    ):
        assert hasattr(save_validation, name)


@pytest.mark.parametrize("scenario", ["clean", "warning", "strict"])
@pytest.mark.parametrize("normalize", [False, True])
def test_source_metadata_is_built_once_per_distinct_text_and_never_shared_between_requests(user, scenario, normalize):
    from collections import Counter
    from codemie.workflows.validation.line_lookup import SourceMetadata

    data = _clean() if scenario == "clean" else _derived_warning()
    data["assistants"][0]["model"] = "o1"
    if scenario == "strict":
        data["save_validation"] = "strict"
    source = "# Client source\n\n" + yaml.safe_dump(data, sort_keys=False)
    models = (("o1", "o1-2024-12-17"),) if normalize else ()
    for submitted in (source, "# Second request\n" + source):
        with (
            _model_catalog(*models),
            patch("codemie.workflows.validation.line_lookup.SourceMetadata", wraps=SourceMetadata) as build,
        ):
            dump = _dump(yaml_text=submitted)
            candidate = prepare(dump, user, defer_parse_failure=True)
            if scenario == "strict":
                with pytest.raises(ValueError) as failure:
                    validate(candidate, dump, user, WorkflowErrorFormat.JSON, link_contracts=True)
                assert failure.value.args[0]["error_type"] == "contract_validation"
            else:
                result = validate(candidate, dump, user, WorkflowErrorFormat.JSON, link_contracts=True)
                assert result.coverage["analysed"] is True
            counts = Counter(call.args[0] for call in build.call_args_list)
            assert counts == Counter(dict.fromkeys({submitted, candidate.yaml_config}, 1))
            if scenario == "warning":
                entry = next(w for w in result.warnings if w["kind"] == "contract_validation")
                assert "task:" in submitted.splitlines()[entry["config_line"] - 1]


def test_successful_executor_does_not_build_a_rejection_only_finder(user):
    with patch("codemie.workflows.workflow.contract_line_finder") as finder:
        WorkflowExecutor.validate_workflow_with_report(_candidate(), user, link_contracts=True)
    finder.assert_not_called()


def test_internal_tool_credentials_preserve_database_errors():
    from codemie.service.assistant.credential_validator import CredentialValidator

    failure = SQLAlchemyError("private diagnostic")
    with patch("codemie.service.assistant.credential_validator.SettingsService.get_git_creds", side_effect=failure):
        with pytest.raises(SQLAlchemyError) as caught:
            CredentialValidator.validate_tool_credentials("Git", "git_tool", MagicMock(id="user"), "demo")
    assert caught.value is failure


def test_tool_config_resolution_preserves_database_errors():
    from codemie.service.tools.tool_metadata_service import ToolMetadataService

    failure = SQLAlchemyError("private diagnostic")
    with (
        patch.object(ToolMetadataService, "requires_credentials", return_value=True),
        patch.object(ToolMetadataService, "get_config_class", return_value=object),
        patch("codemie.service.tools.tool_metadata_service.SettingsService.get_config", side_effect=failure),
    ):
        with pytest.raises(SQLAlchemyError) as caught:
            ToolMetadataService.resolve_config("tool", "toolkit", "user", "demo")
    assert caught.value is failure


def test_nested_assistant_validation_preserves_database_errors():
    from codemie.service.assistant.assistant_integration_validator import AssistantIntegrationValidator

    failure = SQLAlchemyError("private diagnostic")
    with patch("codemie.service.assistant.assistant_integration_validator.Assistant.get_by_ids", side_effect=failure):
        with pytest.raises(SQLAlchemyError) as caught:
            AssistantIntegrationValidator._validate_sub_assistants(["nested"], MagicMock(), "demo")
    assert caught.value is failure


def test_sub_workflow_validation_preserves_database_errors():
    from codemie.workflows.validation.resources import _validate_sub_workflow_availability

    failure = SQLAlchemyError("private diagnostic")
    candidate = MagicMock(id="parent", states=[MagicMock(id="step", workflow_id="child")])
    with (
        patch("codemie.workflows.validation.resources.customer_config") as customer_config,
        patch("codemie.workflows.validation.resources.WorkflowService.get_workflow", side_effect=failure),
    ):
        customer_config.is_feature_enabled.return_value = True
        with pytest.raises(SQLAlchemyError) as caught:
            _validate_sub_workflow_availability(candidate, MagicMock())
    assert caught.value is failure
