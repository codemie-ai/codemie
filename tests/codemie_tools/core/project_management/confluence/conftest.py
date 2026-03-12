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

from unittest.mock import MagicMock

import pytest

from codemie_tools.core.project_management.confluence.models import ConfluenceConfig


@pytest.fixture
def confluence_config():
    return ConfluenceConfig(url="https://confluence.example.com", token="abc123", username="testuser", cloud=False)


@pytest.fixture
def mock_confluence_response():
    response = MagicMock()
    response.status_code = 200
    response.reason = "OK"
    response.text = '{"id": "12345", "title": "Test Page", "body": {"storage": {"value": "<p>Test content</p>"}}}'
    return response


@pytest.fixture
def mock_confluence():
    confluence = MagicMock()
    confluence.url = "https://confluence.example.com"
    return confluence
