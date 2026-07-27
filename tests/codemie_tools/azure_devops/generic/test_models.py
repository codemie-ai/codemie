# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

from codemie_tools.azure_devops.generic.models import GenericAzureDevOpsConfig


class TestGenericAzureDevOpsConfig:
    def test_valid_config(self):
        config = GenericAzureDevOpsConfig(url="https://dev.azure.com/myorg", token="test_token")
        assert config.url == "https://dev.azure.com/myorg"
        assert config.token == "test_token"

    def test_url_placeholder_from_tool_defaults(self, monkeypatch):
        from codemie.configs.customer_config import customer_config

        monkeypatch.setattr(
            customer_config,
            "tool_defaults",
            {"azuredevops": {"url_placeholder": "MY_ORG_URL"}},
        )
        assert customer_config.get_tool_default("azuredevops", "url_placeholder") == "MY_ORG_URL"

    def test_url_default_from_tool_defaults(self, monkeypatch):
        from codemie.configs.customer_config import customer_config

        monkeypatch.setattr(
            customer_config,
            "tool_defaults",
            {"azuredevops": {"url": "https://dev.azure.com/myorg"}},
        )
        assert customer_config.get_tool_default("azuredevops", "url") == "https://dev.azure.com/myorg"


def test_url_default_wired_to_tool_default():
    from codemie_tools.azure_devops.generic.models import GenericAzureDevOpsConfig
    from codemie.configs.customer_config import customer_config

    expected = customer_config.get_tool_default(GenericAzureDevOpsConfig.TOOL_NAME, "url") or ""
    assert GenericAzureDevOpsConfig.model_fields["url"].default == expected
