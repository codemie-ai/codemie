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

from codemie_tools.core.vcs.gitlab.models import GitlabConfig
from codemie_tools.core.vcs.gitlab.tools import GitlabTool


@pytest.fixture
def gitlab_config():
    """Create a GitlabConfig instance for testing."""
    return GitlabConfig(url="https://gitlab.example.com", token="test_token")


@pytest.fixture
def gitlab_tool(gitlab_config):
    """Create a GitlabTool instance for testing."""
    return GitlabTool(config=gitlab_config)


@pytest.fixture
def mock_response():
    """Create a mock response object."""
    mock = Mock()
    mock.status_code = 200
    mock.reason = "OK"
    mock.text = '{"id": 1, "name": "test"}'
    return mock


@pytest.fixture
def mock_error_response():
    """Create a mock error response object."""
    mock = Mock()
    mock.status_code = 404
    mock.reason = "Not Found"
    mock.text = '{"message": "Resource not found"}'
    return mock
