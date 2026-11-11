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

import pytest
from pydantic import ValidationError

from codemie.core.models import AssistantChatRequest, ToolCallPolicy


class TestAssistantChatRequestSaveHistory:
    """Tests for save_history field"""

    def test_save_history_default_true(self):
        """save_history defaults to True"""
        request = AssistantChatRequest(text="Hello")
        assert request.save_history is True

    def test_save_history_explicit_true(self):
        """Can explicitly set save_history=True"""
        request = AssistantChatRequest(text="Hello", save_history=True)
        assert request.save_history is True

    def test_save_history_explicit_false(self):
        """Can set save_history=False"""
        request = AssistantChatRequest(text="Hello", save_history=False)
        assert request.save_history is False

    def test_save_history_invalid_type(self):
        """Invalid type raises ValidationError"""
        with pytest.raises(ValidationError) as exc_info:
            AssistantChatRequest(text="Hello", save_history="invalid")
        assert "save_history" in str(exc_info.value)

    def test_save_history_serialization(self):
        """save_history appears in serialized output"""
        request = AssistantChatRequest(text="Hello", save_history=False)
        data = request.model_dump()
        assert data["save_history"] is False

    def test_save_history_deserialization(self):
        """Can deserialize save_history from dict"""
        data = {"text": "Hello", "save_history": False}
        request = AssistantChatRequest(**data)
        assert request.save_history is False

    def test_save_history_camel_case_alias(self):
        """CamelCase alias works in JSON"""
        json_data = '{"text": "Hello", "saveHistory": false}'
        request = AssistantChatRequest.model_validate_json(json_data)
        assert request.save_history is False

    def test_save_history_backward_compatibility(self):
        """Omitting save_history defaults to True (backward compatible)"""
        data = {"text": "Hello", "stream": False}
        request = AssistantChatRequest(**data)
        assert request.save_history is True


class TestAssistantChatRequestToolCallPolicy:
    def test_defaults_to_none(self):
        request = AssistantChatRequest(text="Hello")
        assert request.tool_call_policy is None

    def test_explicit_ask_for_approval(self):
        request = AssistantChatRequest(text="Hello", tool_call_policy=ToolCallPolicy.ASK_FOR_APPROVAL)
        assert request.tool_call_policy == ToolCallPolicy.ASK_FOR_APPROVAL

    def test_explicit_auto_approve(self):
        request = AssistantChatRequest(text="Hello", tool_call_policy=ToolCallPolicy.AUTO_APPROVE)
        assert request.tool_call_policy == ToolCallPolicy.AUTO_APPROVE

    def test_camelcase_alias(self):
        import json

        payload = json.loads('{"text": "Hello", "toolCallPolicy": "approve_for_me"}')
        request = AssistantChatRequest.model_validate(payload)
        assert request.tool_call_policy == ToolCallPolicy.APPROVE_FOR_ME


class TestAssistantChatRequestAttributes:
    def test_defaults_to_none(self):
        assert AssistantChatRequest(text="hi").attributes is None

    def test_keeps_valid_attributes(self):
        request = AssistantChatRequest(text="hi", attributes={"com.epam.gen_ai.service.work_item.id": "ABC-123"})

        assert request.attributes == {"com.epam.gen_ai.service.work_item.id": "ABC-123"}

    @pytest.mark.parametrize(
        "attributes, message",
        [
            ({"Team": "core"}, "Invalid attribute key"),
            ({"codemie.team": "core"}, "reserved prefix"),
            ({"team": "v" * 129}, "1-128"),
            ({"team": "lone\ud800surrogate"}, "unpaired surrogates"),
        ],
    )
    def test_rejects_invalid_attributes(self, attributes, message):
        with pytest.raises(ValidationError, match=message):
            AssistantChatRequest(text="hi", attributes=attributes)
