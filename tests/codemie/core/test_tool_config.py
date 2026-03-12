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

from codemie.core.models import ToolConfig


def test_tool_config_with_tool_creds():
    """Test that ToolConfig can be created with tool_creds."""
    tool_config = ToolConfig(name="test_tool", tool_creds={"api_key": "test_key"})
    assert tool_config.name == "test_tool"
    assert tool_config.tool_creds == {"api_key": "test_key"}
    assert tool_config.integration_id is None


def test_tool_config_with_integration_id():
    """Test that ToolConfig can be created with integration_id."""
    tool_config = ToolConfig(name="test_tool", integration_id="test_integration_id")
    assert tool_config.name == "test_tool"
    assert tool_config.tool_creds is None
    assert tool_config.integration_id == "test_integration_id"


def test_tool_config_with_both():
    """Test that ToolConfig can be created with both tool_creds and integration_id."""
    with pytest.raises(ValidationError) as excinfo:
        ToolConfig(name="test_tool", tool_creds={"api_key": "test_key"}, integration_id="test_integration_id")
    # Check that the error message contains our custom validation message
    assert "Either tool_creds or integration_id must be provided, but not both" in str(excinfo.value)


def test_tool_config_without_either_fails():
    """Test that ToolConfig cannot be created without either tool_creds or integration_id."""
    with pytest.raises(ValidationError) as excinfo:
        ToolConfig(name="test_tool")

    # Check that the error message contains our custom validation message
    assert "Either tool_creds or integration_id must be provided" in str(excinfo.value)
