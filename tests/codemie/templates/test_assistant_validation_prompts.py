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

"""LLM hardening invariants for the assistant validation prompts.

Implements the cross-cutting section of
``docs/superpowers/specs/2026-08-13-marketplace-publish-validation-llm-hardening-appendix.md``
(A-01 leniency tie-break removal, A-03 no duplication of deterministic rules,
A-04 the structural-checks header).

These are prompt-text invariants, deliberately asserted against the source file
rather than a rendered prompt: a leniency instruction that reaches the model is
a leniency instruction regardless of which template carries it.
"""

import pathlib
import re

import pytest

from codemie.templates.agents import assistant_validation_prompts as prompts

PROMPTS_PATH = pathlib.Path(prompts.__file__)

# The four templates the appendix governs.
TEMPLATES = {
    "METADATA_VALIDATION_TEMPLATE": prompts.METADATA_VALIDATION_TEMPLATE.template,
    "SYSTEM_PROMPT_VALIDATION_TEMPLATE": prompts.SYSTEM_PROMPT_VALIDATION_TEMPLATE.template,
    "TOOLS_DECISION_TEMPLATE": prompts.TOOLS_DECISION_TEMPLATE.template,
    "CONTEXT_VALIDATION_TEMPLATE": prompts.CONTEXT_VALIDATION_TEMPLATE.template,
}

STRUCTURAL_CHECKS_HEADER = (
    "Structural checks (length, presence, banned lists) are enforced separately. "
    "Focus only on qualitative judgment: alignment, professionalism, coherence, "
    "and content appropriateness."
)

# The eight rules owned by the deterministic gate layer
# (`service/marketplace_management/validator/gates.py`). Each entry maps the
# gate `rule=` id to regexes that catch the rule being restated in a prompt.
DETERMINISTIC_RULE_PATTERNS: dict[str, list[str]] = {
    "name_length": [
        r"appropriate length",
        r"\d\s*-\s*\d\s*words",
        r"too short",
        r"too long",
    ],
    "name_not_banned": [
        r"generic word",
        r"just \"bot\"",
        r"\"bot\", \"helper\"",
        r"banned",
    ],
    "description_length": [
        r"at least \d+ characters",
        r"minimum length",
        r"maximum length",
        r"completely missing description",
    ],
    "description_not_equal_name": [
        r"same as (the )?name",
        r"identical to the name",
        r"description equals",
    ],
    "system_prompt_length": [
        r"prompt length",
        r"prompt is (completely )?missing",
        r"missing or empty",
    ],
    "at_least_one_category": [
        r"at least one category",
        r"category count",
        r"categories are required",
    ],
    "substance": [
        r"at least one tool",
        r"must have (at least )?(one|a) tool",
    ],
    "no_inline_credentials": [
        r"\bapi key\b",
        r"\bcredential",
        r"hardcoded (token|password|secret)",
    ],
}


def test_no_leniency_tiebreak() -> None:
    """A-01: no 'When in doubt' tie-break survives in a prompt the gate-ARMED path sends.

    A gate prompt that resolves its own uncertainty in the submitter's favour
    approves exactly what it could not judge. This holds for the armed prompts
    (`TEMPLATES`, and the friendly-message prompt). The gate-OFF variant
    deliberately restores the lenient context rule -- with the deterministic
    gates not running it is the only check a publish gets, and it must behave as
    it did before the gates existed (see `TestGateOffVariant`).
    """
    sent = {**TEMPLATES, "FRIENDLY_MESSAGE_GENERATION_TEMPLATE": prompts.FRIENDLY_MESSAGE_GENERATION_TEMPLATE}

    hits = [
        f"{name}:{lineno}: {line.strip()}"
        for name, text in sent.items()
        for lineno, line in enumerate(text.splitlines(), start=1)
        if "when in doubt" in line.lower()
    ]

    assert hits == [], "Leniency tie-break instructions still present:\n" + "\n".join(hits)


@pytest.mark.parametrize("template_name", sorted(TEMPLATES))
@pytest.mark.parametrize("rule", sorted(DETERMINISTIC_RULE_PATTERNS))
def test_structural_rules_are_not_duplicated_in_prompts(template_name: str, rule: str) -> None:
    """A-03: the LLM layer must not restate a rule the deterministic gates own.

    Two layers judging the same rule means the model can re-litigate a verdict
    the gates already returned, inconsistently and in the lenient direction.
    """
    # The A-04 header itself names the deterministic families ("banned lists")
    # in order to disown them, so it is excluded from the scan.
    template = TEMPLATES[template_name].replace(STRUCTURAL_CHECKS_HEADER, "")

    matched = [pattern for pattern in DETERMINISTIC_RULE_PATTERNS[rule] if re.search(pattern, template, re.IGNORECASE)]

    assert matched == [], (
        f"{template_name} restates the deterministic gate rule {rule!r} "
        f"(matched: {matched}). Deterministic gates own it; the prompt must not."
    )


@pytest.mark.parametrize("template_name", sorted(TEMPLATES))
def test_each_prompt_carries_the_structural_checks_header(template_name: str) -> None:
    """A-04: every prompt states that structural checks live elsewhere."""
    template = TEMPLATES[template_name]

    normalized = " ".join(template.split())
    assert (
        " ".join(STRUCTURAL_CHECKS_HEADER.split()) in normalized
    ), f"{template_name} is missing the structural-checks header required by appendix A-04."


@pytest.mark.parametrize(
    "template_name",
    [
        "METADATA_VALIDATION_TEMPLATE",
        "SYSTEM_PROMPT_VALIDATION_TEMPLATE",
        "TOOLS_DECISION_TEMPLATE",
        "CONTEXT_VALIDATION_TEMPLATE",
    ],
)
def test_every_prompt_asks_for_a_severity_in_its_output(template_name: str) -> None:
    """The severity contract is only real if each prompt asks the model to emit one.

    ``TOOLS_DECISION_TEMPLATE`` and ``CONTEXT_VALIDATION_TEMPLATE`` previously
    defined no severity at all, so every finding they produced inherited the
    CRITICAL default.
    """
    template = TEMPLATES[template_name]

    assert '"severity"' in template, f"{template_name} does not ask the model for a severity"
    assert "## Severity Guidelines" in template, f"{template_name} carries no severity definitions"


def test_severity_is_not_advertised_to_the_model_as_informational() -> None:
    """The field description the LLM sees must not say severity does not matter."""
    from codemie.rest_api.models import assistant_generator as api_models
    from codemie.workflows.assistant_generator.models import validation_models

    for label, text in (
        ("assistant_generator.SEVERITY_DESCRIPTION", api_models.SEVERITY_DESCRIPTION),
        ("validation_models.SEVERITY_LEVEL_DESCRIPTION", validation_models.SEVERITY_LEVEL_DESCRIPTION),
    ):
        assert "informational" not in text.lower(), (
            f"{label} still tells the model (and the reader) that severity is informational only, "
            f"while CRITICAL now blocks publication: {text!r}"
        )


class TestOutputIsBudgeted:
    """The validation prompts must ask for output that fits.

    Measured on the live stack: a publish-time validation burned the model's
    entire 32,768-token completion ceiling over 150 seconds and then failed to
    parse, because nothing bounded what it could write:

        Could not parse response content as the length limit was reached
        CompletionUsage(completion_tokens=32768, prompt_tokens=4744)

    gpt-4.1 is CodeMie's default model and declares max_output_tokens=32768, so
    the run hit exactly its ceiling. A bigger model would not fix this — it
    would raise the ceiling and cost more. The free-text fields have to be
    budgeted instead. The recommendation messages already are ("ONE sentence
    only"); the reasoning fields were not.
    """

    def test_no_prompt_asks_for_a_detailed_explanation(self) -> None:
        """ "Detailed explanation" is an invitation to fill the context window."""
        source = PROMPTS_PATH.read_text(encoding="utf-8")

        assert "Detailed explanation" not in source, (
            "a validation prompt still asks for a 'Detailed explanation'; the "
            "reasoning fields must carry an explicit budget instead"
        )

    def test_every_reasoning_field_states_a_budget(self) -> None:
        """Each free-text field tells the model how much to write."""
        source = PROMPTS_PATH.read_text(encoding="utf-8")

        for field in ("overall_reasoning", "reasoning"):
            # The line that documents the field in the JSON shape must name a
            # limit, so the model sees the budget where it sees the field.
            documented = [line for line in source.splitlines() if f'"{field}"' in line and ":" in line]
            assert documented, f"{field} is not documented in any prompt"
            assert any(
                "sentence" in line.lower() or "char" in line.lower() for line in documented
            ), f"the {field} field is documented without a length budget: {documented[:2]}"

    def test_no_example_models_a_wall_of_text(self) -> None:
        """An over-long example teaches the behaviour the budget forbids."""
        source = PROMPTS_PATH.read_text(encoding="utf-8")

        overlong = [
            line.strip()[:120]
            for line in source.splitlines()
            if '"overall_reasoning"' in line or '"reasoning"' in line
            if len(line) > 400
        ]

        assert not overlong, (
            f"an example reasoning value is over 400 chars, which models the "
            f"verbosity the budget is meant to prevent: {overlong}"
        )


def _off() -> "prompts.ValidationTemplates":
    return prompts.get_validation_templates(False)


def _armed() -> "prompts.ValidationTemplates":
    return prompts.get_validation_templates(True)


class TestGateOffVariant:
    """With the approval gate not armed the deterministic gates do not run, so these
    prompts are the only structural check a publish gets (EPMCDME-14234)."""

    def test_no_unfilled_tokens_in_either_variant(self) -> None:
        for gate_armed in (True, False):
            for template in prompts.get_validation_templates(gate_armed):
                assert "@@" not in template.template

    def test_armed_variant_is_the_module_level_default(self) -> None:
        armed = _armed()
        assert armed.metadata.template == prompts.METADATA_VALIDATION_TEMPLATE.template
        assert armed.system_prompt.template == prompts.SYSTEM_PROMPT_VALIDATION_TEMPLATE.template
        assert armed.tools.template == prompts.TOOLS_DECISION_TEMPLATE.template
        assert armed.context.template == prompts.CONTEXT_VALIDATION_TEMPLATE.template

    @pytest.mark.parametrize("field", ["metadata", "system_prompt", "tools", "context"])
    def test_header_claiming_structural_checks_elsewhere_is_armed_only(self, field: str) -> None:
        header = " ".join(STRUCTURAL_CHECKS_HEADER.split())
        assert header in " ".join(getattr(_armed(), field).template.split())
        assert header not in " ".join(getattr(_off(), field).template.split())

    def test_metadata_restores_name_length_and_uniqueness_criteria(self) -> None:
        off = _off().metadata.template
        assert "**Length**" in off
        assert "**Uniqueness**" in off
        assert "**Length**" not in _armed().metadata.template

    def test_metadata_restores_critical_structural_examples(self) -> None:
        off = _off().metadata.template
        assert "the description is completely missing" in off
        assert '"Bot", "Helper"' in off
        assert 'one word like "Helps"' in off
        assert 'Name: "Bot"' in off

    def test_system_prompt_restores_missing_or_empty_as_critical(self) -> None:
        assert "completely missing or empty" in _off().system_prompt.template
        assert "completely missing or empty" not in _armed().system_prompt.template

    def test_context_restores_the_lenient_rule(self) -> None:
        off = _off().context.template
        assert "BE LENIENT - Default to VALID" in off
        assert "When in doubt, mark as valid" in off
        assert "Missing context is OK" in off

    def test_context_armed_variant_keeps_the_stricter_rule(self) -> None:
        armed = _armed().context.template
        assert "BE LENIENT - Default to VALID" not in armed
        assert "**Missing context**" in armed

    def test_gate_off_context_does_not_flag_missing_context(self) -> None:
        off = _off().context.template
        assert "**Missing context** - the stated purpose requires domain knowledge" not in off
        assert "Absence of context is never a finding here" in off

    @pytest.mark.parametrize("field", ["metadata", "system_prompt", "tools", "context"])
    def test_severity_contract_holds_in_both_variants(self, field: str) -> None:
        """Severity is enforced downstream, so the gate-off prompts must still emit it."""
        for variant in (_armed(), _off()):
            template = getattr(variant, field).template
            assert '"severity"' in template
            assert "## Severity Guidelines" in template

    def test_tools_template_is_otherwise_unchanged_by_gate_state(self) -> None:
        armed = " ".join(_armed().tools.template.split())
        off = " ".join(_off().tools.template.split())
        assert off == armed.replace(" ".join(STRUCTURAL_CHECKS_HEADER.split()) + " ", "")


class TestCallSitesPickTheVariantByGateState:
    """Seam: each caller must hand the model the variant for the live gate state."""

    @pytest.mark.parametrize("armed", [True, False])
    def test_workflow_uses_the_variant_for_the_gate_state(self, armed: bool) -> None:
        from unittest.mock import patch

        from codemie.workflows.assistant_generator import assistant_validation_workflow as workflow

        with (
            patch.object(workflow, "is_approval_gate_armed", return_value=armed),
            patch.object(workflow, "ClarificationAnalysisNode"),
            patch.object(workflow, "ValidateToolsNode"),
            patch.object(workflow, "ValidateContextNode"),
            patch.object(workflow, "MakeDecisionNode"),
            patch.object(workflow.AssistantValidationWorkflow, "_build_graph"),
            patch.object(workflow, "GenericValidationNode") as generic_node,
        ):
            workflow.AssistantValidationWorkflow(llm_model="m")

        sent = [call.kwargs["prompt_template"] for call in generic_node.call_args_list]
        expected = prompts.get_validation_templates(armed)
        assert sent == [expected.metadata.template, expected.system_prompt.template]

    def test_context_node_sends_the_variant_for_the_gate_state(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from codemie.workflows.assistant_generator.nodes.validation import validate_context_node as node_mod

        assistant = SimpleNamespace(name="A", description="d", system_prompt="p")
        state = {"assistant": assistant, "user": None, "clarifications": None}
        sent = {}

        for armed in (True, False):
            node = node_mod.ValidateContextNode(llm_model="m", request_id="r")
            with (
                patch.object(node_mod, "is_approval_gate_armed", return_value=armed),
                patch.object(node_mod, "get_configured_context_names", return_value=["kb"]),
                patch.object(node_mod, "get_validated_context_info", return_value=([], ["kb"])),
                patch.object(node_mod, "format_context_for_prompt", return_value=""),
                patch.object(node, "invoke_llm_with_retry", return_value=MagicMock()) as llm,
            ):
                node(state)
            sent[armed] = llm.call_args.args[0]

        assert "BE LENIENT - Default to VALID" in sent[False]
        assert "BE LENIENT - Default to VALID" not in sent[True]

    def test_tools_node_sends_the_variant_for_the_gate_state(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch

        from codemie.workflows.assistant_generator.nodes.validation import validate_tools_node as node_mod

        assistant = SimpleNamespace(
            name="A", description="d", categories=[], conversation_starters=[], system_prompt="p", toolkits=[]
        )
        sent = {}

        for armed in (True, False):
            node = node_mod.ValidateToolsNode(llm_model="m", request_id="r")
            with (
                patch.object(node_mod, "is_approval_gate_armed", return_value=armed),
                patch.object(node_mod, "format_existing_tools_for_prompt", return_value=""),
                patch.object(node_mod, "format_configured_context_for_prompt", return_value=""),
                patch.object(node_mod, "format_rag_toolkits_for_prompt", return_value=""),
                patch.object(node, "invoke_llm_with_retry", return_value=MagicMock()) as llm,
            ):
                node._make_tools_decision_with_llm(assistant, [], {"clarifications": None, "user": None})
            sent[armed] = llm.call_args.args[0]

        header = " ".join(STRUCTURAL_CHECKS_HEADER.split())
        assert header in " ".join(sent[True].split())
        assert header not in " ".join(sent[False].split())
