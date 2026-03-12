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
import pytest
from unittest.mock import MagicMock, patch
from azure.identity import DefaultAzureCredential
from azure.keyvault.keys import KeyClient
from azure.keyvault.keys.crypto import CryptographyClient, EncryptionAlgorithm
from codemie.service.encryption.azure_encryption_service import AzureKMSEncryptionService
from azure.core.exceptions import AzureError


@pytest.fixture
def mock_credentials():
    return MagicMock(spec=DefaultAzureCredential)


@pytest.fixture
def mock_key_client():
    return MagicMock(spec=KeyClient)


@pytest.fixture
def mock_crypto_client():
    return MagicMock(spec=CryptographyClient)


@pytest.fixture
def azure_encryption_service(mock_credentials, mock_key_client, mock_crypto_client):
    with (
        patch.object(DefaultAzureCredential, '__init__', return_value=None),
        patch.object(KeyClient, '__init__', return_value=None),
        patch.object(CryptographyClient, '__init__', return_value=None),
        patch(
            'codemie.service.encryption.azure_encryption_service.DefaultAzureCredential', return_value=mock_credentials
        ),
        patch('codemie.service.encryption.azure_encryption_service.KeyClient', return_value=mock_key_client),
        patch(
            'codemie.service.encryption.azure_encryption_service.CryptographyClient', return_value=mock_crypto_client
        ),
    ):
        service = AzureKMSEncryptionService()
        service.secret_client = mock_key_client
        service.crypto_client = mock_crypto_client
        return service


def test_encrypt_success(azure_encryption_service, mock_crypto_client):
    # Testing encryption with normal string
    test_data = 'test data'
    encoded_data = test_data.encode()
    encrypted_data = base64.b64encode(b'encrypted').decode('utf-8')
    mock_crypto_client.encrypt.return_value = MagicMock(ciphertext=b'encrypted')
    assert azure_encryption_service.encrypt(test_data) == encrypted_data
    mock_crypto_client.encrypt.assert_called_once_with(EncryptionAlgorithm.rsa_oaep, encoded_data)


def test_decrypt_success(azure_encryption_service, mock_crypto_client):
    # Testing decryption with normal string
    test_data = 'test data'
    encrypted_data = base64.b64encode(test_data.encode()).decode('utf-8')
    mock_crypto_client.decrypt.return_value = MagicMock(plaintext=test_data.encode())
    assert azure_encryption_service.decrypt(encrypted_data) == test_data
    mock_crypto_client.decrypt.assert_called_once_with(EncryptionAlgorithm.rsa_oaep, base64.b64decode(encrypted_data))


def test_encrypt_failure(azure_encryption_service, mock_crypto_client):
    # Testing encryption with failure scenario
    test_data = 'test data'
    mock_crypto_client.encrypt.side_effect = AzureError('encryption failed')
    with pytest.raises(AzureError) as exc_info:
        azure_encryption_service.encrypt(test_data)
    assert 'encryption failed' in str(exc_info.value)


def test_decrypt_failure(azure_encryption_service, mock_crypto_client):
    # Testing decryption with failure scenario
    test_data = 'test data'
    mock_crypto_client.decrypt.side_effect = AzureError('decryption failed')

    result = azure_encryption_service.decrypt(test_data)
    assert test_data == result


# Additional tests covering edge cases and negative testing scenarios
def test_encrypt_empty_string(azure_encryption_service):
    # Testing encryption with an empty string
    test_data = ''
    with pytest.raises(ValueError):
        azure_encryption_service.encrypt(test_data)


def test_decrypt_invalid_input(azure_encryption_service):
    # Testing decryption with invalid input type
    test_data = 123  # Not a string
    with pytest.raises(TypeError):
        azure_encryption_service.decrypt(test_data)
