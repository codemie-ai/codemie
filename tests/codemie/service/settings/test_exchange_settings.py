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

"""EPMCDME-15199: MS Exchange settings must encrypt/mask their secret fields and be resolvable
through SettingsService.get_config, for both the oauth_azure and ntlm auth paths."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.rest_api.models.settings import CredentialValues, Settings, SettingType
from codemie.service.encryption.base_encryption_service import Base64EncryptionService
from codemie.service.settings.settings import SettingsService
from codemie_tools.base.models import CredentialTypes
from codemie_tools.ms_exchange.models import ExchangeAuthType, ExchangeToolConfig

OAUTH_AZURE_VALUES = {
    "auth_type": ExchangeAuthType.OAUTH_AZURE.value,
    "mailbox": "shared@contoso.com",
    "oauth_tenant_id": "tenant-1",
    "oauth_client_id": "client-1",
    "oauth_client_secret": "test-secret",
}

NTLM_VALUES = {
    "auth_type": ExchangeAuthType.NTLM.value,
    "mailbox": "shared@contoso.com",
    "ews_url": "https://mail.contoso.com/EWS/Exchange.asmx",
    "service_account_username": "svc-exchange",
    "service_account_password": "test-secret",
}

AUTH_PATHS = [
    (OAUTH_AZURE_VALUES, "oauth_client_secret", "mailbox"),
    (NTLM_VALUES, "service_account_password", "ews_url"),
]


@pytest.fixture(autouse=True)
def base64_encryption(monkeypatch):
    """Use a reversible, non-identity encryption so tests can prove the secret is actually
    transformed on save and restored on read (the default test encryption is a plaintext no-op)."""
    monkeypatch.setattr(SettingsService, "encryption_service", Base64EncryptionService())


def _setting(values):
    return Settings(
        id="exch-1",
        user_id="user123",
        project_name="test_project",
        alias="exchange",
        credential_type=CredentialTypes.MS_EXCHANGE,
        credential_values=[CredentialValues(key=k, value=v) for k, v in values.items()],
        setting_type=SettingType.USER,
    )


@pytest.mark.parametrize("values, secret_field, plaintext_field", AUTH_PATHS)
def test_secret_is_encrypted_when_saved(values, secret_field, plaintext_field):
    creds = [CredentialValues(key=k, value=v) for k, v in values.items()]

    encrypted = SettingsService._encrypt_fields(creds)

    secret_cred = next(c for c in encrypted if c.key == secret_field)
    assert secret_cred.value != "test-secret"


@pytest.mark.parametrize("values, secret_field, plaintext_field", AUTH_PATHS)
def test_hide_sensitive_fields_masks_the_secret(values, secret_field, plaintext_field):
    setting = _setting(values)

    result = SettingsService.hide_sensitive_fields(setting)

    secret_cred = next(c for c in result.credential_values if c.key == secret_field)
    assert secret_cred.value == SettingsService.MASKED_VALUE


@pytest.mark.parametrize("values, secret_field, plaintext_field", AUTH_PATHS)
def test_hide_sensitive_fields_keeps_mailbox_and_ews_url_plaintext(values, secret_field, plaintext_field):
    setting = _setting(values)

    result = SettingsService.hide_sensitive_fields(setting)

    mailbox_cred = next(c for c in result.credential_values if c.key == "mailbox")
    assert mailbox_cred.value == values["mailbox"]
    plaintext_cred = next(c for c in result.credential_values if c.key == plaintext_field)
    assert plaintext_cred.value == values[plaintext_field]


@pytest.mark.parametrize("values, secret_field, plaintext_field", AUTH_PATHS)
def test_get_config_returns_decrypted_exchange_config(values, secret_field, plaintext_field):
    encrypted_creds = SettingsService._encrypt_fields([CredentialValues(key=k, value=v) for k, v in values.items()])
    raw_setting = Settings(
        id="exch-1",
        user_id="user123",
        project_name="test_project",
        alias="exchange",
        credential_type=CredentialTypes.MS_EXCHANGE,
        credential_values=encrypted_creds,
        setting_type=SettingType.USER,
    )

    handler = MagicMock()
    handler.handle.return_value = raw_setting
    with patch("codemie.service.settings.settings.build_settings_handlers", return_value=handler):
        config = SettingsService.get_config(ExchangeToolConfig, user_id="user123", project_name="test_project")

    assert isinstance(config, ExchangeToolConfig)
    assert getattr(config, secret_field) == "test-secret"
    assert config.mailbox == values["mailbox"]
    assert getattr(config, plaintext_field) == values[plaintext_field]
