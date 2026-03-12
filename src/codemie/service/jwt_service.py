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
from typing import Optional
from uuid import UUID

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException


class JwtLocalService:
    """JWT service for local authentication using RS256"""

    _private_key: Optional[str] = None
    _public_key: Optional[str] = None

    def load_or_create_keys(self) -> tuple[str, str]:
        """Load existing keys or generate new RSA key pair

        Returns:
            Tuple of (private_key_pem, public_key_pem)
        """
        if self._private_key and self._public_key:
            return self._private_key, self._public_key

        private_path = config.JWT_PRIVATE_KEY_PATH
        public_path = config.JWT_PUBLIC_KEY_PATH

        if not os.path.exists(private_path):
            logger.info("JWT keys not found, generating new RSA key pair")
            self._generate_key_pair(private_path, public_path)

        # Load keys from files
        with open(private_path, "rb") as f:
            self._private_key = f.read().decode()

        with open(public_path, "rb") as f:
            self._public_key = f.read().decode()

        logger.info("JWT keys loaded successfully")
        return self._private_key, self._public_key

    def _generate_key_pair(self, private_path: str, public_path: str) -> None:
        """Generate new RSA key pair and save to files

        Args:
            private_path: Path to save private key
            public_path: Path to save public key
        """
        # Generate RSA key pair (2048-bit)
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048, backend=default_backend())
        public_key = private_key.public_key()

        # Ensure directory exists
        os.makedirs(os.path.dirname(private_path), exist_ok=True)

        # Save private key (PKCS8 format, no encryption)
        with open(private_path, "wb") as f:
            f.write(
                private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )

        # Save public key (SubjectPublicKeyInfo format)
        with open(public_path, "wb") as f:
            f.write(
                public_key.public_bytes(
                    encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo
                )
            )

        logger.info(f"Generated new RSA key pair at {private_path}")

    def generate_access_token(self, user_id: str, email: str, auth_source: str = "local") -> str:
        """Generate RS256 JWT for local authentication

        Args:
            user_id: User UUID
            email: User email
            auth_source: Authentication source (default: 'local')

        Returns:
            JWT token string
        """
        private_key, _ = self.load_or_create_keys()

        now = datetime.now(timezone.utc)
        payload = {
            "sub": user_id,
            "email": email,
            "auth_source": auth_source,
            "iss": config.JWT_ISSUER,  # "codemie-local"
            "iat": now,
            "exp": now + timedelta(hours=config.JWT_EXPIRATION_HOURS),
        }

        return jwt.encode(payload, private_key, algorithm=config.JWT_ALGORITHM)

    def validate_local_jwt(self, token: str) -> dict:
        """Validate local JWT and return claims

        Args:
            token: JWT token string

        Returns:
            Dict of claims

        Raises:
            ExtendedHTTPException: 401 for invalid token, 422 for invalid user ID
        """
        _, public_key = self.load_or_create_keys()

        try:
            claims = jwt.decode(
                token,
                public_key,
                algorithms=[config.JWT_ALGORITHM],
                options={"verify_signature": True, "verify_exp": True, "require": ["sub", "iss"]},
            )

            # Verify issuer is codemie-local
            if claims.get("iss") != config.JWT_ISSUER:
                raise ExtendedHTTPException(code=401, message="Invalid JWT issuer")

            # Validate sub is UUID
            try:
                UUID(claims["sub"])
            except (ValueError, KeyError):
                raise ExtendedHTTPException(code=422, message="User ID must be a valid UUID")

            return claims

        except jwt.ExpiredSignatureError:
            raise ExtendedHTTPException(code=401, message="JWT has expired")
        except jwt.InvalidIssuerError:
            raise ExtendedHTTPException(code=401, message="Invalid JWT issuer")
        except jwt.InvalidTokenError as e:
            raise ExtendedHTTPException(code=401, message=f"Invalid JWT: {str(e)}")

    def get_public_jwks(self) -> dict:
        """Get public key in JWKS format for /.well-known/jwks.json

        Returns:
            JWKS dict with public key
        """
        _, public_key_pem = self.load_or_create_keys()

        # Load public key
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        public_key = load_pem_public_key(public_key_pem.encode(), backend=default_backend())

        # Get key numbers
        public_numbers = public_key.public_numbers()

        # Convert to base64url encoding
        import base64

        def int_to_base64url(n: int) -> str:
            """Convert integer to base64url encoding"""
            byte_length = (n.bit_length() + 7) // 8
            n_bytes = n.to_bytes(byte_length, byteorder='big')
            return base64.urlsafe_b64encode(n_bytes).rstrip(b'=').decode('ascii')

        return {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": config.JWT_ALGORITHM,
                    "n": int_to_base64url(public_numbers.n),
                    "e": int_to_base64url(public_numbers.e),
                }
            ]
        }

    def clear_cached_keys(self) -> None:
        """Clear cached keys (for testing)"""
        self._private_key = None
        self._public_key = None


# Singleton instance
jwt_service = JwtLocalService()
