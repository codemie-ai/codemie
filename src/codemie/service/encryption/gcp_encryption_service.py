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

from google.cloud import kms

from codemie.configs import config, logger
from codemie.service.encryption.base_encryption_service import BaseEncryptionService


class GCPKMSEncryptionService(BaseEncryptionService):
    def __init__(self):
        self.client = kms.KeyManagementServiceClient()
        self.project_id = config.GOOGLE_KMS_PROJECT_ID
        self.location_id = config.GOOGLE_KMS_REGION
        self.key_ring_id = config.GOOGLE_KMS_KEY_RING
        self.key_id = config.GOOGLE_KMS_CRYPTO_KEY
        self.key_name = self.client.crypto_key_path(self.project_id, self.location_id, self.key_ring_id, self.key_id)

    def encrypt(self, data: str):
        try:
            response = self.client.encrypt(request={'name': self.key_name, 'plaintext': data.encode()})
            return base64.b64encode(response.ciphertext).decode('utf-8')
        except Exception as e:
            logger.error(f"Failed to encrypt data: {e}")
            raise e

    def decrypt(self, data: str):
        try:
            encoded_data = base64.b64decode(data)
            response = self.client.decrypt(request={'name': self.key_name, 'ciphertext': encoded_data})
            return response.plaintext.decode()
        except Exception as e:
            logger.error(f"Failed to decrypt data: {e}")
            return data
