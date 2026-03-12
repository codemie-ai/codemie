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

from pydantic import BaseModel

from codemie.configs import config, logger
from codemie.service.encryption.base_encryption_service import PlainEncryptionService, Base64EncryptionService
from codemie.service.encryption.gcp_encryption_service import GCPKMSEncryptionService
from codemie.service.encryption.aws_encryption_service import AWSKMSEncryptionService
from codemie.service.encryption.azure_encryption_service import AzureKMSEncryptionService
from codemie.service.encryption.vault_encryption_service import VaultEncryptionService


class EncryptionType(str, Enum):
    PLAIN_TEXT = "plain"
    GCP_ENCRYPTION = "gcp"
    AWS_ENCRYPTION = "aws"
    AZURE_ENCRYPTION = "azure"
    BASE64_ENCRYPTION = "base64"
    VAULT_ENCRYPTION = "vault"


class EncryptionFactory(BaseModel):
    @classmethod
    def get_current_encryption_service(cls):
        service_type = cls.get_current_encryption_service_type()
        return cls.get_encryption_service(service_type)

    @classmethod
    def get_encryption_service(cls, encryption_type: EncryptionType):
        if encryption_type == EncryptionType.GCP_ENCRYPTION:
            return GCPKMSEncryptionService()
        elif encryption_type == EncryptionType.AWS_ENCRYPTION:
            return AWSKMSEncryptionService()
        elif encryption_type == EncryptionType.AZURE_ENCRYPTION:
            return AzureKMSEncryptionService()
        elif encryption_type == EncryptionType.VAULT_ENCRYPTION:
            return VaultEncryptionService()
        elif encryption_type == EncryptionType.PLAIN_TEXT:
            return PlainEncryptionService()
        elif encryption_type == EncryptionType.BASE64_ENCRYPTION:
            return Base64EncryptionService()
        else:
            logger.error(f"Unsupported encryption service type: {encryption_type}")
            raise ValueError("Unsupported encryption service type")

    @classmethod
    def get_current_encryption_service_type(cls):
        try:
            if config.ENCRYPTION_TYPE:
                return EncryptionType(config.ENCRYPTION_TYPE)
            else:
                return EncryptionType.PLAIN_TEXT
        except Exception as e:
            logger.error(f"Failed to get encryption service type: {e}")
            return EncryptionType.PLAIN_TEXT
