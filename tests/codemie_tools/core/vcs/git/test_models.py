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

from codemie_tools.core.vcs.git.models import GenericGitConfig


class TestGenericGitConfig:
    def test_valid_config(self):
        config = GenericGitConfig(url="https://git.example.com", token="test_token")
        assert config.url == "https://git.example.com"
        assert config.token == "test_token"

    def test_auth_type_explicit(self):
        config = GenericGitConfig(url="https://git.example.com", token="tok", auth_type="oauth")
        assert config.auth_type == "oauth"

    def test_url_placeholder_from_tool_defaults(self, monkeypatch):
        from codemie.configs.customer_config import customer_config

        monkeypatch.setattr(customer_config, "tool_defaults", {"git": {"url_placeholder": "MY_GIT_URL"}})
        assert customer_config.get_tool_default("git", "url_placeholder") == "MY_GIT_URL"

    def test_url_default_from_tool_defaults(self, monkeypatch):
        from codemie.configs.customer_config import customer_config

        monkeypatch.setattr(customer_config, "tool_defaults", {"git": {"url": "https://git.company.com"}})
        assert customer_config.get_tool_default("git", "url") == "https://git.company.com"

    def test_auth_type_from_tool_defaults(self, monkeypatch):
        from codemie.configs.customer_config import customer_config

        monkeypatch.setattr(customer_config, "tool_defaults", {"git": {"auth_type": "oauth"}})
        assert customer_config.get_tool_default("git", "auth_type") == "oauth"


def test_url_default_wired_to_tool_default():
    from codemie_tools.core.vcs.git.models import GenericGitConfig
    from codemie.configs.customer_config import customer_config

    expected = customer_config.get_tool_default(GenericGitConfig.TOOL_NAME, "url") or ""
    assert GenericGitConfig.model_fields["url"].default == expected
