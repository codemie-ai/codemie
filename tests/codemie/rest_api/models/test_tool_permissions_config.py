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

from codemie.core.models import ToolCallPolicy
from codemie.rest_api.models.assistant import ToolPermissionsConfig, AssistantBase, AssistantRequest


def test_tool_permissions_config_defaults():
    cfg = ToolPermissionsConfig()
    assert cfg.tool_call_policy == ToolCallPolicy.AUTO_APPROVE


def test_tool_permissions_config_explicit():
    cfg = ToolPermissionsConfig(tool_call_policy=ToolCallPolicy.ASK_FOR_APPROVAL)
    assert cfg.tool_call_policy == ToolCallPolicy.ASK_FOR_APPROVAL


def test_assistant_base_has_tool_permissions_field():
    assert hasattr(AssistantBase, 'model_fields')
    assert 'tool_permissions' in AssistantBase.model_fields


def test_assistant_request_has_tool_permissions_field():
    assert 'tool_permissions' in AssistantRequest.model_fields
