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

import pytest

from codemie_tools.notification.email.models import (
    DEFAULT_OAUTH_AUTHORITY,
    EmailToolConfig,
    resolve_oauth_authority,
)


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


def test_default_oauth_authority_constant_has_no_trailing_slash():
    assert DEFAULT_OAUTH_AUTHORITY == "https://login.microsoftonline.com"


@pytest.mark.parametrize("blank", ["", None])
def test_resolve_oauth_authority_blank_returns_builtin_default(monkeypatch, blank):
    from codemie.configs.customer_config import customer_config

    monkeypatch.setattr(customer_config, "tool_defaults", {})
    assert resolve_oauth_authority(blank) == DEFAULT_OAUTH_AUTHORITY


def test_resolve_oauth_authority_no_arg_returns_builtin_default(monkeypatch):
    from codemie.configs.customer_config import customer_config

    monkeypatch.setattr(customer_config, "tool_defaults", {})
    assert resolve_oauth_authority() == DEFAULT_OAUTH_AUTHORITY


@pytest.mark.parametrize("explicit", ["https://login.microsoftonline.cn", "https://login.microsoftonline.us"])
def test_resolve_oauth_authority_explicit_value_returned_unchanged(monkeypatch, explicit):
    from codemie.configs.customer_config import customer_config

    monkeypatch.setattr(customer_config, "tool_defaults", {})
    assert resolve_oauth_authority(explicit) == explicit


@pytest.mark.parametrize("blank", ["", None])
def test_resolve_oauth_authority_blank_uses_customer_override(monkeypatch, blank):
    from codemie.configs.customer_config import customer_config

    monkeypatch.setattr(
        customer_config,
        "tool_defaults",
        {"email": {"oauth_authority": "https://login.microsoftonline.cn"}},
    )
    assert resolve_oauth_authority(blank) == "https://login.microsoftonline.cn"
    assert resolve_oauth_authority() == "https://login.microsoftonline.cn"


def test_resolve_oauth_authority_explicit_beats_customer_override(monkeypatch):
    from codemie.configs.customer_config import customer_config

    monkeypatch.setattr(
        customer_config,
        "tool_defaults",
        {"email": {"oauth_authority": "https://login.microsoftonline.cn"}},
    )
    assert resolve_oauth_authority("https://login.microsoftonline.com") == "https://login.microsoftonline.com"


def test_email_config_oauth_authority_defaults_for_missing_key():
    # Import-time default: relies on the shipped customer config having no email authority override.
    assert EmailToolConfig().oauth_authority == DEFAULT_OAUTH_AUTHORITY
