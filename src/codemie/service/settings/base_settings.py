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

from enum import Enum
from typing import List
from codemie.rest_api.models.settings import Settings, SettingsBase, CredentialValues
from codemie_tools.base.models import CredentialTypes
from codemie.service.encryption.base_encryption_service import BaseEncryptionService
from codemie.service.encryption.encryption_factory import EncryptionFactory


class SearchFields(str, Enum):
    USER_ID = "user_id.keyword"
    DEFAULT = "default"
    ASSISTANT_ID = "assistant_id.keyword"
    PROJECT_NAME = "project_name.keyword"
    ALIAS = "alias.keyword"
    SETTING_TYPE = "setting_type"
    CREDENTIAL_TYPE = "credential_type.keyword"
    CREDENTIAL_VALUES_KEY = "credential_values.key.keyword"
    CREDENTIAL_VALUES_VALUE = "credential_values.value.keyword"
    IS_GLOBAL = "is_global"
    SETTING_HASH = "setting_hash.keyword"


# Do not mask this phrase if encountered in sensitive fields
CHANGEME_PROMPT = '!!!changeme!!!'
CHANGEME_URL = 'https://changeme.example.com'
PASSTHROUGH_PHRASES = [CHANGEME_PROMPT]


class BaseSettingsService:
    """Abstract base class for settings services"""

    MASKED_VALUE: str = "*" * 10

    encryption_service: BaseEncryptionService = EncryptionFactory().get_current_encryption_service()

    @classmethod
    def retrieve_setting(cls, search_fields):
        settings = Settings.get_by_fields(search_fields)

        if settings and settings.credential_values:
            creds = cls._decrypt_fields(
                settings.credential_values, force_all=settings.credential_type == CredentialTypes.ENVIRONMENT_VARS
            )
            settings.credential_values = creds
        return settings

    @classmethod
    def _encrypt_fields(cls, credential_values: List[CredentialValues], force_all: bool = False):
        """Call encryption service to encrypt sensitive data"""
        for cred in credential_values:
            if force_all or cred.key in cls.LIST_OF_SENSITIVE_FIELDS:
                encrypted = cls.encryption_service.encrypt(cred.value)
                cred.value = encrypted
        return credential_values

    @classmethod
    def _decrypt_fields(cls, credential_values: List[CredentialValues], force_all: bool = False):
        """Call encryption service to decrypt sensitive data"""
        for cred in credential_values:
            if force_all or cred.key in cls.LIST_OF_SENSITIVE_FIELDS:
                decrypted = cls.encryption_service.decrypt(cred.value)
                cred.value = decrypted
        return credential_values

    @classmethod
    def hide_sensitive_fields(cls, data: SettingsBase, force_all: bool = False):
        for cred in data.credential_values:
            if (force_all or cred.key in cls.LIST_OF_SENSITIVE_FIELDS) and not any(
                cred.value == as_is for as_is in PASSTHROUGH_PHRASES
            ):
                cred.value = cls.MASKED_VALUE
        return data
