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

"""Severity enforcement in the assistant validation decision node.

Implements the "Enforcement wiring" section of
``docs/superpowers/specs/2026-08-13-marketplace-publish-validation-llm-hardening-appendix.md``
(spec P1-17): a CRITICAL recommendation blocks, an OPTIONAL one is advisory.

Before this, ``_all_phases_valid`` ANDed four ``is_valid`` booleans and read no
severity at all, so a prompt-tone nit and a misleading description produced the
same 422.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from codemie.rest_api.models.assistant_generator import RecommendationSeverity
from codemie.workflows.assistant_generator.models.validation_models import (
    ContextValidationResult,
    MetadataFieldValidation,
    MetadataValidationResult,
    ToolsValidationResult,
    ValidationResult,
)
from codemie.workflows.assistant_generator.nodes.validation.make_decision_node import (
    FriendlyMessages,
    MakeDecisionNode,
)

CRITICAL = RecommendationSeverity.CRITICAL
OPTIONAL = RecommendationSeverity.OPTIONAL


def _assistant():
    return SimpleNamespace(
        id="assistant-1",
        name="Jira Triage",
        description="Triages Jira tickets for the platform team.",
        categories=["productivity"],
        toolkits=[],
    )


def _valid_metadata() -> MetadataValidationResult:
    return MetadataValidationResult(
        fields=[
            MetadataFieldValidation(field_name="name", is_valid=True),
            MetadataFieldValidation(field_name="description", is_valid=True),
        ],
        is_valid=True,
        overall_reasoning="Fine.",
    )


def _metadata_with(severity: RecommendationSeverity) -> MetadataValidationResult:
    return MetadataValidationResult(
        fields=[
            MetadataFieldValidation(
                field_name="description",
                is_valid=False,
                issues=["Tone could be more professional"],
                recommendation="A better description",
                severity=severity,
            ),
        ],
        is_valid=False,
        overall_reasoning="One field flagged.",
    )


def _valid_prompt() -> ValidationResult:
    return ValidationResult(is_valid=True)


def _prompt_with(severity: RecommendationSeverity) -> ValidationResult:
    return ValidationResult(
        is_valid=False,
        issues=["Output format guidance could be clearer"],
        recommendation="An improved prompt",
        severity=severity,
    )


def _valid_tools() -> ToolsValidationResult:
    return ToolsValidationResult(is_valid=True, rag_query="q", reasoning="ok")


def _tools_with(severity: RecommendationSeverity) -> ToolsValidationResult:
    return ToolsValidationResult(
        is_valid=False,
        recommended_additions=[],
        recommended_deletions=[],
        rag_query="q",
        reasoning="Toolset fit could improve",
        severity=severity,
    )


def _valid_context() -> ContextValidationResult:
    return ContextValidationResult(is_valid=True, reasoning="ok")


def _context_with(severity: RecommendationSeverity) -> ContextValidationResult:
    return ContextValidationResult(
        is_valid=False,
        context_to_update=["company-docs"],
        available_context=["company-docs"],
        reasoning="Knowledge base mismatch",
        severity=severity,
    )


def _state(**overrides) -> dict:
    state = {
        "assistant": _assistant(),
        "user": None,
        "context_info": [],
        "metadata_result": _valid_metadata(),
        "system_prompt_result": _valid_prompt(),
        "tools_result": _valid_tools(),
        "context_result": _valid_context(),
    }
    state.update(overrides)
    return state


@pytest.fixture
def node():
    decision_node = MakeDecisionNode(llm_model="gpt-4.1-mini", request_id="req-1")
    with patch.object(
        MakeDecisionNode,
        "invoke_llm_with_retry",
        return_value=FriendlyMessages(field_messages=[], tool_messages=[], context_messages=[]),
    ):
        yield decision_node


def test_critical_blocks_and_optional_does_not(node) -> None:
    """A CRITICAL finding rejects; an OPTIONAL finding alone does not."""
    critical = node(_state(metadata_result=_metadata_with(CRITICAL)))
    optional = node(_state(metadata_result=_metadata_with(OPTIONAL)))

    assert critical["decision"] == "reject"
    assert optional["decision"] == "accept", (
        "An OPTIONAL finding is advisory - it must not block publish. " "Severity is not decorative (appendix P1-17)."
    )


@pytest.mark.parametrize(
    "field, factory",
    [
        ("metadata_result", _metadata_with),
        ("system_prompt_result", _prompt_with),
        ("tools_result", _tools_with),
        ("context_result", _context_with),
    ],
)
def test_every_phase_honours_severity(node, field, factory) -> None:
    """Each of the four phases blocks on CRITICAL and waives OPTIONAL."""
    assert node(_state(**{field: factory(CRITICAL)}))["decision"] == "reject"
    assert node(_state(**{field: factory(OPTIONAL)}))["decision"] == "accept"


def test_accept_still_carries_the_optional_recommendations(node) -> None:
    """An accept caused by OPTIONAL findings must still return them to the caller.

    The appendix requires the user to acknowledge OPTIONAL findings; an empty
    recommendation list would hide them entirely.
    """
    result = node(_state(metadata_result=_metadata_with(OPTIONAL)))

    assert result["decision"] == "accept"
    severities = [rec.severity for rec in result["recommendations"].fields]
    assert severities == [OPTIONAL]


def test_all_valid_accepts_with_no_recommendations(node) -> None:
    """The unchanged happy path: nothing flagged, nothing returned."""
    result = node(_state())

    assert result["decision"] == "accept"
    assert result["recommendations"].fields == []
    assert result["recommendations"].toolkits == []
    assert result["recommendations"].context == []


def test_critical_in_one_phase_blocks_despite_optional_elsewhere(node) -> None:
    """A single CRITICAL outranks any number of OPTIONAL findings."""
    result = node(
        _state(
            metadata_result=_metadata_with(OPTIONAL),
            context_result=_context_with(CRITICAL),
        )
    )

    assert result["decision"] == "reject"


@pytest.mark.parametrize(
    "field",
    ["metadata_result", "system_prompt_result", "tools_result", "context_result"],
)
def test_a_phase_that_did_not_run_blocks(node, field) -> None:
    """A missing phase result blocks: the gate must not approve what it never judged."""
    assert node(_state(**{field: None}))["decision"] == "reject"
