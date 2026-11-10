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

"""Unit tests for GoogleOAuthStateStore."""

import json
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.google_oauth import state_store as state_store_module
from codemie.service.google_oauth.state_store import (
    GoogleOAuthStateStore,
    STATE_KEY_PREFIX,
    RESULT_KEY_PREFIX,
)


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    return MagicMock()


@pytest.fixture
def mock_encryption():
    """Mock encryption service."""
    mock_enc = MagicMock()
    mock_enc.encrypt.side_effect = lambda x: f"encrypted_{x}".encode() if isinstance(x, str) else b"encrypted_" + x
    mock_enc.decrypt.side_effect = lambda x: (
        x.decode().replace("encrypted_", "") if isinstance(x, bytes) else str(x).replace("encrypted_", "")
    )
    return mock_enc


@pytest.fixture
def store(mock_redis, mock_encryption):
    """GoogleOAuthStateStore instance with mocked dependencies."""
    return GoogleOAuthStateStore(redis_client=mock_redis, encryption_service=mock_encryption)


class TestStoreState:
    """Test OAuth state storage."""

    def test_store_state_encrypts_and_stores_in_redis(self, store, mock_redis, mock_encryption):
        """Should encrypt state data and store in Redis with TTL."""
        state_data = {"code_verifier": "verifier", "client_id": "client", "user_id": "user123"}

        store.store_state("test_state", state_data)

        # Should have called Redis set with encrypted data
        mock_redis.set.assert_called_once()
        call_args = mock_redis.set.call_args
        assert STATE_KEY_PREFIX in call_args[0][0]
        assert "test_state" in call_args[0][0]
        assert call_args[1]["ex"] == 600  # STATE_TTL

    def test_store_state_raises_on_redis_failure(self, store, mock_redis):
        """Should raise ExtendedHTTPException when Redis fails (non-Standalone: unchanged 502)."""
        mock_redis.set.side_effect = Exception("Redis error")

        with patch.object(state_store_module.config, "STANDALONE", False):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                store.store_state("test_state", {"data": "test"})

        assert exc_info.value.code == 502
        assert exc_info.value.message == "Failed to initiate authentication"

    def test_store_state_raises_503_in_standalone(self, store, mock_redis):
        """In Standalone, a Redis failure raises the same 503 as its sibling consume_state."""
        mock_redis.set.side_effect = Exception("Redis error")

        with patch.object(state_store_module.config, "STANDALONE", True):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                store.store_state("test_state", {"data": "test"})

        assert exc_info.value.code == 503
        assert exc_info.value.message == "Authentication service temporarily unavailable. Please try again."


class TestConsumeState:
    """Test OAuth state consumption."""

    def test_consume_state_uses_getdel_atomically(self, store, mock_redis, mock_encryption):
        """Should use getdel to consume state once."""
        state_data = {"code_verifier": "verifier", "client_id": "client", "user_id": "user123"}
        encrypted = mock_encryption.encrypt(json.dumps(state_data))
        mock_redis.getdel.return_value = encrypted

        result = store.consume_state("test_state")

        assert result == state_data
        mock_redis.getdel.assert_called_once()
        assert STATE_KEY_PREFIX in mock_redis.getdel.call_args[0][0]

    def test_consume_state_returns_none_when_missing(self, store, mock_redis):
        """Should return None for invalid/expired state."""
        mock_redis.getdel.return_value = None

        result = store.consume_state("missing_state")

        assert result is None

    def test_consume_state_raises_on_redis_failure(self, store, mock_redis):
        """Should raise ExtendedHTTPException when Redis unavailable."""
        mock_redis.getdel.side_effect = Exception("Redis error")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            store.consume_state("test_state")

        assert exc_info.value.code == 503


class TestStoreResult:
    """Test OAuth result storage."""

    def test_store_result_stores_encrypted_payload(self, store, mock_redis):
        """Should store encrypted result payload in Redis."""
        result = store.store_result("test_state", "success", "user123", message="Done", email="test@example.com")

        assert result is True
        mock_redis.set.assert_called_once()
        call_args = mock_redis.set.call_args
        assert RESULT_KEY_PREFIX in call_args[0][0]
        assert call_args[1]["ex"] == 300  # RESULT_TTL

    def test_store_result_returns_false_on_redis_failure(self, store, mock_redis):
        """Should return False when Redis fails."""
        mock_redis.set.side_effect = Exception("Redis error")

        result = store.store_result("test_state", "error", "user123")

        assert result is False


class TestGetResult:
    """Test OAuth result retrieval."""

    def test_get_result_returns_decrypted_payload(self, store, mock_redis, mock_encryption):
        """Should return decrypted result when available."""
        result_data = {"status": "success", "user_id": "user123", "email": "test@example.com"}
        encrypted = mock_encryption.encrypt(json.dumps(result_data))
        mock_redis.get.return_value = encrypted

        result = store.get_result("test_state")

        assert result == result_data
        assert RESULT_KEY_PREFIX in mock_redis.get.call_args[0][0]

    def test_get_result_returns_none_when_missing(self, store, mock_redis):
        """Should return None when result doesn't exist."""
        mock_redis.get.return_value = None

        result = store.get_result("test_state")

        assert result is None

    def test_get_result_handles_redis_exception(self, store, mock_redis):
        """Should return None and log on Redis exception."""
        mock_redis.get.side_effect = Exception("Redis error")

        result = store.get_result("test_state")

        assert result is None


class TestGetPendingState:
    """Test pending state retrieval."""

    def test_get_pending_state_returns_decrypted_state(self, store, mock_redis, mock_encryption):
        """Should return state data when flow is pending."""
        state_data = {"code_verifier": "verifier", "client_id": "client", "user_id": "user123"}
        encrypted = mock_encryption.encrypt(json.dumps(state_data))
        mock_redis.get.return_value = encrypted

        result = store.get_pending_state("test_state")

        assert result == state_data

    def test_get_pending_state_returns_none_when_missing(self, store, mock_redis):
        """Should return None when state expired."""
        mock_redis.get.return_value = None

        result = store.get_pending_state("test_state")

        assert result is None
