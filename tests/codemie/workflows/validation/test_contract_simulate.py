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

"""Availability simulator (EPMCDME-16503, T6).

One case set per AC: AC-1..AC-12 availability rules, AC-37 transition identifiers, AC-13/AC-15 tool bindings with
`resolver=None` (so a tool without a usable args schema stays UNVERIFIED, AC-38), plus the structural rows the plan
asks for: a reset later in the cycle body than the consumer, nested cycles sharing a header, an iteration branch that
ends without `finish_iteration`, a 1,000-state chain, a malformed contract degrading only its own state, the work cap
and the lookup budget.
"""

import json
import time
from unittest.mock import patch

import pytest

from codemie.rest_api.models.assistant import MCPServerDetails
from codemie.core.workflow_models.workflow_models import (
    CustomWorkflowNode,
    WorkflowState,
    WorkflowTool,
)
from codemie.workflows.contracts.derive import (
    LABEL_RENDERS_EMPTY,
    ORIGIN_PLACEHOLDER,
    REASON_ITER_KEY_NOT_ARRAY,
    REASON_NO_USABLE_OUTPUT_CONTRACT,
    REASON_NOT_IN_OUTPUT_CONTRACT,
)
from codemie.workflows.contracts.abstract_context import (
    INITIAL_INPUT_SOURCE,
    Mechanism,
)
from codemie.workflows.contracts.effects import NodeKind
from codemie.workflows.contracts.models import Outcome, RecordKind
from codemie.workflows.contracts.reader import ContractForm
from codemie.workflows.contracts import simulate as simulate_module
from codemie.workflows.contracts.simulate import (
    LOOKUP_BUDGET,
    WORK_CAP,
    simulate_workflow,
)
from codemie.workflows.contracts.simulate_graph import out_edges
from codemie.workflows.contracts.simulate_model import (
    CONTEXT_PATH_PREFIX,
    ITERATION_ITEM_SOURCE,
    REASON_CONTEXT_VALUE_WINS,
    REASON_DERIVATION_FAILED,
    REASON_ITERATION_ITEM_MAY_SUPPLY,
    REASON_LOOKUP_BUDGET_EXHAUSTED,
    REASON_MANDATORY_PARAMETER_NOT_BOUND,
    REASON_MULTIPLE_PARALLEL_WRITERS,
    REASON_NOT_GUARANTEED,
    REASON_NOT_ON_EVERY_ALTERNATIVE,
    REASON_OK,
    REASON_OPTIONAL_SOURCE,
    REASON_POSSIBLY_PRESENT,
    REASON_POSSIBLY_REMOVED,
    REASON_PRODUCED_ONLY_IN_CYCLE,
    REASON_PRODUCER_UNKNOWN_SOURCE,
    REASON_REMOVAL_RACES_WRITE,
    REASON_REMOVED,
    REASON_STATE_NOT_ANALYSABLE,
    REASON_TOOL_INPUT_KEY_NAMESPACE,
    REASON_TOOL_SCHEMA_UNKNOWN,
    REASON_TRANSITION_TARGET_MISSING,
    REASON_TYPE_MISMATCH,
    REASON_TYPE_UNVERIFIED,
    REASON_UNKNOWN_SOURCE_ON_PATH,
    REASON_WORK_CAP_REACHED,
    REASON_WRITE_SUPPRESSED,
    TOOL_ARGS_PATH,
    EdgeKind,
)
from codemie.workflows.contracts.simulate_tools import normalise_parameters

# --- fixtures and helpers -----------------------------------------------------------------------


def _schema(types: dict[str, str], required: list[str] | None = None) -> str:
    """A schema-form contract; every property is required unless `required` says otherwise."""
    return json.dumps(
        {
            "type": "object",
            "properties": {name: {"type": kind} for name, kind in types.items()},
            "required": list(types) if required is None else list(required),
        }
    )


DOC_SCHEMA = _schema({"doc": "string"})
DOC_OPTIONAL_SCHEMA = _schema({"doc": "string"}, required=[])
DOC_ARRAY_SCHEMA = _schema({"doc": "array"})
SEED_SCHEMA = _schema({"seed": "string"})
EXAMPLE_DOC = '{"doc": "the document text"}'


def _state(state_id: str, next_: dict | None = None, **overrides) -> WorkflowState:
    data: dict = {"id": state_id, "assistant_id": "assistant-1", "next": next_ or {}}
    data.update(overrides)
    return WorkflowState(**data)


def _needs(state_id: str, types: dict[str, str], next_: dict | None = None, **overrides) -> WorkflowState:
    """A consumer state declaring an input contract over `types`."""
    return _state(state_id, next_, input_schema=_schema(types), **overrides)


def _entry(next_id: str, input_schema: str | None = None) -> WorkflowState:
    """An entry state with a known empty output. `input_schema` is the entry's declared input contract; the default is
    a declared empty one, so the initial input is a known source that supplies nothing."""
    empty = _schema({})
    return _state(
        "entry",
        {"state_id": next_id},
        input_schema=empty if input_schema is None else input_schema,
        output_schema=empty,
    )


def _tool_state(state_id: str, next_: dict | None = None, **overrides) -> WorkflowState:
    data: dict = {"id": state_id, "tool_id": "tool-1", "next": next_ or {}}
    data.update(overrides)
    return WorkflowState(**data)


def _transform(config: dict, node_id: str = "transform-1") -> CustomWorkflowNode:
    return CustomWorkflowNode(id=node_id, custom_node_id="transform_node", config=config)


def _findings(result, state_id: str, path: str, name: str | None = None) -> list:
    return [
        item
        for item in result.findings
        if item.record.consumer_state_id == state_id
        and item.record.path == path
        and (name is None or (item.requirement is not None and item.requirement.name == name))
    ]


def _row(result, state_id: str, path: str, name: str | None = None) -> tuple[str, str]:
    """The single `(outcome, reason)` of the finding at `state_id` / `path` (optionally for requirement `name`)."""
    matches = _findings(result, state_id, path, name)
    assert len(matches) == 1, f"expected one finding at {state_id}:{path}:{name}, got {[m.record for m in matches]}"
    return matches[0].record.outcome.value, matches[0].reason


def _paths(result, state_id: str) -> list[str]:
    return sorted(item.record.path for item in result.findings if item.record.consumer_state_id == state_id)


def _analysis(result, state_id: str):
    return next(item for item in result.states if item.state_id == state_id)


# --- AC-1: no contracts anywhere ----------------------------------------------------------------


def test_ac1_no_contracts_anywhere_gives_unverified_and_no_error():
    states = [
        _state("a", {"state_id": "b"}),
        _state("b", task="{{ doc }}", resolve_dynamic_values_in_prompt=True),
    ]

    result = simulate_workflow(states)

    assert _row(result, "b", "task", "doc") == (Outcome.UNVERIFIED.value, REASON_UNKNOWN_SOURCE_ON_PATH)
    assert [item.record.outcome for item in result.findings].count(Outcome.ERROR) == 0


def test_ac1_fully_satisfied_workflow_has_no_issue_and_full_coverage():
    states = [
        _state("a", {"state_id": "b"}, output_schema=DOC_SCHEMA, input_schema=_schema({})),
        _needs("b", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    assert all(item.record.outcome is Outcome.OK for item in result.findings), result.records
    assert result.uncovered_state_ids == ()
    assert result.validated_count == 2
    assert result.reachable_total == 2


def test_ac1_uncovered_reachable_node_is_listed_not_blocked():
    states = [_state("a", {"state_id": "b"}), _state("b")]

    result = simulate_workflow(states)

    assert result.uncovered_state_ids == ("a", "b")
    assert result.validated_count == 0
    assert result.findings == ()


# --- AC-2: known producer, intermediate unknown source ------------------------------------------


def test_ac2_known_producer_gives_ok_naming_the_producer():
    states = [_state("a", {"state_id": "b"}, output_schema=DOC_SCHEMA), _needs("b", {"doc": "string"})]

    result = simulate_workflow(states)

    finding = _findings(result, "b", "input_schema.doc")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.OK, REASON_OK)
    assert finding.record.provenance.producers == ("a",)
    assert finding.record.kind is RecordKind.DECLARED


def test_ac2_intermediate_node_without_contract_makes_the_type_unverified():
    states = [
        _state("a", {"state_id": "middle"}, output_schema=DOC_SCHEMA),
        _state("middle", {"state_id": "b"}),
        _needs("b", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    finding = _findings(result, "b", "input_schema.doc")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.UNVERIFIED, REASON_TYPE_UNVERIFIED)
    assert finding.record.provenance.producers == ("a",)
    assert finding.record.provenance.overwritten_by == "middle"


# --- AC-3: nothing produces the field, every source known ---------------------------------------


def test_ac3_no_source_produces_the_field_and_all_sources_known_is_error():
    states = [
        _state("a", {"state_id": "b", "clear_context_store": "keep_current"}, output_schema=DOC_SCHEMA),
        _needs("b", {"other": "string"}),
    ]

    result = simulate_workflow(states)

    finding = _findings(result, "b", "input_schema.other")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.ERROR, REASON_NOT_GUARANTEED)
    assert "a" in finding.record.provenance.sources_checked


def test_ac3_declared_initial_input_makes_a_missing_field_an_error():
    states = [_entry("a"), _needs("a", {"doc": "string"})]

    result = simulate_workflow(states)

    finding = _findings(result, "a", "input_schema.doc")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.ERROR, REASON_NOT_GUARANTEED)
    assert INITIAL_INPUT_SOURCE in finding.record.provenance.sources_checked


def test_ac3_the_parameter_alone_still_makes_every_source_known_without_a_declaration():
    states = [_state("entry", {"state_id": "a"}, output_schema=_schema({})), _needs("a", {"doc": "string"})]

    result = simulate_workflow(states, unknown_initial_input=False)

    assert _row(result, "a", "input_schema.doc") == (Outcome.ERROR.value, REASON_NOT_GUARANTEED)


def test_ac2_a_required_entry_field_is_ok_for_the_entry_and_a_downstream_node_naming_the_entry_input():
    states = [_entry("a", _schema({"doc": "string"})), _needs("a", {"doc": "string"})]

    result = simulate_workflow(states)

    for state_id in ("entry", "a"):
        finding = _findings(result, state_id, "input_schema.doc")[0]
        assert (finding.record.outcome, finding.reason) == (Outcome.OK, REASON_OK)
        assert finding.record.provenance.producers == (INITIAL_INPUT_SOURCE,)


def test_ac2_an_optional_entry_field_is_a_warning_for_a_downstream_requirement():
    states = [_entry("a", _schema({"doc": "string"}, required=[])), _needs("a", {"doc": "string"})]

    result = simulate_workflow(states)

    assert _row(result, "a", "input_schema.doc") == (Outcome.WARNING.value, REASON_OPTIONAL_SOURCE)


def test_ac2_the_entry_field_type_seeds_the_context():
    states = [_entry("a", _schema({"doc": "integer"})), _needs("a", {"doc": "string"})]

    result = simulate_workflow(states)

    assert _row(result, "a", "input_schema.doc") == (Outcome.WARNING.value, REASON_TYPE_MISMATCH)


@pytest.mark.parametrize("unknown_initial_input", [True, False])
def test_ac2_a_declared_entry_contract_wins_over_the_unknown_initial_input_parameter(unknown_initial_input):
    states = [_entry("a", _schema({"doc": "string"})), _needs("a", {"doc": "string"})]

    result = simulate_workflow(states, unknown_initial_input=unknown_initial_input)

    assert _row(result, "a", "input_schema.doc") == (Outcome.OK.value, REASON_OK)


# --- AC-4: produced then removed, or never written -----------------------------------------------


def test_ac4_reset_key_is_error_naming_the_node_and_the_mechanism():
    states = [
        _state("a", {"state_id": "b", "clear_context_store": "keep_current"}, output_schema=DOC_SCHEMA),
        _state("b", {"state_id": "c", "reset_keys_in_context_store": ["doc"]}),
        _needs("c", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    finding = _findings(result, "c", "input_schema.doc")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.ERROR, REASON_REMOVED)
    assert finding.record.provenance.removed_by == "b"
    assert finding.record.provenance.mechanism == Mechanism.RESET_KEYS.value


def test_ac4_reset_key_stays_error_with_an_unknown_initial_input():
    states = [
        _state("a", {"state_id": "b"}, output_schema=DOC_SCHEMA),
        _state("b", {"state_id": "c", "reset_keys_in_context_store": ["doc"]}),
        _needs("c", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    assert _row(result, "c", "input_schema.doc") == (Outcome.ERROR.value, REASON_REMOVED)


def test_ac4_output_writing_off_is_error_naming_the_mechanism_when_sources_are_known():
    states = [
        _state("a", {"state_id": "b", "clear_context_store": "keep_current"}, output_schema=SEED_SCHEMA),
        _state("b", {"state_id": "c", "store_in_context": False}, output_schema=DOC_SCHEMA),
        _needs("c", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    finding = _findings(result, "c", "input_schema.doc")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.ERROR, REASON_WRITE_SUPPRESSED)
    assert finding.record.provenance.mechanism == Mechanism.OUTPUT_WRITING_OFF.value


def test_ac4_output_writing_off_is_unverified_with_an_unknown_source_on_the_path():
    states = [
        _state("a", {"state_id": "b"}),
        _state("b", {"state_id": "c", "store_in_context": False}, output_schema=DOC_SCHEMA),
        _needs("c", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    assert _row(result, "c", "input_schema.doc") == (Outcome.UNVERIFIED.value, REASON_UNKNOWN_SOURCE_ON_PATH)


def test_ac4_write_suppression_is_blamed_only_on_an_ancestor_of_the_consumer():
    states = [
        _state(
            "a",
            {
                "condition": {"expression": "True", "then": "left", "otherwise": "right"},
                "clear_context_store": "keep_current",
            },
            output_schema=SEED_SCHEMA,
        ),
        _state("left", {"store_in_context": False}, output_schema=DOC_SCHEMA),
        _needs("right", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    finding = _findings(result, "right", "input_schema.doc")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.ERROR, REASON_NOT_GUARANTEED)
    assert finding.record.provenance.removed_by is None
    assert finding.record.provenance.mechanism is None


def test_ac4_write_suppression_on_the_consumers_own_path_is_still_blamed_after_a_join():
    states = [
        _state(
            "a",
            {
                "condition": {"expression": "True", "then": "left", "otherwise": "right"},
                "clear_context_store": "keep_current",
            },
            output_schema=SEED_SCHEMA,
        ),
        _state("left", {"state_id": "join", "store_in_context": False}, output_schema=DOC_SCHEMA),
        _state("right", {"state_id": "join"}, output_schema=SEED_SCHEMA),
        _needs("join", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    finding = _findings(result, "join", "input_schema.doc")[0]
    assert finding.record.outcome is Outcome.ERROR
    assert finding.record.provenance.removed_by == "left"


# --- AC-5: an unknown source never gives an ERROR ------------------------------------------------


def test_ac5_undeclared_initial_input_is_an_unknown_source():
    states = [_entry("a", None).model_copy(update={"input_schema": None}), _needs("a", {"doc": "string"})]

    result = simulate_workflow(states)

    finding = _findings(result, "a", "input_schema.doc")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.UNVERIFIED, REASON_UNKNOWN_SOURCE_ON_PATH)
    assert INITIAL_INPUT_SOURCE in finding.record.provenance.sources_checked


def test_ac5_an_unusable_entry_contract_leaves_the_initial_input_unknown():
    states = [_entry("a", '{"doc": "Title"}'), _needs("a", {"doc": "string"})]

    result = simulate_workflow(states)

    assert _row(result, "a", "input_schema.doc") == (Outcome.UNVERIFIED.value, REASON_UNKNOWN_SOURCE_ON_PATH)


def test_ac5_unknown_source_after_a_reset_resurrects_the_removal():
    states = [
        _state("a", {"state_id": "b", "clear_context_store": "keep_current"}, output_schema=DOC_SCHEMA),
        _state("b", {"state_id": "c", "reset_keys_in_context_store": ["doc"]}, output_schema=SEED_SCHEMA),
        _state("c", {"state_id": "d"}),
        _needs("d", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    assert _row(result, "d", "input_schema.doc")[0] == Outcome.UNVERIFIED.value


# --- AC-6: alternatives and parallel branches ----------------------------------------------------


def test_ac6_alternative_missing_the_field_is_a_warning_naming_the_branch():
    states = [
        _state("a", {"condition": {"expression": "True", "then": "left", "otherwise": "right"}}),
        _state("left", {"state_id": "join", "clear_context_store": "keep_current"}, output_schema=DOC_SCHEMA),
        _state("right", {"state_id": "join", "clear_context_store": "keep_current"}, output_schema=SEED_SCHEMA),
        _needs("join", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    finding = _findings(result, "join", "input_schema.doc")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.WARNING, REASON_NOT_ON_EVERY_ALTERNATIVE)
    assert finding.record.provenance.sources_checked == ("right",)


def test_ac6_every_alternative_providing_the_field_is_ok():
    states = [
        _state("a", {"condition": {"expression": "True", "then": "left", "otherwise": "right"}}),
        _state("left", {"state_id": "join", "clear_context_store": "keep_current"}, output_schema=DOC_SCHEMA),
        _state("right", {"state_id": "join", "clear_context_store": "keep_current"}, output_schema=DOC_SCHEMA),
        _needs("join", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    assert _row(result, "join", "input_schema.doc") == (Outcome.OK.value, REASON_OK)


def test_ac6_unknown_source_on_one_alternative_keeps_the_merge_unverified():
    states = [
        _state("a", {"condition": {"expression": "True", "then": "left", "otherwise": "right"}}),
        _state("left", {"state_id": "join", "clear_context_store": "keep_current"}, output_schema=SEED_SCHEMA),
        _state("right", {"state_id": "join", "clear_context_store": "keep_current"}),
        _needs("join", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    assert _row(result, "join", "input_schema.doc") == (Outcome.UNVERIFIED.value, REASON_UNKNOWN_SOURCE_ON_PATH)


def test_ac6_parallel_branch_that_guarantees_the_field_is_ok():
    states = [
        _state("a", {"state_ids": ["left", "right"], "clear_context_store": "keep_current"}),
        _state("left", {"state_id": "join"}, output_schema=DOC_SCHEMA),
        _state("right", {"state_id": "join"}, output_schema=SEED_SCHEMA),
        _needs("join", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    assert _row(result, "join", "input_schema.doc") == (Outcome.OK.value, REASON_OK)


def test_ac6_two_parallel_writers_of_one_field_is_a_convergence_warning():
    states = [
        _state("a", {"state_ids": ["left", "right"], "clear_context_store": "keep_current"}),
        _state("left", {"state_id": "join"}, output_schema=DOC_SCHEMA),
        _state("right", {"state_id": "join"}, output_schema=DOC_SCHEMA),
        _state("join"),
    ]

    result = simulate_workflow(states)

    finding = _findings(result, "join", f"{CONTEXT_PATH_PREFIX}doc")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.WARNING, REASON_MULTIPLE_PARALLEL_WRITERS)
    assert finding.record.kind is RecordKind.CONVERGENCE


def test_ac6_accumulating_parallel_writers_give_no_convergence_warning():
    states = [
        _state("a", {"state_ids": ["left", "right"], "clear_context_store": "keep_current"}),
        _state("left", {"state_id": "join", "append_to_context": True}, output_schema=DOC_SCHEMA),
        _state("right", {"state_id": "join", "append_to_context": True}, output_schema=DOC_SCHEMA),
        _state("join"),
    ]

    result = simulate_workflow(states)

    assert _findings(result, "join", f"{CONTEXT_PATH_PREFIX}doc") == []


def test_ac6_removal_in_one_parallel_branch_with_no_other_writer_is_error():
    states = [
        _state(
            "a",
            {"state_ids": ["left", "right"], "clear_context_store": "keep_current"},
            output_schema=DOC_SCHEMA,
        ),
        _state("left", {"state_id": "join", "reset_keys_in_context_store": ["doc"]}),
        _state("right", {"state_id": "join"}),
        _needs("join", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    assert _row(result, "join", "input_schema.doc") == (Outcome.ERROR.value, REASON_REMOVED)


def test_ac6_removal_racing_another_writer_is_a_warning():
    states = [
        _state(
            "a",
            {"state_ids": ["left", "right"], "clear_context_store": "keep_current"},
            output_schema=DOC_SCHEMA,
        ),
        _state("left", {"state_id": "join", "reset_keys_in_context_store": ["doc"]}),
        _state("right", {"state_id": "join"}, output_schema=DOC_SCHEMA),
        _needs("join", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    assert _row(result, "join", "input_schema.doc") == (Outcome.WARNING.value, REASON_POSSIBLY_REMOVED)
    assert _row(result, "join", f"{CONTEXT_PATH_PREFIX}doc") == (
        Outcome.WARNING.value,
        REASON_REMOVAL_RACES_WRITE,
    )


# --- AC-7: types and optionality ----------------------------------------------------------------


def test_ac7_type_mismatch_is_a_warning():
    states = [
        _state("a", {"state_id": "b"}, output_schema=DOC_SCHEMA),
        _state("b", input_schema=DOC_ARRAY_SCHEMA),
    ]

    result = simulate_workflow(states)

    assert _row(result, "b", "input_schema.doc") == (Outcome.WARNING.value, REASON_TYPE_MISMATCH)


def test_ac7_optional_source_is_a_warning():
    states = [
        _state("a", {"state_id": "b"}, output_schema=DOC_OPTIONAL_SCHEMA),
        _needs("b", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    assert _row(result, "b", "input_schema.doc") == (Outcome.WARNING.value, REASON_OPTIONAL_SOURCE)


def _typed_schema(declared: object, *, required: bool = True) -> str:
    return json.dumps(
        {"type": "object", "properties": {"n": {"type": declared}}, "required": ["n"] if required else []}
    )


@pytest.mark.parametrize(
    ("produced", "consumed"),
    [("integer", "number"), ("number", "integer"), ("integer", "integer"), ("number", "number")],
)
def test_ac7_integer_and_number_are_compatible_in_both_directions(produced, consumed):
    states = [
        _state("a", {"state_id": "b"}, output_schema=_typed_schema(produced)),
        _state("b", input_schema=_typed_schema(consumed)),
    ]

    result = simulate_workflow(states)

    assert _row(result, "b", "input_schema.n") == (Outcome.OK.value, REASON_OK)


def test_ac7_a_genuine_type_mismatch_still_warns_beside_the_integer_number_rule():
    states = [
        _state("a", {"state_id": "b"}, output_schema=_typed_schema("integer")),
        _state("b", input_schema=_typed_schema("string")),
    ]

    result = simulate_workflow(states)

    assert _row(result, "b", "input_schema.n") == (Outcome.WARNING.value, REASON_TYPE_MISMATCH)


def test_ac7_nullable_source_is_a_warning_when_the_consumer_requires_a_value():
    states = [
        _state("a", {"state_id": "b"}, output_schema=_typed_schema(["string", "null"])),
        _state("b", input_schema=_typed_schema("string")),
    ]

    result = simulate_workflow(states)

    assert _row(result, "b", "input_schema.n") == (Outcome.WARNING.value, REASON_OPTIONAL_SOURCE)


def test_ac7_nullable_source_is_ok_when_the_consumer_accepts_null_too():
    states = [
        _state("a", {"state_id": "b"}, output_schema=_typed_schema(["string", "null"])),
        _state("b", input_schema=_typed_schema(["string", "null"])),
    ]

    result = simulate_workflow(states)

    assert _row(result, "b", "input_schema.n") == (Outcome.OK.value, REASON_OK)


def test_ac7_nullable_source_reaching_a_derived_requirement_is_a_warning():
    states = [
        _state("a", {"state_id": "b"}, output_schema=_schema_with_nullable("doc")),
        _state("b", task="{{ doc }}", resolve_dynamic_values_in_prompt=True),
    ]

    result = simulate_workflow(states)

    assert _row(result, "b", "task", "doc") == (Outcome.WARNING.value, REASON_OPTIONAL_SOURCE)


def _schema_with_nullable(name: str) -> str:
    return json.dumps({"type": "object", "properties": {name: {"type": ["string", "null"]}}, "required": [name]})


def test_ac7_example_form_source_has_presence_checked_and_type_unverified():
    states = [_state("a", {"state_id": "b"}, output_schema=EXAMPLE_DOC), _needs("b", {"doc": "string"})]

    result = simulate_workflow(states)

    finding = _findings(result, "b", "input_schema.doc")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.UNVERIFIED, REASON_TYPE_UNVERIFIED)
    assert finding.record.provenance.producers == ("a",)


# --- AC-8: ignored schema keywords --------------------------------------------------------------


def test_ac8_nested_keywords_yield_no_extra_finding():
    producer = json.dumps(
        {
            "type": "object",
            "properties": {
                "doc": {
                    "type": "object",
                    "properties": {"inner": {"type": "string", "enum": ["a", "b"], "format": "uuid"}},
                    "required": ["inner"],
                }
            },
            "required": ["doc"],
        }
    )
    consumer = json.dumps(
        {
            "type": "object",
            "properties": {"doc": {"type": "object", "properties": {"inner": {"type": "string"}}}},
            "required": ["doc"],
        }
    )
    states = [_state("a", {"state_id": "b"}, output_schema=producer), _state("b", input_schema=consumer)]

    result = simulate_workflow(states)

    assert _paths(result, "b") == ["input_schema.doc"]
    assert _row(result, "b", "input_schema.doc") == (Outcome.OK.value, REASON_OK)


# --- AC-9: iteration scopes ---------------------------------------------------------------------


def _iteration_states(**head_overrides) -> list[WorkflowState]:
    head: dict = {"state_id": "branch", "iter_key": "items", "include_in_iterator_context": ["doc"]}
    head.update(head_overrides)
    return [
        _state("seed", {"state_id": "source", "clear_context_store": "keep_current"}, output_schema=DOC_SCHEMA),
        _state("source", head, output_schema=_schema({"items": "array"}, required=["items"])),
        _needs("branch", {"doc": "string", "extra": "string", "title": "string"}, {"state_id": "after"}),
        _state("after"),
    ]


def _filtered_field_states(first_node: WorkflowState) -> list[WorkflowState]:
    """`seed` writes doc and extra, `source` iterates with a whitelist that keeps only doc, `first_node` is the head."""
    return [
        _state(
            "seed",
            {"state_id": "source", "clear_context_store": "keep_current"},
            output_schema=_schema({"doc": "string", "extra": "string"}),
        ),
        _state(
            "source",
            {"state_id": "branch", "iter_key": "items", "include_in_iterator_context": ["doc"]},
            output_schema=_schema({"items": "array"}, required=["items"]),
        ),
        first_node,
        _state("after"),
    ]


@pytest.mark.parametrize("unknown_initial_input", [True, False])
def test_ac9_agent_first_node_reading_a_filtered_field_is_unverified_because_the_item_may_supply_it(
    unknown_initial_input,
):
    states = _filtered_field_states(
        _needs("branch", {"doc": "string", "extra": "string"}, {"state_id": "after"}),
    )

    result = simulate_workflow(states, unknown_initial_input=unknown_initial_input)

    assert _row(result, "branch", "input_schema.doc") == (Outcome.OK.value, REASON_OK)
    finding = _findings(result, "branch", "input_schema.extra")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.UNVERIFIED, REASON_ITERATION_ITEM_MAY_SUPPLY)
    assert finding.record.provenance.mechanism == Mechanism.ITERATION_FILTER.value
    assert finding.record.provenance.removed_by == "source"
    assert ITERATION_ITEM_SOURCE in finding.record.provenance.sources_checked


@pytest.mark.parametrize("unknown_initial_input", [True, False])
def test_ac9_tool_first_node_reading_a_filtered_field_is_unverified_because_the_item_may_supply_it(
    unknown_initial_input,
):
    first = WorkflowState(id="branch", tool_id="tool-1", tool_args={"extra": None}, next={"state_id": "after"})
    states = _filtered_field_states(first)

    result = simulate_workflow(
        states,
        tools=[WorkflowTool(id="tool-1", tool="generic")],
        unknown_initial_input=unknown_initial_input,
    )

    finding = _findings(result, "branch", "tool_args.extra", "extra")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.UNVERIFIED, REASON_ITERATION_ITEM_MAY_SUPPLY)
    assert finding.record.provenance.mechanism == Mechanism.ITERATION_FILTER.value


@pytest.mark.parametrize("unknown_initial_input", [True, False])
def test_ac9_transform_first_node_reading_a_filtered_field_stays_an_error(unknown_initial_input):
    first = WorkflowState(id="branch", custom_node_id="transform-1", next={"state_id": "after"})
    config = {"mappings": [{"output_field": "out", "type": "extract", "source_path": "extra"}]}

    result = simulate_workflow(
        _filtered_field_states(first),
        custom_nodes=[_transform(config)],
        unknown_initial_input=unknown_initial_input,
    )

    finding = _findings(result, "branch", "config.mappings[0].source_path")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.ERROR, REASON_REMOVED)
    assert finding.record.provenance.mechanism == Mechanism.ITERATION_FILTER.value
    assert finding.record.provenance.removed_by == "source"


def test_ac9_second_node_reading_a_filtered_field_stays_unverified():
    states = [
        _state(
            "seed",
            {"state_id": "source", "clear_context_store": "keep_current"},
            output_schema=_schema({"doc": "string", "extra": "string"}),
        ),
        _state(
            "source",
            {"state_id": "branch", "iter_key": "items", "include_in_iterator_context": ["doc"]},
            output_schema=_schema({"items": "array"}, required=["items"]),
        ),
        _state("branch", {"state_id": "second"}),
        _needs("second", {"extra": "string"}, {"state_id": "after"}),
        _state("after"),
    ]

    result = simulate_workflow(states)

    assert _row(result, "second", "input_schema.extra")[0] == Outcome.UNVERIFIED.value


def test_ac9_second_node_with_a_schema_form_output_still_reads_a_filtered_field_as_unverified():
    states = [
        _state(
            "seed",
            {"state_id": "source", "clear_context_store": "keep_current"},
            output_schema=_schema({"doc": "string", "extra": "string"}),
        ),
        _state(
            "source",
            {"state_id": "branch", "iter_key": "items", "include_in_iterator_context": ["doc"]},
            output_schema=_schema({"items": "array"}, required=["items"]),
        ),
        _state("branch", {"state_id": "second"}, output_schema=_schema({"made": "string"})),
        _needs("second", {"extra": "string"}, {"state_id": "after"}),
        _state("after"),
    ]

    result = simulate_workflow(states)

    finding = _findings(result, "second", "input_schema.extra")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.UNVERIFIED, REASON_ITERATION_ITEM_MAY_SUPPLY)


def test_ac9_first_level_iteration_sees_only_the_whitelisted_fields():
    states = _iteration_states()
    states[0] = _state(
        "seed",
        {"state_id": "source", "clear_context_store": "keep_current"},
        output_schema=_schema({"doc": "string", "extra": "string"}),
    )

    result = simulate_workflow(states)

    assert _row(result, "branch", "input_schema.doc") == (Outcome.OK.value, REASON_OK)
    finding = _findings(result, "branch", "input_schema.extra")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.UNVERIFIED, REASON_ITERATION_ITEM_MAY_SUPPLY)
    assert finding.record.provenance.mechanism == Mechanism.ITERATION_FILTER.value
    assert finding.record.provenance.removed_by == "source"


def test_ac9_requirement_on_an_item_field_is_unverified_on_the_first_node():
    result = simulate_workflow(_iteration_states())

    finding = _findings(result, "branch", "input_schema.title")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.UNVERIFIED, REASON_UNKNOWN_SOURCE_ON_PATH)
    assert ITERATION_ITEM_SOURCE in finding.record.provenance.sources_checked


def test_ac9_nested_iteration_sees_the_parent_context_unfiltered():
    states = _iteration_states()
    states[2] = _state(
        "branch",
        {"state_id": "inner", "iter_key": "rows", "include_in_iterator_context": ["none"]},
        output_schema=_schema({"rows": "array"}),
    )
    states.append(_needs("inner", {"doc": "string"}, {"state_id": "after"}))

    result = simulate_workflow(states)

    assert _row(result, "inner", "input_schema.doc") == (Outcome.OK.value, REASON_OK)


def test_ac9_finish_iteration_closes_the_scope():
    states = _iteration_states()
    states[2] = _state("branch", {"state_id": "after"}, finish_iteration=True, output_schema=SEED_SCHEMA)
    states[3] = _needs("after", {"seed": "string"})

    result = simulate_workflow(states)

    assert _row(result, "after", "input_schema.seed") == (Outcome.OK.value, REASON_OK)
    assert _analysis(result, "after").iteration_depth == 0


def _filtered_iteration_then_summary(summary_input_schema: str | None) -> tuple[list, list]:
    """MR !4725 R3: `source` iterates over `items` passing only `items` into the branch; `summary` reads `title`."""
    source = WorkflowState(
        id="source",
        custom_node_id="source",
        input_schema=_schema({}),
        output_schema=_schema({"items": "array", "title": "string"}),
        next={"state_id": "worker", "iter_key": "items", "include_in_iterator_context": ["items"]},
    )
    worker = WorkflowState(id="worker", custom_node_id="worker", finish_iteration=True, next={"state_id": "summary"})
    summary = WorkflowState(
        id="summary", custom_node_id="summary", input_schema=summary_input_schema, next={"state_id": "end"}
    )
    nodes = [
        _transform(
            {
                "input_source": "context_store",
                "mappings": [
                    {"output_field": "items", "type": "constant", "value": [{"n": 1}, {"n": 2}]},
                    {"output_field": "title", "type": "constant", "value": "Report"},
                ],
            },
            node_id="source",
        ),
        _transform(
            {
                "input_source": "context_store",
                "mappings": [{"output_field": "done", "type": "constant", "value": True}],
            },
            node_id="worker",
        ),
        _transform(
            {
                "input_source": "context_store",
                "mappings": [{"output_field": "heading", "type": "extract", "source_path": "title"}],
            },
            node_id="summary",
        ),
    ]
    return [source, worker, summary], nodes


@pytest.mark.parametrize("summary_input_schema", [None, _schema({"title": "string"})], ids=["derived", "declared"])
def test_finish_iteration_brings_back_the_fields_the_iteration_filter_hid(summary_input_schema):
    states, nodes = _filtered_iteration_then_summary(summary_input_schema)

    result = simulate_workflow(states, custom_nodes=nodes)

    assert _row(result, "summary", "config.mappings[0].source_path") == (Outcome.OK.value, REASON_OK)
    if summary_input_schema is not None:
        assert _row(result, "summary", "input_schema.title") == (Outcome.OK.value, REASON_OK)


@pytest.mark.parametrize("summary_input_schema", [None, _schema({"title": "string"})], ids=["derived", "declared"])
def test_a_conditional_join_inside_the_iteration_keeps_the_fields_the_filter_hid(summary_input_schema):
    """MR !4725: the paths of a condition inside the branch converge before `finish_iteration`; the merge keeps the
    hidden state, so `title` is back after the iteration."""
    states, nodes = _filtered_iteration_then_summary(summary_input_schema)
    states[1] = WorkflowState(id="worker", custom_node_id="worker", next=_condition("path_a", "path_b"))
    states += [
        WorkflowState(id="path_a", custom_node_id="worker", next={"state_id": "join"}),
        WorkflowState(id="path_b", custom_node_id="worker", next={"state_id": "join"}),
        WorkflowState(id="join", custom_node_id="worker", finish_iteration=True, next={"state_id": "summary"}),
    ]

    result = simulate_workflow(states, custom_nodes=nodes)

    assert _row(result, "summary", "config.mappings[0].source_path") == (Outcome.OK.value, REASON_OK)
    if summary_input_schema is not None:
        assert _row(result, "summary", "input_schema.title") == (Outcome.OK.value, REASON_OK)


_WORKER_SPLITS = {
    "condition": {"condition": {"expression": "True", "then": "path_a", "otherwise": "path_b"}},
    "parallel": {"state_ids": ["path_a", "path_b"]},
}


def _filtered_iteration_with_split(split: str, summary_input_schema: str | None, path_a: str) -> tuple[list, list]:
    """MR !4725: inside the filtered iteration `worker` splits into `path_a` (custom node `path_a`) and `path_b`,
    which leaves `title` hidden; both meet at `join`, which finishes the iteration before `summary` reads `title`."""
    states, nodes = _filtered_iteration_then_summary(summary_input_schema)
    states[1] = WorkflowState(id="worker", custom_node_id="worker", next=_WORKER_SPLITS[split])
    states += [
        WorkflowState(id="path_a", custom_node_id=path_a, next={"state_id": "join"}),
        WorkflowState(id="path_b", custom_node_id="worker", next={"state_id": "join"}),
        WorkflowState(id="join", custom_node_id="worker", finish_iteration=True, next={"state_id": "summary"}),
    ]
    return states, nodes


def _title_writer(node_id: str) -> CustomWorkflowNode:
    return _transform(
        {
            "input_source": "context_store",
            "mappings": [{"output_field": "title", "type": "constant", "value": "Refund"}],
        },
        node_id=node_id,
    )


@pytest.mark.parametrize("summary_input_schema", [None, _schema({"title": "string"})], ids=["derived", "declared"])
@pytest.mark.parametrize("split", list(_WORKER_SPLITS))
def test_a_join_where_one_path_writes_a_hidden_field_brings_it_back(split, summary_input_schema):
    """MR !4725: the main context holds `title` on both paths, the original on one and the written value on the other,
    so it is there after the iteration and the parallel join reports no race."""
    states, nodes = _filtered_iteration_with_split(split, summary_input_schema, path_a="writer")
    nodes.append(_title_writer("writer"))

    result = simulate_workflow(states, custom_nodes=nodes)

    assert _row(result, "summary", "config.mappings[0].source_path") == (Outcome.OK.value, REASON_OK)
    if summary_input_schema is not None:
        assert _row(result, "summary", "input_schema.title") == (Outcome.OK.value, REASON_OK)
    assert _paths(result, "join") == []


@pytest.mark.parametrize(
    ("split", "expected"),
    [
        ("condition", (Outcome.WARNING.value, REASON_NOT_ON_EVERY_ALTERNATIVE)),
        ("parallel", (Outcome.OK.value, REASON_OK)),
    ],
)
def test_inside_the_iteration_a_hidden_field_one_path_writes_is_seen_as_the_branch_sees_it(split, expected):
    """The branch view is unchanged: after a condition only one path has `title`, after a fan-out the writer's value
    reaches the join."""
    states, nodes = _filtered_iteration_with_split(split, None, path_a="writer")
    nodes.append(_title_writer("writer"))
    states[-1] = _needs("join", {"title": "string"}, {"state_id": "summary"}, finish_iteration=True)

    result = simulate_workflow(states, custom_nodes=nodes)

    assert _row(result, "join", "input_schema.title") == expected


def test_a_hidden_field_one_alternative_deletes_is_partially_available_after_the_iteration():
    """A path that deletes `title` removes it from the main context, the other leaves it there: after the iteration
    `title` is on some paths only, not removed."""
    states, nodes = _filtered_iteration_with_split("condition", _schema({"title": "string"}), path_a="writer")
    states[-3] = WorkflowState(
        id="path_a",
        custom_node_id="writer",
        next={"state_id": "join", "reset_keys_in_context_store": ["title"]},
    )
    nodes.append(_title_writer("writer"))

    result = simulate_workflow(states, custom_nodes=nodes)

    assert _row(result, "summary", "input_schema.title") == (Outcome.WARNING.value, REASON_NOT_ON_EVERY_ALTERNATIVE)


def test_a_hidden_field_deleted_after_the_join_stays_removed_after_the_iteration():
    states, nodes = _filtered_iteration_with_split("condition", _schema({"title": "string"}), path_a="writer")
    nodes.append(_title_writer("writer"))
    states[-1] = WorkflowState(
        id="join",
        custom_node_id="worker",
        finish_iteration=True,
        next={"state_id": "summary", "reset_keys_in_context_store": ["title"]},
    )

    result = simulate_workflow(states, custom_nodes=nodes)

    assert _row(result, "summary", "input_schema.title") == (Outcome.ERROR.value, REASON_REMOVED)


@pytest.mark.parametrize(
    "worker_next",
    [
        {"state_id": "summary", "reset_keys_in_context_store": ["title"]},
        {"state_id": "summary", "clear_context_store": True},
    ],
    ids=["reset-key", "full-reset"],
)
def test_a_hidden_field_the_branch_deletes_stays_removed_after_the_iteration(worker_next):
    states, nodes = _filtered_iteration_then_summary(_schema({"title": "string"}))
    states[1] = WorkflowState(id="worker", custom_node_id="worker", finish_iteration=True, next=worker_next)

    result = simulate_workflow(states, custom_nodes=nodes)

    assert _row(result, "summary", "input_schema.title") == (Outcome.ERROR.value, REASON_REMOVED)


def test_a_nested_finish_iteration_keeps_the_outer_filter():
    states, nodes = _filtered_iteration_then_summary(None)
    states[1] = WorkflowState(
        id="worker",
        custom_node_id="worker",
        output_schema=_schema({"rows": "array"}),
        next={"state_id": "inner", "iter_key": "rows"},
    )
    states.append(
        _needs("inner", {"title": "string"}, {"state_id": "summary"}, finish_iteration=True, output_schema=_schema({}))
    )

    result = simulate_workflow(states, custom_nodes=nodes)

    assert _analysis(result, "summary").iteration_depth == 1
    assert _row(result, "summary", "config.mappings[0].source_path") == (Outcome.ERROR.value, REASON_REMOVED)


def test_ac9_branch_ending_without_finish_iteration_still_analyses_the_join():
    states = _iteration_states()
    states[2] = _state("branch", {"state_id": "after"}, output_schema=SEED_SCHEMA)
    states[3] = _needs("after", {"seed": "string"})

    result = simulate_workflow(states)

    assert _row(result, "after", "input_schema.seed") == (Outcome.OK.value, REASON_OK)
    assert _analysis(result, "after").iteration_depth == 1


def test_ac9_transform_inside_an_iteration_branch_reads_the_filtered_context():
    states = _iteration_states()
    states[2] = WorkflowState(
        id="branch",
        custom_node_id="transform-1",
        next={"state_id": "after"},
    )
    config = {"mappings": [{"output_field": "out", "type": "extract", "source_path": "extra"}]}

    result = simulate_workflow(states, custom_nodes=[_transform(config)])

    finding = _findings(result, "branch", "config.mappings[0].source_path")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.UNVERIFIED, REASON_UNKNOWN_SOURCE_ON_PATH)


# --- AC-10: output spread -----------------------------------------------------------------------


def test_ac10_output_writing_off_with_an_output_key_leaves_only_the_key():
    states = [
        _state("a", {"state_id": "b", "clear_context_store": "keep_current"}, output_schema=SEED_SCHEMA),
        _state("b", {"state_id": "c", "store_in_context": False, "output_key": "result"}, output_schema=DOC_SCHEMA),
        _needs("c", {"result": "string", "doc": "string"}),
    ]

    result = simulate_workflow(states)

    assert _row(result, "c", "input_schema.result") == (Outcome.OK.value, REASON_OK)
    assert _row(result, "c", "input_schema.doc") == (Outcome.ERROR.value, REASON_WRITE_SUPPRESSED)


def test_ac10_accumulating_transition_gives_a_list_typed_key():
    states = [
        _state("a", {"state_id": "b", "clear_context_store": "keep_current"}),
        _state("b", {"state_id": "c", "output_key": "result", "append_to_context": True}),
        _needs("c", {"result": "string"}),
    ]

    result = simulate_workflow(states)

    assert _row(result, "c", "input_schema.result") == (Outcome.WARNING.value, REASON_TYPE_MISMATCH)


def test_ac10_sub_workflow_without_a_contract_is_not_a_known_source():
    states = [
        WorkflowState(id="a", workflow_id="child-1", next={"state_id": "b", "clear_context_store": "keep_current"}),
        _needs("b", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    assert _row(result, "b", "input_schema.doc")[0] == Outcome.UNVERIFIED.value
    assert _analysis(result, "a").known_source is False


def test_ac40_full_reset_with_an_output_key_leaves_only_the_key():
    states = [
        _state("a", {"state_id": "b", "clear_context_store": "keep_current"}, output_schema=SEED_SCHEMA),
        _state("b", {"state_id": "c", "clear_context_store": True, "output_key": "result"}),
        _needs("c", {"result": "string", "seed": "string"}),
    ]

    result = simulate_workflow(states)

    assert _row(result, "c", "input_schema.result") == (Outcome.OK.value, REASON_OK)
    finding = _findings(result, "c", "input_schema.seed")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.ERROR, REASON_REMOVED)
    assert finding.record.provenance.mechanism == Mechanism.FULL_RESET.value


# --- AC-11: cycles ------------------------------------------------------------------------------


def _cycle_states(tail_next: dict, **head_overrides) -> list[WorkflowState]:
    head: dict = {"state_id": "tail"}
    head.update(head_overrides)
    return [
        _state("seed", {"state_id": "head", "clear_context_store": "keep_current"}, output_schema=SEED_SCHEMA),
        _needs("head", {"seed": "string", "made_in_tail": "string"}, head),
        _state("tail", tail_next, output_schema=_schema({"made_in_tail": "string"})),
        _state("exit"),
    ]


def test_ac11_field_produced_only_inside_the_cycle_is_unverified():
    states = _cycle_states({"condition": {"expression": "True", "then": "head", "otherwise": "exit"}})

    result = simulate_workflow(states)

    assert _row(result, "head", "input_schema.made_in_tail") == (
        Outcome.UNVERIFIED.value,
        REASON_PRODUCED_ONLY_IN_CYCLE,
    )


def test_ac11_field_available_before_the_cycle_and_untouched_is_ok():
    states = _cycle_states({"condition": {"expression": "True", "then": "head", "otherwise": "exit"}})

    result = simulate_workflow(states)

    assert _row(result, "head", "input_schema.seed") == (Outcome.OK.value, REASON_OK)


def test_reset_later_in_the_cycle_body_than_the_consumer_is_a_warning():
    states = _cycle_states(
        {
            "condition": {"expression": "True", "then": "head", "otherwise": "exit"},
            "reset_keys_in_context_store": ["seed"],
        }
    )

    result = simulate_workflow(states)

    finding = _findings(result, "head", "input_schema.seed")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.WARNING, REASON_POSSIBLY_REMOVED)
    assert finding.record.provenance.removed_by == "tail"


def test_nested_cycles_sharing_a_header_form_one_component():
    states = [
        _state("seed", {"state_id": "head", "clear_context_store": "keep_current"}, output_schema=SEED_SCHEMA),
        _state(
            "head",
            {"condition": {"expression": "True", "then": "left", "otherwise": "right"}},
            output_schema=_schema({"branch_flag": "string"}),
        ),
        _state("left", {"state_id": "head"}, output_schema=_schema({"from_left": "string"})),
        _needs("right", {"from_left": "string", "seed": "string"}, {"state_id": "head"}),
    ]

    result = simulate_workflow(states)

    assert _row(result, "right", "input_schema.from_left") == (
        Outcome.UNVERIFIED.value,
        REASON_PRODUCED_ONLY_IN_CYCLE,
    )
    assert _row(result, "right", "input_schema.seed") == (Outcome.OK.value, REASON_OK)


def test_self_loop_is_treated_as_a_cycle():
    states = [
        _state("seed", {"state_id": "loop", "clear_context_store": "keep_current"}, output_schema=SEED_SCHEMA),
        _needs(
            "loop",
            {"own": "string"},
            {"condition": {"expression": "True", "then": "loop", "otherwise": "exit"}},
            output_schema=_schema({"own": "string"}),
        ),
        _state("exit"),
    ]

    result = simulate_workflow(states)

    assert _row(result, "loop", "input_schema.own") == (Outcome.UNVERIFIED.value, REASON_PRODUCED_ONLY_IN_CYCLE)


# --- AC-12: unreachable nodes -------------------------------------------------------------------


def test_ac12_unreachable_node_is_not_a_source_and_is_not_linked():
    states = [
        _state("a", {"state_id": "b", "clear_context_store": "keep_current"}, output_schema=SEED_SCHEMA),
        _needs("b", {"doc": "string"}),
        _needs("orphan", {"doc": "string"}, output_schema=DOC_SCHEMA),
    ]

    result = simulate_workflow(states)

    assert result.unreachable_state_ids == ("orphan",)
    assert result.reachable_state_ids == ("a", "b")
    assert _findings(result, "orphan", "input_schema.doc") == []
    assert _row(result, "b", "input_schema.doc") == (Outcome.ERROR.value, REASON_NOT_GUARANTEED)
    assert "orphan" not in result.uncovered_state_ids


# --- AC-37: transition identifiers --------------------------------------------------------------


@pytest.mark.parametrize(
    ("output_schema", "expected"),
    [
        (_schema({"status": "string"}), (Outcome.OK.value, REASON_OK)),
        (None, (Outcome.UNVERIFIED.value, REASON_NO_USABLE_OUTPUT_CONTRACT)),
        (_schema({"other": "string"}), (Outcome.ERROR.value, REASON_NOT_IN_OUTPUT_CONTRACT)),
    ],
)
def test_ac37_condition_identifier_is_checked_against_the_own_output_contract(output_schema, expected):
    states = [
        _state(
            "a",
            {"condition": {"expression": "status == 'ok'", "then": "b", "otherwise": "b"}},
            output_schema=output_schema,
        ),
        _state("b"),
    ]

    result = simulate_workflow(states)

    assert _row(result, "a", "next.condition.expression") == expected


def test_ac37_iteration_key_declared_non_array_is_a_warning():
    states = [
        _state("a", {"state_id": "b", "iter_key": "items"}, output_schema=_schema({"items": "string"})),
        _state("b"),
    ]

    result = simulate_workflow(states)

    assert _row(result, "a", "next.iter_key") == (Outcome.WARNING.value, REASON_ITER_KEY_NOT_ARRAY)


def test_ac37_transition_requirements_do_not_cover_the_state():
    states = [
        _state("a", {"state_id": "b", "iter_key": "items"}, output_schema=_schema({"items": "array"})),
        _state("b"),
    ]

    result = simulate_workflow(states)

    assert "a" in result.uncovered_state_ids


# --- AC-13 / AC-15 / AC-38: tool bindings with resolver=None -------------------------------------


def test_ac13_binding_to_a_field_nobody_produces_follows_the_not_guaranteed_rule():
    states = [
        _state("a", {"state_id": "b", "clear_context_store": "keep_current"}, output_schema=SEED_SCHEMA),
        _tool_state("b", tool_args={"url": None}),
    ]

    result = simulate_workflow(states, tools=[WorkflowTool(id="tool-1", tool="generic")])

    assert _row(result, "b", "tool_args.url", "url") == (Outcome.ERROR.value, REASON_NOT_GUARANTEED)


def test_ac13_binding_to_an_available_field_is_ok():
    states = [
        _state("a", {"state_id": "b"}, output_schema=_schema({"url": "string"})),
        _tool_state("b", tool_args={"url": None}),
    ]

    result = simulate_workflow(states, tools=[WorkflowTool(id="tool-1", tool="generic")])

    assert _row(result, "b", "tool_args.url", "url") == (Outcome.OK.value, REASON_OK)


def test_ac15_literal_binding_plus_a_same_named_context_field_is_a_warning():
    states = [
        _state("a", {"state_id": "b"}, output_schema=_schema({"url": "string"})),
        _tool_state("b", tool_args={"url": "https://example.test"}),
    ]

    result = simulate_workflow(states, tools=[WorkflowTool(id="tool-1", tool="generic")])

    finding = _findings(result, "b", "tool_args.url", "url")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.WARNING, REASON_CONTEXT_VALUE_WINS)
    assert "example.test" not in finding.record.message


def test_ac15_literal_binding_with_no_same_named_field_is_ok():
    states = [
        _state("a", {"state_id": "b", "clear_context_store": "keep_current"}),
        _tool_state("b", tool_args={"url": "https://example.test"}),
    ]

    result = simulate_workflow(states, tools=[WorkflowTool(id="tool-1", tool="generic")])

    assert _row(result, "b", "tool_args.url", "url") == (Outcome.OK.value, REASON_OK)


def test_ac38_tool_without_a_usable_schema_is_unverified_and_uncovered():
    states = [_tool_state("a")]

    result = simulate_workflow(states, tools=[WorkflowTool(id="tool-1", tool="generic")])

    assert _row(result, "a", TOOL_ARGS_PATH) == (Outcome.UNVERIFIED.value, REASON_TOOL_SCHEMA_UNKNOWN)
    assert result.uncovered_state_ids == ("a",)


def test_ac38_binding_under_a_tool_input_key_stays_unverified():
    states = [
        _state("a", {"state_id": "b", "clear_context_store": "keep_current"}),
        _tool_state("b", tool_args={"url": None}),
    ]
    tools = [WorkflowTool(id="tool-1", tool="generic", input_key="payload")]

    result = simulate_workflow(states, tools=tools)

    assert _row(result, "b", "tool_args.url", "url") == (
        Outcome.UNVERIFIED.value,
        REASON_TOOL_INPUT_KEY_NAMESPACE,
    )


# --- AC-S1 / AC-S2 / AC-S4: derived requirement plumbing -----------------------------------------


def test_missing_placeholder_name_is_labelled_renders_empty():
    states = [
        _state("a", {"state_id": "b", "clear_context_store": "keep_current"}, output_schema=SEED_SCHEMA),
        _state("b", task="{{ doc }}", resolve_dynamic_values_in_prompt=True),
    ]

    result = simulate_workflow(states)

    finding = _findings(result, "b", "task", "doc")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.ERROR, REASON_NOT_GUARANTEED)
    assert finding.record.label == LABEL_RENDERS_EMPTY
    assert finding.requirement.origin == ORIGIN_PLACEHOLDER


def test_transform_extract_root_is_evaluated_against_the_context():
    states = [
        _state("a", {"state_id": "b", "clear_context_store": "keep_current"}, output_schema=SEED_SCHEMA),
        WorkflowState(id="b", custom_node_id="transform-1", next={}),
    ]
    config = {"mappings": [{"output_field": "out", "type": "extract", "source_path": "seed.title"}]}

    result = simulate_workflow(states, custom_nodes=[_transform(config)])

    assert _row(result, "b", "config.mappings[0].source_path") == (Outcome.OK.value, REASON_OK)
    assert _analysis(result, "b").covered is True


def test_platform_keys_are_always_available_and_never_not_guaranteed():
    states = [_tool_state("a", tool_args={"file_names": None})]

    result = simulate_workflow(states, tools=[WorkflowTool(id="tool-1", tool="generic")])

    assert _row(result, "a", "tool_args.file_names", "file_names") == (Outcome.OK.value, REASON_OK)


# --- AC-17: producer notes ----------------------------------------------------------------------


def test_list_contract_without_dict_items_warns_on_the_producer():
    states = [_state("a", {"state_id": "b"}, output_schema='["List of found features"]'), _state("b")]

    result = simulate_workflow(states)

    finding = _findings(result, "a", "output_schema")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.WARNING, REASON_PRODUCER_UNKNOWN_SOURCE)
    assert finding.record.kind is RecordKind.CONTRACT_FORM


def test_schema_form_is_reported_per_state_for_the_mode_policy():
    states = [
        _state("a", {"state_id": "b"}, output_schema=DOC_SCHEMA),
        _state("b", output_schema=EXAMPLE_DOC),
    ]

    result = simulate_workflow(states)

    assert _analysis(result, "a").schema_form is True
    assert _analysis(result, "b").schema_form is False
    assert _analysis(result, "a").contract_form is ContractForm.SCHEMA
    assert _analysis(result, "b").contract_form is ContractForm.EXAMPLE
    assert _analysis(result, "a").kind is NodeKind.AGENT


# --- failure isolation --------------------------------------------------------------------------


class _HostileState:
    """A state whose `input_schema` read raises, as a `model_construct`-ed object could."""

    id = "hostile"
    assistant_id = "assistant-1"
    next = None
    output_schema = None
    task = ""
    finish_iteration = False

    @property
    def input_schema(self):
        raise RuntimeError("boom")


def test_derivation_failure_degrades_only_its_own_state():
    states = [
        _state("a", {"state_id": "hostile", "clear_context_store": "keep_current"}, output_schema=DOC_SCHEMA),
        _HostileState(),
        _needs("b", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    assert _row(result, "hostile", "") == (Outcome.UNVERIFIED.value, REASON_DERIVATION_FAILED)
    assert _analysis(result, "hostile").analysed is False


def test_malformed_contract_degrades_only_its_own_state():
    states = [
        _state("a", {"state_ids": ["bad", "good"], "clear_context_store": "keep_current"}),
        _state("bad", {"state_id": "join"}, output_schema="123"),
        _state("good", {"state_id": "join"}, output_schema=DOC_SCHEMA),
        _needs("join", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    assert _row(result, "join", "input_schema.doc") == (Outcome.OK.value, REASON_OK)
    assert _analysis(result, "bad").known_source is False
    assert _analysis(result, "good").known_source is True


def test_non_workflow_state_gives_one_could_not_analyse_record():
    states = [_state("a", {"state_id": "b"}), {"id": "b"}]

    result = simulate_workflow(states)

    assert any(item.reason == REASON_STATE_NOT_ANALYSABLE for item in result.findings)


def test_terminal_target_is_not_a_missing_state():
    states = [_state("a", {"state_id": "end"})]

    result = simulate_workflow(states)

    assert result.findings == ()
    assert result.reachable_state_ids == ("a",)


def test_missing_transition_target_gives_a_record_not_an_exception():
    states = [_state("a", {"state_id": "nowhere"})]

    result = simulate_workflow(states)

    assert _row(result, "a", "next.state_id") == (Outcome.UNVERIFIED.value, REASON_TRANSITION_TARGET_MISSING)


def test_null_next_is_tolerated():
    states = [WorkflowState.model_construct(id="a", next=None, assistant_id="assistant-1")]

    result = simulate_workflow(states)

    assert result.reachable_state_ids == ("a",)


def test_no_states_gives_an_empty_result():
    result = simulate_workflow([])

    assert result.findings == ()
    assert result.reachable_total == 0


# --- nested fan-outs, switch merge and cycles with fan-outs, resets and iterations ------------------


def _condition(then: str, otherwise: str) -> dict:
    return {"condition": {"expression": "True", "then": then, "otherwise": otherwise}}


def _condition_inside_a_fan_out(x2_schema: str, y_schema: str) -> list[WorkflowState]:
    """`a` fans out to `x` (a condition choosing x1 or x2) and `y`; all of them feed `j`, which needs `doc`."""
    return [
        _state("a", {"state_ids": ["x", "y"], "clear_context_store": "keep_current"}),
        _state("x", _condition("x1", "x2")),
        _state("x1", {"state_id": "j"}, output_schema=DOC_SCHEMA),
        _state("x2", {"state_id": "j"}, output_schema=x2_schema),
        _state("y", {"state_id": "j"}, output_schema=y_schema),
        _needs("j", {"doc": "string"}),
    ]


def test_a_condition_inside_a_fan_out_guarantees_a_field_every_alternative_writes():
    result = simulate_workflow(_condition_inside_a_fan_out(DOC_SCHEMA, SEED_SCHEMA))

    assert _row(result, "j", "input_schema.doc") == (Outcome.OK.value, REASON_OK)


def test_a_condition_inside_a_fan_out_does_not_guarantee_a_field_only_one_alternative_writes():
    result = simulate_workflow(_condition_inside_a_fan_out(SEED_SCHEMA, SEED_SCHEMA))

    assert _row(result, "j", "input_schema.doc") == (Outcome.UNVERIFIED.value, REASON_POSSIBLY_PRESENT)


def test_two_alternatives_of_one_condition_inside_a_fan_out_are_not_a_parallel_race():
    """Only one arm of the condition runs, so two arms writing the field are one writer, not two."""
    result = simulate_workflow(_condition_inside_a_fan_out(DOC_SCHEMA, SEED_SCHEMA))

    assert _row(result, "j", "input_schema.doc") == (Outcome.OK.value, REASON_OK)
    assert [
        item
        for item in _findings(result, "j", f"{CONTEXT_PATH_PREFIX}doc")
        if item.reason == REASON_MULTIPLE_PARALLEL_WRITERS
    ] == []


def test_a_parallel_sibling_of_a_condition_guarantees_the_field_whatever_the_condition_picks():
    result = simulate_workflow(_condition_inside_a_fan_out(SEED_SCHEMA, DOC_SCHEMA))

    assert _row(result, "j", "input_schema.doc") == (Outcome.OK.value, REASON_OK)


def _switch_states(default_schema: str) -> list[WorkflowState]:
    switch = {
        "cases": [{"condition": "True", "state_id": "s1"}, {"condition": "False", "state_id": "s2"}],
        "default": "s3",
    }
    return [
        _state("a", {"switch": switch, "clear_context_store": "keep_current"}),
        _state("s1", {"state_id": "j"}, output_schema=DOC_SCHEMA),
        _state("s2", {"state_id": "j"}, output_schema=DOC_SCHEMA),
        _state("s3", {"state_id": "j"}, output_schema=default_schema),
        _needs("j", {"doc": "string"}),
    ]


def test_switch_merge_is_ok_when_every_case_and_the_default_write_the_field():
    result = simulate_workflow(_switch_states(DOC_SCHEMA))

    assert _row(result, "j", "input_schema.doc") == (Outcome.OK.value, REASON_OK)


def test_switch_merge_warns_when_only_the_default_does_not_write_the_field():
    result = simulate_workflow(_switch_states(SEED_SCHEMA))

    assert _row(result, "j", "input_schema.doc") == (Outcome.WARNING.value, REASON_NOT_ON_EVERY_ALTERNATIVE)


def _cycle_with_fan_out(join_next: dict | None = None, other_schema: str | None = None) -> list[WorkflowState]:
    """`head` needs `seed` (before the cycle) and `made` (written inside it); `p` and `q` run in parallel to `j`."""
    return [
        _state("seed", {"state_id": "head", "clear_context_store": "keep_current"}, output_schema=SEED_SCHEMA),
        _needs("head", {"seed": "string", "made": "string"}, {"state_ids": ["p", "q"]}),
        _state("p", {"state_id": "j"}, output_schema=_schema({"made": "string"})),
        _state("q", {"state_id": "j"}, output_schema=other_schema or _schema({"other": "string"})),
        _state("j", {**_condition("head", "exit"), **(join_next or {})}),
        _state("exit"),
    ]


def test_a_cycle_containing_a_fan_out_keeps_the_field_from_before_the_cycle_ok():
    result = simulate_workflow(_cycle_with_fan_out())

    assert _row(result, "head", "input_schema.seed") == (Outcome.OK.value, REASON_OK)
    assert _row(result, "head", "input_schema.made") == (Outcome.UNVERIFIED.value, REASON_PRODUCED_ONLY_IN_CYCLE)


def test_a_cycle_containing_a_fan_out_with_a_reset_at_the_join_warns_for_the_reset_field():
    result = simulate_workflow(_cycle_with_fan_out({"reset_keys_in_context_store": ["seed"]}))

    finding = _findings(result, "head", "input_schema.seed")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.WARNING, REASON_POSSIBLY_REMOVED)
    assert finding.record.provenance.removed_by == "j"


def test_a_cycle_containing_a_fan_out_whose_branches_write_one_field_warns_about_the_race():
    result = simulate_workflow(_cycle_with_fan_out(other_schema=_schema({"made": "string"})))

    finding = _findings(result, "j", f"{CONTEXT_PATH_PREFIX}made")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.WARNING, REASON_MULTIPLE_PARALLEL_WRITERS)


def _cycle_with_iteration(branch_next: dict | None = None) -> list[WorkflowState]:
    return [
        _state("seed", {"state_id": "head", "clear_context_store": "keep_current"}, output_schema=SEED_SCHEMA),
        _needs(
            "head",
            {"seed": "string", "done": "string"},
            {"state_id": "branch", "iter_key": "items"},
            output_schema=_schema({"items": "array"}, required=["items"]),
        ),
        _state(
            "branch",
            {"state_id": "j", **(branch_next or {})},
            finish_iteration=True,
            output_schema=_schema({"done": "string"}),
        ),
        _state("j", _condition("head", "exit")),
        _state("exit"),
    ]


def test_a_cycle_containing_an_iteration_reads_a_field_written_by_its_branch_as_unverified():
    result = simulate_workflow(_cycle_with_iteration())

    assert _row(result, "head", "input_schema.seed") == (Outcome.OK.value, REASON_OK)
    assert _row(result, "head", "input_schema.done") == (Outcome.UNVERIFIED.value, REASON_PRODUCED_ONLY_IN_CYCLE)


def test_a_cycle_containing_an_iteration_whose_branch_resets_a_field_warns_for_that_field():
    result = simulate_workflow(_cycle_with_iteration({"reset_keys_in_context_store": ["seed"]}))

    finding = _findings(result, "head", "input_schema.seed")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.WARNING, REASON_POSSIBLY_REMOVED)
    assert finding.record.provenance.removed_by == "branch"


# --- fan-outs nested under a condition or under another fan-out (AC-6 composes) ----------------------
#
# AC-6 composes: the branches of each fan-out are merged as a union first, then the results of the alternatives are
# merged by the alternatives rule. A field is guaranteed when every alternative guarantees it, and an alternative that
# is a fan-out guarantees it when any of its branches does.


def _fan_out_behind_a_condition(p1: str, p2: str, other: str) -> list[WorkflowState]:
    """`a` picks the fan-out `f` (branches p1, p2) or the single state `r`; all three feed `j`, which needs `doc`."""
    return [
        _state("a", {**_condition("f", "r"), "clear_context_store": "keep_current"}),
        _state("f", {"state_ids": ["p1", "p2"]}),
        _state("p1", {"state_id": "j"}, output_schema=p1),
        _state("p2", {"state_id": "j"}, output_schema=p2),
        _state("r", {"state_id": "j"}, output_schema=other),
        _needs("j", {"doc": "string"}),
    ]


def _convergence(result, state_id: str, reason: str) -> list:
    return [
        item
        for item in _findings(result, state_id, f"{CONTEXT_PATH_PREFIX}doc")
        if item.record.kind is RecordKind.CONVERGENCE and item.reason == reason
    ]


def test_a_fan_out_behind_a_condition_guarantees_the_field_when_one_of_its_branches_writes_it():
    result = simulate_workflow(_fan_out_behind_a_condition(DOC_SCHEMA, SEED_SCHEMA, DOC_SCHEMA))

    assert _row(result, "j", "input_schema.doc") == (Outcome.OK.value, REASON_OK)
    assert _convergence(result, "j", REASON_MULTIPLE_PARALLEL_WRITERS) == []


def test_two_fan_outs_behind_a_condition_each_guarantee_the_field_through_one_branch():
    states = [
        _state("a", {**_condition("f1", "f2"), "clear_context_store": "keep_current"}),
        _state("f1", {"state_ids": ["l1", "l2"]}),
        _state("f2", {"state_ids": ["r1", "r2"]}),
        _state("l1", {"state_id": "j"}, output_schema=DOC_SCHEMA),
        _state("l2", {"state_id": "j"}, output_schema=SEED_SCHEMA),
        _state("r1", {"state_id": "j"}, output_schema=DOC_SCHEMA),
        _state("r2", {"state_id": "j"}, output_schema=SEED_SCHEMA),
        _needs("j", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    assert _row(result, "j", "input_schema.doc") == (Outcome.OK.value, REASON_OK)


def test_two_parallel_writers_inside_a_fan_out_behind_a_condition_are_a_race_naming_both_writers():
    result = simulate_workflow(_fan_out_behind_a_condition(DOC_SCHEMA, DOC_SCHEMA, DOC_SCHEMA))

    assert _row(result, "j", "input_schema.doc") == (Outcome.OK.value, REASON_OK)
    races = _convergence(result, "j", REASON_MULTIPLE_PARALLEL_WRITERS)
    assert len(races) == 1
    assert races[0].record.outcome is Outcome.WARNING
    assert set(races[0].record.provenance.producers) == {"p1", "p2"}


def test_a_writer_outside_the_fan_out_is_not_a_parallel_race():
    """`r` is an alternative to the fan-out, not a sibling of its branches, so it is never named in the race."""
    result = simulate_workflow(_fan_out_behind_a_condition(DOC_SCHEMA, SEED_SCHEMA, DOC_SCHEMA))

    assert _convergence(result, "j", REASON_MULTIPLE_PARALLEL_WRITERS) == []


def test_a_removal_racing_a_write_inside_a_fan_out_behind_a_condition_is_reported():
    states = [
        _state("a", {**_condition("f", "r"), "clear_context_store": "keep_current"}, output_schema=DOC_SCHEMA),
        _state("f", {"state_ids": ["p1", "p2"]}),
        _state("p1", {"state_id": "j", "reset_keys_in_context_store": ["doc"]}),
        _state("p2", {"state_id": "j"}, output_schema=DOC_SCHEMA),
        _state("r", {"state_id": "j"}),
        _needs("j", {"doc": "string"}),
    ]

    result = simulate_workflow(states)

    races = _convergence(result, "j", REASON_REMOVAL_RACES_WRITE)
    assert len(races) == 1
    assert races[0].record.outcome is Outcome.WARNING
    assert set(races[0].record.provenance.producers) == {"p2"}


def _two_levels_deep(inner_a1: str, inner_a2: str, other_branch: str) -> list[WorkflowState]:
    """`a` picks the fan-out `A` (branches `a1`/`a2` run in parallel with `B`) or `r`; all of them feed `j`."""
    return [
        _state("a", {**_condition("top", "r"), "clear_context_store": "keep_current"}),
        _state("top", {"state_ids": ["A", "B"]}),
        _state("A", {"state_ids": ["a1", "a2"]}),
        _state("a1", {"state_id": "j"}, output_schema=inner_a1),
        _state("a2", {"state_id": "j"}, output_schema=inner_a2),
        _state("B", {"state_id": "j"}, output_schema=other_branch),
        _state("r", {"state_id": "j"}, output_schema=DOC_SCHEMA),
        _needs("j", {"doc": "string"}),
    ]


def test_a_fan_out_nested_two_levels_deep_guarantees_the_field_through_one_inner_branch():
    result = simulate_workflow(_two_levels_deep(DOC_SCHEMA, SEED_SCHEMA, SEED_SCHEMA))

    assert _row(result, "j", "input_schema.doc") == (Outcome.OK.value, REASON_OK)
    assert _convergence(result, "j", REASON_MULTIPLE_PARALLEL_WRITERS) == []


def test_a_fan_out_nested_two_levels_deep_reports_the_inner_race_and_the_outer_one():
    result = simulate_workflow(_two_levels_deep(DOC_SCHEMA, DOC_SCHEMA, DOC_SCHEMA))

    assert _row(result, "j", "input_schema.doc") == (Outcome.OK.value, REASON_OK)
    races = _convergence(result, "j", REASON_MULTIPLE_PARALLEL_WRITERS)
    assert len(races) == 1
    assert set(races[0].record.provenance.producers) == {"a1", "a2", "B"}


def test_a_fan_out_nested_two_levels_deep_warns_when_no_branch_of_it_writes_the_field():
    states = _two_levels_deep(SEED_SCHEMA, SEED_SCHEMA, SEED_SCHEMA)

    result = simulate_workflow(states)

    finding = _findings(result, "j", "input_schema.doc")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.WARNING, REASON_NOT_ON_EVERY_ALTERNATIVE)
    assert set(finding.record.provenance.sources_checked) == {"a1", "a2", "B"}


def test_the_warning_names_the_alternative_that_lacks_the_field_not_a_sibling_branch_that_has_it():
    """p1 writes `doc`, p2 does not, and `r` does not: the alternative that lacks it is `r`, not p2."""
    result = simulate_workflow(_fan_out_behind_a_condition(DOC_SCHEMA, SEED_SCHEMA, SEED_SCHEMA))

    finding = _findings(result, "j", "input_schema.doc")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.WARNING, REASON_NOT_ON_EVERY_ALTERNATIVE)
    assert finding.record.provenance.sources_checked == ("r",)


def test_the_warning_names_every_branch_of_a_fan_out_alternative_that_lacks_the_field():
    result = simulate_workflow(_fan_out_behind_a_condition(SEED_SCHEMA, SEED_SCHEMA, DOC_SCHEMA))

    finding = _findings(result, "j", "input_schema.doc")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.WARNING, REASON_NOT_ON_EVERY_ALTERNATIVE)
    assert finding.record.provenance.sources_checked == ("p1", "p2")


# --- failures degrade to unknown, never to a false OK -----------------------------------------------


def test_a_failed_transition_leaves_downstream_reads_unverified_not_ok():
    states = [
        _state("seed", {"state_id": "a", "clear_context_store": "keep_current"}, output_schema=DOC_SCHEMA),
        _state("a", {"state_id": "b"}),
        _needs("b", {"doc": "string"}),
    ]
    real = simulate_module.apply_transition

    def failing(context, *, state_id, **kwargs):
        if state_id == "a":
            raise RuntimeError("boom")
        return real(context, state_id=state_id, **kwargs)

    with patch.object(simulate_module, "apply_transition", side_effect=failing):
        result = simulate_workflow(states)

    assert _row(result, "b", "input_schema.doc")[0] == Outcome.UNVERIFIED.value


@pytest.mark.parametrize("work_cap", range(1, 120, 3))
def test_the_work_cap_inside_a_cycle_never_produces_a_definite_error_for_a_cycle_field(work_cap):
    states = _cycle_states({"condition": {"expression": "True", "then": "head", "otherwise": "exit"}})

    result = simulate_workflow(states, work_cap=work_cap)

    for item in _findings(result, "head", "input_schema.made_in_tail"):
        assert item.record.outcome is not Outcome.ERROR


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (["url", "url"], frozenset({"url"})),
        (("url", ""), frozenset({"url"})),
        ({"url", 5}, frozenset({"url"})),
        ([], frozenset()),
        (frozenset(), frozenset()),
        ([""], None),
        ([5, None], None),
        ("url", None),
        (None, None),
    ],
)
def test_a_resolver_answer_keeps_its_usable_names_and_only_an_unusable_one_is_unknown(answer, expected):
    assert normalise_parameters(answer) == expected


# --- work cap and lookup budget -----------------------------------------------------------------


def test_work_cap_default_is_one_hundred_thousand_operations():
    assert WORK_CAP == 100_000


def test_lookup_budget_covers_every_registered_built_in_tool():
    from codemie_tools.base import tool_registry

    tool_registry.ensure_loaded()

    # A strict workflow must be able to use every registered built-in tool: past the budget the schema stays unknown
    # and AC-19 rejects the save, which the author cannot fix by editing contracts.
    assert len(tool_registry._CLASSES_BY_NAME) <= LOOKUP_BUDGET


def test_reaching_the_work_cap_turns_the_rest_unverified():
    states = [
        _state("a", {"state_id": "b", "clear_context_store": "keep_current"}, output_schema=DOC_SCHEMA),
        _needs("b", {"doc": "string"}, {"state_id": "c"}),
        _needs("c", {"doc": "string"}),
    ]

    result = simulate_workflow(states, work_cap=1)

    assert result.cap_reached is True
    assert _row(result, "c", "input_schema.doc") == (Outcome.UNVERIFIED.value, REASON_WORK_CAP_REACHED)
    assert any(
        item.reason == REASON_WORK_CAP_REACHED and item.record.consumer_state_id == "" for item in result.findings
    )


class _FakeResolver:
    """Stands in for T10's `ToolSchemaResolver`: a fixed mandatory set per tool name."""

    def __init__(self, mandatory: dict[str, frozenset[str]]):
        self.mandatory = mandatory
        self.calls: list[str] = []

    def mandatory_params(self, tool):
        self.calls.append(tool.tool)
        return self.mandatory.get(tool.tool)


def test_ac14a_non_empty_bindings_omitting_a_mandatory_parameter_is_an_error():
    states = [_tool_state("a", tool_args={"url": "literal"})]
    tools = [WorkflowTool(id="tool-1", tool="searcher")]
    resolver = _FakeResolver({"searcher": frozenset({"url", "query"})})

    result = simulate_workflow(states, tools=tools, resolver=resolver)

    assert _row(result, "a", "tool_args.query") == (
        Outcome.ERROR.value,
        REASON_MANDATORY_PARAMETER_NOT_BOUND,
    )


def test_ac14b_empty_bindings_resolve_mandatory_parameters_by_name():
    states = [
        _state("a", {"state_id": "b"}, output_schema=_schema({"query": "string"})),
        _tool_state("b"),
    ]
    tools = [WorkflowTool(id="tool-1", tool="searcher")]
    resolver = _FakeResolver({"searcher": frozenset({"query"})})

    result = simulate_workflow(states, tools=tools, resolver=resolver)

    assert _row(result, "b", "tool_args.query", "query") == (Outcome.OK.value, REASON_OK)
    assert _analysis(result, "b").covered is True


def test_lookup_budget_is_memoised_per_tool_name_and_bounded():
    tools = [WorkflowTool(id=f"tool-{index}", tool=f"tool-{index}") for index in range(4)]
    states = [
        WorkflowState(
            id=f"s{index}",
            tool_id=f"tool-{index}",
            next={"state_id": f"s{index + 1}"} if index < 3 else {},
        )
        for index in range(4)
    ]
    states.append(_tool_state("repeat", tool_id="tool-0"))
    states[3] = WorkflowState(id="s3", tool_id="tool-3", next={"state_id": "repeat"})
    resolver = _FakeResolver({})

    result = simulate_workflow(states, tools=tools, resolver=resolver, lookup_budget=3)

    assert result.lookups == 3
    assert resolver.calls == ["tool-0", "tool-1", "tool-2"]
    assert _row(result, "s3", TOOL_ARGS_PATH) == (Outcome.UNVERIFIED.value, REASON_LOOKUP_BUDGET_EXHAUSTED)
    assert _row(result, "repeat", TOOL_ARGS_PATH) == (Outcome.UNVERIFIED.value, REASON_TOOL_SCHEMA_UNKNOWN)


def test_only_built_in_tools_spend_the_lookup_budget():
    server = MCPServerDetails(name="srv")
    tools = [
        WorkflowTool(id="builtin-a", tool="builtin-a"),
        WorkflowTool(id="mcp-a", tool="search", mcp_server=server),
        WorkflowTool(id="mcp-b", tool="fetch", mcp_server=server),
        WorkflowTool(id="plugin-a", tool="_plugin"),
        WorkflowTool(id="builtin-b", tool="builtin-b"),
    ]
    order = ["builtin-a", "mcp-a", "mcp-b", "plugin-a", "builtin-b"]
    states = [
        WorkflowState(id=f"s{index}", tool_id=tool_id, next={"state_id": f"s{index + 1}"} if index < 4 else {})
        for index, tool_id in enumerate(order)
    ]
    resolver = _FakeResolver({})

    result = simulate_workflow(states, tools=tools, resolver=resolver, lookup_budget=1)

    assert resolver.calls == ["builtin-a", "search", "fetch", "_plugin"]
    assert result.lookups == 1
    assert _row(result, "s4", TOOL_ARGS_PATH) == (Outcome.UNVERIFIED.value, REASON_LOOKUP_BUDGET_EXHAUSTED)
    for state_id in ("s1", "s2", "s3"):
        assert _row(result, state_id, TOOL_ARGS_PATH) == (Outcome.UNVERIFIED.value, REASON_TOOL_SCHEMA_UNKNOWN)


def test_resolver_exception_degrades_only_that_tool():
    class _Raising:
        def mandatory_params(self, tool):
            raise RuntimeError("lookup exploded")

    states = [_tool_state("a")]

    result = simulate_workflow(states, tools=[WorkflowTool(id="tool-1", tool="generic")], resolver=_Raising())

    assert _row(result, "a", TOOL_ARGS_PATH) == (Outcome.UNVERIFIED.value, REASON_TOOL_SCHEMA_UNKNOWN)


# --- edges --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "next_",
    [
        {},
        {"state_id": "b"},
        {"state_ids": ["b", "c"]},
        {"condition": {"expression": "True", "then": "b", "otherwise": "c"}},
        {
            "switch": {
                "cases": [{"condition": "x == 1", "state_id": "b"}, {"condition": "x == 2", "state_id": "c"}],
                "default": "d",
            }
        },
        {"state_id": "b", "iter_key": "items"},
        {"iter_key": "items", "condition": {"expression": "True", "then": "b", "otherwise": "c"}},
        {"state_id": "end"},
    ],
    ids=["none", "single", "parallel", "condition", "switch", "iteration", "iteration-condition", "terminal"],
)
def test_edge_targets_equal_what_leads_to_says_apart_from_terminal_targets(next_):
    state = _state("a", next_)

    edge_targets = {edge.target for edge in out_edges(state)}

    assert edge_targets == {target for target in state.next.leads_to() if target != "end"}


def test_a_target_only_leads_to_names_still_gets_an_edge():
    state = _state("a", {"state_id": "b"})
    with patch.object(type(state.next), "leads_to", return_value={"b", "c", "end"}):
        edges = out_edges(state)

    assert [(edge.target, edge.kind, edge.path) for edge in edges] == [
        ("b", EdgeKind.SINGLE, "next.state_id"),
        ("c", EdgeKind.SINGLE, "next"),
    ]


# --- scale ---------------------------------------------------------------------------------------


def test_one_thousand_state_chain_is_iterative_and_stays_under_the_cap():
    states = [
        _state(f"s{index}", {"state_id": f"s{index + 1}"} if index < 999 else {}, output_schema=DOC_SCHEMA)
        for index in range(1000)
    ]
    states[999] = _needs("s999", {"doc": "string"})

    started = time.monotonic()
    result = simulate_workflow(states)
    elapsed = time.monotonic() - started

    assert result.cap_reached is False
    assert result.reachable_total == 1000
    assert _row(result, "s999", "input_schema.doc") == (Outcome.OK.value, REASON_OK)
    assert elapsed < 10.0


def _diamond_chain(diamonds: int, total: int) -> list[WorkflowState]:
    """`total` states in a chain whose first `diamonds` links are condition diamonds (s0 -> s1 | s2 -> s2, ...)."""
    states: list[WorkflowState] = []
    index = 0
    for _ in range(diamonds):
        states.append(
            _state(
                f"s{index}",
                {"condition": {"expression": "True", "then": f"s{index + 1}", "otherwise": f"s{index + 2}"}},
            )
        )
        states.append(_state(f"s{index + 1}", {"state_id": f"s{index + 2}"}))
        index += 2
    while index < total:
        states.append(_state(f"s{index}", {"state_id": f"s{index + 1}"}))
        index += 1
    states.append(_state(f"s{total}"))
    return states


def test_two_hundred_state_chain_with_twenty_condition_diamonds_stays_under_two_seconds():
    states = _diamond_chain(diamonds=20, total=200)

    elapsed = min(_timed(lambda: simulate_workflow(states)) for _ in range(3))
    result = simulate_workflow(states)

    assert result.cap_reached is False
    assert result.reachable_total == 201
    assert elapsed < 2.0


def _timed(call) -> float:
    started = time.monotonic()
    call()
    return time.monotonic() - started


def test_two_hundred_state_chain_with_condition_diamonds_completes():
    states: list[WorkflowState] = []
    for index in range(0, 200, 2):
        states.append(
            _state(
                f"s{index}",
                {"condition": {"expression": "True", "then": f"s{index + 1}", "otherwise": f"s{index + 2}"}},
            )
        )
        states.append(_state(f"s{index + 1}", {"state_id": f"s{index + 2}"}))
    states.append(_state("s200"))

    started = time.monotonic()
    result = simulate_workflow(states)
    elapsed = time.monotonic() - started

    assert result.cap_reached is False
    assert elapsed < 10.0


def test_extract_with_an_index_reads_the_literal_key_the_runtime_looks_up():
    source = WorkflowState(
        id="source",
        custom_node_id="source",
        input_schema=_schema({}),
        output_schema=_schema({"items": "array"}),
        next={"state_id": "pick"},
    )
    pick = WorkflowState(id="pick", custom_node_id="pick", next={"state_id": "end"})
    nodes = [
        _transform(
            {"mappings": [{"output_field": "items", "type": "constant", "value": [{"x": "first"}]}]}, node_id="source"
        ),
        _transform(
            {"mappings": [{"output_field": "picked", "type": "extract", "source_path": "items[0].x"}]}, node_id="pick"
        ),
    ]

    result = simulate_workflow([source, pick], custom_nodes=nodes)

    finding = _findings(result, "pick", "config.mappings[0].source_path")[0]
    assert (finding.record.outcome, finding.reason) == (Outcome.ERROR, REASON_NOT_GUARANTEED)
    assert finding.requirement.name == "items[0]"
