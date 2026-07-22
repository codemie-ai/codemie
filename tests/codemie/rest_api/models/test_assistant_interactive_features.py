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

from codemie.core.interactive import InteractiveFeaturesConfig
from codemie.rest_api.models.assistant import AssistantBase, AssistantRequest


def test_assistant_request_accepts_interactive_features():
    req = AssistantRequest(
        name="a", system_prompt="prompt", llm_model_type="gpt-4o", interactive_features={"choice": True}
    )
    assert isinstance(req.interactive_features, InteractiveFeaturesConfig)
    assert req.interactive_features.choice is True
    assert req.interactive_features.action_buttons is False


def test_assistant_request_defaults_to_none():
    assert AssistantRequest(name="a", system_prompt="prompt", llm_model_type="gpt-4o").interactive_features is None


def test_assistant_base_declares_interactive_features_column():
    field = AssistantBase.model_fields["interactive_features"]
    assert field.default is None
