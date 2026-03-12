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

from codemie_tools.cloud.kubernetes.models import KubernetesConfig
from codemie_tools.cloud.kubernetes.tools_vars import KUBERNETES_TOOL


class TestKubernetesToolMetadata:
    def test_tool_name(self):
        """Test that Kubernetes tool has correct name."""
        assert KUBERNETES_TOOL.name == "Kubernetes"

    def test_tool_description(self):
        """Test that Kubernetes tool has description."""
        assert KUBERNETES_TOOL.description is not None
        assert len(KUBERNETES_TOOL.description) > 0
        assert "kubernetes" in KUBERNETES_TOOL.description.lower()

    def test_tool_label(self):
        """Test that Kubernetes tool has correct label."""
        assert KUBERNETES_TOOL.label == "Kubernetes"

    def test_tool_user_description(self):
        """Test that Kubernetes tool has user description."""
        assert KUBERNETES_TOOL.user_description is not None
        assert len(KUBERNETES_TOOL.user_description) > 0

    def test_tool_settings_config(self):
        """Test that Kubernetes tool requires settings configuration."""
        assert KUBERNETES_TOOL.settings_config is True

    def test_tool_config_class(self):
        """Test that Kubernetes tool has correct config class."""
        assert KUBERNETES_TOOL.config_class == KubernetesConfig
