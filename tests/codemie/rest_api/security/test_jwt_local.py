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

from __future__ import annotations

from unittest.mock import patch

from codemie.rest_api.security import jwt_local


class TestJwtLocalDelegation:
    """Test suite for jwt_local module - delegation to jwt_service"""

    @patch("codemie.rest_api.security.jwt_local.jwt_service")
    def test_generate_access_token_delegates(self, mock_jwt_service):
        """Test that generate_access_token delegates to jwt_service"""
        # Arrange
        user_id = "user-123"
        email = "test@example.com"
        auth_source = "local"
        expected_token = "mock.jwt.token"
        mock_jwt_service.generate_access_token.return_value = expected_token

        # Act
        result = jwt_local.generate_access_token(user_id, email, auth_source)

        # Assert
        mock_jwt_service.generate_access_token.assert_called_once_with(user_id, email, auth_source)
        assert result == expected_token

    @patch("codemie.rest_api.security.jwt_local.jwt_service")
    def test_generate_access_token_delegates_with_default_auth_source(self, mock_jwt_service):
        """Test that generate_access_token uses default auth_source='local'"""
        # Arrange
        user_id = "user-456"
        email = "default@example.com"
        expected_token = "default.jwt.token"
        mock_jwt_service.generate_access_token.return_value = expected_token

        # Act
        result = jwt_local.generate_access_token(user_id, email)

        # Assert
        mock_jwt_service.generate_access_token.assert_called_once_with(user_id, email, "local")
        assert result == expected_token

    @patch("codemie.rest_api.security.jwt_local.jwt_service")
    def test_validate_local_jwt_delegates(self, mock_jwt_service):
        """Test that validate_local_jwt delegates to jwt_service"""
        # Arrange
        token = "test.jwt.token"
        expected_claims = {"sub": "user-789", "email": "validate@example.com", "iss": "codemie-local"}
        mock_jwt_service.validate_local_jwt.return_value = expected_claims

        # Act
        result = jwt_local.validate_local_jwt(token)

        # Assert
        mock_jwt_service.validate_local_jwt.assert_called_once_with(token)
        assert result == expected_claims

    @patch("codemie.rest_api.security.jwt_local.jwt_service")
    def test_get_public_jwks_delegates(self, mock_jwt_service):
        """Test that get_public_jwks delegates to jwt_service"""
        # Arrange
        expected_jwks = {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "n": "mock_modulus",
                    "e": "mock_exponent",
                }
            ]
        }
        mock_jwt_service.get_public_jwks.return_value = expected_jwks

        # Act
        result = jwt_local.get_public_jwks()

        # Assert
        mock_jwt_service.get_public_jwks.assert_called_once()
        assert result == expected_jwks

    @patch("codemie.rest_api.security.jwt_local.jwt_service")
    def test_load_or_create_keys_delegates(self, mock_jwt_service):
        """Test that load_or_create_keys delegates to jwt_service"""
        # Arrange
        expected_private = "-----BEGIN PRIVATE KEY-----\nmock_private\n-----END PRIVATE KEY-----"
        expected_public = "-----BEGIN PUBLIC KEY-----\nmock_public\n-----END PUBLIC KEY-----"
        mock_jwt_service.load_or_create_keys.return_value = (expected_private, expected_public)

        # Act
        private_key, public_key = jwt_local.load_or_create_keys()

        # Assert
        mock_jwt_service.load_or_create_keys.assert_called_once()
        assert private_key == expected_private
        assert public_key == expected_public
