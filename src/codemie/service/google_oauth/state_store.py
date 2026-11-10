# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Google OAuth state storage service using Redis."""

import json
from typing import Optional

from codemie.clients.redis import create_redis_client
from codemie.configs import config, logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.encryption.encryption_factory import EncryptionFactory

STATE_KEY_PREFIX = "codemie:google_oauth:state:"
RESULT_KEY_PREFIX = "codemie:google_oauth:result:"
STATE_TTL = 600  # 10 minutes for OAuth state
RESULT_TTL = 300  # 5 minutes for callback results


class GoogleOAuthStateStore:
    """Manages OAuth state and result storage in Redis with encryption."""

    def __init__(self, redis_client=None, encryption_service=None):
        self.redis_client = redis_client or create_redis_client()
        self.encryption_service = encryption_service or EncryptionFactory().get_current_encryption_service()

    # ===== Private Helpers =====

    def _redis_get_encrypted(self, key: str) -> Optional[dict]:
        """Get and decrypt JSON payload from Redis.

        Args:
            key: Redis key.

        Returns:
            Decrypted dict or None if key doesn't exist.
        """
        try:
            raw = self.redis_client.get(key)
            if raw is None:
                return None
            decrypted = self.encryption_service.decrypt(raw.decode())
            return json.loads(decrypted)
        except Exception:
            return None

    def _redis_set_encrypted(self, key: str, payload: dict, ttl: int) -> bool:
        """Encrypt and store JSON payload in Redis.

        Args:
            key: Redis key.
            payload: Dict to encrypt and store.
            ttl: Time-to-live in seconds.

        Returns:
            True on success, False if Redis is unavailable.
        """
        try:
            encrypted = self.encryption_service.encrypt(json.dumps(payload))
            self.redis_client.set(key, encrypted, ex=ttl)
            return True
        except Exception as exc:
            logger.error(f"Google OAuth: Redis unavailable while storing {key}: {exc}")
            return False

    def _redis_getdel_encrypted(self, key: str) -> Optional[dict]:
        """Get, decrypt, and delete JSON payload from Redis atomically.

        Args:
            key: Redis key.

        Returns:
            Decrypted dict or None if key doesn't exist.

        Raises:
            Exception: On Redis or decryption failure.
        """
        raw = self.redis_client.getdel(key)
        if raw is None:
            return None
        decrypted = self.encryption_service.decrypt(raw.decode())
        return json.loads(decrypted)

    # ===== Public API =====

    def store_state(self, state: str, state_data: dict) -> None:
        """Store OAuth state in Redis.

        Args:
            state: State token.
            state_data: Dict with code_verifier, client_id, user_id.

        Raises:
            ExtendedHTTPException: If state cannot be stored.
        """
        state_key = f"{STATE_KEY_PREFIX}{state}"
        if not self._redis_set_encrypted(state_key, state_data, STATE_TTL):
            if config.STANDALONE:
                raise ExtendedHTTPException(503, "Authentication service temporarily unavailable. Please try again.")
            raise ExtendedHTTPException(502, "Failed to initiate authentication")

    def consume_state(self, state: str) -> Optional[dict]:
        """Consume OAuth state from Redis (atomic getdel).

        Args:
            state: State token.

        Returns:
            State data dict or None if state invalid/expired.

        Raises:
            ExtendedHTTPException: If Redis is unavailable.
        """
        state_key = f"{STATE_KEY_PREFIX}{state}"
        try:
            return self._redis_getdel_encrypted(state_key)
        except Exception as exc:
            logger.error(f"Google OAuth: Redis unavailable reading state in callback: {exc}")
            raise ExtendedHTTPException(503, "Authentication service temporarily unavailable. Please try again.")

    def store_result(self, state: str, status: str, user_id: str, **kwargs) -> bool:
        """Store OAuth callback result in Redis.

        Args:
            state: State token.
            status: Result status ("success", "error").
            user_id: User ID.
            **kwargs: Additional payload fields (message, token_data, email).

        Returns:
            True on success, False if Redis is unavailable.
        """
        result_key = f"{RESULT_KEY_PREFIX}{state}"
        payload = {"status": status, "user_id": user_id, **kwargs}
        return self._redis_set_encrypted(result_key, payload, RESULT_TTL)

    def get_result(self, state: str) -> Optional[dict]:
        """Get OAuth callback result from Redis.

        Args:
            state: State token.

        Returns:
            Result dict or None if not available.
        """
        result_key = f"{RESULT_KEY_PREFIX}{state}"
        return self._redis_get_encrypted(result_key)

    def get_pending_state(self, state: str) -> Optional[dict]:
        """Get pending OAuth state from Redis (non-destructive).

        Args:
            state: State token.

        Returns:
            State data dict or None if state invalid/expired.
        """
        state_key = f"{STATE_KEY_PREFIX}{state}"
        return self._redis_get_encrypted(state_key)
