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

from typing import Any, Optional, Dict, List
from uuid import UUID

from langchain_core.callbacks import StreamingStdOutCallbackHandler
from langchain_core.messages import BaseMessage

from codemie.core.utils import calculate_tokens
from codemie.service.monitoring.agent_monitoring_service import AgentMonitoringService


class MonitoringCallback(StreamingStdOutCallbackHandler):
    tools_run_map = {}

    def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        inputs: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        self.tools_run_map[str(run_id)] = {
            "metadata": metadata,
            "name": serialized["name"],
            "base_tool_name": serialized["name"],
        }

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        current_run = self.tools_run_map.get(str(run_id), {})
        if current_run:
            AgentMonitoringService.send_tool_metrics(
                tool_name=current_run["name"],
                tool_metadata=current_run["metadata"],
                output_tokens_used=calculate_tokens(output),
                success=True,
                additional_attributes={
                    "base_tool_name": current_run["base_tool_name"],
                },
            )

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        current_run = self.tools_run_map.get(str(run_id), {})
        if current_run:
            AgentMonitoringService.send_tool_metrics(
                tool_name=current_run["name"],
                tool_metadata=current_run["metadata"],
                output_tokens_used=calculate_tokens(str(error)),
                success=False,
                additional_attributes={
                    "base_tool_name": current_run["base_tool_name"],
                    "error_class": error.__class__.__name__,
                },
            )

    def on_chain_end(
        self,
        outputs: Dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        current_run = self.tools_run_map.get(str(run_id), {})
        if current_run:
            current_run["output_tokens_used"] = calculate_tokens(str(outputs))
            self.tools_run_map.pop(str(run_id))
            self.tools_run_map[str(run_id)] = current_run

    def on_chat_model_start(self, serialized: dict[str, Any], messages: list[list[BaseMessage]], **kwargs: Any) -> None:
        """Run when LLM starts running.

        Args:
            serialized (Dict[str, Any]): The serialized LLM.
            messages (List[List[BaseMessage]]): The messages to run.
            **kwargs (Any): Additional keyword arguments.
        """
