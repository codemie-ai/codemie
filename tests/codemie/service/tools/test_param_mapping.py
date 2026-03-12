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

"""
Test for map_params_to_method_signature in ToolExecutionService.
"""

from codemie.service.tools.tool_execution_service import ToolExecutionService


def test_map_params_complete():
    """Test mapping parameters with complete override."""
    # Method parameters
    method_params = {"param1": "default1", "param2": "default2", "param3": "default3"}

    # Input parameters for complete override
    input_params = {"param1": "override1", "param2": "override2", "param3": "override3"}

    # Map parameters
    result = ToolExecutionService.map_params_to_method_signature(method_params, input_params)

    # Assert all parameters were overridden
    expected = {"param1": "override1", "param2": "override2", "param3": "override3"}
    assert result == expected


def test_map_params_partial():
    """Test mapping parameters with partial override."""
    # Method parameters
    method_params = {"param1": "default1", "param2": "default2", "param3": "default3"}

    # Input parameters for partial override
    input_params = {"param1": "override1", "param3": "override3"}

    # Map parameters
    result = ToolExecutionService.map_params_to_method_signature(method_params, input_params)

    # Assert only specified parameters were overridden
    expected = {"param1": "override1", "param2": "default2", "param3": "override3"}
    assert result == expected
