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

import hvac

from codemie.configs import config, logger
from codemie.service.encryption.base_encryption_service import BaseEncryptionService


class VaultEncryptionService(BaseEncryptionService):
    """
    HashiCorp Vault Transit Engine encryption service.

    Uses Vault's Transit secrets engine to encrypt/decrypt data without storing it.
    The Transit engine handles cryptographic operations server-side while the encrypted
    data is stored in PostgreSQL.

    Configuration required:
    - VAULT_URL: Vault server URL (e.g., http://vault.example.com:8200)
    - VAULT_TOKEN: Vault authentication token
    - VAULT_NAMESPACE: Optional namespace for Vault Enterprise
    - VAULT_TRANSIT_KEY_NAME: Name of the transit encryption key
    - VAULT_TRANSIT_MOUNT_POINT: Mount point for transit engine (default: "transit")
    """

    def __init__(self):
        """
        Initialize Vault client and configure transit engine settings.

        Raises:
            ValueError: If required configuration is missing
            hvac.exceptions.VaultError: If Vault connection fails
        """
        if not config.VAULT_URL:
            raise ValueError("VAULT_URL configuration is required for Vault encryption")
        if not config.VAULT_TOKEN:
            raise ValueError("VAULT_TOKEN configuration is required for Vault encryption")

        client_kwargs = {
            'url': config.VAULT_URL,
            'token': config.VAULT_TOKEN,
        }

        # Add namespace if configured (Vault Enterprise feature)
        if config.VAULT_NAMESPACE:
            client_kwargs['namespace'] = config.VAULT_NAMESPACE

        self.client = hvac.Client(**client_kwargs)

        if not self.client.is_authenticated():
            raise ValueError("Failed to authenticate with Vault server. Check VAULT_TOKEN.")

        self.transit_key_name = config.VAULT_TRANSIT_KEY_NAME
        self.mount_point = config.VAULT_TRANSIT_MOUNT_POINT

        logger.debug(
            "Initialized Vault encryption service",
            extra={
                'vault_url': config.VAULT_URL,
                'transit_mount_point': self.mount_point,
                'transit_key_name': self.transit_key_name,
            },
        )

    def encrypt(self, data: str) -> str:
        """
        Encrypt data using Vault Transit engine.

        Args:
            data: Plain text string to encrypt

        Returns:
            Base64-encoded ciphertext string

        Raises:
            ValueError: If data is empty
            Exception: If encryption fails
        """
        if not data:
            raise ValueError("Data to encrypt cannot be empty")

        try:
            plaintext_b64 = base64.b64encode(data.encode('utf-8')).decode('utf-8')

            response = self.client.secrets.transit.encrypt_data(
                name=self.transit_key_name,
                plaintext=plaintext_b64,
                mount_point=self.mount_point,
            )

            ciphertext = response['data']['ciphertext']

            logger.debug("Successfully encrypted data using Vault Transit engine")

            return ciphertext

        except hvac.exceptions.VaultError as e:
            logger.error(f"Vault error during encryption: {e}", exc_info=True)
            raise e
        except Exception as e:
            logger.error(f"Failed to encrypt data with Vault: {e}", exc_info=True)
            raise e

    def decrypt(self, data: str) -> str:
        """
        Decrypt data using Vault Transit engine.

        Args:
            data: Ciphertext string from Vault (format: "vault:v1:...")

        Returns:
            Decrypted plain text string

        Note:
            If decryption fails, returns the original data to maintain backward compatibility
            with the existing pattern used in other encryption services.
        """
        if not isinstance(data, str):
            raise TypeError("Data to decrypt must be a string")

        try:
            response = self.client.secrets.transit.decrypt_data(
                name=self.transit_key_name,
                ciphertext=data,
                mount_point=self.mount_point,
            )

            plaintext_b64 = response['data']['plaintext']
            plaintext = base64.b64decode(plaintext_b64).decode('utf-8')

            logger.debug("Successfully decrypted data using Vault Transit engine")

            return plaintext

        except hvac.exceptions.VaultError as e:
            logger.error(f"Vault error during decryption: {e}", exc_info=True)
            return data
        except Exception as e:
            logger.error(f"Failed to decrypt data with Vault: {e}", exc_info=True)
            return data
