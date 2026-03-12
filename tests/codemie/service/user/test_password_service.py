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

from unittest.mock import MagicMock, patch

from argon2.exceptions import InvalidHash, VerifyMismatchError

from codemie.service.password_service import PasswordService, password_service


class TestPasswordService:
    """Test suite for PasswordService - Argon2 password hashing operations"""

    def test_hash_password_returns_argon2_hash(self):
        """Test that hash_password returns a valid Argon2 hash string"""
        # Arrange
        service = PasswordService()
        password = "test_password_123"

        # Act
        password_hash = service.hash_password(password)

        # Assert
        assert password_hash.startswith("$argon2")
        assert len(password_hash) > 50  # Argon2 hashes are long

    def test_hash_password_different_inputs_different_hashes(self):
        """Test that different passwords produce different hashes"""
        # Arrange
        service = PasswordService()
        password1 = "password_one"
        password2 = "password_two"

        # Act
        hash1 = service.hash_password(password1)
        hash2 = service.hash_password(password2)

        # Assert
        assert hash1 != hash2

    def test_hash_password_same_input_different_hashes(self):
        """Test that same password produces different hashes (salt is random)"""
        # Arrange
        service = PasswordService()
        password = "same_password"

        # Act
        hash1 = service.hash_password(password)
        hash2 = service.hash_password(password)

        # Assert
        assert hash1 != hash2  # Due to random salt

    def test_verify_password_correct(self):
        """Test that verify_password returns True for correct password"""
        # Arrange
        service = PasswordService()
        password = "correct_password_456"
        password_hash = service.hash_password(password)

        # Act
        result = service.verify_password(password_hash, password)

        # Assert
        assert result is True

    def test_verify_password_incorrect(self):
        """Test that verify_password returns False for incorrect password"""
        # Arrange
        service = PasswordService()
        password = "correct_password"
        wrong_password = "wrong_password"
        password_hash = service.hash_password(password)

        # Act
        result = service.verify_password(password_hash, wrong_password)

        # Assert
        assert result is False

    def test_verify_password_invalid_hash(self):
        """Test that verify_password returns False for invalid hash format"""
        # Arrange
        service = PasswordService()
        invalid_hash = "not_a_valid_argon2_hash"
        password = "any_password"

        # Act
        result = service.verify_password(invalid_hash, password)

        # Assert
        assert result is False

    @patch("codemie.service.password_service.logger")
    def test_verify_password_invalid_hash_logs_warning(self, mock_logger):
        """Test that verify_password logs warning for invalid hash"""
        # Arrange
        service = PasswordService()
        invalid_hash = "invalid_hash_format"
        password = "test_password"

        # Act
        service.verify_password(invalid_hash, password)

        # Assert
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args[0][0]
        assert "Invalid hash format" in call_args

    def test_verify_password_handles_verify_mismatch_error(self):
        """Test that verify_password handles VerifyMismatchError gracefully"""
        # Arrange
        service = PasswordService()
        mock_hasher = MagicMock()
        mock_hasher.verify.side_effect = VerifyMismatchError()
        service._hasher = mock_hasher

        # Act
        result = service.verify_password("some_hash", "password")

        # Assert
        assert result is False

    def test_needs_rehash_current_params(self):
        """Test that needs_rehash returns False for hash with current parameters"""
        # Arrange
        service = PasswordService()
        password = "test_password"
        # Generate hash with current parameters
        current_hash = service.hash_password(password)

        # Act
        result = service.needs_rehash(current_hash)

        # Assert
        assert result is False

    def test_needs_rehash_outdated_params(self):
        """Test that needs_rehash returns True for hash with outdated parameters"""
        # Arrange
        service = PasswordService()
        mock_hasher = MagicMock()
        mock_hasher.check_needs_rehash.return_value = True
        service._hasher = mock_hasher
        outdated_hash = "$argon2id$v=19$m=4096,t=1,p=1$oldparams"

        # Act
        result = service.needs_rehash(outdated_hash)

        # Assert
        assert result is True

    def test_needs_rehash_invalid_hash_returns_true(self):
        """Test that needs_rehash returns True for invalid hash (should be replaced)"""
        # Arrange
        service = PasswordService()
        invalid_hash = "completely_invalid_hash"

        # Act
        result = service.needs_rehash(invalid_hash)

        # Assert
        assert result is True  # Invalid hashes should be replaced

    def test_needs_rehash_handles_invalid_hash_exception(self):
        """Test that needs_rehash handles InvalidHash exception"""
        # Arrange
        service = PasswordService()
        mock_hasher = MagicMock()
        mock_hasher.check_needs_rehash.side_effect = InvalidHash()
        service._hasher = mock_hasher

        # Act
        result = service.needs_rehash("some_hash")

        # Assert
        assert result is True


class TestPasswordServiceSingleton:
    """Test the password_service singleton instance"""

    def test_singleton_instance_exists(self):
        """Test that password_service singleton is properly initialized"""
        # Assert
        assert password_service is not None
        assert isinstance(password_service, PasswordService)

    def test_singleton_hash_and_verify(self):
        """Test that singleton instance can hash and verify passwords"""
        # Arrange
        password = "singleton_test_password"

        # Act
        password_hash = password_service.hash_password(password)
        is_valid = password_service.verify_password(password_hash, password)

        # Assert
        assert password_hash.startswith("$argon2")
        assert is_valid is True
