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

"""Tool parameter schema resolver (EPMCDME-16503, T10, delivery slice C).

AC-13 / AC-14 / AC-15 outcome rows over a real resolver whose service lookups are replaced by fakes, the same rows
UNVERIFIED for a schema-less tool (AC-38), the resource-stage feed (`schema_sink`), the resolver lifecycle inside
`validate_workflow_with_report`, the provider read count, the mandatory set against the tool classes for the built-ins,
and the timing guards (one recorded, one asserted).
"""

import json
import logging
import time
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml
from pydantic import BaseModel, Field, create_model

from codemie.core.json_schema_utils import json_schema_to_model
from codemie.core.workflow_models import WorkflowConfig
from codemie.core.workflow_models.workflow_models import WorkflowState, WorkflowTool
from codemie.rest_api.models.assistant import MCPServerDetails
from codemie.service.provider import ProviderToolkitsFactory
from codemie.service.tools.discovery import ToolDiscoveryService
from codemie.workflows.contracts import protocols
from codemie.workflows.contracts.models import Outcome
from codemie.workflows.contracts.protocols import UNKNOWN
from codemie.workflows.contracts.simulate import simulate_workflow
from codemie.workflows.contracts.simulate_model import (
    REASON_MANDATORY_PARAMETER_NOT_BOUND,
    REASON_NOT_GUARANTEED,
    REASON_OK,
    REASON_REMOVED,
    REASON_TOOL_SCHEMA_UNKNOWN,
    TOOL_ARGS_PATH,
)
from codemie.workflows.validation import tool_schemas
from codemie.workflows.validation.resources import (
    _is_tool_available,
    _validate_tools_avaiability,
    validate_workflow_config_resources_availability,
)
from codemie.workflows.validation.tool_schemas import SaveTimeToolSchemaResolver
from codemie.workflows.workflow import WorkflowExecutor

RESOURCES = "codemie.workflows.validation.resources"


# --- helpers ------------------------------------------------------------------------------------------------------


def _model(fields: dict[str, bool], name: str = "ArgsFake") -> type[BaseModel]:
    """An args schema class: `fields` maps a parameter to whether it is mandatory (no default)."""
    definitions = {param: (str, ... if required else Field(default="x")) for param, required in fields.items()}
    return create_model(name, **definitions)


@pytest.fixture
def built_ins():
    """Replaces the two service lookups of the resolver; the extractor view flags every parameter mandatory."""
    catalog: dict[str, dict[str, bool]] = {}
    models: dict[str, list[type[BaseModel]]] = {}

    def find(name: str):
        fields = catalog.get(name)
        if fields is None:
            return None
        return SimpleNamespace(args_schema={param: {"type": str, "required": True} for param in fields})

    def add(name: str, fields: dict[str, bool], *, models_override: list[type[BaseModel]] | None = None) -> None:
        catalog[name] = fields
        models[name] = _model(fields) if models_override is None else models_override
        if not isinstance(models[name], list):
            models[name] = [models[name]]

    find_mock = MagicMock(side_effect=find)
    registry_mock = MagicMock(side_effect=lambda name: list(models.get(name, [])))
    with (
        patch.object(ToolDiscoveryService, "find_tool_by_name", find_mock),
        patch.object(tool_schemas, "_registered_args_models", registry_mock),
    ):
        yield SimpleNamespace(catalog=catalog, models=models, add=add, find=find_mock, registry=registry_mock)


def _ready() -> SaveTimeToolSchemaResolver:
    resolver = SaveTimeToolSchemaResolver()
    resolver.enable_lookup()
    return resolver


def _built_in_tool(name: str, tool_id: str = "tool-1", **extra) -> WorkflowTool:
    return WorkflowTool(id=tool_id, tool=name, **extra)


def _mcp_tool(tool_id: str = "mcp-1", name: str = "search", *, dynamic: bool = False) -> WorkflowTool:
    server = MCPServerDetails(name="srv", resolve_dynamic_values_in_arguments=dynamic)
    return WorkflowTool(id=tool_id, tool=name, mcp_server=server)


def _tool_state(state_id: str, tool_id: str = "tool-1", next_: dict | None = None, **overrides) -> WorkflowState:
    return WorkflowState(id=state_id, tool_id=tool_id, next=next_ or {}, **overrides)


def _producer(state_id: str, next_state: str, names: list[str], **next_options) -> WorkflowState:
    schema = {"type": "object", "properties": {name: {"type": "string"} for name in names}, "required": names}
    return WorkflowState(
        id=state_id,
        assistant_id="assistant-1",
        output_schema=json.dumps(schema),
        next={"state_id": next_state, **next_options},
    )


def _row(result, state_id: str, path: str, name: str | None = None) -> tuple[str, str]:
    matches = [
        item
        for item in result.findings
        if item.record.consumer_state_id == state_id
        and item.record.path == path
        and (name is None or (item.requirement is not None and item.requirement.name == name))
    ]
    assert len(matches) == 1, f"expected one finding at {state_id}:{path}:{name}, got {[m.record for m in matches]}"
    return matches[0].record.outcome.value, matches[0].reason


# --- the protocol and the UNKNOWN sentinel ---------------------------------------------------------------------------


def test_unknown_is_one_sentinel_and_not_a_collection_of_names():
    assert protocols.UNKNOWN is UNKNOWN
    assert not isinstance(UNKNOWN, set | frozenset | tuple | list)
    assert repr(UNKNOWN) == "UNKNOWN"


def test_the_resolver_satisfies_the_protocol():
    assert isinstance(SaveTimeToolSchemaResolver(), protocols.ToolSchemaResolver)


# --- built-in tools ---------------------------------------------------------------------------------------------------


def test_built_in_mandatory_set_comes_from_the_args_model_and_not_from_the_extractor_flags(built_ins):
    built_ins.add("scraper", {"url": True, "extract_images": False})

    assert _ready().mandatory_params(_built_in_tool("scraper")) == frozenset({"url"})


def test_built_in_with_only_optional_parameters_has_an_empty_mandatory_set(built_ins):
    built_ins.add("lister", {"path": False})

    assert _ready().mandatory_params(_built_in_tool("lister")) == frozenset()


def test_built_in_accepts_a_bare_tool_name(built_ins):
    built_ins.add("scraper", {"url": True})

    assert _ready().mandatory_params("scraper") == frozenset({"url"})


def test_extractor_names_may_be_a_subset_of_the_model_fields(built_ins):
    built_ins.add("scraper", {"url": True, "inherited": True})
    built_ins.catalog["scraper"] = {"url": True}

    assert _ready().mandatory_params(_built_in_tool("scraper")) == frozenset({"url", "inherited"})


def test_classes_sharing_a_tool_name_give_the_parameters_mandatory_in_all_of_them(built_ins):
    built_ins.add(
        "shared",
        {"a": True, "b": True},
        models_override=[_model({"a": True, "b": True}), _model({"a": True, "b": False})],
    )

    assert _ready().mandatory_params(_built_in_tool("shared")) == frozenset({"a"})


def _no_model(built_ins):
    built_ins.catalog["bare"] = {"a": True}


def _field_mismatch(built_ins):
    built_ins.add("bare", {"other": True})
    built_ins.catalog["bare"] = {"a": True}


def _empty_extractor_schema(built_ins):
    built_ins.add("bare", {"a": True})
    built_ins.catalog["bare"] = {}


def _extractor_raises(built_ins):
    built_ins.add("bare", {"a": True})
    built_ins.find.side_effect = RuntimeError("discovery exploded")


def _registry_raises(built_ins):
    built_ins.add("bare", {"a": True})
    built_ins.registry.side_effect = RuntimeError("registry exploded")


@pytest.mark.parametrize(
    "arrange",
    [
        pytest.param(lambda b: None, id="tool_unknown_to_discovery"),
        pytest.param(_empty_extractor_schema, id="empty_schema_as_for_a_provider_tool"),
        pytest.param(_no_model, id="no_registered_args_model"),
        pytest.param(_field_mismatch, id="extractor_found_another_class"),
        pytest.param(_extractor_raises, id="lookup_raises"),
        pytest.param(_registry_raises, id="registry_raises"),
    ],
)
def test_built_in_without_a_usable_schema_is_unknown(built_ins, arrange):
    arrange(built_ins)

    assert _ready().mandatory_params(_built_in_tool("bare")) is UNKNOWN


@pytest.mark.parametrize("name", ["_read_bootstrap_file", "", "   "])
def test_plugin_and_unnamed_tools_are_unknown_without_a_lookup(built_ins, name):
    built_ins.add(name or "x", {"a": True})

    assert _ready().mandatory_params(_built_in_tool(name)) is UNKNOWN
    built_ins.find.assert_not_called()


def test_before_the_lookup_is_enabled_every_tool_is_unknown_and_no_service_is_called(built_ins):
    built_ins.add("scraper", {"url": True})
    resolver = SaveTimeToolSchemaResolver()
    resolver.record_mcp_args_schema("mcp-1", _model({"q": True}))

    assert resolver.mandatory_params(_built_in_tool("scraper")) is UNKNOWN
    assert resolver.mandatory_params(_mcp_tool("mcp-1")) is UNKNOWN
    built_ins.find.assert_not_called()

    resolver.enable_lookup()

    assert resolver.mandatory_params(_built_in_tool("scraper")) == frozenset({"url"})
    assert resolver.mandatory_params(_mcp_tool("mcp-1")) == frozenset({"q"})


def test_answers_are_memoised_per_tool_name_failures_included(built_ins):
    built_ins.add("scraper", {"url": True})
    resolver = _ready()

    for _ in range(3):
        resolver.mandatory_params(_built_in_tool("scraper", tool_id="a"))
        resolver.mandatory_params(_built_in_tool("scraper", tool_id="b"))
    assert built_ins.find.call_count == 1

    built_ins.find.side_effect = RuntimeError("down")
    for _ in range(3):
        assert resolver.mandatory_params(_built_in_tool("other")) is UNKNOWN
    assert built_ins.find.call_count == 2


def test_resolvers_do_not_share_answers(built_ins):
    built_ins.add("scraper", {"url": True})
    first, second = _ready(), _ready()

    first.mandatory_params(_built_in_tool("scraper"))
    second.mandatory_params(_built_in_tool("scraper"))

    assert built_ins.find.call_count == 2


# --- static MCP tools, recorded by the resource stage -----------------------------------------------------------------


def test_recorded_mcp_schema_gives_the_mandatory_names(built_ins):
    resolver = _ready()
    resolver.record_mcp_args_schema("mcp-1", _model({"url": True, "limit": False}))

    assert resolver.mandatory_params(_mcp_tool("mcp-1")) == frozenset({"url"})
    built_ins.find.assert_not_called()


def test_recorded_mcp_schema_built_from_a_real_input_schema_gives_the_required_names(built_ins):
    """The model the MCP toolkit builds from `inputSchema` (not a hand-made one) decides the mandatory set."""
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
            "cursor": {"type": "string", "description": "optional, no default"},
        },
        "required": ["query"],
    }
    resolver = _ready()
    resolver.record_mcp_args_schema("mcp-1", json_schema_to_model(input_schema))

    assert resolver.mandatory_params(_mcp_tool("mcp-1")) == frozenset({"query"})


def test_recorded_mcp_schema_built_from_an_input_schema_without_required_has_no_mandatory_names(built_ins):
    input_schema = {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}}}
    resolver = _ready()
    resolver.record_mcp_args_schema("mcp-1", json_schema_to_model(input_schema))

    assert resolver.mandatory_params(_mcp_tool("mcp-1")) == frozenset()


@pytest.mark.parametrize(
    "schema",
    [None, create_model("EmptyArgs"), object(), "not a model"],
    ids=["none", "empty_model", "no_model_fields", "text"],
)
def test_recorded_mcp_schema_that_is_empty_or_missing_is_unknown(built_ins, schema):
    resolver = _ready()
    resolver.record_mcp_args_schema("mcp-1", schema)

    assert resolver.mandatory_params(_mcp_tool("mcp-1")) is UNKNOWN


def test_mcp_tool_the_stage_did_not_record_is_unknown_and_never_looked_up_as_a_built_in(built_ins):
    built_ins.add("search", {"q": True})

    assert _ready().mandatory_params(_mcp_tool("mcp-1", "search")) is UNKNOWN
    built_ins.find.assert_not_called()


def test_dynamic_mcp_server_is_unknown_even_when_something_was_recorded(built_ins):
    resolver = _ready()
    resolver.record_mcp_args_schema("mcp-1", _model({"q": True}))

    assert resolver.mandatory_params(_mcp_tool("mcp-1", dynamic=True)) is UNKNOWN


def test_mcp_schemas_are_keyed_by_the_workflow_tool_id_and_not_by_the_tool_name():
    resolver = _ready()
    resolver.record_mcp_args_schema("mcp-1", _model({"a": True}))
    resolver.record_mcp_args_schema("mcp-2", _model({"b": True}))

    assert resolver.mandatory_params(_mcp_tool("mcp-1", "search")) == frozenset({"a"})
    assert resolver.mandatory_params(_mcp_tool("mcp-2", "search")) == frozenset({"b"})


def test_only_names_are_stored_not_the_schema_class():
    resolver = _ready()
    model = _model({"a": True})
    resolver.record_mcp_args_schema("mcp-1", model)

    stored = resolver._mcp
    assert stored == {"mcp-1": frozenset({"a"})}
    assert all(isinstance(names, frozenset) for names in stored.values())


def test_two_mcp_tools_with_one_name_on_different_servers_are_not_merged_by_the_simulator():
    resolver = _ready()
    resolver.record_mcp_args_schema("mcp-1", _model({"a": True}))
    resolver.record_mcp_args_schema("mcp-2", _model({"b": True}))
    states = [
        _tool_state("s1", "mcp-1", {"state_id": "s2"}, tool_args={"a": "literal"}),
        _tool_state("s2", "mcp-2", tool_args={"b": "literal"}),
    ]
    tools = [_mcp_tool("mcp-1", "search"), _mcp_tool("mcp-2", "search")]

    result = simulate_workflow(states, tools=tools, resolver=resolver)

    assert not [item for item in result.findings if item.reason == REASON_MANDATORY_PARAMETER_NOT_BOUND]


# --- AC-13 / AC-14 / AC-15 / AC-38 rows through the real resolver ------------------------------------------------------


def _chain(tool_state: WorkflowState, produced: list[str] | None = None, **next_options) -> list[WorkflowState]:
    return [_producer("a", "t", produced or ["query"], **next_options), tool_state]


def test_ac14a_non_empty_bindings_omitting_a_mandatory_parameter_is_error(built_ins):
    built_ins.add("searcher", {"url": True, "query": True})
    states = [_tool_state("t", tool_args={"url": "literal"})]

    result = simulate_workflow(states, tools=[_built_in_tool("searcher")], resolver=_ready())

    assert _row(result, "t", "tool_args.query") == (Outcome.ERROR.value, REASON_MANDATORY_PARAMETER_NOT_BOUND)


def test_ac14b_empty_bindings_with_the_value_available_is_ok_and_covers_the_node(built_ins):
    built_ins.add("searcher", {"query": True, "limit": False})
    states = _chain(_tool_state("t"))

    result = simulate_workflow(states, tools=[_built_in_tool("searcher")], resolver=_ready())

    assert _row(result, "t", "tool_args.query", "query") == (Outcome.OK.value, REASON_OK)
    assert "t" not in result.uncovered_state_ids
    assert not [item for item in result.findings if item.record.path == "tool_args.limit"]


def test_ac14b_empty_bindings_with_the_value_removed_is_error(built_ins):
    built_ins.add("searcher", {"query": True})
    states = _chain(_tool_state("t"), reset_keys_in_context_store=["query"])

    result = simulate_workflow(states, tools=[_built_in_tool("searcher")], resolver=_ready())

    assert _row(result, "t", "tool_args.query", "query") == (Outcome.ERROR.value, REASON_REMOVED)


def test_ac14b_empty_bindings_with_nobody_producing_the_value_is_not_guaranteed(built_ins):
    built_ins.add("searcher", {"query": True})
    states = _chain(_tool_state("t"), ["other"], clear_context_store="keep_current")

    result = simulate_workflow(states, tools=[_built_in_tool("searcher")], resolver=_ready())

    assert _row(result, "t", "tool_args.query", "query") == (Outcome.ERROR.value, REASON_NOT_GUARANTEED)


def test_ac15_literal_binding_plus_a_same_named_context_field_is_a_warning(built_ins):
    built_ins.add("searcher", {"query": True})
    states = _chain(_tool_state("t", tool_args={"query": "literal"}))

    result = simulate_workflow(states, tools=[_built_in_tool("searcher")], resolver=_ready())

    finding = next(item for item in result.findings if item.record.path == "tool_args.query")
    assert finding.record.outcome is Outcome.WARNING
    assert "literal" not in finding.record.message


@pytest.mark.parametrize(
    "arrange",
    [
        pytest.param(lambda b: None, id="unknown_to_discovery"),
        pytest.param(lambda b: b.add("searcher", {}), id="empty_schema"),
        pytest.param(lambda b: setattr(b.find, "side_effect", RuntimeError("down")), id="lookup_raises"),
    ],
)
def test_ac38_the_same_rows_stay_unverified_for_a_tool_without_a_usable_schema(built_ins, arrange):
    arrange(built_ins)
    states = _chain(_tool_state("t"), reset_keys_in_context_store=["query"])

    result = simulate_workflow(states, tools=[_built_in_tool("searcher")], resolver=_ready())

    assert _row(result, "t", TOOL_ARGS_PATH) == (Outcome.UNVERIFIED.value, REASON_TOOL_SCHEMA_UNKNOWN)
    assert "t" in result.uncovered_state_ids
    assert not [item for item in result.findings if item.record.outcome is Outcome.ERROR]


def test_ac38_plugin_tool_is_unverified_and_uncovered(built_ins):
    states = [_tool_state("t")]

    result = simulate_workflow(states, tools=[_built_in_tool("_read_bootstrap_file")], resolver=_ready())

    assert _row(result, "t", TOOL_ARGS_PATH) == (Outcome.UNVERIFIED.value, REASON_TOOL_SCHEMA_UNKNOWN)
    assert result.uncovered_state_ids == ("t",)


def test_a_declared_input_schema_is_never_a_mandatory_parameter_source(built_ins):
    built_ins.add("searcher", {})
    declared = json.dumps({"type": "object", "properties": {"zzz": {"type": "string"}}, "required": ["zzz"]})
    states = [_tool_state("t", input_schema=declared)]

    result = simulate_workflow(states, tools=[_built_in_tool("searcher")], resolver=_ready())

    assert not [item for item in result.findings if item.reason == REASON_MANDATORY_PARAMETER_NOT_BOUND]
    assert _row(result, "t", TOOL_ARGS_PATH) == (Outcome.UNVERIFIED.value, REASON_TOOL_SCHEMA_UNKNOWN)


# --- the resource-stage feed -------------------------------------------------------------------------------------------


def _stage_config() -> MagicMock:
    config = MagicMock()
    config.id = "wf-1"
    config.project = "demo"
    config.states = []
    return config


@patch(f"{RESOURCES}.MCPToolkitService.get_mcp_server_tools")
def test_stage_records_the_args_schema_of_the_tool_it_selects_with_one_mcp_call(get_tools):
    schema = _model({"url": True})
    get_tools.return_value = [
        SimpleNamespace(name="other", args_schema=_model({})),
        SimpleNamespace(name="search", args_schema=schema),
    ]
    sink = MagicMock()

    available = _is_tool_available(_stage_config(), _mcp_tool("mcp-1", "search"), MagicMock(id="u1"), sink)

    assert available is True
    get_tools.assert_called_once()
    sink.assert_called_once_with("mcp-1", schema)


@patch(f"{RESOURCES}.MCPToolkitService.get_mcp_server_tools")
def test_stage_records_nothing_for_a_tool_it_does_not_find(get_tools):
    get_tools.return_value = []
    sink = MagicMock()

    available = _is_tool_available(_stage_config(), _mcp_tool("mcp-1", "search"), MagicMock(id="u1"), sink)

    assert available is False
    sink.assert_not_called()


@patch(f"{RESOURCES}.MCPToolkitService.get_mcp_server_tools")
def test_stage_records_nothing_for_a_dynamic_server_and_makes_no_call(get_tools):
    sink = MagicMock()

    available = _is_tool_available(_stage_config(), _mcp_tool("mcp-1", dynamic=True), MagicMock(id="u1"), sink)

    assert available is True
    get_tools.assert_not_called()
    sink.assert_not_called()


@patch(f"{RESOURCES}.ToolsService.find_toolkit_for_tool", return_value={"toolkit": "x"})
def test_stage_records_nothing_for_a_built_in_tool(find_toolkit):
    sink = MagicMock()

    assert _is_tool_available(_stage_config(), _built_in_tool("scraper"), MagicMock(id="u1"), sink) is True
    assert _is_tool_available(_stage_config(), "scraper", MagicMock(id="u1"), sink) is True
    sink.assert_not_called()


@patch(f"{RESOURCES}.MCPToolkitService.get_mcp_server_tools")
def test_a_failing_sink_never_changes_the_stage_answer(get_tools):
    get_tools.return_value = [SimpleNamespace(name="search", args_schema=_model({"url": True}))]
    sink = MagicMock(side_effect=RuntimeError("sink exploded"))

    assert _is_tool_available(_stage_config(), _mcp_tool("mcp-1", "search"), MagicMock(id="u1"), sink) is True


@patch(f"{RESOURCES}.MCPToolkitService.get_mcp_server_tools")
def test_stage_without_a_sink_behaves_as_before(get_tools):
    get_tools.return_value = [SimpleNamespace(name="search", args_schema=_model({"url": True}))]

    assert _is_tool_available(_stage_config(), _mcp_tool("mcp-1", "search"), MagicMock(id="u1")) is True


@patch(f"{RESOURCES}.MCPToolkitService.get_mcp_server_tools")
def test_tools_availability_feeds_each_mcp_tool_once(get_tools):
    schema = _model({"url": True})
    get_tools.return_value = [SimpleNamespace(name="search", args_schema=schema)]
    config = _stage_config()
    config.tools = [_mcp_tool("mcp-1", "search")]
    sink = MagicMock()

    unavailable = _validate_tools_avaiability(config, MagicMock(id="u1"), sink)

    assert unavailable == []
    get_tools.assert_called_once()
    sink.assert_called_once_with("mcp-1", schema)


def test_the_stage_entry_point_forwards_the_sink_to_the_tool_check():
    config = MagicMock()
    config.yaml_config = None
    config._source_yaml_config = None
    sink = MagicMock()
    with ExitStack() as stack:
        for name in (
            "_validate_assistants_availability",
            "_validate_referenced_assistants_integrations",
            "_validate_datasources_availability",
            "_validate_sub_workflow_availability",
            "validate_tool_access",
        ):
            stack.enter_context(patch(f"{RESOURCES}.{name}", return_value=[]))
        stack.enter_context(
            patch(f"{RESOURCES}._validate_tools_from_assistants_availability", return_value=([], [], []))
        )
        tools_check = stack.enter_context(patch(f"{RESOURCES}._validate_tools_avaiability", return_value=[]))

        validate_workflow_config_resources_availability(config, MagicMock(id="u1"), schema_sink=sink)

    assert tools_check.call_args.kwargs["schema_sink"] is sink


# --- the resolver inside validate_workflow_with_report -------------------------------------------------------------------


def _mcp_workflow(states: list[dict]) -> WorkflowConfig:
    config = {
        "assistants": [{"id": "a1", "system_prompt": "do it"}],
        "tools": [{"id": "mcp-1", "tool": "search", "mcp_server": {"name": "srv"}}],
        "states": states,
    }
    return WorkflowConfig(
        id="wf-1", name="wf", description="d", project="demo", mode="Sequential", yaml_config=yaml.safe_dump(config)
    )


def _stage_that_records(schema):
    """A stand-in resource stage: like the real one it hands the schema of the MCP tool it selected to the sink."""

    def stage(workflow_config, user, schema_sink=None):
        if schema_sink is not None:
            schema_sink("mcp-1", schema)

    return stage


def _validate(states: list[dict], stage, **kwargs):
    with patch("codemie.workflows.workflow.validate_workflow_config_resources_availability", stage):
        return WorkflowExecutor.validate_workflow_with_report(
            _mcp_workflow(states), MagicMock(id="u1"), link_contracts=True, **kwargs
        )


QUERY_PRODUCER = {
    "id": "a",
    "assistant_id": "a1",
    "output_schema": json.dumps({"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}),
    "next": {"state_id": "t"},
}


def test_the_recorded_mcp_schema_reaches_the_linker_ac14a_and_ac14b():
    stage = _stage_that_records(_model({"url": True, "query": True}))
    states = [QUERY_PRODUCER, {"id": "t", "tool_id": "mcp-1", "tool_args": {"url": "literal"}, "next": {}}]

    _, report = _validate(states, stage)

    errors = [r for r in report.records if r.outcome is Outcome.ERROR]
    assert [(r.consumer_state_id, r.path) for r in errors] == [("t", "tool_args.query")]


def test_a_stage_that_reports_no_schema_leaves_the_tool_unverified():
    stage = _stage_that_records(None)
    states = [QUERY_PRODUCER, {"id": "t", "tool_id": "mcp-1", "tool_args": {"url": "literal"}, "next": {}}]

    _, report = _validate(states, stage)

    assert not [r for r in report.records if r.outcome is Outcome.ERROR]
    assert [(r.consumer_state_id, r.outcome) for r in report.records if r.path == TOOL_ARGS_PATH] == [
        ("t", Outcome.UNVERIFIED)
    ]
    assert "t" in report.uncovered_state_ids


def test_the_resolver_answers_unknown_while_the_stage_is_still_running():
    seen = {}

    def stage(workflow_config, user, schema_sink=None):
        resolver = schema_sink.__self__
        resolver.record_mcp_args_schema("mcp-1", _model({"query": True}))
        seen["during"] = resolver.mandatory_params(_mcp_tool("mcp-1"))
        seen["resolver"] = resolver

    states = [QUERY_PRODUCER, {"id": "t", "tool_id": "mcp-1", "next": {}}]
    _validate(states, stage)

    assert seen["during"] is UNKNOWN
    assert seen["resolver"].mandatory_params(_mcp_tool("mcp-1")) == frozenset({"query"})


def test_a_stage_that_raises_never_enables_the_lookup_and_the_linker_does_not_run():
    seen = {}

    def stage(workflow_config, user, schema_sink=None):
        seen["resolver"] = schema_sink.__self__
        raise ValueError("resources are missing")

    with patch("codemie.workflows.workflow.link_workflow_contracts") as linker, pytest.raises(ValueError):
        _validate([{"id": "t", "tool_id": "mcp-1", "next": {}}], stage)

    linker.assert_not_called()
    assert seen["resolver"].mandatory_params(_mcp_tool("mcp-1")) is UNKNOWN


def test_with_the_flag_off_no_resolver_is_created_and_the_stage_gets_no_sink():
    stage = MagicMock()
    with patch("codemie.workflows.workflow.validate_workflow_config_resources_availability", stage):
        WorkflowExecutor.validate_workflow_with_report(
            _mcp_workflow([{"id": "t", "tool_id": "mcp-1", "next": {}}]), MagicMock(id="u1")
        )

    assert stage.call_args.kwargs.get("schema_sink") is None


def test_the_linker_receives_an_enabled_resolver_when_the_flag_is_on():
    seen = {}

    def link(config, resolver=None, document=None):
        seen["resolver"] = resolver
        from codemie.workflows.contracts.models import ContractAnalysisStatus, ContractLinkReport

        return ContractLinkReport(status=ContractAnalysisStatus.ANALYSED)

    with patch("codemie.workflows.workflow.link_workflow_contracts", link):
        _validate([{"id": "t", "tool_id": "mcp-1", "next": {}}], _stage_that_records(_model({"q": True})))

    assert isinstance(seen["resolver"], SaveTimeToolSchemaResolver)
    assert seen["resolver"].mandatory_params(_mcp_tool("mcp-1")) == frozenset({"q"})


# --- provider reads ----------------------------------------------------------------------------------------------------


REAL_BUILT_INS = ["code_executor", "google_search_tool_json", "execute_workspace_script"]


def _provider_reads():
    return patch.object(ProviderToolkitsFactory, "get_toolkits", return_value=[])


def test_provider_toolkits_are_read_once_for_many_states_with_the_same_built_in_tool():
    states = [_tool_state(f"s{i}", next_={"state_id": f"s{i + 1}"} if i < 5 else {}) for i in range(6)]
    with _provider_reads() as reads:
        result = simulate_workflow(states, tools=[_built_in_tool("code_executor")], resolver=_ready())

    assert reads.call_count == 1
    assert result.lookups == 1


def test_provider_toolkits_are_read_once_per_distinct_built_in_name():
    tools = [_built_in_tool(name, tool_id=f"tool-{i}") for i, name in enumerate(REAL_BUILT_INS)]
    states = [_tool_state(f"s{i}", f"tool-{i % 3}", {"state_id": f"s{i + 1}"} if i < 8 else {}) for i in range(9)]
    with _provider_reads() as reads:
        simulate_workflow(states, tools=tools, resolver=_ready())

    assert reads.call_count == len(REAL_BUILT_INS)


# --- the mandatory set against the tool classes ------------------------------------------------------------------------


def _independent_mandatory(name: str) -> frozenset[str] | None:
    """What the tool classes themselves say: the mandatory fields of the `args_schema` model every class carries."""
    from codemie_tools.base import tool_registry

    tool_registry.ensure_loaded()
    record = tool_registry.lookup(name)
    if record is None:
        return None
    sets = []
    for cls in record.classes:
        model = cls.model_fields["args_schema"].default if "args_schema" in cls.model_fields else None
        if model is None or not hasattr(model, "model_fields") or not model.model_fields:
            continue
        sets.append(frozenset(param for param, field in model.model_fields.items() if field.is_required()))
    if not sets:
        return None
    return frozenset.intersection(*sets)


def _corpus_built_in_names() -> list[str]:
    """Built-in tool names used by the template corpus (not MCP, not plugin)."""
    from pathlib import Path

    from codemie.core.workflow_models.workflow_config import WorkflowConfigTemplate

    templates = Path(__file__).resolve().parents[4] / "config" / "templates" / "workflow"
    names: set[str] = set()
    for path in sorted(templates.glob("*_template.yaml")):
        template = WorkflowConfigTemplate.from_yaml(path.read_text(encoding="utf-8"))
        template.parse_execution_config()
        for tool in template.tools or []:
            if tool.tool and not tool.tool.startswith("_") and not tool.mcp_server:
                names.add(tool.tool)
    return sorted(names)


PINNED_BUILT_INS = {
    "web_scrapper": frozenset({"url"}),
    "code_executor": frozenset({"code"}),
    "execute_workspace_script": frozenset({"script_path"}),
    "read_workspace_file": frozenset({"file_path"}),
    "write_workspace_file": frozenset({"file_path", "content"}),
    "list_workspace_files": frozenset(),
}


@pytest.mark.parametrize("name", sorted(set(PINNED_BUILT_INS) | set(_corpus_built_in_names())))
def test_resolver_set_matches_the_tool_class_for_corpus_and_pinned_built_ins(name):
    with _provider_reads():
        answer = _ready().mandatory_params(_built_in_tool(name))

    expected = _independent_mandatory(name)
    if name in PINNED_BUILT_INS:
        assert answer == PINNED_BUILT_INS[name]
    if expected is not None and expected:
        # A tool class that names mandatory parameters must be resolved, not silently degraded to UNKNOWN.
        assert answer == expected
    assert answer is UNKNOWN or answer == expected


def test_the_resolver_never_claims_a_parameter_mandatory_that_the_tool_class_does_not():
    from codemie_tools.base import tool_registry

    tool_registry.ensure_loaded()
    names = sorted(tool_registry._CLASSES_BY_NAME)
    resolver = _ready()
    wrong = []
    resolved = 0
    with _provider_reads():
        for name in names:
            answer = resolver.mandatory_params(_built_in_tool(name))
            expected = _independent_mandatory(name)
            if answer is UNKNOWN:
                continue
            resolved += 1
            if expected is None or answer != expected:
                wrong.append(name)
    assert wrong == []
    # UNKNOWN is always allowed, so guard against the whole registry degrading to it and passing vacuously.
    assert resolved > len(names) // 2


# --- timing ------------------------------------------------------------------------------------------------------------

# The figures are recorded, not asserted. A warm run only times memo hits on one resolver, and a real save builds a
# new resolver, so a warm bound would say nothing about the cost a save pays (`LOOKUP_BUDGET` rests on the cold cost).


def test_twenty_built_in_lookups_cold_and_warm_are_recorded(record_property):
    from codemie_tools.base import tool_registry

    tool_registry.ensure_loaded()
    names = []
    with _provider_reads():
        probe = _ready()
        for name in sorted(tool_registry._CLASSES_BY_NAME):
            if probe.mandatory_params(_built_in_tool(name)) is not UNKNOWN:
                names.append(name)
            if len(names) == 20:
                break
    assert len(names) == 20

    with _provider_reads():
        started = time.perf_counter()
        cold = _ready()
        for name in names:
            cold.mandatory_params(_built_in_tool(name))
        elapsed = time.perf_counter() - started

        started = time.perf_counter()
        for name in names:
            cold.mandatory_params(_built_in_tool(name))
        warm = time.perf_counter() - started

    record_property("twenty_built_in_lookups_seconds", round(elapsed, 4))
    record_property("twenty_built_in_lookups_warm_seconds", round(warm, 4))
    logging.getLogger(__name__).info(
        "20 distinct built-in lookups (provider reads patched out): cold %.4fs, warm %.4fs", elapsed, warm
    )


def test_two_hundred_state_chain_with_a_real_resolver_stays_under_ten_seconds(built_ins):
    for index in range(20):
        built_ins.add(f"tool-{index}", {"p": True})
    tools = [_built_in_tool(f"tool-{index}", tool_id=f"id-{index}") for index in range(20)]
    states = [_producer("s0", "s1", ["p"])]
    for index in range(1, 200):
        states.append(
            _tool_state(f"s{index}", f"id-{index % 20}", {"state_id": f"s{index + 1}"} if index < 199 else {})
        )

    started = time.perf_counter()
    result = simulate_workflow(states, tools=tools, resolver=_ready())
    elapsed = time.perf_counter() - started

    assert result.cap_reached is False
    assert result.reachable_total == 200
    assert elapsed < 10.0
