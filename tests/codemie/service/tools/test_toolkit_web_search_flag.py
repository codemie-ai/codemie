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

from unittest.mock import Mock

import pytest
from codemie_tools.base.models import ToolSet

from codemie.configs.component_resolution import clear_snapshot, publish_snapshot
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.security.user import User
from codemie.service.tools.toolkit_service import ToolkitService


@pytest.fixture(autouse=True)
def reset_snapshot():
    clear_snapshot()
    yield
    clear_snapshot()


@pytest.fixture
def request_with_web_search():
    request = Mock(spec=AssistantChatRequest)
    request.enable_web_search = True
    request.enable_code_interpreter = None
    request.enable_image_generation = None
    request.image_generation_model = None
    request.tools_config = []
    return request


@pytest.fixture
def user():
    mock_user = Mock(spec=User)
    mock_user.id = "test-user-id"
    mock_user.is_admin = False
    return mock_user


@pytest.fixture
def assistant():
    mock_assistant = Mock()
    mock_assistant.name = "test-assistant"
    return mock_assistant


def _augment(request, assistant, user):
    return ToolkitService._augment_toolkits_with_feature_flags([], request, assistant, user, "gpt-4", "test-uuid")


def test_research_toolkit_is_added_when_the_flag_is_on(request_with_web_search, assistant, user):
    toolkits = _augment(request_with_web_search, assistant, user)
    assert ToolSet.RESEARCH in {tk.toolkit for tk in toolkits}


def test_the_override_removes_the_research_toolkit(request_with_web_search, assistant, user):
    publish_snapshot({"features:webSearch": {"enabled": False}})
    toolkits = _augment(request_with_web_search, assistant, user)
    assert ToolSet.RESEARCH not in {tk.toolkit for tk in toolkits}


def test_declaration_exists_for_the_pilot_flag():
    from codemie.service.customer_config_declarations import by_component_id

    declaration = by_component_id("features:webSearch")
    assert declaration is not None
    assert {field.name for field in declaration.fields} == {"enabled"}
