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

from codemie_tools.ms_exchange.models import ExchangeAuthType, ExchangeToolConfig

VALID_OAUTH = {
    "auth_type": ExchangeAuthType.OAUTH_AZURE,
    "mailbox": "user@example.com",
    "oauth_tenant_id": "tenant-id",
    "oauth_client_id": "client-id",
    "oauth_client_secret": "test-secret",
}

VALID_NTLM = {
    "auth_type": ExchangeAuthType.NTLM,
    "mailbox": "user@example.com",
    "ews_url": "https://ews.example.com/EWS/Exchange.asmx",
    "service_account_username": "svc-account",
    "service_account_password": "test-secret",
}


def test_missing_auth_type_raises():
    kwargs = dict(VALID_OAUTH)
    del kwargs["auth_type"]
    with pytest.raises(ValidationError, match="auth_type"):
        ExchangeToolConfig(**kwargs)


def test_none_auth_type_raises():
    kwargs = dict(VALID_OAUTH)
    kwargs["auth_type"] = None
    with pytest.raises(ValidationError, match="auth_type"):
        ExchangeToolConfig(**kwargs)


@pytest.mark.parametrize("valid_base", [VALID_OAUTH, VALID_NTLM])
@pytest.mark.parametrize("mailbox_value", [None, "", "   "])
def test_mailbox_required(valid_base, mailbox_value):
    kwargs = dict(valid_base)
    if mailbox_value is None:
        del kwargs["mailbox"]
    else:
        kwargs["mailbox"] = mailbox_value
    with pytest.raises(ValidationError, match="mailbox"):
        ExchangeToolConfig(**kwargs)


@pytest.mark.parametrize("field_name", ["oauth_tenant_id", "oauth_client_id", "oauth_client_secret"])
def test_oauth_azure_required_fields(field_name):
    kwargs = dict(VALID_OAUTH)
    del kwargs[field_name]
    with pytest.raises(ValidationError, match=field_name):
        ExchangeToolConfig(**kwargs)


@pytest.mark.parametrize("field_name", ["ews_url", "service_account_username", "service_account_password"])
def test_ntlm_required_fields(field_name):
    kwargs = dict(VALID_NTLM)
    del kwargs[field_name]
    with pytest.raises(ValidationError, match=field_name):
        ExchangeToolConfig(**kwargs)


@pytest.mark.parametrize(
    ("valid_base", "field_name"),
    [(VALID_OAUTH, name) for name in ("oauth_tenant_id", "oauth_client_id", "oauth_client_secret")]
    + [(VALID_NTLM, name) for name in ("ews_url", "service_account_username", "service_account_password")],
)
@pytest.mark.parametrize("blank_value", ["", "   ", "\t\n"])
def test_blank_selected_path_field_raises(valid_base, field_name, blank_value):
    kwargs = dict(valid_base)
    kwargs[field_name] = blank_value
    with pytest.raises(ValidationError, match=field_name):
        ExchangeToolConfig(**kwargs)


def test_valid_oauth_config_passes():
    config = ExchangeToolConfig(**VALID_OAUTH)
    assert config.auth_type == ExchangeAuthType.OAUTH_AZURE
    assert config.mailbox == "user@example.com"
    assert config.ews_url is None
    assert config.service_account_username is None
    assert config.service_account_password is None


def test_valid_ntlm_config_passes():
    config = ExchangeToolConfig(**VALID_NTLM)
    assert config.auth_type == ExchangeAuthType.NTLM
    assert config.mailbox == "user@example.com"
    assert config.oauth_tenant_id is None
    assert config.oauth_client_id is None
    assert config.oauth_client_secret is None


def test_only_two_secrets_marked_sensitive():
    schema = ExchangeToolConfig.model_json_schema()
    properties = schema["properties"]
    sensitive_fields = {name for name, prop in properties.items() if prop.get("sensitive") is True}
    assert sensitive_fields == {"oauth_client_secret", "service_account_password"}
