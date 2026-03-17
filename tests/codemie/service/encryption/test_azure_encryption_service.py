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
import pytest
from unittest.mock import MagicMock, patch
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from azure.identity import DefaultAzureCredential
from azure.keyvault.keys import KeyClient
from azure.keyvault.keys.crypto import CryptographyClient, EncryptionAlgorithm, KeyWrapAlgorithm
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
    # Testing encryption with normal string (now uses envelope encryption)
    test_data = 'test data'
    fake_wrapped_key = b'fake_wrapped_key_bytes'
    mock_crypto_client.wrap_key.return_value = MagicMock(encrypted_key=fake_wrapped_key)

    result = azure_encryption_service.encrypt(test_data)

    # Verify envelope format
    assert result.startswith('v1.')
    parts = result.split('.')
    assert len(parts) == 3
    mock_crypto_client.wrap_key.assert_called_once()


def test_decrypt_success(azure_encryption_service, mock_crypto_client):
    # Testing decryption with normal string
    test_data = 'test data'
    encrypted_data = base64.b64encode(test_data.encode()).decode('utf-8')
    mock_crypto_client.decrypt.return_value = MagicMock(plaintext=test_data.encode())
    assert azure_encryption_service.decrypt(encrypted_data) == test_data
    mock_crypto_client.decrypt.assert_called_once_with(EncryptionAlgorithm.rsa_oaep, base64.b64decode(encrypted_data))


def test_encrypt_failure(azure_encryption_service, mock_crypto_client):
    # Testing encryption with failure scenario (now uses envelope encryption)
    test_data = 'test data'
    mock_crypto_client.wrap_key.side_effect = AzureError('wrap key failed')
    with pytest.raises(AzureError) as exc_info:
        azure_encryption_service.encrypt(test_data)
    assert 'wrap key failed' in str(exc_info.value)


def test_decrypt_failure(azure_encryption_service, mock_crypto_client):
    # Testing decryption with failure scenario (legacy format)
    # Updated: Now raises exception instead of returning plaintext (security fix)
    test_data = 'test data'
    mock_crypto_client.decrypt.side_effect = AzureError('decryption failed')

    with pytest.raises(AzureError) as exc_info:
        azure_encryption_service.decrypt(test_data)
    assert 'decryption failed' in str(exc_info.value)


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


# ============================================================================
# Envelope Encryption Tests (Phase 2)
# ============================================================================


def test_encrypt_envelope_format(azure_encryption_service, mock_crypto_client):
    """Verify envelope encryption produces versioned format."""
    test_data = 'test data'
    fake_wrapped_key = b'fake_wrapped_key_bytes'
    mock_crypto_client.wrap_key.return_value = MagicMock(encrypted_key=fake_wrapped_key)

    result = azure_encryption_service.encrypt(test_data)

    # Format: v1.wrapped_key.encrypted_data
    assert result.startswith('v1.')
    parts = result.split('.')
    assert len(parts) == 3
    # Validate base64 encoding
    base64.b64decode(parts[1])  # wrapped key
    base64.b64decode(parts[2])  # encrypted data
    # Verify wrap_key was called with RSA-OAEP and 32-byte DEK
    mock_crypto_client.wrap_key.assert_called_once()
    call_args = mock_crypto_client.wrap_key.call_args
    assert call_args[0][0] == KeyWrapAlgorithm.rsa_oaep
    assert len(call_args[0][1]) == 32  # 256-bit DEK


def test_encrypt_large_data(azure_encryption_service, mock_crypto_client):
    """AES-GCM should handle data > RSA-OAEP limit (190 bytes)."""
    large_data = 'x' * 500  # Exceeds RSA-OAEP limit
    fake_wrapped_key = b'fake_wrapped_key_bytes'
    mock_crypto_client.wrap_key.return_value = MagicMock(encrypted_key=fake_wrapped_key)

    result = azure_encryption_service.encrypt(large_data)

    assert 'v1.' in result  # Envelope format
    assert len(result) > 0


def test_envelope_encrypt_decrypt_roundtrip(azure_encryption_service, mock_crypto_client):
    """Test encrypt → decrypt → verify original."""
    test_plaintext = 'test data with special chars: 你好, emoji: 🚀'
    dek = os.urandom(32)
    nonce = os.urandom(12)
    ciphertext = AESGCM(dek).encrypt(nonce, test_plaintext.encode(), None)

    fake_wrapped_key = b'fake_wrapped_key_bytes'
    encoded_key = base64.b64encode(fake_wrapped_key).decode()
    encoded_data = base64.b64encode(nonce + ciphertext).decode()
    envelope = f"v1.{encoded_key}.{encoded_data}"

    mock_crypto_client.unwrap_key.return_value = MagicMock(key=dek)

    result = azure_encryption_service.decrypt(envelope)

    assert result == test_plaintext
    mock_crypto_client.unwrap_key.assert_called_once_with(KeyWrapAlgorithm.rsa_oaep, fake_wrapped_key)


def test_decrypt_legacy_format(azure_encryption_service, mock_crypto_client):
    """Ensure legacy RSA-OAEP encrypted data still decrypts."""
    test_plaintext = 'legacy secret'
    legacy_ciphertext = base64.b64encode(b'some_rsa_encrypted_bytes').decode()
    mock_crypto_client.decrypt.return_value = MagicMock(plaintext=test_plaintext.encode())

    result = azure_encryption_service.decrypt(legacy_ciphertext)

    assert result == test_plaintext
    mock_crypto_client.decrypt.assert_called_once_with(
        EncryptionAlgorithm.rsa_oaep, base64.b64decode(legacy_ciphertext)
    )


def test_nonce_uniqueness(azure_encryption_service, mock_crypto_client):
    """Verify nonces are unique across multiple encryptions."""
    test_data = 'test data'
    fake_wrapped_key = b'fake_wrapped_key_bytes'
    mock_crypto_client.wrap_key.return_value = MagicMock(encrypted_key=fake_wrapped_key)

    results = [azure_encryption_service.encrypt(test_data) for _ in range(100)]

    # Extract nonces from encrypted data (after base64 decode, first 12 bytes)
    nonces = []
    for result in results:
        parts = result.split('.')
        encrypted_data = base64.b64decode(parts[2])
        nonce = encrypted_data[:12]
        nonces.append(nonce)

    # All nonces must be unique
    assert len(nonces) == len(set(nonces))


def test_tampered_ciphertext_rejected(azure_encryption_service, mock_crypto_client):
    """GCM auth tag should detect tampering."""
    dek = os.urandom(32)
    nonce = os.urandom(12)
    ciphertext = AESGCM(dek).encrypt(nonce, b'data', None)

    # Tamper with ciphertext
    tampered_ciphertext = bytearray(ciphertext)
    tampered_ciphertext[0] ^= 0xFF  # Flip bits

    fake_wrapped_key = b'fake'
    encoded_key = base64.b64encode(fake_wrapped_key).decode()
    encoded_data = base64.b64encode(nonce + bytes(tampered_ciphertext)).decode()
    envelope = f"v1.{encoded_key}.{encoded_data}"

    mock_crypto_client.unwrap_key.return_value = MagicMock(key=dek)

    with pytest.raises(InvalidTag):  # GCM raises InvalidTag on auth failure
        azure_encryption_service.decrypt(envelope)


def test_decrypt_failure_raises_exception(azure_encryption_service, mock_crypto_client):
    """Decryption failure should raise exception, not return plaintext."""
    # Create valid base64 strings for envelope format
    fake_key = base64.b64encode(b'fake_key_bytes').decode()
    fake_data = base64.b64encode(b'fake_data_bytes').decode()
    envelope = f"v1.{fake_key}.{fake_data}"
    mock_crypto_client.unwrap_key.side_effect = AzureError('unwrap failed')

    with pytest.raises(AzureError):
        azure_encryption_service.decrypt(envelope)
    # Should NOT return original data (security issue)
