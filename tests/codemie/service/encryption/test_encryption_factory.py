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
from unittest.mock import patch, MagicMock

from codemie.service.encryption.encryption_factory import EncryptionFactory, EncryptionType
from codemie.service.encryption.base_encryption_service import PlainEncryptionService, Base64EncryptionService
from codemie.service.encryption.aws_encryption_service import AWSKMSEncryptionService
from codemie.service.encryption.azure_encryption_service import AzureKMSEncryptionService
from codemie.service.encryption.gcp_encryption_service import GCPKMSEncryptionService
from codemie.service.encryption.vault_encryption_service import VaultEncryptionService


class TestEncryptionType:
    """Test EncryptionType enum."""

    def test_encryption_types_exist(self):
        """Test all encryption types are defined."""
        assert EncryptionType.PLAIN_TEXT == "plain"
        assert EncryptionType.GCP_ENCRYPTION == "gcp"
        assert EncryptionType.AWS_ENCRYPTION == "aws"
        assert EncryptionType.AZURE_ENCRYPTION == "azure"
        assert EncryptionType.BASE64_ENCRYPTION == "base64"
        assert EncryptionType.VAULT_ENCRYPTION == "vault"


class TestEncryptionFactoryGetService:
    """Test EncryptionFactory.get_encryption_service method."""

    def test_get_plain_encryption_service(self):
        """Test factory returns PlainEncryptionService for plain type."""
        # Act
        service = EncryptionFactory.get_encryption_service(EncryptionType.PLAIN_TEXT)

        # Assert
        assert isinstance(service, PlainEncryptionService)

    def test_get_base64_encryption_service(self):
        """Test factory returns Base64EncryptionService for base64 type."""
        # Act
        service = EncryptionFactory.get_encryption_service(EncryptionType.BASE64_ENCRYPTION)

        # Assert
        assert isinstance(service, Base64EncryptionService)

    @patch('codemie.service.encryption.aws_encryption_service.boto3')
    @patch('codemie.service.encryption.aws_encryption_service.config')
    def test_get_aws_encryption_service(self, mock_config, mock_boto3):
        """Test factory returns AWSKMSEncryptionService for aws type."""
        # Arrange
        mock_config.AWS_KMS_KEY_ID = "test-key-id"
        mock_config.AWS_KMS_REGION = "us-west-2"
        mock_kms_client = MagicMock()
        mock_boto3.client.return_value = mock_kms_client

        # Act
        service = EncryptionFactory.get_encryption_service(EncryptionType.AWS_ENCRYPTION)

        # Assert
        assert isinstance(service, AWSKMSEncryptionService)

    @patch('codemie.service.encryption.azure_encryption_service.KeyClient')
    @patch('codemie.service.encryption.azure_encryption_service.CryptographyClient')
    @patch('codemie.service.encryption.azure_encryption_service.DefaultAzureCredential')
    @patch('codemie.service.encryption.azure_encryption_service.config')
    def test_get_azure_encryption_service(self, mock_config, mock_cred, mock_crypto, mock_key_client):
        """Test factory returns AzureKMSEncryptionService for azure type."""
        # Arrange
        mock_config.AZURE_KEY_VAULT_URL = "https://vault.azure.net"
        mock_config.AZURE_KEY_NAME = "test-key"
        mock_key_client_instance = MagicMock()
        mock_key = MagicMock()
        mock_key_client_instance.get_key.return_value = mock_key
        mock_key_client.return_value = mock_key_client_instance

        # Act
        service = EncryptionFactory.get_encryption_service(EncryptionType.AZURE_ENCRYPTION)

        # Assert
        assert isinstance(service, AzureKMSEncryptionService)

    @patch('codemie.service.encryption.gcp_encryption_service.kms.KeyManagementServiceClient')
    @patch('codemie.service.encryption.gcp_encryption_service.config')
    def test_get_gcp_encryption_service(self, mock_config, mock_kms_client):
        """Test factory returns GCPKMSEncryptionService for gcp type."""
        # Arrange
        mock_config.GOOGLE_KMS_PROJECT_ID = "test-project"
        mock_config.GOOGLE_KMS_REGION = "us-central1"
        mock_config.GOOGLE_KMS_KEY_RING = "test-keyring"
        mock_config.GOOGLE_KMS_CRYPTO_KEY = "test-key"

        mock_client_instance = MagicMock()
        mock_client_instance.crypto_key_path.return_value = "projects/test/locations/us/keyRings/ring/cryptoKeys/key"
        mock_kms_client.return_value = mock_client_instance

        # Act
        service = EncryptionFactory.get_encryption_service(EncryptionType.GCP_ENCRYPTION)

        # Assert
        assert isinstance(service, GCPKMSEncryptionService)

    @patch('codemie.service.encryption.vault_encryption_service.hvac.Client')
    @patch('codemie.service.encryption.vault_encryption_service.config')
    def test_get_vault_encryption_service(self, mock_config, mock_hvac_client):
        """Test factory returns VaultEncryptionService for vault type."""
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
        service = EncryptionFactory.get_encryption_service(EncryptionType.VAULT_ENCRYPTION)

        # Assert
        assert isinstance(service, VaultEncryptionService)

    def test_get_encryption_service_invalid_type(self):
        """Test factory raises error for unsupported type."""
        # Act & Assert
        with pytest.raises(ValueError, match="Unsupported encryption service type"):
            EncryptionFactory.get_encryption_service("invalid_type")  # type: ignore


class TestEncryptionFactoryGetCurrentService:
    """Test EncryptionFactory.get_current_encryption_service method."""

    @patch('codemie.service.encryption.encryption_factory.config')
    def test_get_current_service_plain_text(self, mock_config):
        """Test getting current service when ENCRYPTION_TYPE is plain."""
        # Arrange
        mock_config.ENCRYPTION_TYPE = "plain"

        # Act
        service = EncryptionFactory.get_current_encryption_service()

        # Assert
        assert isinstance(service, PlainEncryptionService)

    @patch('codemie.service.encryption.encryption_factory.config')
    def test_get_current_service_base64(self, mock_config):
        """Test getting current service when ENCRYPTION_TYPE is base64."""
        # Arrange
        mock_config.ENCRYPTION_TYPE = "base64"

        # Act
        service = EncryptionFactory.get_current_encryption_service()

        # Assert
        assert isinstance(service, Base64EncryptionService)

    @patch('codemie.service.encryption.vault_encryption_service.hvac.Client')
    @patch('codemie.service.encryption.vault_encryption_service.config')
    @patch('codemie.service.encryption.encryption_factory.config')
    def test_get_current_service_vault(self, mock_factory_config, mock_vault_config, mock_hvac_client):
        """Test getting current service when ENCRYPTION_TYPE is vault."""
        # Arrange
        mock_factory_config.ENCRYPTION_TYPE = "vault"
        mock_vault_config.VAULT_URL = "http://vault.example.com:8200"
        mock_vault_config.VAULT_TOKEN = "test-token"
        mock_vault_config.VAULT_NAMESPACE = ""
        mock_vault_config.VAULT_TRANSIT_KEY_NAME = "codemie"
        mock_vault_config.VAULT_TRANSIT_MOUNT_POINT = "transit"

        mock_client_instance = MagicMock()
        mock_client_instance.is_authenticated.return_value = True
        mock_hvac_client.return_value = mock_client_instance

        # Act
        service = EncryptionFactory.get_current_encryption_service()

        # Assert
        assert isinstance(service, VaultEncryptionService)

    @patch('codemie.service.encryption.encryption_factory.config')
    def test_get_current_service_empty_defaults_to_plain(self, mock_config):
        """Test getting current service defaults to plain when ENCRYPTION_TYPE is empty."""
        # Arrange
        mock_config.ENCRYPTION_TYPE = ""

        # Act
        service = EncryptionFactory.get_current_encryption_service()

        # Assert
        assert isinstance(service, PlainEncryptionService)

    @patch('codemie.service.encryption.encryption_factory.config')
    def test_get_current_service_none_defaults_to_plain(self, mock_config):
        """Test getting current service defaults to plain when ENCRYPTION_TYPE is None."""
        # Arrange
        mock_config.ENCRYPTION_TYPE = None

        # Act
        service = EncryptionFactory.get_current_encryption_service()

        # Assert
        assert isinstance(service, PlainEncryptionService)

    @patch('codemie.service.encryption.encryption_factory.logger')
    @patch('codemie.service.encryption.encryption_factory.config')
    def test_get_current_service_exception_defaults_to_plain(self, mock_config, mock_logger):
        """Test getting current service defaults to plain when exception occurs."""
        # Arrange
        mock_config.ENCRYPTION_TYPE = "invalid"

        # Act
        service = EncryptionFactory.get_current_encryption_service()

        # Assert
        assert isinstance(service, PlainEncryptionService)
        mock_logger.error.assert_called()


class TestEncryptionFactoryGetServiceType:
    """Test EncryptionFactory.get_current_encryption_service_type method."""

    @patch('codemie.service.encryption.encryption_factory.config')
    def test_get_service_type_plain(self, mock_config):
        """Test getting service type when ENCRYPTION_TYPE is plain."""
        # Arrange
        mock_config.ENCRYPTION_TYPE = "plain"

        # Act
        service_type = EncryptionFactory.get_current_encryption_service_type()

        # Assert
        assert service_type == EncryptionType.PLAIN_TEXT

    @patch('codemie.service.encryption.encryption_factory.config')
    def test_get_service_type_vault(self, mock_config):
        """Test getting service type when ENCRYPTION_TYPE is vault."""
        # Arrange
        mock_config.ENCRYPTION_TYPE = "vault"

        # Act
        service_type = EncryptionFactory.get_current_encryption_service_type()

        # Assert
        assert service_type == EncryptionType.VAULT_ENCRYPTION

    @patch('codemie.service.encryption.encryption_factory.config')
    def test_get_service_type_empty_defaults_to_plain(self, mock_config):
        """Test getting service type defaults to plain when ENCRYPTION_TYPE is empty."""
        # Arrange
        mock_config.ENCRYPTION_TYPE = ""

        # Act
        service_type = EncryptionFactory.get_current_encryption_service_type()

        # Assert
        assert service_type == EncryptionType.PLAIN_TEXT

    @patch('codemie.service.encryption.encryption_factory.logger')
    @patch('codemie.service.encryption.encryption_factory.config')
    def test_get_service_type_exception_defaults_to_plain(self, mock_config, mock_logger):
        """Test getting service type defaults to plain when exception occurs."""
        # Arrange
        mock_config.ENCRYPTION_TYPE = MagicMock(side_effect=Exception("Test error"))

        # Act
        service_type = EncryptionFactory.get_current_encryption_service_type()

        # Assert
        assert service_type == EncryptionType.PLAIN_TEXT
        mock_logger.error.assert_called()
