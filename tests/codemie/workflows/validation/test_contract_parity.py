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

"""Parity of the four save entry points (EPMCDME-16504, AC-8): create, update, saved dry-run and POST /validate.

One config, one user and one project go through the real routers and the real save pipeline. Only persistence, the
catalog and the lookups behind the advisory warnings are stubbed. The non-global case compares the contract entries
(ids included), the `consumer_slot` entries and the coverage; the global case is pinned separately, because only a
stored global workflow reaches the owner-integration warning and marketplace validation.
"""

from contextlib import ExitStack
from unittest.mock import MagicMock, patch
import json

from sqlalchemy.exc import StatementError

import pytest
import yaml
from fastapi import status
from fastapi.testclient import TestClient

from codemie.configs import config
from codemie.core.workflow_models import WorkflowConfig, WorkflowMode
from codemie.rest_api.main import app
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

_SAVE = "codemie.service.workflow.save_validation"
_RESOURCES = "codemie.workflows.validation.resources"
_USER = User(id="123", username="123", name="123")
_HEADERS = {"user-id": _USER.id, "username": _USER.username, "name": _USER.name}
_NEEDS_X = '{"type":"object","properties":{"x":{"type":"string"}},"required":["x"]}'
_NAME = {"name": "Parity", "description": "Same config through every entry point"}
_WORKFLOW_ID = "wf-parity"


def _contract_yaml(*, declared_need: bool) -> str:
    """s1 produces x, s2 removes it and c1 needs it: a declared need blocks, a placeholder read only warns."""
    consumer = {"id": "c1", "assistant_id": "a1", "next": {"state_id": "end"}}
    if declared_need:
        consumer["input_schema"] = _NEEDS_X
        consumer.update({"task": "use {{ missing_warning }}", "resolve_dynamic_values_in_prompt": True})
    else:
        consumer.update({"task": "use {{ x }}", "resolve_dynamic_values_in_prompt": True})
    return yaml.safe_dump(
        {
            "assistants": [{"id": "a1", "assistant_id": "stored-assistant", "system_prompt": "do it"}],
            "states": [
                {"id": "s1", "assistant_id": "a1", "output_schema": _NEEDS_X, "next": {"state_id": "s2"}},
                {"id": "s2", "assistant_id": "a1", "next": {"state_id": "c1", "reset_keys_in_context_store": ["x"]}},
                consumer,
            ],
        }
    )


def _script_yaml() -> str:
    return yaml.safe_dump(
        {
            "tools": [
                {
                    "id": "script_tool",
                    "tool": "execute_workspace_script",
                    "tool_access": [{"name": "generic_jira_tool", "integration_alias": "my-jira"}],
                }
            ],
            "states": [{"id": "run_script", "tool_id": "script_tool", "next": {"state_id": "end"}}],
        }
    )


def _stored(yaml_text: str, *, is_global: bool) -> WorkflowConfig:
    return WorkflowConfig(
        id=_WORKFLOW_ID,
        name="Stored",
        description="Stored workflow",
        project="demo",
        yaml_config=yaml_text,
        is_global=is_global,
    )


@pytest.fixture
def client():
    app.dependency_overrides[authenticate] = lambda: _USER
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides = {}


@pytest.fixture
def pipeline(request):
    """Real routers and save pipeline; the stored workflow, persistence and lookups are stubbed. Yields the stubs."""
    with ExitStack() as stack:
        stack.enter_context(patch.object(config, "WORKFLOW_CONTRACT_LINKING_ENABLED", True))
        stubs = MagicMock()
        from codemie.service.workflow import save_validation as save_module
        from codemie.workflows.validation import resources as resource_module
        from codemie.service.workflow_service import WorkflowService

        stubs.real_project = save_module._get_project_for_workflow
        stubs.real_models = save_module.llm_service.get_allowed_chat_models
        stubs.real_resources = resource_module.validate_workflow_config_resources_availability
        stubs.real_load_assistant = resource_module._load_referenced_assistant
        stubs.real_missing_integrations = resource_module._safely_collect_missing_integrations
        service = WorkflowService()
        stubs.real_create = service.create_workflow
        stubs.real_update = service.update_workflow
        for name, target, kwargs in (
            ("create", "codemie.service.workflow_service.WorkflowService.create_workflow", {}),
            ("update", "codemie.service.workflow_service.WorkflowService.update_workflow", {}),
            ("get_workflow", "codemie.service.workflow_service.WorkflowService.get_workflow", {}),
            ("save_schema", "codemie.service.workflow_service.WorkflowService.save_workflow_schema", {}),
            ("draw", "codemie.workflows.workflow.WorkflowExecutor.validate_workflow_and_draw", {}),
            ("resources", "codemie.workflows.workflow.validate_workflow_config_resources_availability", {}),
            (
                "executor",
                "codemie.workflows.workflow.WorkflowExecutor.create_executor",
                {"return_value": MagicMock(_init_workflow=MagicMock(return_value=None))},
            ),
            ("can", "codemie.core.ability.Ability.can", {"return_value": True}),
            ("access", "codemie.rest_api.routers.workflow.project_access_check", {}),
            (
                "guardrails",
                "codemie.service.guardrail.guardrail_service.GuardrailService.get_entity_guardrail_assignments",
                {"return_value": None},
            ),
            ("models", f"{_SAVE}.llm_service.get_allowed_chat_models", {"return_value": []}),
            ("project", f"{_SAVE}._get_project_for_workflow", {"return_value": None}),
            ("marketplace", f"{_SAVE}._marketplace_service", {}),
            ("slot_assistant", f"{_RESOURCES}._load_referenced_assistant", {"return_value": MagicMock()}),
            (
                "slot_missing",
                f"{_RESOURCES}._safely_collect_missing_integrations",
                {"return_value": [MagicMock(tool="jira_tool", credential_type="Jira", toolkit="Jira")]},
            ),
            ("slot_open", f"{_RESOURCES}._is_slot_left_to_the_user", {"return_value": True}),
        ):
            if getattr(request, "param", False) and name in {"can", "access"}:
                continue
            setattr(stubs, name, stack.enter_context(patch(target, **kwargs)))
        stubs.create.return_value = _stored("", is_global=False)
        stubs.update.return_value = _stored("", is_global=False)
        stubs.get_workflow.return_value = _stored("", is_global=False)
        yield stubs


def _send_all(client: TestClient, yaml_text: str, **query) -> dict[str, object]:
    """The same config through the four entry points."""
    body = {"project": "demo", "yaml_config": yaml_text, **_NAME}
    new_body = {"project": "demo", "yaml_config": yaml_text}
    return {
        "create": client.post("/v1/workflows", json=body, headers=_HEADERS, params=query),
        "update": client.put(f"/v1/workflows/{_WORKFLOW_ID}", json=body, headers=_HEADERS, params=query),
        "dry_run": client.post(f"/v1/workflows/{_WORKFLOW_ID}/validate", json=body, headers=_HEADERS, params=query),
        "validate_new": client.post("/v1/workflows/validate", json=new_body, headers=_HEADERS, params=query),
    }


def _of_kind(warnings: list[dict], kind: str) -> list[dict]:
    return [w for w in warnings if w["kind"] == kind]


def test_non_global_success_gives_identical_contract_slot_and_coverage_on_every_entry(client, pipeline):
    responses = _send_all(client, _contract_yaml(declared_need=False))

    assert {name: r.status_code for name, r in responses.items()} == dict.fromkeys(responses, status.HTTP_200_OK)
    bodies = {name: r.json() for name, r in responses.items()}
    reference = bodies["create"]
    assert len(_of_kind(reference["warnings"], "contract_validation")) == 1
    assert len(_of_kind(reference["warnings"], "consumer_slot")) == 1
    assert reference["coverage"]["analysed"] is True
    assert not _of_kind(reference["warnings"], "tool_access_owner_integration")
    for name, body in bodies.items():
        assert _of_kind(body["warnings"], "contract_validation") == _of_kind(
            reference["warnings"], "contract_validation"
        ), name
        assert _of_kind(body["warnings"], "consumer_slot") == _of_kind(reference["warnings"], "consumer_slot"), name
        assert body["coverage"] == reference["coverage"], name
        assert body["warnings"] == reference["warnings"], name


def test_non_global_rejection_gives_identical_errors_warnings_and_coverage_on_every_entry(client, pipeline):
    responses = _send_all(client, _contract_yaml(declared_need=True), error_format="json")

    assert {name: r.status_code for name, r in responses.items()} == dict.fromkeys(
        responses, status.HTTP_400_BAD_REQUEST
    )
    details = {name: r.json()["error"]["details"] for name, r in responses.items()}
    reference = details["create"]
    assert reference["error_type"] == "contract_validation"
    assert reference["errors"]
    assert reference["warnings"]
    assert reference["coverage"]["analysed"] is True
    for name, entry in details.items():
        assert entry["errors"] == reference["errors"], name
        assert entry["warnings"] == reference["warnings"], name
        assert entry["coverage"] == reference["coverage"], name
    pipeline.create.assert_not_called()
    pipeline.update.assert_not_called()


@pytest.mark.parametrize("is_global", [True, False])
def test_owner_integration_warning_is_identical_on_every_entry_whatever_the_global_state(client, pipeline, is_global):
    yaml_text = _script_yaml()
    pipeline.get_workflow.return_value = _stored(yaml_text, is_global=is_global)
    pipeline.update.return_value = _stored(yaml_text, is_global=is_global)
    body = {"project": "demo", "yaml_config": yaml_text, **_NAME}

    responses = {
        "create": client.post("/v1/workflows", json=body, headers=_HEADERS),
        "update": client.put(f"/v1/workflows/{_WORKFLOW_ID}", json=body, headers=_HEADERS),
        "dry_run": client.post(f"/v1/workflows/{_WORKFLOW_ID}/validate", json=body, headers=_HEADERS),
        "validate_new": client.post("/v1/workflows/validate", json=body, headers=_HEADERS),
    }

    assert {name: r.status_code for name, r in responses.items()} == dict.fromkeys(responses, status.HTTP_200_OK)
    owner = {name: _of_kind(r.json()["warnings"], "tool_access_owner_integration") for name, r in responses.items()}
    assert owner["create"]
    assert owner["create"][0]["step_id"] == "run_script"
    assert owner["create"][0]["integration_alias"] == "my-jira"
    for name, warnings in owner.items():
        assert warnings == owner["create"], name


def test_marketplace_validation_still_follows_the_stored_global_state(client, pipeline):
    yaml_text = _script_yaml()
    body = {"project": "demo", "yaml_config": yaml_text, **_NAME}
    pipeline.get_workflow.return_value = _stored(yaml_text, is_global=True)
    pipeline.update.return_value = _stored(yaml_text, is_global=True)

    client.put(f"/v1/workflows/{_WORKFLOW_ID}", json=body, headers=_HEADERS)
    assert pipeline.marketplace.validate.call_count == 1

    client.post("/v1/workflows", json=body, headers=_HEADERS)
    client.post("/v1/workflows/validate", json=body, headers=_HEADERS)
    assert pipeline.marketplace.validate.call_count == 1


@pytest.mark.parametrize("declared_need", [False, True])
def test_a_config_that_validate_accepts_is_not_contract_rejected_on_create(client, pipeline, declared_need):
    yaml_text = _contract_yaml(declared_need=declared_need)
    new_body = {"project": "demo", "yaml_config": yaml_text}

    previewed = client.post("/v1/workflows/validate", json=new_body, headers=_HEADERS, params={"error_format": "json"})
    created = client.post(
        "/v1/workflows", json={**new_body, **_NAME}, headers=_HEADERS, params={"error_format": "json"}
    )

    assert previewed.status_code == created.status_code
    if previewed.status_code == status.HTTP_200_OK:
        assert pipeline.create.called
    else:
        assert created.json()["error"]["details"]["error_type"] == "contract_validation"
        assert previewed.json()["error"]["details"] == created.json()["error"]["details"]


_ROUTES = {
    "create": ("post", "/v1/workflows"),
    "update": ("put", f"/v1/workflows/{_WORKFLOW_ID}"),
    "dry_run": ("post", f"/v1/workflows/{_WORKFLOW_ID}/validate"),
    "validate_new": ("post", "/v1/workflows/validate"),
}
_GUARDRAILS = "codemie.service.guardrail.guardrail_service.GuardrailService"
_DB_SENTINELS = ("SQL_PRIVATE_16504", "BIND_PRIVATE_16504", "postgresql://PRIVATE_16504")


def _database_failure():
    return StatementError(
        "database failure", f"SELECT {_DB_SENTINELS[0]}", {"secret": _DB_SENTINELS[1]}, RuntimeError(_DB_SENTINELS[2])
    )


_DATABASE_STAGES = [
    (route, stage)
    for route in _ROUTES
    for stage in (
        ["project", "models", "validation"]
        + (["stored"] if route in {"update", "dry_run"} else [])
        + (["guardrail_validation"] if route in {"create", "update", "dry_run"} else [])
        + (["persistence", "guardrail_persistence", "enrichment"] if route in {"create", "update"} else [])
    )
]


@pytest.mark.parametrize("error_format", ["json", "string"])
@pytest.mark.parametrize("route,stage", _DATABASE_STAGES)
def test_database_errors_are_safe_at_every_route_stage(client, pipeline, error_format, route, stage):
    method, url = _ROUTES[route]
    body = {"project": "demo", "yaml_config": _contract_yaml(declared_need=False), **_NAME}
    failure = _database_failure()
    with ExitStack() as stack:
        sync = stack.enter_context(patch(f"{_GUARDRAILS}.sync_guardrail_assignments_for_entity"))
        if stage in {"project", "models"}:
            getattr(pipeline, stage).side_effect = failure
        elif stage == "stored":
            pipeline.get_workflow.side_effect = failure
        elif stage == "validation":
            stack.enter_context(patch(f"{_SAVE}.validate", side_effect=failure))
        elif stage == "persistence":
            getattr(pipeline, route).side_effect = failure
        elif stage == "enrichment":
            pipeline.guardrails.side_effect = failure
        else:

            def sync_stage(**kwargs):
                if bool(kwargs.get("dry_run")) == (stage == "guardrail_validation"):
                    raise failure

            sync.side_effect = sync_stage
        response = getattr(client, method)(url, json=body, headers=_HEADERS, params={"error_format": error_format})
    assert response.status_code == 503, response.text
    assert response.json()["error"]["message"] == "Workflow database is unavailable"
    assert all(sentinel not in response.text for sentinel in _DB_SENTINELS)
    if stage not in {"persistence", "guardrail_persistence", "enrichment"}:
        pipeline.create.assert_not_called()
        pipeline.update.assert_not_called()
        assert all(call.kwargs.get("dry_run") is True for call in sync.call_args_list)
    if stage == "persistence":
        assert all(call.kwargs.get("dry_run") is True for call in sync.call_args_list)
    pipeline.draw.assert_not_called()
    pipeline.save_schema.assert_not_called()


@pytest.mark.parametrize("route", ["update", "dry_run"])
def test_autonomous_update_refusal_precedes_guardrails_and_shared_validation(client, pipeline, route):
    method, url = _ROUTES[route]
    body = {"project": "demo", "yaml_config": _contract_yaml(declared_need=False), "mode": "Autonomous", **_NAME}
    with patch(f"{_SAVE}.validate") as validate, patch(f"{_GUARDRAILS}.sync_guardrail_assignments_for_entity") as sync:
        response = getattr(client, method)(url, json=body, headers=_HEADERS)
    assert response.status_code == 403, response.text
    sync.assert_not_called()
    validate.assert_not_called()
    pipeline.create.assert_not_called()
    pipeline.update.assert_not_called()


@pytest.mark.parametrize("code", [404, 403, 400])
def test_create_and_update_report_a_bad_guardrail_as_the_same_configuration_error(client, pipeline, code):
    from codemie.core.exceptions import ExtendedHTTPException

    body = {"project": "demo", "yaml_config": _contract_yaml(declared_need=False), **_NAME}
    failure = ExtendedHTTPException(code=code, message="Guardrail not found", details="no such guardrail")
    with patch(f"{_GUARDRAILS}.sync_guardrail_assignments_for_entity", side_effect=failure):
        responses = {
            route: getattr(client, _ROUTES[route][0])(_ROUTES[route][1], json=body, headers=_HEADERS)
            for route in ("create", "update")
        }
    for route, response in responses.items():
        assert response.status_code == 400, (route, response.text)
        assert response.json()["error"]["message"] == "Workflow Configuration error", route
        assert response.json()["error"]["details"] == "Guardrail not found", route
    pipeline.create.assert_not_called()
    pipeline.update.assert_not_called()


@pytest.mark.parametrize("yaml_mode", ["Legacy", "Autonomous", "NotAWorkflowMode"])
def test_yaml_mode_key_is_ignored_on_every_route(client, pipeline, yaml_mode):
    responses = _send_all(client, f"mode: {yaml_mode}\n" + _contract_yaml(declared_need=False))
    assert {name: r.status_code for name, r in responses.items()} == dict.fromkeys(responses, status.HTTP_200_OK)
    created, _user = pipeline.create.call_args.args
    _stored_workflow, updated, _user = pipeline.update.call_args.args
    assert created.mode == updated.mode == WorkflowMode.SEQUENTIAL


@pytest.mark.parametrize("declared_need", [False, True])
def test_real_model_normalization_preserves_findings_and_original_lines_on_all_routes(client, pipeline, declared_need):
    from codemie.configs.llm_config import LLMModel
    from codemie.service.workflow.save_validation import validate

    pipeline.models.return_value = [
        LLMModel(base_name="o1", deployment_name="o1-2024-12-17", label="o1", enabled=True, provider="azure_openai")
    ]
    data = yaml.safe_load(_contract_yaml(declared_need=declared_need))
    data["assistants"][0]["model"] = "o1"
    original = "# Original source\n\n" + yaml.safe_dump(data, sort_keys=False).replace(
        "states:", "# Before states\n\nstates:"
    )
    candidates = []

    def observe(candidate, *args, **kwargs):
        candidates.append(candidate)
        return validate(candidate, *args, **kwargs)

    with patch(f"{_SAVE}.validate", side_effect=observe):
        responses = _send_all(client, original, error_format="json")
    assert len(candidates) == 4
    for candidate in candidates:
        assert candidate.assistants[0].model == "o1-2024-12-17"
        assert "o1-2024-12-17" in candidate.yaml_config
        assert candidate._source_yaml_config == original
    bodies = [
        response.json()["error"]["details"] if declared_need else response.json() for response in responses.values()
    ]
    findings = []
    for body in bodies:
        entries = body.get("errors", []) + _of_kind(body["warnings"], "contract_validation")
        findings.append(entries)
        assert entries
        for entry in entries:
            assert entry["id"] and entry["config_section"] == "states"
            line = original.splitlines()[entry["config_line"] - 1]
            assert "task:" in line or "input_schema:" in line
    assert all(entries == findings[0] for entries in findings)
    assert all(body["coverage"] == bodies[0]["coverage"] for body in bodies)


def test_final_issue_fallback_keeps_all_four_rejections_structured(client, pipeline):
    with patch(
        "codemie.workflows.validation.contract_issues._issue_id", side_effect=ValueError("PRIVATE_FORMAT_FAILURE")
    ):
        responses = _send_all(client, _contract_yaml(declared_need=True), error_format="json")
    for response in responses.values():
        assert response.status_code == 400, response.text
        details = response.json()["error"]["details"]
        assert details["error_type"] == "contract_validation"
        assert details["errors"] and details["warnings"]
        assert all(entry["outcome"] == "ERROR" for entry in details["errors"])
        assert all(entry["outcome"] != "ERROR" for entry in details["warnings"])
        assert "PRIVATE_FORMAT_FAILURE" not in json.dumps(details)
    pipeline.create.assert_not_called()
    pipeline.update.assert_not_called()


@pytest.mark.parametrize("pipeline", [True], indirect=True)
@pytest.mark.parametrize("member", [False, True])
def test_new_validation_checks_real_project_membership_without_write_permission(client, pipeline, member):
    from codemie.core.ability import Ability, Action
    from codemie.core.models import UserEntity
    from codemie.service.workflow.save_validation import prepare

    with patch.object(config, "ENV", "test"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        caller = User(
            id="plain-member", project_names=["demo"] if member else [], admin_project_names=[], is_admin=False
        )
    assert not caller.is_admin_or_maintainer and not caller.admin_project_names
    stored = _stored("", is_global=False)
    stored.created_by = UserEntity(user_id="other-owner", name="Owner", username="other-owner")
    assert not Ability(caller).can(Action.WRITE, stored)
    pipeline.get_workflow.return_value = stored
    app.dependency_overrides[authenticate] = lambda: caller
    body = {"project": "demo", "yaml_config": _contract_yaml(declared_need=False)}
    with patch(f"{_SAVE}.prepare", wraps=prepare) as preparation:
        response = client.post("/v1/workflows/validate", json=body, headers=_HEADERS)
    assert response.status_code == (200 if member else 403), response.text
    if member:
        assert response.json()["coverage"]["analysed"] is True
        saved = client.post(f"/v1/workflows/{_WORKFLOW_ID}/validate", json={**body, **_NAME}, headers=_HEADERS)
        assert saved.status_code == 401, saved.text  # existing write-access refusal contract
    else:
        preparation.assert_not_called()
        pipeline.models.assert_not_called()
    pipeline.create.assert_not_called()
    pipeline.update.assert_not_called()


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize(
    "case,expected_source",
    [
        ("workflow", "workflow"),
        ("state", "state"),
        ("form_workflow", "workflow"),
        ("form_state", "state"),
        ("graph", "workflow"),
        ("coverage", None),
        ("settings", None),
    ],
)
def test_strict_source_survives_every_route_and_recoverable_formatter_failure(
    client, pipeline, case, expected_source, fallback
):
    data = yaml.safe_load(_contract_yaml(declared_need=False))
    if case in {"workflow", "form_workflow", "graph"}:
        data["save_validation"] = "strict"
    elif case in {"state", "form_state"}:
        data["states"][-1]["save_validation"] = "strict"
    elif case == "coverage":
        data["require_input_contracts"] = True
    if case.startswith("form_"):
        data["states"][0]["output_schema"] = '{"x":"example"}'
        # A consumer of the example-style producer creates the strict contract-form record.
        data["states"][1]["next"].pop("reset_keys_in_context_store")
    with ExitStack() as stack:
        if case == "graph":
            stack.enter_context(
                patch("codemie.workflows.contracts.linker.simulate_workflow", side_effect=RuntimeError("PRIVATE_GRAPH"))
            )
        if case == "settings":
            stack.enter_context(
                patch(
                    "codemie.workflows.contracts.linker.read_contract_settings",
                    side_effect=RuntimeError("PRIVATE_SETTINGS"),
                )
            )
        if fallback:
            stack.enter_context(
                patch(
                    "codemie.workflows.validation.contract_issues._detail", side_effect=RuntimeError("PRIVATE_FORMAT")
                )
            )
        responses = _send_all(client, yaml.safe_dump(data), error_format="json")
    payloads = []
    for response in responses.values():
        assert response.status_code == 400, response.text
        details = response.json()["error"]["details"]
        assert details["error_type"] == "contract_validation"
        payloads.append(details)
        sources = [(entry.get("meta") or {}).get("strict_source") for entry in details["errors"]]
        if expected_source:
            assert expected_source in sources
            if case.startswith("form_"):
                assert any(entry.get("meta") == {"strict_source": expected_source} for entry in details["errors"])
        else:
            assert all(value is None for value in sources)
        assert "PRIVATE_" not in response.text
        if case in {"graph", "settings"}:
            assert all("config_section" not in entry for entry in details["errors"])
    assert all(payload == payloads[0] for payload in payloads)
    pipeline.create.assert_not_called()
    pipeline.update.assert_not_called()


@pytest.mark.parametrize("error_format", ["json", "string"])
def test_rejected_routes_keep_nonempty_warnings_and_truncation(client, pipeline, error_format):
    data = yaml.safe_load(_contract_yaml(declared_need=True))
    data["states"][-1]["task"] = ' '.join('{{ missing_' + str(index) + ' }}' for index in range(110))
    responses = _send_all(client, yaml.safe_dump(data), error_format=error_format)
    payloads = []
    for response in responses.values():
        assert response.status_code == 400, response.text
        details = response.json()["error"]["details"]
        payloads.append(details)
        if error_format == "json":
            assert details["errors"]
            assert len(details["warnings"]) == 100
            assert details["warnings_truncated"] == 10
            assert details["coverage"]["analysed"] is True
        else:
            assert "warnings_truncated: 10" in details
            assert "Contracts checked on" in details
    assert all(payload == payloads[0] for payload in payloads)
    pipeline.create.assert_not_called()
    pipeline.update.assert_not_called()


@pytest.mark.parametrize("suffix", ["", "\x01\x7f\x85\u2028\u2029", "x" * 250])
def test_json_response_preserves_semantic_names_without_html_entity_encoding(client, pipeline, suffix):
    from codemie.workflows.contracts.policy import raw_name

    hostile = '<img src=x onerror=alert(1)>&ключ' + suffix
    data = yaml.safe_load(_contract_yaml(declared_need=True))
    data["save_validation"] = "strict"
    data["states"][-1]["input_schema"] = json.dumps(
        {"type": "object", "properties": {hostile: {"type": "string"}}, "required": [hostile]}
    )
    source = yaml.safe_dump(data)
    responses = _send_all(client, source, error_format="json")
    for response in responses.values():
        assert response.status_code == 400, response.text
        entries = response.json()["error"]["details"]["errors"]
        entry = next(entry for entry in entries if entry["path"] == raw_name(hostile))
        assert entry["config_path"] == raw_name(f"input_schema.properties.{hostile}")
        assert entry["config_section"] == "states"
        assert "input_schema:" in source.splitlines()[entry["config_line"] - 1]
        assert "&lt;" not in entry["path"]
        assert "<img" not in entry["message"]  # existing fixed-message display policy


_PROVIDER_STAGES = [
    (route, stage)
    for route in _ROUTES
    for stage in (
        ["project_repository", "model_provider", "assistant_lookup", "assistant_integrations", "advisory_lookup"]
        + (["guardrail_repository", "guardrail_permissions"] if route != "validate_new" else [])
        + (["persist_model"] if route in {"create", "update"} else [])
    )
]


@pytest.mark.parametrize("error_format", ["json", "string"])
@pytest.mark.parametrize("route,stage", _PROVIDER_STAGES)
def test_database_errors_from_provider_boundaries_reach_safe_route_mapper(client, pipeline, route, stage, error_format):
    method, url = _ROUTES[route]
    body = {"project": "demo", "yaml_config": _contract_yaml(declared_need=False), **_NAME}
    failure = _database_failure()
    repo = "codemie.service.guardrail.guardrail_service.GuardrailRepository"
    with ExitStack() as stack:
        assign = stack.enter_context(patch(f"{repo}.assign_guardrail_to_entity"))
        remove = stack.enter_context(patch(f"{repo}.remove_guardrails_assignments_by_ids"))
        if stage == "project_repository":
            pipeline.project.side_effect = pipeline.real_project
            stack.enter_context(patch(f"{_SAVE}.get_session"))
            stack.enter_context(patch(f"{_SAVE}.application_repository.get_by_name", side_effect=failure))
        elif stage == "model_provider":
            pipeline.models.side_effect = pipeline.real_models
            stack.enter_context(patch(f"{_SAVE}.llm_service.get_allowed_models", side_effect=failure))
        elif stage.startswith("assistant_") or stage == "advisory_lookup":
            pipeline.resources.side_effect = pipeline.real_resources
            pipeline.slot_assistant.side_effect = pipeline.real_load_assistant
            pipeline.slot_missing.side_effect = pipeline.real_missing_integrations
            assistant = MagicMock(toolkits=[], assistant_ids=[])
            lookup = stack.enter_context(patch(f"{_RESOURCES}.Assistant.get_by_ids", return_value=[assistant]))
            if stage == "assistant_lookup":
                # First query is availability; second query is the helper with a broad catch.
                lookup.side_effect = [[assistant], failure]
            elif stage == "advisory_lookup":
                # Availability and resource integrations pass; the later advisory read fails.
                lookup.side_effect = [[assistant], [assistant], failure]
            else:
                stack.enter_context(
                    patch(
                        f"{_RESOURCES}.AssistantIntegrationValidator.collect_missing_integrations", side_effect=failure
                    )
                )
        elif stage.startswith("guardrail_"):
            body["guardrail_assignments"] = [{"guardrail_id": "guardrail-1", "source": "input", "mode": "filtered"}]
            lookup = stack.enter_context(patch(f"{repo}.get_guardrail_assignments_for_entity", return_value=[]))
            if stage == "guardrail_repository":
                lookup.side_effect = failure
            else:
                stack.enter_context(
                    patch("codemie.service.guardrail.guardrail_service.Guardrail.find_by_id", side_effect=failure)
                )
        else:
            getattr(pipeline, route).side_effect = getattr(pipeline, f"real_{route}")
            stack.enter_context(patch("codemie.service.workflow_service.ensure_application_exists"))
            stack.enter_context(
                patch.object(WorkflowConfig, "save" if route == "create" else "update", side_effect=failure)
            )
        response = getattr(client, method)(url, json=body, headers=_HEADERS, params={"error_format": error_format})
    assert response.status_code == 503, response.text
    assert all(sentinel not in response.text for sentinel in _DB_SENTINELS)
    assign.assert_not_called()
    remove.assert_not_called()
    if stage != "persist_model":
        pipeline.create.assert_not_called()
        pipeline.update.assert_not_called()
    pipeline.draw.assert_not_called()
    pipeline.save_schema.assert_not_called()


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("error_format", ["json", "string"])
def test_sequential_yaml_mode_cannot_bypass_autonomous_refusal(client, pipeline, route, error_format):
    method, url = _ROUTES[route]
    body = {
        "project": "demo",
        "mode": "Autonomous",
        "yaml_config": "mode: Sequential\n" + _contract_yaml(declared_need=False),
        **_NAME,
    }
    with patch(f"{_GUARDRAILS}.sync_guardrail_assignments_for_entity") as guardrails:
        response = getattr(client, method)(url, json=body, headers=_HEADERS, params={"error_format": error_format})
    assert response.status_code == (410 if route in {"create", "validate_new"} else 403), response.text
    pipeline.project.assert_not_called()
    pipeline.models.assert_not_called()
    pipeline.resources.assert_not_called()
    pipeline.create.assert_not_called()
    pipeline.update.assert_not_called()
    guardrails.assert_not_called()


@pytest.mark.parametrize("route", _ROUTES)
def test_stored_assistant_integration_database_failure_is_a_503_on_workflow_routes(client, pipeline, route):
    """The assistant routes report integrations as valid on a database error; the workflow routes answer 503."""
    from codemie.rest_api.models.assistant import ToolDetails, ToolKitDetails

    method, url = _ROUTES[route]
    body = {"project": "demo", "yaml_config": _contract_yaml(declared_need=False), **_NAME}
    assistant = MagicMock(
        id="stored-assistant",
        toolkits=[ToolKitDetails(toolkit="Project Management", tools=[ToolDetails(name="generic_jira_tool")])],
        assistant_ids=[],
    )
    pipeline.resources.side_effect = pipeline.real_resources
    pipeline.slot_assistant.side_effect = pipeline.real_load_assistant
    pipeline.slot_missing.side_effect = pipeline.real_missing_integrations
    with (
        patch(f"{_RESOURCES}.Assistant.get_by_ids", return_value=[assistant]),
        patch(
            "codemie.service.tools.tool_metadata_service.SettingsService.get_config", side_effect=_database_failure()
        ) as settings,
        patch(f"{_GUARDRAILS}.sync_guardrail_assignments_for_entity"),
    ):
        response = getattr(client, method)(url, json=body, headers=_HEADERS)
    assert response.status_code == 503, response.text
    assert response.json()["error"]["message"] == "Workflow database is unavailable"
    assert all(sentinel not in response.text for sentinel in _DB_SENTINELS)
    settings.assert_called()
    pipeline.create.assert_not_called()
    pipeline.update.assert_not_called()


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("error_format", ["json", "string"])
def test_inline_assistant_settings_database_failure_reaches_route_without_persistence(
    client, pipeline, route, error_format
):
    method, url = _ROUTES[route]
    data = {
        "assistants": [{"id": "inline", "system_prompt": "work", "tools": [{"name": "git_tool"}]}],
        "states": [{"id": "run", "assistant_id": "inline", "next": {"state_id": "end"}}],
    }
    body = {"project": "demo", "yaml_config": yaml.safe_dump(data), **_NAME}
    pipeline.resources.side_effect = pipeline.real_resources
    with (
        patch(f"{_RESOURCES}.ToolsService.find_toolkit_for_tool", return_value={"toolkit": "Git"}),
        patch(
            "codemie.service.assistant.credential_validator.SettingsService.get_git_creds",
            side_effect=_database_failure(),
        ) as settings,
        patch(f"{_GUARDRAILS}.sync_guardrail_assignments_for_entity") as guardrails,
    ):
        response = getattr(client, method)(url, json=body, headers=_HEADERS, params={"error_format": error_format})
    assert response.status_code == 503, response.text
    assert response.json()["error"]["message"] == "Workflow database is unavailable"
    assert all(sentinel not in response.text for sentinel in _DB_SENTINELS)
    settings.assert_called_once()
    pipeline.create.assert_not_called()
    pipeline.update.assert_not_called()
    assert all(call.kwargs.get("dry_run") is True for call in guardrails.call_args_list)
    pipeline.draw.assert_not_called()
    pipeline.save_schema.assert_not_called()


# The R8 workflow of MR !4725 as submitted: `pick` declares no input_schema and extracts `items[0].x`, which the
# runtime reads as a key literally named `items[0]` and resolves to null.
_INDEXED_EXTRACT_YAML = """enable_summarization_node: false
custom_nodes:
- id: source
  custom_node_id: transform_node
  config:
    input_source: context_store
    mappings:
    - output_field: items
      type: constant
      value: [{x: first}, {x: second}]
- id: pick
  custom_node_id: transform_node
  config:
    input_source: context_store
    mappings:
    - output_field: picked
      type: extract
      source_path: items[0].x
states:
- id: source
  custom_node_id: source
  input_schema: '{"type":"object","properties":{},"required":[]}'
  output_schema: '{"type":"object","properties":{"items":{"type":"array"}},"required":["items"]}'
  next:
    state_id: pick
- id: pick
  custom_node_id: pick
  next:
    state_id: end
"""


def test_an_indexed_extract_path_warns_and_does_not_block_the_save_in_warn_mode(client, pipeline):
    responses = _send_all(client, _INDEXED_EXTRACT_YAML)

    assert {name: r.status_code for name, r in responses.items()} == dict.fromkeys(responses, status.HTTP_200_OK)
    for name, response in responses.items():
        contract = _of_kind(response.json()["warnings"], "contract_validation")
        assert len(contract) == 1, name
        assert contract[0]["message"].startswith("'items_0_' needed by state 'pick' is not guaranteed"), name
