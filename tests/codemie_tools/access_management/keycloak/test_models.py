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

from codemie_tools.access_management.keycloak.models import KeycloakConfig


def test_keycloak_base_url_placeholder_default():
    schema = KeycloakConfig.model_json_schema()
    assert (
        schema["properties"]["base_url"]["placeholder"] == 'Keycloak Base URL, e.g. "https://keycloak.example.com/auth"'
    )


def test_keycloak_base_url_default_url_override(monkeypatch):
    from codemie.configs.customer_config import customer_config

    monkeypatch.setattr(customer_config, "tool_defaults", {"keycloak": {"base_url": "https://keycloak.company.com"}})
    assert customer_config.get_tool_default("keycloak", "base_url") == "https://keycloak.company.com"


def test_base_url_default_wired_to_tool_default():
    from codemie_tools.access_management.keycloak.models import KeycloakConfig
    from codemie.configs.customer_config import customer_config

    expected = customer_config.get_tool_default(KeycloakConfig.TOOL_NAME, "base_url") or ""
    assert KeycloakConfig.model_fields["base_url"].default == expected
