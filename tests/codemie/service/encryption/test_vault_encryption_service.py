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

import hvac.exceptions

from codemie.service.encryption.vault_encryption_service import VaultEncryptionService


class TestVaultEncryptionServiceInitialization:
    """Test VaultEncryptionService initialization."""

    @patch('codemie.service.encryption.vault_encryption_service.hvac.Client')
    @patch('codemie.service.encryption.vault_encryption_service.config')
    def test_init_success(self, mock_config, mock_hvac_client):
        """Test successful initialization with all required configuration."""
        # Arrange
        mock_config.VAULT_URL = "http://vault.example.com:8200"
        mock_config.VAULT_TOKEN = "test-token"
        mock_config.VAULT_NAMESPACE = ""
        mock_config.VAULT_TRANSIT_KEY_NAME = "codemie"
        mock_config.VAULT_TRANSIT_MOUNT_POINT = "transit"

        mock_client_instance = MagicMock()
        mock_client_instance.is_authenticated.return_value = True
        mock_hvac_client.return_value = mock_client_instance

        # Act
        vault_service = VaultEncryptionService()

        # Assert
        assert vault_service.client == mock_client_instance
        assert vault_service.transit_key_name == "codemie"
        assert vault_service.mount_point == "transit"
        mock_hvac_client.assert_called_once_with(
            url="http://vault.example.com:8200",
            token="test-token",
        )
        mock_client_instance.is_authenticated.assert_called_once()

    @patch('codemie.service.encryption.vault_encryption_service.hvac.Client')
    @patch('codemie.service.encryption.vault_encryption_service.config')
    def test_init_with_namespace(self, mock_config, mock_hvac_client):
        """Test initialization with Vault namespace (Enterprise feature)."""
        # Arrange
        mock_config.VAULT_URL = "http://vault.example.com:8200"
        mock_config.VAULT_TOKEN = "test-token"
        mock_config.VAULT_NAMESPACE = "my-namespace"
        mock_config.VAULT_TRANSIT_KEY_NAME = "codemie"
        mock_config.VAULT_TRANSIT_MOUNT_POINT = "transit"

        mock_client_instance = MagicMock()
        mock_client_instance.is_authenticated.return_value = True
        mock_hvac_client.return_value = mock_client_instance

        # Act
        vault_service = VaultEncryptionService()

        # Assert
        assert vault_service is not None
        mock_hvac_client.assert_called_once_with(
            url="http://vault.example.com:8200",
            token="test-token",
            namespace="my-namespace",
        )

    @patch('codemie.service.encryption.vault_encryption_service.config')
    def test_init_missing_vault_url(self, mock_config):
        """Test initialization fails when VAULT_URL is missing."""
        # Arrange
        mock_config.VAULT_URL = ""
        mock_config.VAULT_TOKEN = "test-token"

        # Act & Assert
        with pytest.raises(ValueError, match="VAULT_URL configuration is required"):
            VaultEncryptionService()

    @patch('codemie.service.encryption.vault_encryption_service.config')
    def test_init_missing_vault_token(self, mock_config):
        """Test initialization fails when VAULT_TOKEN is missing."""
        # Arrange
        mock_config.VAULT_URL = "http://vault.example.com:8200"
        mock_config.VAULT_TOKEN = ""

        # Act & Assert
        with pytest.raises(ValueError, match="VAULT_TOKEN configuration is required"):
            VaultEncryptionService()

    @patch('codemie.service.encryption.vault_encryption_service.hvac.Client')
    @patch('codemie.service.encryption.vault_encryption_service.config')
    def test_init_authentication_failed(self, mock_config, mock_hvac_client):
        """Test initialization fails when Vault authentication fails."""
        # Arrange
        mock_config.VAULT_URL = "http://vault.example.com:8200"
        mock_config.VAULT_TOKEN = "invalid-token"
        mock_config.VAULT_NAMESPACE = ""

        mock_client_instance = MagicMock()
        mock_client_instance.is_authenticated.return_value = False
        mock_hvac_client.return_value = mock_client_instance

        # Act & Assert
        with pytest.raises(ValueError, match="Failed to authenticate with Vault server"):
            VaultEncryptionService()


class TestVaultEncryptionServiceEncrypt:
    """Test VaultEncryptionService encryption functionality."""

    @pytest.fixture
    @patch('codemie.service.encryption.vault_encryption_service.hvac.Client')
    @patch('codemie.service.encryption.vault_encryption_service.config')
    def vault_service(self, mock_config, mock_hvac_client):
        """Create a VaultEncryptionService instance with mocked dependencies."""
        mock_config.VAULT_URL = "http://vault.example.com:8200"
        mock_config.VAULT_TOKEN = "test-token"
        mock_config.VAULT_NAMESPACE = ""
        mock_config.VAULT_TRANSIT_KEY_NAME = "codemie"
        mock_config.VAULT_TRANSIT_MOUNT_POINT = "transit"

        mock_client_instance = MagicMock()
        mock_client_instance.is_authenticated.return_value = True
        mock_hvac_client.return_value = mock_client_instance

        return VaultEncryptionService()

    def test_encrypt_success(self, vault_service):
        """Test successful encryption."""
        # Arrange
        test_data = "sensitive-data"
        expected_ciphertext = "vault:v1:ciphertext_base64_encoded"

        vault_service.client.secrets.transit.encrypt_data.return_value = {'data': {'ciphertext': expected_ciphertext}}

        # Act
        result = vault_service.encrypt(test_data)

        # Assert
        assert result == expected_ciphertext
        vault_service.client.secrets.transit.encrypt_data.assert_called_once()
        call_args = vault_service.client.secrets.transit.encrypt_data.call_args
        assert call_args[1]['name'] == 'codemie'
        assert call_args[1]['mount_point'] == 'transit'
        # Verify plaintext was base64 encoded
        plaintext_b64 = call_args[1]['plaintext']
        decoded = base64.b64decode(plaintext_b64).decode('utf-8')
        assert decoded == test_data

    def test_encrypt_empty_data(self, vault_service):
        """Test encryption fails with empty data."""
        # Act & Assert
        with pytest.raises(ValueError, match="Data to encrypt cannot be empty"):
            vault_service.encrypt("")

    def test_encrypt_vault_error(self, vault_service):
        """Test encryption handles VaultError exceptions."""
        # Arrange
        test_data = "sensitive-data"
        vault_service.client.secrets.transit.encrypt_data.side_effect = hvac.exceptions.VaultError("Vault error")

        # Act & Assert
        with pytest.raises(hvac.exceptions.VaultError):
            vault_service.encrypt(test_data)

    def test_encrypt_invalid_path(self, vault_service):
        """Test encryption handles InvalidPath exceptions."""
        # Arrange
        test_data = "sensitive-data"
        vault_service.client.secrets.transit.encrypt_data.side_effect = hvac.exceptions.InvalidPath("Key not found")

        # Act & Assert
        with pytest.raises(hvac.exceptions.InvalidPath):
            vault_service.encrypt(test_data)

    def test_encrypt_generic_exception(self, vault_service):
        """Test encryption handles generic exceptions."""
        # Arrange
        test_data = "sensitive-data"
        vault_service.client.secrets.transit.encrypt_data.side_effect = Exception("Unexpected error")

        # Act & Assert
        with pytest.raises(Exception, match="Unexpected error"):
            vault_service.encrypt(test_data)


class TestVaultEncryptionServiceDecrypt:
    """Test VaultEncryptionService decryption functionality."""

    @pytest.fixture
    @patch('codemie.service.encryption.vault_encryption_service.hvac.Client')
    @patch('codemie.service.encryption.vault_encryption_service.config')
    def vault_service(self, mock_config, mock_hvac_client):
        """Create a VaultEncryptionService instance with mocked dependencies."""
        mock_config.VAULT_URL = "http://vault.example.com:8200"
        mock_config.VAULT_TOKEN = "test-token"
        mock_config.VAULT_NAMESPACE = ""
        mock_config.VAULT_TRANSIT_KEY_NAME = "codemie"
        mock_config.VAULT_TRANSIT_MOUNT_POINT = "transit"

        mock_client_instance = MagicMock()
        mock_client_instance.is_authenticated.return_value = True
        mock_hvac_client.return_value = mock_client_instance

        return VaultEncryptionService()

    def test_decrypt_success(self, vault_service):
        """Test successful decryption."""
        # Arrange
        ciphertext = "vault:v1:ciphertext_base64_encoded"
        expected_plaintext = "sensitive-data"
        plaintext_b64 = base64.b64encode(expected_plaintext.encode('utf-8')).decode('utf-8')

        vault_service.client.secrets.transit.decrypt_data.return_value = {'data': {'plaintext': plaintext_b64}}

        # Act
        result = vault_service.decrypt(ciphertext)

        # Assert
        assert result == expected_plaintext
        vault_service.client.secrets.transit.decrypt_data.assert_called_once_with(
            name='codemie',
            ciphertext=ciphertext,
            mount_point='transit',
        )

    def test_decrypt_vault_error_returns_original(self, vault_service):
        """Test decryption returns original data on VaultError (backward compatibility)."""
        # Arrange
        ciphertext = "vault:v1:ciphertext_base64_encoded"
        vault_service.client.secrets.transit.decrypt_data.side_effect = hvac.exceptions.VaultError("Decryption failed")

        # Act
        result = vault_service.decrypt(ciphertext)

        # Assert - should return original data
        assert result == ciphertext

    def test_decrypt_invalid_path_returns_original(self, vault_service):
        """Test decryption returns original data on InvalidPath (backward compatibility)."""
        # Arrange
        ciphertext = "vault:v1:ciphertext_base64_encoded"
        vault_service.client.secrets.transit.decrypt_data.side_effect = hvac.exceptions.InvalidPath("Key not found")

        # Act
        result = vault_service.decrypt(ciphertext)

        # Assert - should return original data
        assert result == ciphertext

    def test_decrypt_generic_exception_returns_original(self, vault_service):
        """Test decryption returns original data on generic exception (backward compatibility)."""
        # Arrange
        ciphertext = "vault:v1:ciphertext_base64_encoded"
        vault_service.client.secrets.transit.decrypt_data.side_effect = Exception("Unexpected error")

        # Act
        result = vault_service.decrypt(ciphertext)

        # Assert - should return original data
        assert result == ciphertext

    def test_decrypt_invalid_type(self, vault_service):
        """Test decryption fails with non-string data."""
        # Act & Assert
        with pytest.raises(TypeError, match="Data to decrypt must be a string"):
            vault_service.decrypt(12345)  # type: ignore

    def test_decrypt_with_unicode_data(self, vault_service):
        """Test decryption with Unicode characters."""
        # Arrange
        ciphertext = "vault:v1:ciphertext_base64_encoded"
        expected_plaintext = "Hello 世界 🌍"
        plaintext_b64 = base64.b64encode(expected_plaintext.encode('utf-8')).decode('utf-8')

        vault_service.client.secrets.transit.decrypt_data.return_value = {'data': {'plaintext': plaintext_b64}}

        # Act
        result = vault_service.decrypt(ciphertext)

        # Assert
        assert result == expected_plaintext


class TestVaultEncryptionServiceIntegration:
    """Test VaultEncryptionService end-to-end encryption/decryption."""

    @pytest.fixture
    @patch('codemie.service.encryption.vault_encryption_service.hvac.Client')
    @patch('codemie.service.encryption.vault_encryption_service.config')
    def vault_service(self, mock_config, mock_hvac_client):
        """Create a VaultEncryptionService instance with mocked dependencies."""
        mock_config.VAULT_URL = "http://vault.example.com:8200"
        mock_config.VAULT_TOKEN = "test-token"
        mock_config.VAULT_NAMESPACE = ""
        mock_config.VAULT_TRANSIT_KEY_NAME = "codemie"
        mock_config.VAULT_TRANSIT_MOUNT_POINT = "transit"

        mock_client_instance = MagicMock()
        mock_client_instance.is_authenticated.return_value = True
        mock_hvac_client.return_value = mock_client_instance

        return VaultEncryptionService()

    def test_encrypt_decrypt_roundtrip(self, vault_service):
        """Test encrypt followed by decrypt returns original data."""
        # Arrange
        original_data = "my-secret-password"
        ciphertext = "vault:v1:encrypted_data_here"
        plaintext_b64 = base64.b64encode(original_data.encode('utf-8')).decode('utf-8')

        # Mock encrypt
        vault_service.client.secrets.transit.encrypt_data.return_value = {'data': {'ciphertext': ciphertext}}

        # Mock decrypt
        vault_service.client.secrets.transit.decrypt_data.return_value = {'data': {'plaintext': plaintext_b64}}

        # Act
        encrypted = vault_service.encrypt(original_data)
        decrypted = vault_service.decrypt(encrypted)

        # Assert
        assert encrypted == ciphertext
        assert decrypted == original_data
