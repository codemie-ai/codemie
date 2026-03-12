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

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHash

from codemie.configs.logger import logger


class PasswordService:
    """Password hashing service using Argon2

    Uses default PasswordHasher parameters which follow RFC 9106
    recommendations for secure password hashing.
    """

    def __init__(self):
        # Use default parameters (RFC 9106 compliant)
        # time_cost=3, memory_cost=65536, parallelism=4
        self._hasher = PasswordHasher()

    def hash_password(self, password: str) -> str:
        """Hash a password using Argon2

        Args:
            password: Plain text password

        Returns:
            Argon2 hash string (includes algorithm parameters)
        """
        return self._hasher.hash(password)

    def verify_password(self, password_hash: str, password: str) -> bool:
        """Verify a password against its hash

        Args:
            password_hash: Stored Argon2 hash
            password: Plain text password to verify

        Returns:
            True if password matches, False otherwise
        """
        try:
            self._hasher.verify(password_hash, password)
            return True
        except VerifyMismatchError:
            return False
        except InvalidHash:
            logger.warning("Invalid hash format encountered during password verification")
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        """Check if password hash needs to be updated

        If Argon2 parameters have changed (e.g., increased security),
        this returns True to indicate the password should be rehashed
        on next successful login.

        Args:
            password_hash: Stored Argon2 hash

        Returns:
            True if hash should be updated, False otherwise
        """
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHash:
            return True  # Invalid hash should be replaced


# Singleton instance
password_service = PasswordService()
