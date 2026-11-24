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

"""Requirement derivation (EPMCDME-16503, parent §3.2, §3.4): what one state needs from the shared context.

`derive_state_requirements` is the entry. It returns two separate groups, because they are checked against different
things:

* `context_requirements` are checked against the simulated shared context (T6). The declared ones come from
  `input_schema`; the derived ones from agent placeholders (AC-S1), transform `extract` roots (AC-S2) and tool
  bindings (AC-S3, AC-13, AC-14b, AC-15).
* `transition_checks` are checked here, against the state's *own* output contract, because that is all the runtime
  gives a condition, a switch or an iteration key (`utils.py:415-423`, `workflow.py:731-749`). They carry an
  `Outcome` already (AC-37) and they never make a state covered.

Two rules are inverted or narrowed on purpose, and the simulator must honour them:

* `ORIGIN_TOOL_BINDING_OVERRIDE` (AC-15) is satisfied by absence: the binding has a literal or template value, so a
  same-named context field only means the context value wins at runtime — present is a WARNING, absent is fine.
* `UNVERIFIABLE_ORIGINS` are never looked up in the flat context (AC-S3): with a tool `input_key` the names resolve
  inside the opaque `context[input_key]` namespace, so they stay UNVERIFIED.

Extraction is deliberately conservative (AR-18): anything this module is not sure about yields no requirement, so a
false ERROR is impossible where a false OK is merely a missed finding. Nothing here raises on hostile or malformed
input, except `TemplateAnalysisCapReached` for a template too costly to analyse, which names the template so the
simulator can report it; nothing here imports `safe_eval`, the nodes or any service.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from codemie.workflows.constants import (
    FILE_NAMES_CONTEXT_KEY,
    PROPAGATED_HEADERS_CONTEXT_KEY,
    SAFE_EVAL_ALLOWED_FUNCTIONS,
)
from codemie.workflows.context_rules import json_pointer_parts
from codemie.workflows.contracts.jinja_analysis import TemplateAnalysisCapReached, extract_context_names
from codemie.workflows.contracts.models import (
    ContractLocation,
    ContractRequirement,
    LocationSection,
    Outcome,
    RecordKind,
    RequirementKind,
)
from codemie.workflows.contracts.reader import (
    ContractForm,
    OutputContract,
    _schema_fields,
    classify_output_contract,
    read_input_contract_fields,
)
from codemie.workflows.transform_constants import (
    CONFIG_KEY_INPUT_KEY,
    CONFIG_KEY_INPUT_SOURCE,
    CONFIG_KEY_MAPPINGS,
    INPUT_SOURCE_CONTEXT_STORE,
    MAPPING_KEY_DEFAULT,
    MAPPING_KEY_OUTPUT_FIELD,
    MAPPING_KEY_SOURCE_PATH,
    MAPPING_KEY_TYPE,
    MAPPING_TYPE_EXTRACT,
    split_source_path,
)

if TYPE_CHECKING:
    from codemie.core.workflow_models.workflow_models import CustomWorkflowNode, WorkflowState, WorkflowTool

# Seeded at the entry for every execution (`workflow.py:1201-1210`), so they are always optional known sources
# (AC-S4) and a requirement on them is never required.
PLATFORM_CONTEXT_KEYS = frozenset({FILE_NAMES_CONTEXT_KEY, PROPAGATED_HEADERS_CONTEXT_KEY})

# A custom node's kind is its `custom_node_id`, matched against the module names of `codemie.workflows.nodes`
# (`workflow.py:639-649`).
TRANSFORM_NODE_KIND = "transform_node"

# Message label of a missing placeholder name (AC-S1): the runtime renders it as empty text rather than failing.
LABEL_RENDERS_EMPTY = "renders empty"

ORIGIN_INPUT_SCHEMA = "input_schema"
ORIGIN_PLACEHOLDER = "placeholder"
ORIGIN_EXTRACT = "extract"
ORIGIN_EXTRACT_INPUT_KEY = "extract_input_key"
ORIGIN_TOOL_BINDING = "tool_binding"
ORIGIN_TOOL_BINDING_TEMPLATE = "tool_binding_template"
ORIGIN_TOOL_BINDING_OVERRIDE = "tool_binding_override"
ORIGIN_TOOL_BINDING_INPUT_KEY = "tool_binding_input_key"
ORIGIN_CONDITION = "condition"
ORIGIN_SWITCH = "switch"
ORIGIN_ITER_KEY = "iter_key"

RENDERS_EMPTY_ORIGINS = frozenset({ORIGIN_PLACEHOLDER, ORIGIN_TOOL_BINDING_TEMPLATE})
UNVERIFIABLE_ORIGINS = frozenset({ORIGIN_TOOL_BINDING_INPUT_KEY})

REASON_OK = "ok"
REASON_NO_USABLE_OUTPUT_CONTRACT = "no_usable_output_contract"
REASON_NOT_IN_OUTPUT_CONTRACT = "not_in_output_contract"
REASON_LIST_OUTPUT = "list_output_has_no_keys"
REASON_ITER_KEY_NOT_ARRAY = "iteration_key_not_array"

NOTE_DECLARED_CONTRACT_UNUSABLE = "declared_contract_unusable"

# An iteration key that resolves to its first segment, whose type the output contract states directly.
_FIRST_SEGMENT = object()

ALLOWED_FUNCTION_NAMES = frozenset(SAFE_EVAL_ALLOWED_FUNCTIONS)
# `keys` is injected beside the output keys (`utils.py:418`); the three literals are only names in an expression that
# the `WorkflowStateCondition` validator has not normalised.
TRANSITION_RESERVED_NAMES = frozenset({"True", "False", "None", "keys"})
_SKIPPED_EXPRESSION_NAMES = ALLOWED_FUNCTION_NAMES | TRANSITION_RESERVED_NAMES


@dataclass(frozen=True)
class OwnOutputCheck:
    """One transition requirement already evaluated against the state's own output contract (AC-37)."""

    requirement: ContractRequirement
    outcome: Outcome
    reason: str


@dataclass(frozen=True)
class StateRequirements:
    """Everything derivation knows about one state.

    `covered` follows the coverage rule stated once in the spec (parent §4.4, §4.5, AC-21): a declared input contract,
    or a derivable one (placeholders, `extract` roots, a tool whose args schema was obtained). `coverage_source` is
    the origin code that covered it. `tool_schema_unknown` is AC-38's UNVERIFIED case;
    `missing_mandatory_parameters` is AC-14a, decidable from configuration alone.
    """

    state_id: str
    context_requirements: tuple[ContractRequirement, ...] = ()
    transition_checks: tuple[OwnOutputCheck, ...] = ()
    covered: bool = False
    coverage_source: str = ""
    tool_schema_unknown: bool = False
    missing_mandatory_parameters: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    bindings_location: ContractLocation | None = None
    tool_schema_location: ContractLocation | None = None

    @property
    def transition_requirements(self) -> tuple[ContractRequirement, ...]:
        return tuple(check.requirement for check in self.transition_checks)


def derive_state_requirements(
    state: WorkflowState,
    *,
    custom_node: CustomWorkflowNode | None = None,
    tool: WorkflowTool | None = None,
    mandatory_parameters: frozenset[str] | None = None,
) -> StateRequirements:
    """Derive the requirements of one state.

    `custom_node` and `tool` are the objects the caller resolved from `state.custom_node_id` / `state.tool_id`.
    `mandatory_parameters` is the mandatory-parameter set of the tool's args schema, or `None` when no usable schema
    was obtained (AC-38); a declared `input_schema` is never a source of it (DEC-3).
    """
    state_id = str(getattr(state, "id", "") or "")
    declared, declared_usable, notes = _declared_requirements(state_id, getattr(state, "input_schema", None))
    placeholders = _placeholder_requirements(state_id, state)
    extracts = _transform_requirements(state_id, custom_node)
    bindings, tool_schema_unknown, missing, bindings_location = _tool_binding_requirements(
        state_id, state, tool, mandatory_parameters
    )

    covered, coverage_source = _coverage(
        declared_usable, placeholders, extracts, schema_obtained=mandatory_parameters is not None
    )
    return StateRequirements(
        state_id=state_id,
        context_requirements=(*declared, *placeholders, *extracts, *bindings),
        transition_checks=_transition_checks(state_id, state),
        covered=covered,
        coverage_source=coverage_source,
        tool_schema_unknown=tool_schema_unknown,
        missing_mandatory_parameters=missing,
        notes=notes,
        bindings_location=bindings_location,
        tool_schema_location=_tool_location(state_id, tool) if getattr(state, "tool_id", None) else None,
    )


def _coverage(
    declared_usable: bool,
    placeholders: tuple[ContractRequirement, ...],
    extracts: tuple[ContractRequirement, ...],
    *,
    schema_obtained: bool,
) -> tuple[bool, str]:
    if declared_usable:
        return True, ORIGIN_INPUT_SCHEMA
    if placeholders:
        return True, ORIGIN_PLACEHOLDER
    if extracts:
        return True, extracts[0].origin
    if schema_obtained:
        return True, ORIGIN_TOOL_BINDING
    return False, ""


def _requirement(  # noqa: PLR0913 - keyword-only builder/internal helper; a parameter object would add indirection without clarity
    state_id: str,
    path: str,
    name: str,
    origin: str,
    *,
    kind: RequirementKind = RecordKind.DERIVED,
    types: tuple[str, ...] | None = None,
    required: bool = True,
    location: ContractLocation | None = None,
) -> ContractRequirement:
    return ContractRequirement(
        state_id=state_id,
        path=path,
        name=name,
        kind=kind,
        origin=origin,
        types=types,
        required=required and name not in PLATFORM_CONTEXT_KEYS,
        location=location,
    )


# --- declared contract --------------------------------------------------------------------------


def _declared_requirements(
    state_id: str, text: object
) -> tuple[tuple[ContractRequirement, ...], bool, tuple[str, ...]]:
    """Requirements of a declared `input_schema`; a value the AC-16 check rejects derives nothing and covers nothing.

    The linker runs after the schema stage, so a non-conforming value cannot reach it on a save path; a direct call
    (tests, the template corpus) can, and is reported as a note rather than guessed at.
    """
    if text is None:
        return (), False, ()
    fields = read_input_contract_fields(text)
    if fields is None:
        return (), False, (NOTE_DECLARED_CONTRACT_UNUSABLE,)
    requirements = tuple(
        ContractRequirement(
            state_id=state_id,
            path=f"{ORIGIN_INPUT_SCHEMA}.{entry.name}",
            name=entry.name,
            kind=RecordKind.DECLARED,
            origin=ORIGIN_INPUT_SCHEMA,
            types=entry.types,
            required=bool(entry.required),
            location=ContractLocation.state(state_id, "input_schema", "properties", entry.name),
        )
        for entry in fields
    )
    return requirements, True, ()


# --- AC-S1: agent placeholders ------------------------------------------------------------------


def _placeholder_requirements(state_id: str, state: WorkflowState) -> tuple[ContractRequirement, ...]:
    """Task placeholders of an agent state, and only when the runtime resolves them (`agent_node.py:141`)."""
    if not getattr(state, "resolve_dynamic_values_in_prompt", False) or not getattr(state, "assistant_id", None):
        return ()
    location = ContractLocation.state(state_id, "task")
    return tuple(
        _requirement(state_id, "task", name, ORIGIN_PLACEHOLDER, location=location)
        for name in _template_names(getattr(state, "task", None), "task", location)
    )


def _template_names(text: object, path: str, location: ContractLocation) -> tuple[str, ...]:
    try:
        return extract_context_names(text)
    except TemplateAnalysisCapReached as error:
        raise TemplateAnalysisCapReached(path, location) from error


# --- AC-S2: transform extract roots -------------------------------------------------------------


def _transform_requirements(state_id: str, custom_node: CustomWorkflowNode | None) -> tuple[ContractRequirement, ...]:
    """`extract` mappings that read the context (`transform_node.py:228-247, 287-344`).

    Other mapping types, other `input_source` values and a root produced by an earlier mapping read nothing from the
    context. With `input_key` set, the single context read is that key's root; what lives inside it is opaque.
    """
    context_config = _transform_context_config(custom_node)
    if context_config is None:
        return ()
    config, mappings = context_config
    location = ContractLocation(section=LocationSection.CUSTOM_NODES, owner_id=custom_node.id, segments=("config",))
    input_key = config.get(CONFIG_KEY_INPUT_KEY)
    if isinstance(input_key, str) and input_key.strip():
        return _transform_input_key_requirements(state_id, input_key, mappings, location)
    return _transform_extract_requirements(state_id, mappings, location)


def _transform_context_config(
    custom_node: CustomWorkflowNode | None,
) -> tuple[dict[str, object], list[object]] | None:
    """Config and mappings of a transform node that reads the context; `None` when it reads nothing from it."""
    if getattr(custom_node, "custom_node_id", None) != TRANSFORM_NODE_KIND:
        return None
    config = getattr(custom_node, "config", None)
    if not isinstance(config, dict):
        return None
    if config.get(CONFIG_KEY_INPUT_SOURCE, INPUT_SOURCE_CONTEXT_STORE) != INPUT_SOURCE_CONTEXT_STORE:
        return None
    mappings = config.get(CONFIG_KEY_MAPPINGS)
    if not isinstance(mappings, list):
        return None
    return config, mappings


def _transform_input_key_requirements(
    state_id: str, input_key: str, mappings: list[object], location: ContractLocation
) -> tuple[ContractRequirement, ...]:
    """The single context read of an `input_key` transform, and only when some mapping extracts from it."""
    if not any(_is_context_extract(mapping) for mapping in mappings):
        return ()
    return (
        _requirement(
            state_id,
            "config.input_key",
            _root_segment(input_key),
            ORIGIN_EXTRACT_INPUT_KEY,
            location=location.child("input_key"),
        ),
    )


def _transform_extract_requirements(
    state_id: str, mappings: list[object], location: ContractLocation
) -> tuple[ContractRequirement, ...]:
    """One requirement per extract root, first mapping wins; a root an earlier mapping produced is no context read."""
    requirements: dict[str, ContractRequirement] = {}
    produced: set[str] = set()
    for index, mapping in enumerate(mappings):
        if not isinstance(mapping, dict):
            continue
        root = _context_extract_root(mapping)
        if root and root not in produced and root not in requirements:
            requirements[root] = _requirement(
                state_id,
                f"config.mappings[{index}].source_path",
                root,
                ORIGIN_EXTRACT,
                required=MAPPING_KEY_DEFAULT not in mapping,
                location=location.child("mappings", index, "source_path"),
            )
        output_field = mapping.get(MAPPING_KEY_OUTPUT_FIELD)
        if isinstance(output_field, str) and output_field:
            produced.add(output_field)
    return tuple(requirements.values())


def _context_extract_root(mapping: dict[str, object]) -> str:
    """Root a context `extract` mapping reads, as `extract_field` splits it: `items[0].x` reads a key `items[0]`.
    Empty for any other mapping."""
    return split_source_path(str(mapping.get(MAPPING_KEY_SOURCE_PATH)))[0] if _is_context_extract(mapping) else ""


def _is_context_extract(mapping: object) -> bool:
    if not isinstance(mapping, dict):
        return False
    if mapping.get(MAPPING_KEY_TYPE, MAPPING_TYPE_EXTRACT) != MAPPING_TYPE_EXTRACT:
        return False
    source_path = mapping.get(MAPPING_KEY_SOURCE_PATH)
    return isinstance(source_path, str) and bool(source_path.strip())


def _root_segment(path: str) -> str:
    """First segment of an `input_key` path with array indices, as `parse_array_index` reads it."""
    head = path.strip().split(".", 1)[0]
    return head.split("[", 1)[0].strip()


# --- AC-S3 / AC-13 / AC-14 / AC-15: tool bindings -----------------------------------------------


def _tool_binding_requirements(
    state_id: str,
    state: WorkflowState,
    tool: WorkflowTool | None,
    mandatory_parameters: frozenset[str] | None,
) -> tuple[tuple[ContractRequirement, ...], bool, tuple[str, ...], ContractLocation | None]:
    """Bindings of a tool state (`tool_node.py:280-291, 465-492`).

    State bindings replace the tool's entirely. An empty set makes every mandatory parameter a name-matched context
    read (AC-14b); a non-empty set resolves only the names it lists, so a mandatory parameter it omits is wrong from
    configuration alone (AC-14a).
    """
    if not getattr(state, "tool_id", None):
        return (), False, (), None
    bindings, bindings_location = _bindings(state_id, state, tool)
    input_key = getattr(tool, "input_key", None)
    namespaced = isinstance(input_key, str) and bool(input_key.strip())
    schema_unknown = mandatory_parameters is None

    if not bindings:
        if mandatory_parameters is None:
            return (), True, (), bindings_location
        requirements = _implicit_binding_requirements(state_id, mandatory_parameters, namespaced, bindings_location)
        return requirements, False, (), bindings_location

    explicit: list[ContractRequirement] = []
    for raw_name, value in bindings.items():
        explicit.extend(
            _explicit_binding_requirements(state_id, raw_name, value, namespaced, bindings_location.child(raw_name))
        )
    missing = (
        () if mandatory_parameters is None else tuple(sorted(set(mandatory_parameters) - {str(k) for k in bindings}))
    )
    return tuple(explicit), schema_unknown, missing, bindings_location


def _implicit_binding_requirements(
    state_id: str, mandatory_parameters: frozenset[str], namespaced: bool, location: ContractLocation
) -> tuple[ContractRequirement, ...]:
    """Every mandatory parameter as a name-matched context read when no bindings are configured (AC-14b)."""
    origin = ORIGIN_TOOL_BINDING_INPUT_KEY if namespaced else ORIGIN_TOOL_BINDING
    return tuple(
        _requirement(state_id, f"tool_args.{name}", name, origin, required=not namespaced, location=location)
        for name in sorted(mandatory_parameters)
    )


def _explicit_binding_requirements(
    state_id: str, raw_name: object, value: object, namespaced: bool, location: ContractLocation
) -> list[ContractRequirement]:
    """Requirements of one configured binding: a namespaced read, a context read, or an override plus its references."""
    name = str(raw_name)
    path = f"tool_args.{name}"
    if namespaced:
        return [_requirement(state_id, path, name, ORIGIN_TOOL_BINDING_INPUT_KEY, required=False, location=location)]
    if value is None or (isinstance(value, str) and not value.strip()):
        return [_requirement(state_id, path, name, ORIGIN_TOOL_BINDING, location=location)]
    # A context field named like the binding wins over the configured value (`dynamic_value_utils.py:447-452`),
    # so the name is read but never needed (AC-15).
    requirements = [_requirement(state_id, path, name, ORIGIN_TOOL_BINDING_OVERRIDE, required=False, location=location)]
    if isinstance(value, str):
        requirements.extend(
            _requirement(state_id, path, reference, ORIGIN_TOOL_BINDING_TEMPLATE, location=location)
            for reference in _template_names(value, path, location)
        )
    return requirements


def _tool_location(state_id: str, tool: WorkflowTool | None) -> ContractLocation:
    if tool is not None:
        return ContractLocation(section=LocationSection.TOOLS, owner_id=tool.id)
    return ContractLocation.state(state_id, "tool_id")


def _bindings(
    state_id: str, state: WorkflowState, tool: WorkflowTool | None
) -> tuple[dict[str, object], ContractLocation]:
    """Select bindings and their source together; implicit parameters point at the tool item."""
    candidate = getattr(state, "tool_args", None)
    if isinstance(candidate, dict) and candidate:
        return candidate, ContractLocation.state(state_id, "tool_args")
    location = _tool_location(state_id, tool)
    candidate = getattr(tool, "tool_args", None)
    if isinstance(candidate, dict) and candidate:
        return candidate, location.child("tool_args")
    return {}, location


# --- AC-37: transition identifiers --------------------------------------------------------------


def _transition_checks(state_id: str, state: WorkflowState) -> tuple[OwnOutputCheck, ...]:
    """Condition, switch and iteration-key identifiers, checked against the state's own output contract."""
    next_state = getattr(state, "next", None)
    if next_state is None:
        return ()
    contract = classify_output_contract(getattr(state, "output_schema", None))
    checks: dict[tuple[str, str], OwnOutputCheck] = {}

    condition = getattr(next_state, "condition", None)
    if condition is not None:
        for name in extract_expression_names(getattr(condition, "expression", None)):
            _add_check(
                checks,
                state_id,
                "next.condition.expression",
                name,
                ORIGIN_CONDITION,
                contract,
                location=ContractLocation.state(state_id, "next", "condition", "expression"),
            )
    switch = getattr(next_state, "switch", None)
    if switch is not None:
        for index, case in enumerate(getattr(switch, "cases", None) or []):
            path = f"next.switch.cases[{index}].condition"
            for name in extract_expression_names(getattr(case, "condition", None)):
                _add_check(
                    checks,
                    state_id,
                    path,
                    name,
                    ORIGIN_SWITCH,
                    contract,
                    location=ContractLocation.state(state_id, "next", "switch", "cases", index, "condition"),
                )
    iter_key = getattr(next_state, "iter_key", None)
    # A list output is iterated whole and the key is ignored (`workflow.py:705-712`), so there is nothing to check.
    if isinstance(iter_key, str) and iter_key.strip() and contract.form is not ContractForm.LIST:
        parts = _iteration_key_parts(iter_key)
        if parts[0]:
            _add_check(
                checks,
                state_id,
                "next.iter_key",
                parts[0],
                ORIGIN_ITER_KEY,
                contract,
                location=ContractLocation.state(state_id, "next", "iter_key"),
                iterated=_iterated_types(getattr(state, "output_schema", None), contract, parts, iter_key),
            )
    return tuple(checks.values())


def _add_check(  # noqa: PLR0913 - keyword-only builder/internal helper; a parameter object would add indirection without clarity
    checks: dict[tuple[str, str], OwnOutputCheck],
    state_id: str,
    path: str,
    name: str,
    origin: str,
    contract: OutputContract,
    *,
    location: ContractLocation,
    iterated: object = _FIRST_SEGMENT,
) -> None:
    if (path, name) in checks:
        return
    outcome, reason, types = _evaluate_against_own_output(name, origin, contract, iterated)
    requirement = ContractRequirement(
        state_id=state_id,
        path=path,
        name=name,
        kind=RecordKind.DERIVED,
        origin=origin,
        types=types,
        location=location,
    )
    checks[(path, name)] = OwnOutputCheck(requirement=requirement, outcome=outcome, reason=reason)


def _evaluate_against_own_output(
    name: str, origin: str, contract: OutputContract, iterated: object = _FIRST_SEGMENT
) -> tuple[Outcome, str, tuple[str, ...] | None]:
    """`iterated` is the declared type of what an iteration key resolves to, when that is not its first segment."""
    if contract.form is ContractForm.NONE or not contract.known_source:
        return Outcome.UNVERIFIED, REASON_NO_USABLE_OUTPUT_CONTRACT, None
    if contract.form is ContractForm.LIST:
        # A condition or switch sees the parsed output's keys only; a list has none, so it is always False at runtime
        # (`utils.py:415-423`) and the union of the items' keys must never make it OK.
        return Outcome.ERROR, REASON_LIST_OUTPUT, None
    entry = next((field for field in contract.fields if field.name == name), None)
    if entry is None:
        return Outcome.ERROR, REASON_NOT_IN_OUTPUT_CONTRACT, None
    types = entry.types if iterated is _FIRST_SEGMENT else cast(tuple[str, ...] | None, iterated)
    if origin == ORIGIN_ITER_KEY and types is not None and "array" not in types:
        return Outcome.WARNING, REASON_ITER_KEY_NOT_ARRAY, types
    return Outcome.OK, REASON_OK, types


def _iteration_key_parts(iter_key: str) -> list[str]:
    """The keys an iteration key follows: one for a plain key, the runtime's own split for a JSON pointer."""
    key = iter_key.strip()
    return json_pointer_parts(key) if key.startswith("/") else [key]


def _iterated_types(output_schema: object, contract: OutputContract, parts: list[str], iter_key: str) -> object:
    """The declared types at the end of an iteration key, `_FIRST_SEGMENT` when that is the top-level field.

    A pointer is followed through nested `properties`; anything the schema does not state there is unknown (`None`),
    and so is a string under a trailing slash, which the runtime parses as JSON before iterating.
    """
    if len(parts) == 1:
        types: object = _FIRST_SEGMENT
        if iter_key.strip().endswith("/"):
            entry = next((field for field in contract.fields if field.name == parts[0]), None)
            if entry is not None and entry.types is not None and "string" in entry.types:
                types = None
        return types
    if contract.form is not ContractForm.SCHEMA:
        return None
    node = _schema_document(output_schema)
    for part in parts[:-1]:
        node = _property_schema(node, part)
    leaf = _property_schema(node, parts[-1])
    if leaf is None:
        return None
    declared = _schema_fields({parts[-1]: leaf}, None)[0].types
    if iter_key.strip().endswith("/") and declared is not None and "string" in declared:
        return None
    return declared


def _schema_document(output_schema: object) -> object:
    if isinstance(output_schema, dict):
        return output_schema
    try:
        return json.loads(str(output_schema))
    except (ValueError, RecursionError):
        return None


def _property_schema(node: object, name: str) -> dict[str, object] | None:
    properties = node.get("properties") if isinstance(node, dict) else None
    child = properties.get(name) if isinstance(properties, dict) else None
    return child if isinstance(child, dict) else None


def extract_expression_names(expression: object) -> tuple[str, ...]:
    """Root `ast.Name` values of a condition or switch expression, as `safe_eval` would resolve them."""
    if not isinstance(expression, str) or not expression.strip():
        return ()
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return ()
    bound = _locally_bound_names(tree)
    names: dict[str, None] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
            continue
        if node.id in _SKIPPED_EXPRESSION_NAMES or node.id in bound:
            continue
        names.setdefault(node.id)
    return tuple(names)


def _locally_bound_names(tree: ast.AST) -> set[str]:
    """Comprehension targets and lambda parameters; `safe_eval` rejects both, so they are never context names."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.comprehension):
            bound.update(item.id for item in ast.walk(node.target) if isinstance(item, ast.Name))
        elif isinstance(node, ast.Lambda):
            arguments = node.args
            bound.update(argument.arg for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs))
            for extra in (arguments.vararg, arguments.kwarg):
                if extra is not None:
                    bound.add(extra.arg)
    return bound
