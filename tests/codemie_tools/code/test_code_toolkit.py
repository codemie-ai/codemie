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

from codemie_tools.base.models import ToolSet
from codemie_tools.code.toolkit import CodeToolkit, CodeToolkitUI


class TestCodeToolkit:
    def test_get_definition(self):
        toolkit_ui = CodeToolkit.get_definition()
        assert isinstance(toolkit_ui, CodeToolkitUI)
        assert toolkit_ui.toolkit == ToolSet.CODEBASE_TOOLS
        assert len(toolkit_ui.tools) == 1
        assert toolkit_ui.label == ToolSet.CODEBASE_TOOLS.value


class TestCodeToolkitUI:
    def test_toolkit_property(self):
        toolkit_ui = CodeToolkitUI()
        assert toolkit_ui.toolkit == ToolSet.CODEBASE_TOOLS

    def test_tools_property(self):
        toolkit_ui = CodeToolkitUI()
        assert len(toolkit_ui.tools) == 1

        # Check that the tool is correctly defined
        tool_names = [tool.name for tool in toolkit_ui.tools]
        assert "Sonar" in tool_names
