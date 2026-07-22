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

"""Fixed-schema interactive element protocol for agent chat.

Defines the element catalog, the request/response payloads carried over the
NDJSON stream, and validation helpers that enforce the per-assistant
interactive features configuration server-side.
"""

import json
import re
import uuid
from datetime import date
from typing import Annotated, ClassVar, Literal, Optional, Union

# The `regex` module provides a match-time timeout that stdlib `re` lacks, used to
# bound agent-authored regex validation (ReDoS defense). It is a declared direct
# backend dependency (see pyproject.toml); the import stays guarded purely as
# defense in depth, so that if it is ever unavailable the module degrades
# gracefully (skip regex format-checks) instead of crashing every module on the
# core import path.
try:
    import regex

    _HAS_REGEX = True
except ImportError:  # pragma: no cover - regex is a declared dependency; guard is defensive
    regex = None
    _HAS_REGEX = False
from pydantic import BaseModel, BeforeValidator, Field, create_model

# Linear-time email check: the label before the required dot excludes "." ([^@\s.]),
# so no quantified group overlaps the "." separator — the match cannot backtrack (no ReDoS).
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

# The surface (and thus any regex) is authored by the LLM agent and the value by
# the user, so both are untrusted. Length caps reduce blast radius; the regex
# match runs under a hard wall-clock timeout (via the `regex` module) so a
# catastrophic-backtracking pattern cannot hang the request worker.
MAX_FIELD_VALUE_LEN = 4096
MAX_REGEX_PATTERN_LEN = 512
MAX_PAYLOAD_BYTES = 65536
REGEX_MATCH_TIMEOUT_SECONDS = 0.1
# Agent-authored surfaces are untrusted (steerable via prompt injection); bound
# their size/nesting BEFORE pydantic parses them so a hostile tree cannot exhaust
# the stack (RecursionError) or the DB/token budget.
MAX_SURFACE_DEPTH = 12
MAX_SURFACE_ELEMENTS = 100


class InteractiveFeaturesConfig(BaseModel):
    """Per-assistant toggles controlling which interactive elements the agent may use."""

    action_buttons: bool = False
    choice: bool = False
    short_forms: bool = False

    def any_enabled(self) -> bool:
        return self.action_buttons or self.choice or self.short_forms


class _InteractiveElement(BaseModel):
    """Base for every catalog element.

    Catalog metadata lives here as class attributes so the whole registry (the union,
    the type map, the feature gating, the response-kind coverage, and per-element answer
    validation) derives from ONE place. Adding an element = define a subclass (set its
    metadata + ``validate_answer``) and list it in ``ELEMENT_REGISTRY`` — nothing else.
    """

    # Feature flags on ``InteractiveFeaturesConfig`` that enable this element (ANY of
    # them). Empty for layout elements (always available when any feature is enabled).
    FEATURES: ClassVar[tuple[str, ...]] = ()
    # Layout/structure element (text/column/row): available whenever any feature is on.
    IS_LAYOUT: ClassVar[bool] = False
    # Response ``kind`` values whose payload is able to carry this element's answer.
    ANSWERABLE_KINDS: ClassVar[tuple[str, ...]] = ()

    def validate_answer(self, answer) -> None:  # noqa: ARG002 - overridden by answerable elements
        """Validate this element's submitted answer. Default: nothing to validate."""
        return None


class TextElement(_InteractiveElement):
    IS_LAYOUT: ClassVar[bool] = True
    type: Literal["text"] = "text"
    content: str


class ButtonElement(_InteractiveElement):
    # A button is the submit trigger, so it is available with either action buttons or
    # short forms enabled.
    FEATURES: ClassVar[tuple[str, ...]] = ("action_buttons", "short_forms")
    type: Literal["button"] = "button"
    id: str
    label: str
    style: Optional[Literal["primary", "secondary", "danger"]] = "primary"


class ChoiceOption(BaseModel):
    value: str
    label: str


class MultipleChoiceElement(_InteractiveElement):
    FEATURES: ClassVar[tuple[str, ...]] = ("choice",)
    ANSWERABLE_KINDS: ClassVar[tuple[str, ...]] = ("choice", "submit")
    type: Literal["multiple_choice"] = "multiple_choice"
    id: str
    options: list[ChoiceOption]
    max_allowed_selections: int = Field(default=1, ge=1)

    def validate_answer(self, answer) -> None:
        # A choice may be left unanswered (empty selection); validate only if present.
        if answer is not None:
            if not isinstance(answer, dict):
                raise ValueError(f"Answer for '{self.id}' must be an object")
            _validate_selected(self, answer.get("selected", []))


class DropdownElement(_InteractiveElement):
    """Single-select drop-down; the user picks exactly one option value.

    A presentation-and-semantics sibling of a single-select multiple_choice, but
    value-based (answer is ``{"value": <option value>}``) so it slots into the
    combined-submit contract like the short-form fields.
    """

    FEATURES: ClassVar[tuple[str, ...]] = ("choice",)
    ANSWERABLE_KINDS: ClassVar[tuple[str, ...]] = ("submit",)
    type: Literal["dropdown"] = "dropdown"
    id: str
    label: str
    options: list[ChoiceOption]
    placeholder: Optional[str] = None
    required: bool = False

    def validate_answer(self, answer) -> None:
        _validate_dropdown_value(self, answer)


class DatePickerElement(_InteractiveElement):
    """Calendar date input. ``value`` is an ISO ``YYYY-MM-DD`` string.

    Optional ``min``/``max`` (inclusive, ISO dates) bound the acceptable range.
    """

    FEATURES: ClassVar[tuple[str, ...]] = ("short_forms",)
    ANSWERABLE_KINDS: ClassVar[tuple[str, ...]] = ("submit",)
    type: Literal["date_picker"] = "date_picker"
    id: str
    label: str
    min: Optional[str] = None
    max: Optional[str] = None
    required: bool = False

    def validate_answer(self, answer) -> None:
        _validate_date_value(self, answer)


class FieldValidation(BaseModel):
    required: bool = False
    regex: Optional[str] = None
    email: bool = False


class TextFieldElement(_InteractiveElement):
    FEATURES: ClassVar[tuple[str, ...]] = ("short_forms",)
    ANSWERABLE_KINDS: ClassVar[tuple[str, ...]] = ("form", "submit")
    type: Literal["text_field"] = "text_field"
    id: str
    label: str
    validation: Optional[FieldValidation] = None

    def validate_answer(self, answer) -> None:
        _validate_field_value(self, _answer_value(answer))


class CheckBoxElement(_InteractiveElement):
    FEATURES: ClassVar[tuple[str, ...]] = ("short_forms",)
    ANSWERABLE_KINDS: ClassVar[tuple[str, ...]] = ("form", "submit")
    type: Literal["checkbox"] = "checkbox"
    id: str
    label: str
    validation: Optional[FieldValidation] = None

    def validate_answer(self, answer) -> None:
        _validate_field_value(self, _answer_value(answer))


class ColumnElement(_InteractiveElement):
    IS_LAYOUT: ClassVar[bool] = True
    type: Literal["column"] = "column"
    children: list["AnyElement"]


class RowElement(_InteractiveElement):
    IS_LAYOUT: ClassVar[bool] = True
    type: Literal["row"] = "row"
    children: list["AnyElement"]


# THE registry: the single source of truth for the element catalog. Everything below
# (the discriminated union, the type map, feature gating, kind coverage, and answer
# validation) derives from it. To add an element type, define its model above and add
# it here — nothing else in this module enumerates the catalog.
ELEMENT_REGISTRY: tuple[type[_InteractiveElement], ...] = (
    TextElement,
    ColumnElement,
    RowElement,
    ButtonElement,
    MultipleChoiceElement,
    DropdownElement,
    DatePickerElement,
    TextFieldElement,
    CheckBoxElement,
)


def _element_type(model: type[_InteractiveElement]) -> str:
    """The wire discriminator (``type`` literal default) of an element model."""
    return model.model_fields["type"].default


_ELEMENT_BY_TYPE: dict[str, type[_InteractiveElement]] = {_element_type(model): model for model in ELEMENT_REGISTRY}
_LAYOUT_TYPES: set[str] = {t for t, model in _ELEMENT_BY_TYPE.items() if model.IS_LAYOUT}

AnyElement = Annotated[Union[ELEMENT_REGISTRY], Field(discriminator="type")]
ColumnElement.model_rebuild()
RowElement.model_rebuild()


class InteractiveRequest(BaseModel):
    """Interactive UI request emitted by the agent as a stream chunk."""

    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    surface: list[AnyElement]


class InteractiveResponse(BaseModel):
    """Structured user response referencing an earlier InteractiveRequest."""

    request_id: str
    kind: Literal["action", "choice", "form", "submit", "text_fallback"]
    payload: dict


def default_element_catalog() -> dict:
    """The catalog (layout + feature->types) derived from the registry defaults.

    This is the shape a deployment can override via customer config to gate which
    registered elements each feature exposes, without touching code. New element TYPES
    still require a model in the registry (a widget cannot be rendered from config alone).
    """
    features: dict[str, list[str]] = {}
    for element_type, model in _ELEMENT_BY_TYPE.items():
        for feature in model.FEATURES:
            features.setdefault(feature, []).append(element_type)
    return {"layout": sorted(_LAYOUT_TYPES), "features": features}


def _known_type_set(value) -> set[str]:
    """Registry-known element types from an untrusted catalog list; else empty.

    Tolerates a malformed catalog value (non-iterable, non-string entries): the whole
    ``catalog`` comes raw from customer config, so a bad shape must degrade — never raise.
    """
    if not isinstance(value, (list, tuple, set)):
        return set()
    return {t for t in value if isinstance(t, str)} & set(_ELEMENT_BY_TYPE)


def _catalog_enabled_types(config: "InteractiveFeaturesConfig", catalog: dict) -> set[str]:
    """Resolve enabled element types from a (possibly overridden) catalog config.

    Only types that actually exist in the registry are honored, so a stale/typo catalog
    entry can never smuggle an unknown element type into the allowed set. Malformed shapes
    are treated as empty (fail-closed) rather than raising.
    """
    # The catalog "layout" list may only enable LAYOUT element types; a non-layout
    # (leaf) type listed there must NOT be force-enabled past per-assistant feature
    # gating, so intersect with the registry's layout set.
    types = _known_type_set(catalog.get("layout")) & set(_LAYOUT_TYPES)
    features = catalog.get("features")
    if isinstance(features, dict):
        for feature, element_types in features.items():
            if getattr(config, feature, False):
                types.update(_known_type_set(element_types))
    return types


def enabled_element_types(config: InteractiveFeaturesConfig, catalog: Optional[dict] = None) -> set[str]:
    """Element types available to the agent for this config.

    Derived from the registry's per-element ``FEATURES`` metadata; when a ``catalog``
    override (from customer config) is supplied it drives the feature->type mapping instead.
    """
    if not config.any_enabled():
        return set()
    # A non-dict catalog (absent or a malformed customer-config value) falls back to the
    # registry defaults instead of raising.
    if isinstance(catalog, dict):
        return _catalog_enabled_types(config, catalog)
    types = set(_LAYOUT_TYPES)
    for element_type, model in _ELEMENT_BY_TYPE.items():
        if any(getattr(config, feature, False) for feature in model.FEATURES):
            types.add(element_type)
    return types


def _walk(elements) -> list:
    flat = []
    for element in elements:
        flat.append(element)
        children = getattr(element, "children", None)
        if children:
            flat.extend(_walk(children))
    return flat


def _check_surface_limits(surface) -> None:
    """Iteratively bound depth and element count on the RAW surface tree.

    Runs before pydantic parsing (which would itself recurse) and uses an explicit
    stack so a hostile deeply-nested input cannot raise RecursionError here.
    """
    if not isinstance(surface, list):
        return  # structural validation is pydantic's job
    stack = [(element, 1) for element in surface if isinstance(element, dict)]
    count = 0
    while stack:
        element, depth = stack.pop()
        count += 1
        if depth > MAX_SURFACE_DEPTH:
            raise ValueError(f"Surface exceeds max nesting depth ({MAX_SURFACE_DEPTH})")
        if count > MAX_SURFACE_ELEMENTS:
            raise ValueError(f"Surface exceeds max element count ({MAX_SURFACE_ELEMENTS})")
        children = element.get("children")
        if isinstance(children, list):
            stack.extend((child, depth + 1) for child in children if isinstance(child, dict))


def _to_raw_surface(surface):
    """Normalize a surface to plain dicts.

    The tool's args schema (build_surface_args_schema) parses the agent's call into
    DYNAMIC element classes; re-validating those instances against the module-level
    AnyElement union fails on nested column/row (distinct classes). Dumping to dicts
    lets InteractiveRequest re-parse into the canonical classes and lets
    _check_surface_limits see the tree (it inspects dicts).
    """
    if not isinstance(surface, list):
        return surface
    return [element.model_dump() if isinstance(element, BaseModel) else element for element in surface]


def validate_surface(surface: list, config: InteractiveFeaturesConfig, catalog: Optional[dict] = None) -> list:
    """Validate a raw surface tree structurally and against the enabled catalog."""
    surface = _to_raw_surface(surface)
    _check_surface_limits(surface)
    request = InteractiveRequest(surface=surface)
    allowed = enabled_element_types(config, catalog)
    disallowed = sorted({el.type for el in _walk(request.surface) if el.type not in allowed})
    if disallowed:
        raise ValueError(
            f"Elements not allowed by the assistant's interactive features config: {', '.join(disallowed)}. "
            f"Allowed: {', '.join(sorted(allowed)) or 'none'}."
        )
    return request.surface


def build_surface_args_schema(config: InteractiveFeaturesConfig, catalog: Optional[dict] = None) -> type[BaseModel]:
    """Build the tool args schema exposing only the enabled element types.

    Layout containers (column/row) are rebuilt with children restricted to the
    enabled union so disabled element types are absent from the schema entirely,
    not just rejected at runtime.
    """
    allowed = enabled_element_types(config, catalog)
    if not allowed:
        raise ValueError("No interactive features enabled")
    leaf_members = [_ELEMENT_BY_TYPE[element_type] for element_type in sorted(allowed - {"column", "row"})]
    # "RestrictedAnyElement" is a forward reference resolved dynamically below via
    # _types_namespace on model_rebuild, so it is intentionally not a module symbol.
    column_cls = create_model(
        "ColumnElement",
        type=(Literal["column"], "column"),
        children=(list["RestrictedAnyElement"], ...),  # noqa: F821
    )
    row_cls = create_model(
        "RowElement",
        type=(Literal["row"], "row"),
        children=(list["RestrictedAnyElement"], ...),  # noqa: F821
    )
    # Only advertise the layout containers the catalog actually enables, so the schema
    # never tells the model column/row are valid when validate_surface would reject them.
    container_members = [cls for cls, name in ((column_cls, "column"), (row_cls, "row")) if name in allowed]
    members = tuple(leaf_members + container_members)
    union = Annotated[Union[members], Field(discriminator="type")]
    namespace = {"RestrictedAnyElement": union}
    column_cls.model_rebuild(_types_namespace=namespace)
    row_cls.model_rebuild(_types_namespace=namespace)
    # Bound depth/element-count on the RAW surface BEFORE the recursive union is
    # parsed. The tool framework validates args against this schema before execute()
    # (and thus before validate_surface) runs, so a hostile deeply-nested surface
    # would otherwise recurse through pydantic and raise RecursionError instead of the
    # clean ValueError this guard produces.
    bounded_surface = Annotated[list[union], BeforeValidator(_bounded_surface_input)]
    return create_model(
        "RequestUserInputArgs",
        surface=(bounded_surface, Field(description="Tree of interactive UI elements to show the user")),
    )


def _bounded_surface_input(value):
    """Pydantic BeforeValidator: pre-screen the raw surface for depth/count limits."""
    _check_surface_limits(value)
    return value


def render_interactive_elements_prompt(config: InteractiveFeaturesConfig, catalog: Optional[dict] = None) -> str:
    """System prompt section listing the explicit catalog of enabled elements."""
    allowed = sorted(enabled_element_types(config, catalog))
    if not allowed:
        return ""
    return (
        "\n\n## Interactive user input\n"
        "When you need an explicit user decision, option selection, or short-form input, "
        "call the `request_user_input` tool instead of asking in plain text. "
        f"The ONLY interactive element types available are: {', '.join(allowed)}. "
        "Never reference or emit element types outside this list. "
        "Calling the tool ends your turn; the user's structured response arrives as the next message."
    )


def _validate_selected(element: "MultipleChoiceElement", selected) -> None:
    """Validate one choice element's selection against ITS OWN options and cap."""
    if not isinstance(selected, list):
        raise ValueError("'selected' must be a list")
    if not all(isinstance(value, str) for value in selected):
        raise ValueError("'selected' must contain only strings")
    valid_values = {option.value for option in element.options}
    unknown = [value for value in selected if value not in valid_values]
    if unknown:
        raise ValueError(f"Unknown choice values: {unknown}")
    if len(selected) > element.max_allowed_selections:
        raise ValueError(f"Selected {len(selected)} options, max allowed: {element.max_allowed_selections}")


def _validate_choice_response(response: InteractiveResponse, elements: list) -> None:
    # The legacy "choice" kind carries a single ``selected`` list, so it is only
    # well-defined for a surface with exactly one choice element. Multi-choice surfaces
    # must use the answers-by-id "submit" kind (which validates each block by its own id);
    # otherwise one block's values would be validated against another block's options.
    choice_elements = [element for element in elements if isinstance(element, MultipleChoiceElement)]
    if len(choice_elements) > 1:
        raise ValueError("kind='choice' supports only a single choice element; use kind='submit'")
    selected = response.payload.get("selected", [])
    for element in choice_elements:
        _validate_selected(element, selected)


def _check_required_field(element, raw, is_checkbox) -> None:
    # A required checkbox must be checked (True); a required text field must be a
    # non-empty string. Falsy non-strings (0, [], {}, False) never satisfy required.
    if is_checkbox and raw is not True:
        raise ValueError(f"Field '{element.id}' is required")
    if not is_checkbox and (not isinstance(raw, str) or raw.strip() == ""):
        raise ValueError(f"Field '{element.id}' is required")


def _validate_field_value(element, raw) -> None:
    # ``validation`` is optional on text_field/checkbox; a missing block means "no
    # rules" — treat it as such instead of dereferencing None (an unvalidated field
    # in a combined submit must not crash with AttributeError).
    validation = element.validation
    is_checkbox = isinstance(element, CheckBoxElement)
    if validation is not None and validation.required:
        _check_required_field(element, raw, is_checkbox)
    if raw is None or raw == "" or raw is False:
        return
    if is_checkbox:
        return  # a checked checkbox has no further text/regex/email rules
    if not isinstance(raw, str):
        raise ValueError(f"Field '{element.id}' must be a string")
    if len(raw) > MAX_FIELD_VALUE_LEN:
        raise ValueError(f"Field '{element.id}' value too long (max {MAX_FIELD_VALUE_LEN})")
    if validation is None:
        return
    if validation.regex:
        _match_field_regex(element.id, validation.regex, str(raw))
    if validation.email and not EMAIL_RE.fullmatch(str(raw)):
        raise ValueError(f"Field '{element.id}' must be a valid email")


def _match_field_regex(field_id: str, pattern: str, value: str) -> None:
    """Full-match an agent-authored pattern against a user value under a hard timeout."""
    if len(pattern) > MAX_REGEX_PATTERN_LEN:
        raise ValueError(f"Field '{field_id}' validation pattern too long")
    if not _HAS_REGEX:
        # Without the timeout-capable `regex` engine we must NOT run an untrusted
        # pattern through stdlib `re` (unbounded ReDoS); skip — the length/type caps
        # still apply and the client also validates format.
        return
    try:
        matched = regex.fullmatch(pattern, value, timeout=REGEX_MATCH_TIMEOUT_SECONDS) is not None
    except regex.error:
        raise ValueError(f"Field '{field_id}' has an invalid validation pattern")
    except TimeoutError:
        raise ValueError(f"Field '{field_id}' validation pattern is too complex")
    if not matched:
        raise ValueError(f"Field '{field_id}' does not match the required format")


def _answer_value(answer):
    """Extract the ``value`` from a value-based answer object, or None."""
    return answer.get("value") if isinstance(answer, dict) else None


def _parse_iso_date(raw):
    """Parse an ISO ``YYYY-MM-DD`` string to a date; None if empty/unparseable."""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def _validate_dropdown_value(element: "DropdownElement", answer) -> None:
    raw = _answer_value(answer)
    if element.required and (not isinstance(raw, str) or raw == ""):
        raise ValueError(f"Field '{element.id}' is required")
    if raw is None or raw == "":
        return
    if not isinstance(raw, str):
        raise ValueError(f"Field '{element.id}' must be a string")
    valid_values = {option.value for option in element.options}
    if raw not in valid_values:
        raise ValueError(f"Unknown dropdown value for '{element.id}': {raw!r}")


def _validate_date_value(element: "DatePickerElement", answer) -> None:
    raw = _answer_value(answer)
    if element.required and (not isinstance(raw, str) or raw == ""):
        raise ValueError(f"Field '{element.id}' is required")
    if raw is None or raw == "":
        return
    if not isinstance(raw, str):
        raise ValueError(f"Field '{element.id}' must be a string")
    value = _parse_iso_date(raw)
    if value is None:
        raise ValueError(f"Field '{element.id}' must be an ISO date (YYYY-MM-DD)")
    lower = _parse_iso_date(element.min)
    if lower is not None and value < lower:
        raise ValueError(f"Field '{element.id}' must be on or after {element.min}")
    upper = _parse_iso_date(element.max)
    if upper is not None and value > upper:
        raise ValueError(f"Field '{element.id}' must be on or before {element.max}")


def _reject_extra_keys(payload: dict, allowed: set) -> None:
    extra = sorted(set(payload) - allowed)
    if extra:
        raise ValueError(f"Payload has unexpected keys: {extra}")


def _validate_form_response(response: InteractiveResponse, field_elements: list) -> None:
    values = response.payload.get("values", {})
    if not isinstance(values, dict):
        raise ValueError("'values' must be an object")
    field_ids = {element.id for element in field_elements}
    unknown = sorted(set(values) - field_ids)
    if unknown:
        raise ValueError(f"Unknown form fields: {unknown}")
    # Validate EVERY field (not only those with a validation block): a no-validation
    # text_field/checkbox still gets its type + length cap here, matching the submit
    # path. _validate_field_value tolerates validation=None.
    for element in field_elements:
        _validate_field_value(element, values.get(element.id))
    _reject_extra_keys(response.payload, {"values"})


def _validate_submit_response(response: InteractiveResponse, elements: list) -> None:
    """Validate a combined submit: one response answering EVERY block of the surface.

    ``payload`` is ``{"action": <button id | null>, "answers": {<element id>: <answer>}}``.
    Each answer is validated against its OWN element by id, so a value valid for one
    choice is never rejected because another choice does not know it.
    """
    payload = response.payload
    _reject_extra_keys(payload, {"action", "answers"})
    answers = payload.get("answers", {})
    if not isinstance(answers, dict):
        raise ValueError("'answers' must be an object")
    by_id = {el.id: el for el in elements}
    unknown = sorted(set(answers) - set(by_id))
    if unknown:
        raise ValueError(f"Unknown answer ids: {unknown}")

    action = payload.get("action")
    if action is not None:
        button_ids = {el.id for el in elements if isinstance(el, ButtonElement)}
        if action not in button_ids:
            raise ValueError(f"Unknown action id: {action!r}")

    for element in elements:
        _validate_answer_for_element(element, answers.get(element.id))


def _validate_answer_for_element(element, answer) -> None:
    """Validate one element's answer via the element's own ``validate_answer`` (registry)."""
    element.validate_answer(answer)


# Which element models each response ``kind`` can answer — derived from each element's
# ANSWERABLE_KINDS so a narrow kind cannot be used to skip a REQUIRED element it
# structurally cannot carry.
_KIND_COVERAGE: dict[str, tuple[type, ...]] = {
    kind: tuple(model for model in ELEMENT_REGISTRY if kind in model.ANSWERABLE_KINDS)
    for kind in ("action", "choice", "form", "submit")
}


def _element_is_required(element) -> bool:
    if isinstance(element, (TextFieldElement, CheckBoxElement)):
        return element.validation is not None and element.validation.required
    if isinstance(element, (DropdownElement, DatePickerElement)):
        return element.required
    return False


def _reject_kind_missing_required(kind: str, elements: list) -> None:
    """Reject a response ``kind`` that structurally cannot answer a required element.

    ``kind`` is client-supplied, so without this a caller could send e.g. ``action``
    for a surface with a required text field and skip its validation entirely.
    ``text_fallback`` is the deliberate free-text opt-out and is exempt.
    """
    if kind == "text_fallback":
        return
    covered = _KIND_COVERAGE.get(kind, ())
    for element in elements:
        if _element_is_required(element) and not isinstance(element, covered):
            raise ValueError(f"Response kind '{kind}' cannot answer required element '{element.id}'")


def validate_response_values(response: InteractiveResponse, request: InteractiveRequest) -> None:
    """Server-side re-validation of a structured response against its request.

    The response ``kind`` is client-supplied and therefore untrusted: each branch
    validates the payload against the request surface and rejects unexpected keys,
    and a kind that cannot carry a required element is rejected outright, so a caller
    cannot skip validation by lying about ``kind``.
    """
    # Bound the whole payload up front: it is persisted and replayed into the LLM
    # prompt every turn, so an oversized blob is a storage/token DoS regardless of kind.
    if len(json.dumps(response.payload)) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"Payload too large (max {MAX_PAYLOAD_BYTES} bytes)")
    elements = [el for el in _walk(request.surface) if hasattr(el, "id")]
    _reject_kind_missing_required(response.kind, elements)
    button_ids = {el.id for el in elements if isinstance(el, ButtonElement)}
    choice_elements = [el for el in elements if isinstance(el, MultipleChoiceElement)]
    field_elements = [el for el in elements if isinstance(el, (TextFieldElement, CheckBoxElement))]

    if response.kind == "text_fallback":
        # The free-text escape may carry ONLY text — never structured values/selected/action.
        _reject_extra_keys(response.payload, {"text"})
        return
    if response.kind == "action":
        action = response.payload.get("action")
        if action not in button_ids:
            raise ValueError(f"Unknown action id: {action!r}")
        _reject_extra_keys(response.payload, {"action"})
        return
    if response.kind == "choice":
        _validate_choice_response(response, choice_elements)
        _reject_extra_keys(response.payload, {"selected"})
        return
    if response.kind == "submit":
        _validate_submit_response(response, elements)
        return
    if response.kind == "form":
        _validate_form_response(response, field_elements)
