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
from azure.identity import DefaultAzureCredential
from azure.keyvault.keys import KeyClient
from azure.keyvault.keys.crypto import CryptographyClient, EncryptionAlgorithm

from codemie.configs import config, logger
from codemie.service.encryption.base_encryption_service import BaseEncryptionService


class AzureKMSEncryptionService(BaseEncryptionService):
    def __init__(self):
        self.key_vault_url = config.AZURE_KEY_VAULT_URL
        self.key_name = config.AZURE_KEY_NAME
        credentials = DefaultAzureCredential()
        self.secret_client = KeyClient(vault_url=self.key_vault_url, credential=credentials)
        key = self.secret_client.get_key(self.key_name)
        self.crypto_client = CryptographyClient(key, credentials)

    def encrypt(self, data: str) -> str:
        if not data:
            raise ValueError("Data to encrypt cannot be empty.")
        try:
            response = self.crypto_client.encrypt(EncryptionAlgorithm.rsa_oaep, data.encode())
            return base64.b64encode(response.ciphertext).decode('utf-8')
        except Exception as e:
            logger.error(f"Failed to encrypt data with Azure: {e}")
            raise e  # Propagate the specific exception

    def decrypt(self, data: str) -> str:
        if not isinstance(data, str):
            raise TypeError("Data to decrypt must be a string.")
        try:
            encoded_data = base64.b64decode(data)
            response = self.crypto_client.decrypt(EncryptionAlgorithm.rsa_oaep, encoded_data)
            return response.plaintext.decode()
        except Exception as e:
            logger.error(f"Failed to decrypt data with Azure: {e}")
            return data
