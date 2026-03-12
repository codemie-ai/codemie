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
import boto3
from codemie.configs import config, logger
from codemie.service.encryption.base_encryption_service import BaseEncryptionService


class AWSKMSEncryptionService(BaseEncryptionService):
    def __init__(self):
        region_kwargs = {}
        region = str(getattr(config, 'AWS_KMS_REGION', '') or '').strip()
        if region:
            region_kwargs['region_name'] = region
        self.kms_client = boto3.client('kms', **region_kwargs)
        self.key_id = config.AWS_KMS_KEY_ID

    def encrypt(self, data: str):
        try:
            response = self.kms_client.encrypt(KeyId=self.key_id, Plaintext=data.encode())
            return base64.b64encode(response['CiphertextBlob']).decode('utf-8')
        except Exception as e:
            logger.error(f"Failed to encrypt data: {e}")
            raise e

    def decrypt(self, data: str):
        try:
            encoded_data = base64.b64decode(data)
            response = self.kms_client.decrypt(CiphertextBlob=encoded_data)
            return response['Plaintext'].decode()
        except Exception as e:
            logger.error(f"Failed to decrypt data: {e}")
            return data
