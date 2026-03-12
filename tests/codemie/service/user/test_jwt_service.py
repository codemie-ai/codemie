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

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.jwt_service import JwtLocalService, jwt_service


class TestJwtLocalService:
    """Test suite for JwtLocalService - RS256 JWT operations"""

    @pytest.fixture
    def service(self):
        """Create a fresh JwtLocalService instance for each test"""
        service = JwtLocalService()
        service.clear_cached_keys()
        return service

    @pytest.fixture
    def temp_key_paths(self, tmp_path):
        """Provide temporary paths for JWT keys"""
        private_path = str(tmp_path / "jwt_private.pem")
        public_path = str(tmp_path / "jwt_public.pem")
        return private_path, public_path

    def test_load_or_create_keys_generates_when_missing(self, service, temp_key_paths, monkeypatch):
        """Test that load_or_create_keys generates new keys when files don't exist"""
        # Arrange
        private_path, public_path = temp_key_paths
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PRIVATE_KEY_PATH", private_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PUBLIC_KEY_PATH", public_path)

        # Act
        private_key, public_key = service.load_or_create_keys()

        # Assert
        assert private_key.startswith("-----BEGIN PRIVATE KEY-----")
        assert public_key.startswith("-----BEGIN PUBLIC KEY-----")
        assert os.path.exists(private_path)
        assert os.path.exists(public_path)

    def test_load_or_create_keys_loads_existing(self, service, temp_key_paths, monkeypatch):
        """Test that load_or_create_keys loads keys from existing files"""
        # Arrange
        private_path, public_path = temp_key_paths
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PRIVATE_KEY_PATH", private_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PUBLIC_KEY_PATH", public_path)

        # Generate keys first
        service.load_or_create_keys()

        # Read the generated keys
        with open(private_path, "r") as f:
            expected_private = f.read()
        with open(public_path, "r") as f:
            expected_public = f.read()

        # Create a new service instance
        service2 = JwtLocalService()
        service2.clear_cached_keys()

        # Act
        private_key, public_key = service2.load_or_create_keys()

        # Assert
        assert private_key == expected_private
        assert public_key == expected_public

    def test_load_or_create_keys_caches_result(self, service, temp_key_paths, monkeypatch):
        """Test that load_or_create_keys caches keys and doesn't reload from disk"""
        # Arrange
        private_path, public_path = temp_key_paths
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PRIVATE_KEY_PATH", private_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PUBLIC_KEY_PATH", public_path)

        # Act
        private1, public1 = service.load_or_create_keys()
        private2, public2 = service.load_or_create_keys()

        # Assert
        assert private1 is private2  # Same object reference (cached)
        assert public1 is public2  # Same object reference (cached)

    def test_generate_access_token_valid_jwt(self, service, temp_key_paths, monkeypatch):
        """Test that generate_access_token produces a decodable JWT"""
        # Arrange
        private_path, public_path = temp_key_paths
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PRIVATE_KEY_PATH", private_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PUBLIC_KEY_PATH", public_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_ALGORITHM", "RS256")

        user_id = str(uuid4())
        email = "test@example.com"

        # Act
        token = service.generate_access_token(user_id, email, "local")
        _, public_key = service.load_or_create_keys()
        decoded = jwt.decode(token, public_key, algorithms=["RS256"])

        # Assert
        assert decoded is not None
        assert decoded["sub"] == user_id
        assert decoded["email"] == email

    def test_generate_access_token_contains_claims(self, service, temp_key_paths, monkeypatch):
        """Test that JWT contains all required claims"""
        # Arrange
        private_path, public_path = temp_key_paths
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PRIVATE_KEY_PATH", private_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PUBLIC_KEY_PATH", public_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_ALGORITHM", "RS256")
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_ISSUER", "codemie-local")
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_EXPIRATION_HOURS", 24)

        user_id = str(uuid4())
        email = "user@example.com"
        auth_source = "local"

        # Act
        token = service.generate_access_token(user_id, email, auth_source)
        _, public_key = service.load_or_create_keys()
        decoded = jwt.decode(token, public_key, algorithms=["RS256"])

        # Assert
        assert decoded["sub"] == user_id
        assert decoded["email"] == email
        assert decoded["auth_source"] == auth_source
        assert decoded["iss"] == "codemie-local"
        assert "iat" in decoded
        assert "exp" in decoded

        # Check expiration is roughly 24 hours from now
        iat = datetime.fromtimestamp(decoded["iat"], timezone.utc)
        exp = datetime.fromtimestamp(decoded["exp"], timezone.utc)
        delta = exp - iat
        assert abs(delta.total_seconds() - 24 * 3600) < 5  # Within 5 seconds

    def test_validate_local_jwt_valid_token(self, service, temp_key_paths, monkeypatch):
        """Test round-trip: generate -> validate"""
        # Arrange
        private_path, public_path = temp_key_paths
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PRIVATE_KEY_PATH", private_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PUBLIC_KEY_PATH", public_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_ALGORITHM", "RS256")
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_ISSUER", "codemie-local")
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_EXPIRATION_HOURS", 24)

        user_id = str(uuid4())
        email = "validate@example.com"

        token = service.generate_access_token(user_id, email)

        # Act
        claims = service.validate_local_jwt(token)

        # Assert
        assert claims["sub"] == user_id
        assert claims["email"] == email
        assert claims["iss"] == "codemie-local"

    def test_validate_local_jwt_expired_token(self, service, temp_key_paths, monkeypatch):
        """Test that validate_local_jwt raises 401 for expired token"""
        # Arrange
        private_path, public_path = temp_key_paths
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PRIVATE_KEY_PATH", private_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PUBLIC_KEY_PATH", public_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_ALGORITHM", "RS256")
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_ISSUER", "codemie-local")

        user_id = str(uuid4())
        email = "expired@example.com"

        private_key, _ = service.load_or_create_keys()

        # Create an expired token (exp in the past)
        past_time = datetime.now(timezone.utc) - timedelta(hours=1)
        payload = {
            "sub": user_id,
            "email": email,
            "auth_source": "local",
            "iss": "codemie-local",
            "iat": past_time - timedelta(hours=1),
            "exp": past_time,
        }
        expired_token = jwt.encode(payload, private_key, algorithm="RS256")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            service.validate_local_jwt(expired_token)

        assert exc_info.value.code == 401
        assert "expired" in exc_info.value.message.lower()

    def test_validate_local_jwt_wrong_issuer(self, service, temp_key_paths, monkeypatch):
        """Test that validate_local_jwt raises 401 for wrong issuer"""
        # Arrange
        private_path, public_path = temp_key_paths
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PRIVATE_KEY_PATH", private_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PUBLIC_KEY_PATH", public_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_ALGORITHM", "RS256")
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_ISSUER", "codemie-local")
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_EXPIRATION_HOURS", 24)

        user_id = str(uuid4())
        email = "issuer@example.com"

        private_key, _ = service.load_or_create_keys()

        # Create token with wrong issuer
        now = datetime.now(timezone.utc)
        payload = {
            "sub": user_id,
            "email": email,
            "auth_source": "local",
            "iss": "wrong-issuer",
            "iat": now,
            "exp": now + timedelta(hours=24),
        }
        wrong_issuer_token = jwt.encode(payload, private_key, algorithm="RS256")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            service.validate_local_jwt(wrong_issuer_token)

        assert exc_info.value.code == 401
        assert "issuer" in exc_info.value.message.lower()

    def test_validate_local_jwt_invalid_signature(self, service, temp_key_paths, monkeypatch):
        """Test that validate_local_jwt raises 401 for tampered token"""
        # Arrange
        private_path, public_path = temp_key_paths
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PRIVATE_KEY_PATH", private_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PUBLIC_KEY_PATH", public_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_ALGORITHM", "RS256")
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_ISSUER", "codemie-local")

        user_id = str(uuid4())
        email = "signature@example.com"

        token = service.generate_access_token(user_id, email)

        # Tamper with the token by changing a character
        tampered_token = token[:-10] + "XXXX" + token[-6:]

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            service.validate_local_jwt(tampered_token)

        assert exc_info.value.code == 401
        assert "invalid" in exc_info.value.message.lower()

    def test_validate_local_jwt_non_uuid_sub(self, service, temp_key_paths, monkeypatch):
        """Test that validate_local_jwt raises 422 for non-UUID sub claim"""
        # Arrange
        private_path, public_path = temp_key_paths
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PRIVATE_KEY_PATH", private_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PUBLIC_KEY_PATH", public_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_ALGORITHM", "RS256")
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_ISSUER", "codemie-local")
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_EXPIRATION_HOURS", 24)

        private_key, _ = service.load_or_create_keys()

        # Create token with non-UUID sub
        now = datetime.now(timezone.utc)
        payload = {
            "sub": "not-a-uuid",
            "email": "uuid@example.com",
            "auth_source": "local",
            "iss": "codemie-local",
            "iat": now,
            "exp": now + timedelta(hours=24),
        }
        invalid_uuid_token = jwt.encode(payload, private_key, algorithm="RS256")

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            service.validate_local_jwt(invalid_uuid_token)

        assert exc_info.value.code == 422
        assert "uuid" in exc_info.value.message.lower()

    def test_get_public_jwks_structure(self, service, temp_key_paths, monkeypatch):
        """Test that get_public_jwks returns valid JWKS structure"""
        # Arrange
        private_path, public_path = temp_key_paths
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PRIVATE_KEY_PATH", private_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PUBLIC_KEY_PATH", public_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_ALGORITHM", "RS256")

        service.load_or_create_keys()  # Generate keys

        # Act
        jwks = service.get_public_jwks()

        # Assert
        assert "keys" in jwks
        assert len(jwks["keys"]) == 1

        key = jwks["keys"][0]
        assert key["kty"] == "RSA"
        assert key["use"] == "sig"
        assert key["alg"] == "RS256"
        assert "n" in key  # Modulus
        assert "e" in key  # Exponent
        assert isinstance(key["n"], str)
        assert isinstance(key["e"], str)

    def test_clear_cached_keys(self, service):
        """Test that clear_cached_keys clears internal cache"""
        # Arrange
        service._private_key = "cached_private"
        service._public_key = "cached_public"

        # Act
        service.clear_cached_keys()

        # Assert
        assert service._private_key is None
        assert service._public_key is None

    def test_generate_key_pair_creates_directory(self, service, tmp_path):
        """Test that _generate_key_pair creates parent directory if needed"""
        # Arrange
        nested_dir = tmp_path / "nested" / "dir" / "structure"
        private_path = str(nested_dir / "private.pem")
        public_path = str(nested_dir / "public.pem")

        assert not nested_dir.exists()

        # Act
        service._generate_key_pair(private_path, public_path)

        # Assert
        assert nested_dir.exists()
        assert os.path.exists(private_path)
        assert os.path.exists(public_path)


class TestJwtServiceSingleton:
    """Test the jwt_service singleton instance"""

    def test_singleton_instance_exists(self):
        """Test that jwt_service singleton is properly initialized"""
        # Assert
        assert jwt_service is not None
        assert isinstance(jwt_service, JwtLocalService)

    def test_singleton_can_generate_token(self, tmp_path, monkeypatch):
        """Test that singleton instance can generate tokens"""
        # Arrange
        private_path = str(tmp_path / "jwt_private_singleton.pem")
        public_path = str(tmp_path / "jwt_public_singleton.pem")
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PRIVATE_KEY_PATH", private_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_PUBLIC_KEY_PATH", public_path)
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_ALGORITHM", "RS256")
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_ISSUER", "codemie-local")
        monkeypatch.setattr("codemie.service.jwt_service.config.JWT_EXPIRATION_HOURS", 24)

        jwt_service.clear_cached_keys()
        user_id = str(uuid4())
        email = "singleton@example.com"

        # Act
        token = jwt_service.generate_access_token(user_id, email)

        # Assert
        assert isinstance(token, str)
        assert len(token) > 100  # JWT tokens are long
