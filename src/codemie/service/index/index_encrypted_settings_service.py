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

import base64
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.backends import default_backend

from codemie.core.ability import Action
from codemie.configs.authorized_apps_config import authorized_applications_config
from codemie.service.settings.settings_index_service import SettingsIndexService
from codemie.rest_api.security.user import User
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.models.settings import Settings
from codemie.rest_api.models.permission import Permission, PrincipalType, ResourceType


class IndexEncryptedSettingsError(Exception):
    """When user does not have permission to access encrypted settings."""

    pass


class IndexEncryptedSettingsService:
    """Service provides encrypted settings for the shared IndexInfo."""

    def __init__(self, index: IndexInfo, user: User, x_request_id: str):
        self.index = index
        self.user = user
        self.x_request_id = x_request_id
        self.app_config = None

    def run(self):
        """Run the service"""
        self._check_permissions()
        self._check_app_config()
        self._check_index()

        try:
            settings = Settings.get_by_id(self.index.setting_id)
        except KeyError:
            raise IndexEncryptedSettingsError(f"Settings with ID {self.index.setting_id} are not found.")

        try:
            public_key = self.app_config.get_public_key(x_request_id=self.x_request_id)
            settings = self._encrypt_credential_values(settings=settings, public_key=public_key)
        except Exception as exc:
            raise IndexEncryptedSettingsError(
                f"Failed to encrypt settings for index {self.index.id}: {str(exc)}"
            ) from exc

        return settings

    def _check_permissions(self):
        """Check if the user/application has permissions to access the index settings."""
        permission = Permission.get_for(user=self.user, instance=self.index, action=Action.READ)

        if not permission:
            raise IndexEncryptedSettingsError("User does not have permission to read the index settings.")

        if permission.principal_type != PrincipalType.APPLICATION:
            raise IndexEncryptedSettingsError("User is not an application, cannot access encrypted settings.")

    def _check_app_config(self):
        """Check if the application is registered in config"""
        self.app_config = authorized_applications_config.find_by_name(self.user.username)

        if not self.app_config:
            raise IndexEncryptedSettingsError(
                f"Application {self.user.username} is not registered in the authorized applications."
            )

        if ResourceType.DATASOURCE not in self.app_config.allowed_resources:
            raise IndexEncryptedSettingsError(
                f"Application {self.user.username} is not allowed to access the datasource resource."
            )

    def _check_index(self):
        """Check if the index exists and is shared with the user/application."""
        if self.index.setting_id is None:
            raise IndexEncryptedSettingsError("Index does not have associated settings.")

    def _encrypt_credential_values(self, settings: Settings, public_key: bytes) -> Settings:
        """Encrypt the credential values of the settings."""
        # Decrypt the credential values encrypted for everyone
        settings.credential_values = SettingsIndexService._decrypt_fields(settings.credential_values)

        for field in settings.credential_values:
            field.value = self._encrypt_value(value=str(field.value), public_key=public_key)

        return settings

    def _encrypt_value(self, value: str, public_key: bytes) -> str:
        """Encrypt a single value using the provided public key."""
        initialized_key = serialization.load_pem_public_key(public_key, backend=default_backend())

        encrypted_value = initialized_key.encrypt(
            value.encode('utf-8'),
            padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )

        return base64.b64encode(encrypted_value).decode('utf-8')
