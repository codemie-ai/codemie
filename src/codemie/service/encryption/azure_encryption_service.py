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
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from azure.identity import DefaultAzureCredential
from azure.keyvault.keys import KeyClient
from azure.keyvault.keys.crypto import CryptographyClient, EncryptionAlgorithm, KeyWrapAlgorithm

from codemie.configs import config, logger
from codemie.service.encryption.base_encryption_service import BaseEncryptionService

# Envelope encryption format constants
_SEPARATOR = "."
_VERSION = "v1"  # For future algorithm agility


class AzureKMSEncryptionService(BaseEncryptionService):
    def __init__(self):
        self.key_vault_url = config.AZURE_KEY_VAULT_URL
        self.key_name = config.AZURE_KEY_NAME
        credentials = DefaultAzureCredential()
        self.secret_client = KeyClient(vault_url=self.key_vault_url, credential=credentials)
        key = self.secret_client.get_key(self.key_name)
        self.crypto_client = CryptographyClient(key, credentials)

    def _encrypt_envelope(self, data: str) -> str:
        """Encrypt data using envelope encryption (AES-GCM + RSA-OAEP key wrapping).

        This method implements the envelope encryption pattern:
        1. Generate ephemeral 256-bit AES key (DEK)
        2. Encrypt data with AES-256-GCM (no size limits, authenticated encryption)
        3. Wrap DEK with Azure Key Vault RSA-OAEP (only 32 bytes)
        4. Return versioned format: v1.base64(wrapped_key).base64(nonce + ciphertext)

        Args:
            data: Plaintext string to encrypt

        Returns:
            Encrypted data in format: v1.<wrapped_key>.<encrypted_data>
        """
        # Generate ephemeral DEK and nonce
        dek = os.urandom(32)  # 256-bit AES key
        nonce = os.urandom(12)  # 96-bit GCM nonce (NIST recommended)

        # Encrypt data with AES-GCM
        ciphertext = AESGCM(dek).encrypt(nonce, data.encode(), None)
        # ciphertext includes 16-byte GCM auth tag appended by AESGCM

        # Wrap DEK with Azure Key Vault
        wrap_result = self.crypto_client.wrap_key(KeyWrapAlgorithm.rsa_oaep, dek)

        # Encode and combine components
        encoded_key = base64.b64encode(wrap_result.encrypted_key).decode('utf-8')
        encoded_data = base64.b64encode(nonce + ciphertext).decode('utf-8')

        return f"{_VERSION}{_SEPARATOR}{encoded_key}{_SEPARATOR}{encoded_data}"

    def _decrypt_envelope(self, data: str) -> str:
        """Decrypt envelope-encrypted data.

        Args:
            data: Encrypted data in format: v1.<wrapped_key>.<encrypted_data>
                  or <wrapped_key>.<encrypted_data> (backward compat)

        Returns:
            Decrypted plaintext string
        """
        parts = data.split(_SEPARATOR)

        if len(parts) == 3:  # v1.wrapped_key.encrypted_data
            _version, encoded_key, encoded_data = parts  # version reserved for future validation
        elif len(parts) == 2:  # wrapped_key.encrypted_data (backward compat)
            encoded_key, encoded_data = parts
        else:
            raise ValueError("Invalid envelope format")

        # Decode components
        wrapped_key = base64.b64decode(encoded_key)
        nonce_and_ciphertext = base64.b64decode(encoded_data)

        # Unwrap DEK from Azure Key Vault
        unwrap_result = self.crypto_client.unwrap_key(KeyWrapAlgorithm.rsa_oaep, wrapped_key)
        dek = unwrap_result.key

        # Decrypt data with AES-GCM
        nonce = nonce_and_ciphertext[:12]
        ciphertext = nonce_and_ciphertext[12:]
        plaintext = AESGCM(dek).decrypt(nonce, ciphertext, None)

        return plaintext.decode()

    def _decrypt_legacy(self, data: str) -> str:
        """Decrypt legacy RSA-OAEP encrypted data (backward compatibility).

        Args:
            data: Base64-encoded RSA-OAEP ciphertext

        Returns:
            Decrypted plaintext string
        """
        encoded_data = base64.b64decode(data)
        response = self.crypto_client.decrypt(EncryptionAlgorithm.rsa_oaep, encoded_data)
        return response.plaintext.decode()

    def encrypt(self, data: str) -> str:
        """Encrypt data using envelope encryption pattern.

        Args:
            data: Plaintext string to encrypt

        Returns:
            Encrypted data in versioned envelope format

        Raises:
            ValueError: If data is empty
            Exception: If encryption fails
        """
        if not data:
            raise ValueError("Data to encrypt cannot be empty.")
        try:
            return self._encrypt_envelope(data)
        except Exception as e:
            logger.error(f"Failed to encrypt data with Azure: {type(e).__name__}")
            raise e  # Propagate exception, don't swallow

    def decrypt(self, data: str) -> str:
        """Decrypt data with automatic format detection.

        Supports both envelope encryption format and legacy RSA-OAEP format
        for backward compatibility.

        Args:
            data: Encrypted data (envelope or legacy format)

        Returns:
            Decrypted plaintext string

        Raises:
            TypeError: If data is not a string
            Exception: If decryption fails
        """
        if not isinstance(data, str):
            raise TypeError("Data to decrypt must be a string.")
        try:
            if _SEPARATOR in data:
                # Envelope encryption format (v1.wrapped_key.encrypted_data OR wrapped_key.encrypted_data)
                return self._decrypt_envelope(data)
            else:
                # Legacy RSA-OAEP format (backward compatibility)
                return self._decrypt_legacy(data)
        except Exception as e:
            logger.error(f"Failed to decrypt data with Azure: {type(e).__name__}")
            raise e  # DON'T return plaintext on failure (security issue)
