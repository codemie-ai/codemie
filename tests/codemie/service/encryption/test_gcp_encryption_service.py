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

import pytest
import base64
from unittest.mock import patch
from google.cloud.kms_v1.types import EncryptResponse, DecryptResponse

from codemie.service.encryption.gcp_encryption_service import GCPKMSEncryptionService


class ExampleException(Exception):
    pass


@pytest.fixture
@patch('google.cloud.kms.KeyManagementServiceClient')
def gcp_encryption_service(mock_kms_client):
    mock_kms_client.return_value.encrypt.return_value = EncryptResponse(ciphertext=b'test_encrypted_data')
    mock_kms_client.return_value.decrypt.return_value = DecryptResponse(plaintext=b'test_decrypted_data')
    return GCPKMSEncryptionService()


def test_encrypt_success(gcp_encryption_service):
    result = gcp_encryption_service.encrypt('test_data')
    assert result is not None


def test_encrypt_exception(gcp_encryption_service):
    with patch.object(base64, 'b64encode', side_effect=ExampleException('test')):
        with pytest.raises(ExampleException):
            gcp_encryption_service.encrypt('test_data')


def test_decrypt_success(gcp_encryption_service):
    encrypted_data = base64.b64encode(b'test_data').decode('utf-8')
    result = gcp_encryption_service.decrypt(encrypted_data)
    assert result == 'test_decrypted_data'


def test_decrypt_exception(gcp_encryption_service):
    with patch.object(base64, 'b64decode', side_effect=ExampleException('test')):
        result = gcp_encryption_service.decrypt('test_data')
        assert result == 'test_data'
