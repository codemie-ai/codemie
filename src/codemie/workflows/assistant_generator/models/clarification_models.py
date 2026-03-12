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

"""
Pydantic models for assistant clarification analysis.

These models support the automatic clarification workflow that generates and answers
questions about assistant specifications to improve validation accuracy.
"""

from typing import Literal
from pydantic import BaseModel, Field


class ClarificationAnswer(BaseModel):
    """Evidence-based answer to a clarification question.

    Provides structured analysis with confidence levels and implications for validation.
    """

    question: str = Field(description="The original clarification question")
    answer: str = Field(description="The evidence-based answer to the question")
    confidence: Literal["high", "medium", "low"] = Field(
        description="Confidence level based on evidence explicitness in specification"
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Direct quotes from specification supporting this answer",
    )
    implications: str = Field(description="What this answer means for tool/context selection and validation decisions")


class ClarificationAnalysis(BaseModel):
    """Complete clarification analysis result.

    Contains all questions, answers, and formatted summary for downstream validation nodes.
    """

    questions_generated: int = Field(description="Number of questions generated during analysis")
    questions: list[ClarificationAnswer] = Field(
        default_factory=list, description="All clarification questions with their evidence-based answers"
    )
    clarification_summary_markdown: str = Field(
        description="Formatted markdown summary of clarifications for use in validation prompts"
    )


class AnswersGeneration(BaseModel):
    """LLM output model for answer generation step.

    Used to parse structured output from the answer generation prompt.
    """

    clarifications: list[ClarificationAnswer] = Field(description="Evidence-based answers to all generated questions")
