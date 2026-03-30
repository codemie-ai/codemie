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

import json
from typing import Optional

from langchain_core.messages import HumanMessage

from codemie.core.thought_queue import ThoughtQueue
from codemie.core.workflow_models.workflow_models import CustomWorkflowNode, WorkflowState
from codemie.rest_api.models.settings import Settings
from codemie.service.aws_bedrock.bedrock_flow_service import BedrockFlowService
from codemie.service.workflow_execution import WorkflowExecutionService
from codemie.service.workflow_service import WorkflowService
from codemie.workflows.callbacks.base_callback import BaseCallback
from codemie.workflows.models import AgentMessages
from codemie.workflows.nodes.base_node import BaseNode


class BedrockFlowNode(BaseNode):
    def __init__(
        self,
        callbacks: list[BaseCallback],
        workflow_execution_service: WorkflowExecutionService,
        thought_queue: ThoughtQueue,
        workflow_state: WorkflowState,
        node_name: Optional[str] = "",
        *args,
        **kwargs,
    ):
        super().__init__(callbacks, workflow_execution_service, thought_queue, node_name, *args, **kwargs)

        custom_node: CustomWorkflowNode | None = kwargs.get("custom_node")

        if not custom_node:
            raise ValueError("Custom node configuration is required for BedrockFlowNode.")

        custom_node_config = BedrockFlowNode._get_custom_node_config(custom_node=custom_node)

        setting_id = self._get_setting_id(custom_node_config=custom_node_config)

        self.flow_id = custom_node_config["flow_id"]
        self.flow_alias_id = custom_node_config["flow_alias_id"]
        self.setting_id = setting_id
        self.input_node_name = custom_node_config["input_node_name"]
        self.input_node_output_field = custom_node_config["input_node_output_field"]
        self.input_node_output_type = custom_node_config.get("input_node_output_type")

    def execute(self, input_data: dict, execution_context):
        messages: list[HumanMessage] = input_data.get("messages", [])

        text_input = messages[0].content if messages else ""
        normalized_input = BedrockFlowNode._normalize_input(
            text_input=str(text_input),
            expected_type=self.input_node_output_type,
        )

        return BedrockFlowService.invoke_flow(
            flow_id=self.flow_id,
            flow_alias_id=self.flow_alias_id,
            user=self.workflow_execution_service.user,
            setting_id=self.setting_id,
            inputs=[
                {
                    "content": {"document": normalized_input},
                    "nodeName": self.input_node_name,
                    "nodeOutputName": self.input_node_output_field,
                }
            ],
        )

    def get_task(self, state_schema: AgentMessages, *arg, **kwargs):
        return "Triggering aws bedrock flow"

    def post_process_output(self, state_schema, task, output) -> str:
        return output.get("output", "")

    def _get_setting_id(self, custom_node_config: dict):
        setting_id = custom_node_config.get("setting_id")
        if setting_id:
            return str(setting_id)

        integration_alias = custom_node_config.get("integration_alias")
        if not integration_alias:
            raise ValueError("Either 'setting_id' or 'integration_alias' must be provided in the node configuration.")

        settings = Settings.get_by_alias(
            alias=integration_alias,
            project_name=self.workflow_execution_service.workflow_config.project,
            user_id=self.workflow_execution_service.user.id,
        )
        if not settings or not settings.id:
            raise ValueError(f"Settings with alias '{custom_node_config['integration_alias']}' not found.")

        return settings.id

    @staticmethod
    def _get_custom_node_config(custom_node: CustomWorkflowNode):
        workflow_id: Optional[str] = custom_node.config.get("workflow_id")
        if not workflow_id:
            return custom_node.config

        workflow_obj = WorkflowService().get_workflow(workflow_id)
        if not workflow_obj:
            raise ValueError(f"WorkflowConfig with id {workflow_id} not found.")

        # bedrock flows only have one node, the bedrock flow execution node
        bedrock_node = workflow_obj.custom_nodes[0] if workflow_obj.custom_nodes else None

        # the custom_node_id should match the node registry key for BedrockFlowNode
        if not bedrock_node or getattr(bedrock_node, "custom_node_id", None) != "bedrock_flow_node":
            raise ValueError("The referenced workflow does not contain a BedrockFlowNode.")

        return bedrock_node.config

    @staticmethod
    def _normalize_input(text_input: str, expected_type: Optional[str]) -> object:
        """
        Normalize raw input into the expected Bedrock Flow port type.

        Supported expected_type (case-insensitive):
          STRING, NUMBER, BOOLEAN, OBJECT, ARRAY

        Behavior:
        - If expected_type is None: return value unchanged.
        - STRING: return as str (None -> '').
        - NUMBER: accept int/float already; parse string (int, float, scientific). Reject bool & NaN/Inf.
        - BOOLEAN: accept bool; parse common truthy/falsy strings; else error.
        - ARRAY: accept list; parse JSON string list; else error.
        - OBJECT: accept dict; parse JSON string dict; else error.
        - Unknown expected_type: return value unchanged.
        """
        if expected_type is None:
            return text_input

        v = text_input.strip()
        et = expected_type.upper()

        try:
            if et == "STRING":
                return v

            elif et == "NUMBER":
                if "." in v:
                    return float(v)
                return int(v)

            elif et == "BOOLEAN":
                bool_map = {"true": True, "1": True, "yes": True, "false": False, "0": False, "no": False}
                low = v.lower()
                if low not in bool_map:
                    raise ValueError
                return bool_map[low]

            elif et == "ARRAY":
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed

                raise ValueError

            elif et == "OBJECT":
                parsed = json.loads(v)
                if isinstance(parsed, dict):
                    return parsed

                raise ValueError
        except Exception:
            raise ValueError(
                f"Error normalizing input to type '{expected_type}', please try a properly formatted input."
            )

        return v  # unknown type fallback
