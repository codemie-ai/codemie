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

from codemie.rest_api.models.provider import ProviderDataSourceTypeSchema
from codemie.service.encryption.base_encryption_service import BaseEncryptionService
from codemie.service.encryption.encryption_factory import EncryptionFactory

encryption_service: BaseEncryptionService = EncryptionFactory().get_current_encryption_service()


def encrypt_datasource_provider_fields(params: dict, schema: ProviderDataSourceTypeSchema) -> dict:
    """Encrypts sensetive IndexInfo.provider_fields.*_params"""
    sensetive_fields = schema.get_sensetive_fields()
    params = params.copy()

    for key, value in params.items():
        if key not in sensetive_fields:
            continue

        encrypted = encryption_service.encrypt(value)
        params[key] = encrypted

    return params


def decrypt_datasource_provider_fields(params: dict, schema: ProviderDataSourceTypeSchema) -> dict:
    """Decrypts sensetive IndexInfo.provider_fields.*_params"""
    sensetive_fields = schema.get_sensetive_fields()
    params = params.copy()

    for key, value in params.items():
        if key not in sensetive_fields:
            continue

        decrypted = encryption_service.decrypt(value)
        params[key] = decrypted

    return params


def to_class_name(name: str) -> str:
    """Convert a string to snake case for class name"""
    return name.title().replace(" ", "")
