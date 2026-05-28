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

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def _extract_image_blocks(artifact: object) -> list[dict]:
    if not isinstance(artifact, list):
        return []
    return [
        {
            "type": "image",
            "source_type": "base64",
            "data": item["data"],
            "mime_type": item["mime_type"],
        }
        for item in artifact
        if isinstance(item, dict) and "data" in item and "mime_type" in item
    ]


def _image_artifact_pre_model_hook(state: dict) -> dict:
    messages = state.get("messages", [])
    if not messages:
        return {"llm_input_messages": messages}

    image_blocks: list[dict] = []
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            break
        if isinstance(msg, ToolMessage) and getattr(msg, "artifact", None):
            image_blocks.extend(_extract_image_blocks(msg.artifact))

    if not image_blocks:
        return {"llm_input_messages": messages}

    injected = HumanMessage(
        content=[
            {"type": "text", "text": "[Attached images from the tool response above]"},
            *image_blocks,
        ]
    )
    return {"llm_input_messages": [*messages, injected]}


def _compose_pre_model_hooks(*hooks) -> Any | None:
    active_hooks = [hook for hook in hooks if hook is not None]
    if not active_hooks:
        return None

    def composed_pre_model_hook(state: dict[str, Any]) -> dict[str, Any]:
        working_state = dict(state)
        combined_updates: dict[str, Any] = {}

        for hook in active_hooks:
            hook_result = hook(working_state)
            if not hook_result:
                continue

            combined_updates.update(hook_result)

            if "llm_input_messages" in hook_result:
                llm_input_messages = hook_result["llm_input_messages"]
                working_state["messages"] = llm_input_messages
                working_state["llm_input_messages"] = llm_input_messages
            else:
                working_state.update(hook_result)

        return combined_updates

    return composed_pre_model_hook
