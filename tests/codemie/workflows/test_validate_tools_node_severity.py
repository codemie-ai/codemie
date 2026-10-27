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

"""The tools phase must report a severity the LLM actually judged.

Appendix TOOLS_DECISION_TEMPLATE severity contract: CRITICAL only when the
configured toolset makes the stated purpose impossible. Before this,
``ToolsValidationResult`` was constructed without a severity, so every tools
finding inherited the CRITICAL default and the distinction could not exist.
"""

from types import SimpleNamespace
from unittest.mock import patch

from codemie.rest_api.models.assistant_generator import RecommendationSeverity
from codemie.workflows.assistant_generator.models.validation_models import ToolsDecisionResult
from codemie.workflows.assistant_generator.nodes.validation.validate_tools_node import ValidateToolsNode


def _assistant():
    return SimpleNamespace(
        id="assistant-1",
        name="Jira Triage",
        description="Triages Jira tickets.",
        categories=["productivity"],
        conversation_starters=[],
        system_prompt="You triage Jira tickets.",
        toolkits=[],
    )


def test_tools_decision_result_carries_a_severity() -> None:
    """The structured-output model the prompt targets must have the field."""
    decision = ToolsDecisionResult(
        tools_to_include=["jira"],
        tools_to_exclude=[],
        reasoning="Jira toolkit is required by the stated purpose.",
        severity=RecommendationSeverity.CRITICAL,
    )

    assert decision.severity == RecommendationSeverity.CRITICAL


def test_node_propagates_the_llm_severity_instead_of_defaulting() -> None:
    """An OPTIONAL tools verdict must survive into the phase result."""
    node = ValidateToolsNode(llm_model="gpt-4.1-mini", request_id="req-1")

    decision = ToolsDecisionResult(
        tools_to_include=["jira"],
        tools_to_exclude=[],
        reasoning="Nice to have, not required.",
        severity=RecommendationSeverity.OPTIONAL,
    )

    with (
        patch(
            "codemie.workflows.assistant_generator.nodes.validation.validate_tools_node."
            "ToolkitLookupService.get_tools_by_query",
            return_value=[],
        ),
        patch.object(ValidateToolsNode, "_make_tools_decision_with_llm", return_value=decision),
        patch(
            "codemie.workflows.assistant_generator.nodes.validation.validate_tools_node.get_assistant_tool_names",
            return_value=set(),
        ),
    ):
        result = node({"assistant": _assistant(), "user": None, "has_sub_assistants": False, "clarifications": None})

    tools_result = result["tools_result"]
    assert tools_result.is_valid is False
    assert (
        tools_result.severity == RecommendationSeverity.OPTIONAL
    ), "The tools phase dropped the LLM's severity and fell back to the CRITICAL default."
