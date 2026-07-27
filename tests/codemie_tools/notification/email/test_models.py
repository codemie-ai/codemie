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

from codemie_tools.notification.email.models import EmailToolConfig


def test_email_smtp_url_placeholder_default():
    schema = EmailToolConfig.model_json_schema()
    assert schema["properties"]["url"]["placeholder"] == "SMTP Server URL (e.g. smtp.office365.com:587)"


def test_email_oauth_authority_override(monkeypatch):
    from codemie.configs.customer_config import customer_config

    monkeypatch.setattr(
        customer_config,
        "tool_defaults",
        {"email": {"oauth_authority": "https://login.microsoftonline.cn"}},
    )
    assert customer_config.get_tool_default("email", "oauth_authority") == "https://login.microsoftonline.cn"


def test_email_oauth_scope_override(monkeypatch):
    from codemie.configs.customer_config import customer_config

    monkeypatch.setattr(
        customer_config,
        "tool_defaults",
        {"email": {"oauth_scope": "https://partner.outlook.cn/.default"}},
    )
    assert customer_config.get_tool_default("email", "oauth_scope") == "https://partner.outlook.cn/.default"


def test_email_auth_type_default_basic():
    from codemie_tools.notification.email.models import EmailAuthType

    config = EmailToolConfig()
    assert config.auth_type == EmailAuthType.BASIC


def test_email_auth_type_tool_default_override(monkeypatch):
    from codemie.configs.customer_config import customer_config

    monkeypatch.setattr(customer_config, "tool_defaults", {"email": {"auth_type": "oauth_azure"}})
    assert customer_config.get_tool_default("email", "auth_type") == "oauth_azure"
