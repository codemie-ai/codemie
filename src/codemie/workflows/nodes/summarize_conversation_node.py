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

from __future__ import annotations

from typing import Any, Type

import httpx
from langchain_core.messages import HumanMessage, RemoveMessage
from langgraph.constants import END
from langgraph.types import Command
from openai import APIConnectionError, APITimeoutError, RateLimitError
from pydantic import BaseModel

from codemie.configs import logger
from codemie.core.dependecies import get_llm_by_credentials
from codemie.core.thought_queue import ThoughtQueue
from codemie.core.workflow_models import WorkflowConfig
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.workflow_execution import WorkflowExecutionService
from codemie.templates.langgraph.workflow_prompts import result_summarizer_prompt
from codemie.workflows.callbacks.base_callback import BaseCallback
from codemie.workflows.constants import MAX_TOKENS_LIMIT, MESSAGES_VARIABLE, NEXT_KEY
from codemie.workflows.memory_utils import _create_message_batches
from codemie.workflows.models import AgentMessages
from codemie.workflows.nodes.base_node import BaseNode, StateSchemaType
from codemie.workflows.utils import (
    get_messages_from_state_schema,
    prepare_messages,
    should_summarize_memory,
)

# Sentinel: summarization was needed but skipped so the graph continues without rewriting messages.
SKIP_SUMMARIZATION = object()

_TRANSIENT_LLM_ERRORS = (
    ConnectionError,
    TimeoutError,
    httpx.TimeoutException,
    httpx.RequestError,
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
)


class SummarizeConversationNodeConfigSchema(BaseModel):
    """Configuration schema for SummarizeConversationNode (no config parameters)."""

    pass


def _safe_get_state(schema: Any, key: str, default=None):
    """State schema can be dict-like; keep access defensive."""
    try:
        return schema.get(key, default)
    except Exception:
        return default


def _get_custom_node_model_from_workflow_config(
    workflow_config: WorkflowConfig | None,
    state_schema: Any,
    *,
    node_id: str | None = None,
) -> str | None:
    """
    Resolve the model configured for a custom node from workflow_config.custom_nodes.

    IMPORTANT:
    - In UI the "State ID" is typically the graph node id (e.g. "custom_1").
      That value is available as `self.node_name` in nodes.
    - Some runtimes don't store `custom_node_id` in state, so relying only on state_schema
      often returns None. Therefore we prefer explicit node_id first.
    """
    if not workflow_config:
        return None

    # Prefer explicit node_id (graph node / state id, e.g. "custom_1")
    custom_node_id = node_id or (
        _safe_get_state(state_schema, "custom_node_id")
        or _safe_get_state(state_schema, "customNodeId")
        or _safe_get_state(state_schema, "custom_node")
        or _safe_get_state(state_schema, "customNode")
    )
    if not custom_node_id:
        return None

    custom_nodes = getattr(workflow_config, "custom_nodes", None)
    if not custom_nodes:
        return None

    # Support list[object] and list[dict]
    for cn in custom_nodes:
        try:
            cn_id = getattr(cn, "id", None) if not isinstance(cn, dict) else cn.get("id")
            if cn_id != custom_node_id:
                continue
            cn_model = getattr(cn, "model", None) if not isinstance(cn, dict) else cn.get("model")
            return cn_model
        except Exception:
            continue

    return None


class SummarizeConversationNode(BaseNode[AgentMessages]):
    """
    This node may or may not be used depending on your workflow wiring.
    Keep it correct & safe.
    """

    config_schema = SummarizeConversationNodeConfigSchema

    def __init__(
        self,
        callbacks: list[BaseCallback],
        workflow_execution_service: WorkflowExecutionService,
        thought_queue: ThoughtQueue,
        workflow_config: WorkflowConfig,
        *args,
        **kwargs,
    ):
        super().__init__(
            callbacks,
            workflow_execution_service,
            thought_queue,
            *args,
            workflow_config=workflow_config,
            **kwargs,
        )
        self.request_id = workflow_execution_service.workflow_execution_id

    def execute(self, state_schema: AgentMessages, execution_context: dict):
        messages = get_messages_from_state_schema(state_schema=state_schema)

        requested_model = None
        resolved_model = None
        actual_model = None

        if self.workflow_config:
            # Use the project default when no workflow model is configured.
            requested_model = getattr(self.workflow_config, "default_model", None)
            resolved_model, _ = self.resolve_execution_model(requested_model, fallback_model="gpt-3.5-turbo")
            actual_model = resolved_model

        logger.info(
            f"summarize_model_selected "
            f"node={self.node_name} "
            f"execution_id={self.execution_id} "
            f"requested_model={requested_model} "
            f"resolved_model={resolved_model}"
        )

        llm = get_llm_by_credentials(
            request_id=self.request_id, llm_model=actual_model or llm_service.default_llm_model
        )
        response = llm.invoke(messages + [HumanMessage(content=result_summarizer_prompt)])
        return response.content

    def get_task(self, state_schema: AgentMessages, *arg, **kwargs):
        return "Summarizing workflow conversation because it's too long"


class SummarizeConversationCommandNode(BaseNode[AgentMessages]):
    def __init__(
        self,
        callbacks: list[BaseCallback],
        workflow_execution_service: WorkflowExecutionService,
        thought_queue: ThoughtQueue,
        workflow_config: WorkflowConfig,
        *args,
        **kwargs,
    ):
        super().__init__(
            callbacks,
            workflow_execution_service,
            thought_queue,
            *args,
            workflow_config=workflow_config,
            **kwargs,
        )
        self.request_id = workflow_execution_service.workflow_execution_id

    @staticmethod
    def _model_from_execution_context(execution_context: dict) -> str | None:
        """Try to find a model override stored on the execution context (some runtimes store it there)."""
        if not isinstance(execution_context, dict):
            return None
        for key in ("model", "llm_model", "node_model"):
            value = execution_context.get(key)
            if value:
                return value
        node_obj = execution_context.get("node") or execution_context.get("custom_node")
        if node_obj is not None:
            return getattr(node_obj, "model", None)
        return None

    def _assistant_model(self) -> str | None:
        """Fallback to the first configured assistant's model (previous behavior)."""
        if self.workflow_config and getattr(self.workflow_config, "assistants", None):
            return getattr(self.workflow_config.assistants[0], "model", None)
        return None

    def _default_model(self) -> str | None:
        """Fallback to the workflow/project default model."""
        return getattr(self.workflow_config, "default_model", None) if self.workflow_config else None

    def _determine_requested_model(self, execution_context: dict, state_schema: AgentMessages) -> str | None:
        """Determine the requested model, preferring node/context overrides over config fallbacks."""
        # 1) Prefer model set on this node instance (if runtime populates it)
        requested_model = getattr(self, "model", None)

        # 2) Try execution_context (some runtimes store it there)
        requested_model = requested_model or self._model_from_execution_context(execution_context)

        # 3) Resolve from workflow_config.custom_nodes by graph node id (State ID)
        # This is the important fix: UI "State ID" is typically exactly self.node_name ("custom_1").
        custom_node_model = _get_custom_node_model_from_workflow_config(
            self.workflow_config,
            state_schema,
            node_id=self.node_name,
        )
        requested_model = requested_model or custom_node_model

        # 4) Fallback to assistant model (previous behavior)
        assistant_model = self._assistant_model()
        requested_model = requested_model or assistant_model

        # 5) Fallback to workflow/project default
        default_model = self._default_model()
        requested_model = requested_model or default_model

        logger.info(
            f"summarize_model_selected "
            f"node={self.node_name} "
            f"execution_id={self.execution_id} "
            f"node_model_attr={getattr(self, 'model', None)} "
            f"custom_node_model={custom_node_model} "
            f"assistant_model={assistant_model} "
            f"default_model={default_model} "
            f"requested_model={requested_model}"
        )
        return requested_model

    def _resolve_model_for_summarization(self, requested_model: str | None) -> str | None:
        """Resolve the requested model against project availability."""
        resolved_model = None
        if requested_model:
            resolved_model, _ = self.resolve_execution_model(requested_model)

        logger.info(
            f"summarize_model_resolved "
            f"node={self.node_name} "
            f"execution_id={self.execution_id} "
            f"requested_model={requested_model} "
            f"resolved_model={resolved_model}"
        )
        # If still nothing resolved, keep resolved_model=None and let get_llm_by_credentials decide defaults.
        return resolved_model

    def _summarize_in_batches(self, messages: list, resolved_model: str | None) -> str:
        """Summarize an over-limit conversation as per-batch summaries, then combine them."""
        messages_to_process = messages[1:]
        message_batches = _create_message_batches(messages=messages_to_process, max_tokens=MAX_TOKENS_LIMIT)

        logger.info(
            f"llm_call "
            f"node={self.node_name} "
            f"execution_id={self.execution_id} "
            f"request_id={self.request_id} "
            f"llm_model={resolved_model} "
            f"mode=batch "
            f"batches={len(message_batches)} "
            f"max_tokens_batch={MAX_TOKENS_LIMIT} "
            f"added_prompt=result_summarizer_prompt"
        )

        batch_summaries = []
        llm = get_llm_by_credentials(
            request_id=self.request_id, llm_model=resolved_model or llm_service.default_llm_model
        )
        for batch in message_batches:
            response = llm.invoke(batch + [HumanMessage(content=result_summarizer_prompt)])
            batch_summaries.append(str(response.content))

        result = "\n\nCombined Summary:\n" + "\n".join(batch_summaries)
        logger.info(
            f"llm_call_result "
            f"node={self.node_name} "
            f"execution_id={self.execution_id} "
            f"request_id={self.request_id} "
            f"llm_model={resolved_model} "
            f"mode=batch "
            f"output_len={len(result)}"
        )
        return result

    def _summarize_single(self, messages: list, resolved_model: str | None) -> str:
        """Summarize a conversation that fits within the token limit in a single LLM call."""
        logger.info(
            f"llm_call "
            f"node={self.node_name} "
            f"execution_id={self.execution_id} "
            f"request_id={self.request_id} "
            f"llm_model={resolved_model} "
            f"mode=single "
            f"messages_count={len(messages)} "
            f"added_prompt=result_summarizer_prompt"
        )
        llm = get_llm_by_credentials(
            request_id=self.request_id, llm_model=resolved_model or llm_service.default_llm_model
        )
        response = llm.invoke(messages + [HumanMessage(content=result_summarizer_prompt)])
        result = str(response.content)

        logger.info(
            f"llm_invoke_result "
            f"node=summarize_conversation_command "
            f"execution_id={self.execution_id} "
            f"request_id={self.request_id} "
            f"llm_model={resolved_model} "
            f"mode=single "
            f"output_len={len(result)}"
        )
        return result

    def execute(self, state_schema: AgentMessages, execution_context: dict):
        messages = get_messages_from_state_schema(state_schema=state_schema)
        total_tokens, should_summarize = should_summarize_memory(self.workflow_config, messages)

        if not should_summarize:
            return None

        logger.info(
            f"llm_call_prepare "
            f"node={self.node_name} "
            f"execution_id={self.execution_id} "
            f"request_id={self.request_id} "
            f"total_tokens={total_tokens} "
            f"should_summarize={should_summarize} "
            f"messages_count={len(messages)} "
            f"next={_safe_get_state(state_schema, NEXT_KEY)}"
        )
        logger.info(
            f"Summarizing workflow conversation because it's too long, next: {_safe_get_state(state_schema, NEXT_KEY)}"
        )

        try:
            requested_model = self._determine_requested_model(execution_context, state_schema)
            resolved_model = self._resolve_model_for_summarization(requested_model)

            if total_tokens > MAX_TOKENS_LIMIT:
                return self._summarize_in_batches(messages, resolved_model)

            return self._summarize_single(messages, resolved_model)

        except _TRANSIENT_LLM_ERRORS as transient:
            logger.warning(f"Summarization skipped due to transient error: {transient}")
            return SKIP_SUMMARIZATION

    def get_task(self, *arg, **kwargs):
        return "Summarizing workflow conversation because it's too long"

    def post_process_output(self, state_schema: Type[StateSchemaType], task, output) -> str:
        """Preserve markdown formatting; return JSON 'null' when no summarization happened."""
        if output is None or output is SKIP_SUMMARIZATION:
            return "null"
        return str(output)

    def finalize_and_update_state(
        self, raw_output: Any, processed_output: str, success: bool, state_schema: Type[StateSchemaType]
    ) -> Command | None:
        if raw_output is SKIP_SUMMARIZATION:
            next_node_list = _safe_get_state(state_schema, NEXT_KEY) or []
            next_node = next_node_list[-1] if next_node_list else END
            logger.warning(f"Summarization skipped due to transient error; continuing to next node: {next_node}")
            return Command(goto=next_node)

        if not raw_output:
            return Command(goto=END)

        messages = get_messages_from_state_schema(state_schema=state_schema)
        updated_messages = [messages[0]]
        updated_messages.extend([RemoveMessage(id=m.id) for m in messages[1:]])

        next_nodes = _safe_get_state(state_schema, NEXT_KEY) or []
        next_node = next_nodes[-1] if next_nodes else END

        logger.debug(
            f"Summarizing workflow conversation redirects to next node: {next_node}, "
            f"raw_output_len={len(str(raw_output))}"
        )
        update_value = {MESSAGES_VARIABLE: updated_messages + prepare_messages([str(raw_output)], True)}
        return Command(goto=next_node, update=update_value)
