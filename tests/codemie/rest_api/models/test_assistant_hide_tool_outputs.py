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

from codemie.rest_api.models.assistant import Assistant, AssistantRequest

MINIMAL_REQUEST = {"name": "a", "system_prompt": "p", "llm_model_type": "gpt-4o"}


def _make_assistant(**kwargs) -> Assistant:
    return Assistant(name="a", description="d", system_prompt="p", **kwargs)


def test_request_defaults_to_false_and_is_unset():
    req = AssistantRequest.model_validate(MINIMAL_REQUEST)
    assert req.hide_tool_outputs is False
    assert "hide_tool_outputs" not in req.model_fields_set


def test_omitted_field_on_update_keeps_stored_value():
    assistant = _make_assistant(hide_tool_outputs=True)
    req = AssistantRequest.model_validate(MINIMAL_REQUEST)
    assistant._map_assistant_request(req)
    assert assistant.hide_tool_outputs is True


def test_explicit_false_on_update_turns_it_off():
    assistant = _make_assistant(hide_tool_outputs=True)
    req = AssistantRequest.model_validate({**MINIMAL_REQUEST, "hide_tool_outputs": False})
    assistant._map_assistant_request(req)
    assert assistant.hide_tool_outputs is False
