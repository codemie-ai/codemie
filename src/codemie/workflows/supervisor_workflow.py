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

from typing import Optional
from langgraph.graph import StateGraph

from codemie.configs import logger
from codemie.core.workflow_models import WorkflowConfig, WorkflowState, WorkflowNextState
from codemie.rest_api.security.user import User
from codemie.core.thought_queue import ThoughtQueue
from codemie.workflows.constants import (
    SUMMARIZE_MEMORY_NODE,
    NEXT_KEY,
    RESULT_FINALIZER_NODE,
    SUPERVISOR_NODE,
)
from codemie.workflows.models import SupervisorAgentMessages
from codemie.workflows.nodes.agent_node import AgentNode
from codemie.workflows.nodes.result_finalizer_node import ResultFinalizerNode
from codemie.workflows.nodes.summarize_conversation_node import SummarizeConversationNode
from codemie.workflows.nodes.supervisor_node import SupervisorNode
from codemie.workflows.workflow import WorkflowExecutor


class SupervisorWorkflowExecutor(WorkflowExecutor):
    def __init__(
        self,
        workflow_config: WorkflowConfig,
        user_input: str,
        user: User,
        thought_queue: ThoughtQueue = None,
        resume_execution: bool = False,
        execution_id: str = None,
        file_name: Optional[str] = None,
        request_headers: dict[str, str] | None = None,
        session_id: Optional[str] = None,
        disable_cache: Optional[bool] = False,
        tags: Optional[list[str]] = None,
        delete_on_completion: bool = False,
    ):
        super().__init__(
            workflow_config=workflow_config,
            user_input=user_input,
            user=user,
            thought_queue=thought_queue,
            resume_execution=resume_execution,
            execution_id=execution_id,
            file_name=file_name,
            request_headers=request_headers,
            session_id=session_id,
            disable_cache=disable_cache,
            tags=tags,
            delete_on_completion=delete_on_completion,
        )

    def init_state_graph(self) -> StateGraph:
        return StateGraph(SupervisorAgentMessages)

    def build_workflow(self, workflow: StateGraph):
        team_details = ""
        members = []

        if self.workflow_config.enable_summarization_node:
            workflow.add_node(
                RESULT_FINALIZER_NODE,
                ResultFinalizerNode(
                    self.callbacks,
                    self.workflow_execution_service,
                    self.thought_queue,
                    workflow_config=self.workflow_config,
                ),
            )
        workflow.add_node(
            SUMMARIZE_MEMORY_NODE,
            SummarizeConversationNode(self.callbacks, self.workflow_execution_service, self.thought_queue),
        )

        for assistant_config in self.workflow_config.assistants:
            logger.info(
                f"Initialize workflow node. "
                f"WorkflowId={self.workflow_config.id}. "
                f"WorkflowName={self.workflow_config.name}. "
                f"Node={assistant_config.id}"
            )
            state = WorkflowState(
                id=assistant_config.id, assistant_id=assistant_config.id, next=WorkflowNextState(state_id=""), task=""
            )
            assistant = self.initialize_assistant(assistant=assistant_config)
            assistant_name = assistant.agent_name.replace(" ", "")
            team_details = f"\n {team_details} \n{assistant_name}: {assistant.description}"
            members.append(assistant_name)
            workflow.add_node(
                assistant_name,
                AgentNode(
                    callbacks=self.callbacks,
                    workflow_execution_service=self.workflow_execution_service,
                    thought_queue=self.thought_queue,
                    node_name=assistant_name,
                    summarize_history=True,
                    workflow_state=state,
                    current_task_key=None,
                    workflow_config=self.workflow_config,
                    assistant=assistant,
                    user_input=self.user_input,
                    user=self.user,
                    resume_execution=self.resume_execution,
                    execution_id=self.execution_id,
                    request_headers=self.request_headers,
                    disable_cache=self.disable_cache,
                ),
            )
            workflow.add_edge(assistant_name, SUPERVISOR_NODE)

        workflow.add_node(
            SUPERVISOR_NODE,
            SupervisorNode(
                self.callbacks,
                self.workflow_execution_service,
                self.thought_queue,
                members=members,
                team_details=team_details,
                execution_id=self.execution_id,
                messages_limit_before_summarization=self.workflow_config.messages_limit_before_summarization,
                tokens_limit_before_summarization=self.workflow_config.tokens_limit_before_summarization,
                enable_summarization_node=self.workflow_config.enable_summarization_node,
            ),
        )

        # Add transitions
        full_members_map = {k: k for k in members}
        workflow.add_conditional_edges(SUMMARIZE_MEMORY_NODE, lambda x: x[NEXT_KEY], full_members_map)

    def get_workflow_entry_point(self) -> str:
        return SUPERVISOR_NODE
