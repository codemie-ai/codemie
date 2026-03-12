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

import uuid

from codemie.chains.base import StreamedGenerationResult, Thought, ThoughtAuthorType
from codemie.core.utils import extract_text_from_llm_output
from codemie.workflows.callbacks.base_callback import BaseCallback


class LanggraphNodeCallback(BaseCallback):
    def __init__(self, gen, author=ThoughtAuthorType.Tool):
        super().__init__()
        self.gen = gen
        self.author = author
        self._thoughts = {}
        # Use WorkflowState author type for workflow state-level thoughts
        # This allows frontend to distinguish state thoughts from nested tool/agent thoughts
        self.state_author_type = "WorkflowState"

    def _escape_message(self, message: str) -> str:
        """Replace '}{', with '}{\u2002' so frontend can split it properly"""
        text = extract_text_from_llm_output(message)
        return text.replace("}{", "}_{")

    def set_current_thought(self, state_id: str, agent_name: str = ''):
        if state_id in self._thoughts:
            current_thought = self._thoughts[state_id]
            current_thought.in_progress = False
            self.gen.send(
                StreamedGenerationResult(
                    thought=current_thought,
                    context=self.get_thread_context(state_id),
                ).model_dump_json()
            )

        agent_name = agent_name.replace('_', ' ').title()
        # Use WorkflowState author type for state-level thoughts (no parent_id)
        # Nested tool/agent thoughts will have parent_id pointing to this state thought
        thought = Thought(
            id=str(uuid.uuid4()),
            author_name=agent_name,
            author_type=self.state_author_type,  # Mark as WorkflowState
            in_progress=True,
        )
        self._thoughts[state_id] = thought

    def reset_current_thought(self, state_id: str):
        if state_id in self._thoughts:
            del self._thoughts[state_id]

    def set_agent_context(self, state_id: str, agent=None):
        if agent:
            agent.set_thread_context(
                context=self.get_thread_context(state_id),
                parent_thought_id=self._thoughts[state_id].id,
            )

    def get_thread_context(self, state_id: str):
        return {
            "execution_state_id": state_id,
        }

    def on_node_start(self, state_id: str, node_name, task, execution_context=None):
        if execution_context is None:
            execution_context = {}

        self.set_current_thought(state_id, node_name)
        self.set_agent_context(state_id, execution_context.get("assistant"))
        current_thought = self._thoughts[state_id]
        current_thought.input_text = task
        current_thought.message = ""
        self.gen.send(
            StreamedGenerationResult(
                thought=current_thought,
                context=self.get_thread_context(state_id),
            ).model_dump_json()
        )

    def on_node_end(self, output: str, *args, **kwargs):
        state_id = kwargs.get('execution_state_id')
        message = f"{output} \n\n"
        if state_id in self._thoughts:
            current_thought = self._thoughts[state_id]
            current_thought.message = self._escape_message(message)
            current_thought.in_progress = False

            self.gen.send(
                StreamedGenerationResult(
                    thought=current_thought,
                    context=self.get_thread_context(state_id),
                ).model_dump_json()
            )

            self.reset_current_thought(state_id)
