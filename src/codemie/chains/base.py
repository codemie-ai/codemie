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

from enum import Enum
from typing import Optional, List, Iterator

from langchain_core.messages import BaseMessage
from pydantic import BaseModel, Field, model_validator

from codemie.configs import logger
from codemie.core.models import ChatMessage
from codemie.core.errors import AgentErrorDetails, ToolErrorDetails


class ThoughtAuthorType(str, Enum):
    Tool = "Tool"
    Agent = "Agent"
    System = "System"


class ThoughtOutputFormat(str, Enum):
    TEXT = "text"
    MARKDOWN = "markdown"


class Thought(BaseModel):
    """Stores the state of the current generation thought"""

    id: str
    parent_id: Optional[str] = None
    metadata: Optional[dict] = {}
    in_progress: bool = False
    input_text: Optional[str] = None
    message: Optional[str] = None
    author_type: Optional[str] = ThoughtAuthorType.Tool
    author_name: Optional[str] = None
    output_format: Optional[str] = ThoughtOutputFormat.TEXT
    error: Optional[bool] = False
    children: List['Thought'] = Field(default_factory=list)


Thought.model_rebuild()


class WorkflowExecutionResult(BaseModel):
    """Store workflow execution details"""

    id_: str
    status: str


class GenerationResult(BaseModel):
    """
    Result of agent generation with structured error handling.

    Separates successful output from error information to ensure tool errors
    are not absorbed by the model's generated text.
    """

    # Success fields
    generated: Optional[str | dict | BaseModel]  # None if error occurred
    time_elapsed: Optional[float]
    input_tokens_used: Optional[int]
    tokens_used: Optional[int]

    # Error fields - agent errors vs tool errors
    # success: False indicates agent-level failures (timeouts, token limits, callbacks)
    # Tool failures are captured separately in tool_errors field
    success: bool
    agent_error: Optional[AgentErrorDetails] = Field(
        default=None, description="Agent-level error details (token limits, callbacks, etc.)"
    )
    tool_errors: Optional[list[ToolErrorDetails]] = Field(
        default=None, description="Tool execution errors (HTTP errors, auth failures, etc.)"
    )

    @model_validator(mode='after')
    def warn_inconsistencies(self) -> 'GenerationResult':
        """Log warnings for unusual but allowed states."""

        if self.success and not self.generated:
            logger.warning(
                "GenerationResult: success=True but generated is None. "
                "Verify this is intentional (background task, void operation)."
            )

        if not self.success and self.generated:
            logger.warning(
                "GenerationResult: success=False but generated is not empty. "
                "Verify this is intentional (background task, void operation)."
            )

        return self


class WorkflowStateEvent(BaseModel):
    """Workflow state change event for streaming"""

    id: Optional[str] = None  # State ID
    name: str  # State name
    task: Optional[str] = None  # Task description
    status: str  # State status
    event_type: str  # "state_start" or "state_finish"
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class StreamedGenerationResult(BaseModel):
    time_elapsed: Optional[float] = None
    generated_chunk: Optional[str] = None
    generated: Optional[str] = None
    thought: Optional[Thought] = None
    context: Optional[dict] = None
    last: Optional[bool] = False
    debug: Optional[dict] = None
    workflow_execution: Optional[WorkflowExecutionResult] = None  # Workflow execution details
    workflow_state: Optional[WorkflowStateEvent] = None  # Workflow state change events
    execution_error: Optional[str] = Field(
        default=None, description="Error type indicator. None for successful execution"
    )
    # Error handling fields (included in final chunk when last=True)
    success: Optional[bool] = Field(default=None, description="Execution success status. None until final chunk")
    agent_error: Optional[AgentErrorDetails] = Field(default=None, description="Agent-level error details")
    tool_errors: Optional[list[dict]] = Field(
        default=None, description="Tool execution errors (formatted based on detail level)"
    )


class BaseChain:
    def generate(self) -> GenerationResult:
        # To be implemented by subclasses.
        pass

    @staticmethod
    def _transform_history(history: List[ChatMessage]) -> list:
        """Convert history to list of chain-compatible messages"""
        transformed_history = []

        for item in history:
            if isinstance(item, BaseMessage):
                transformed_history.append(item)
            else:
                transformed_history.append(item.convert_to_langchain_message())

        return transformed_history


class StreamingChain(BaseChain):
    def stream(self) -> Iterator[StreamedGenerationResult]:
        # To be implemented by subclasses.
        pass
