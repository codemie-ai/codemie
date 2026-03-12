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

"""JWT local authentication wrapper module

This module provides convenience functions for local JWT authentication by wrapping
the JwtLocalService from the service layer. This allows routers to import from a
security-focused path while keeping the actual implementation in the service layer.
"""

from codemie.service.jwt_service import jwt_service


# Export convenience functions that wrap the service singleton
def generate_access_token(user_id: str, email: str, auth_source: str = "local") -> str:
    """Generate RS256 JWT for local authentication

    Args:
        user_id: User UUID
        email: User email
        auth_source: Authentication source (default: 'local')

    Returns:
        JWT token string
    """
    return jwt_service.generate_access_token(user_id, email, auth_source)


def validate_local_jwt(token: str) -> dict:
    """Validate local JWT and return claims

    Args:
        token: JWT token string

    Returns:
        Dict of claims

    Raises:
        ExtendedHTTPException: 401 for invalid token, 422 for invalid user ID
    """
    return jwt_service.validate_local_jwt(token)


def get_public_jwks() -> dict:
    """Get public key in JWKS format for /.well-known/jwks.json

    Returns:
        JWKS dict with public key
    """
    return jwt_service.get_public_jwks()


def load_or_create_keys() -> tuple[str, str]:
    """Load existing keys or generate new RSA key pair

    Returns:
        Tuple of (private_key_pem, public_key_pem)
    """
    return jwt_service.load_or_create_keys()
