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

"""Unit tests for user_type IDP attribute validation (Story 4: EPMCDME-10160)

Tests validate_user_type function behavior for:
- Valid values (regular, external, case variations)
- Missing attribute (None)
- Invalid values (unknown strings, null, numeric, arrays)
- Error messages and logging

Coverage: validation logic, error handling, normalization
"""

import pytest
from unittest.mock import patch

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user_type_validator import validate_user_type, VALID_USER_TYPES


class TestValidateUserType:
    """Test suite for validate_user_type function"""

    # ============================================================================
    # Valid Values Tests
    # ============================================================================

    def test_validate_regular_lowercase(self) -> None:
        """AC: IDP user with user_type='regular' is stored as 'regular'"""
        result = validate_user_type('regular')
        assert result == 'regular'

    def test_validate_external_lowercase(self) -> None:
        """AC: IDP user with user_type='external' is stored as 'external'"""
        result = validate_user_type('external')
        assert result == 'external'

    def test_validate_regular_uppercase(self) -> None:
        """AC: IDP user with user_type='REGULAR' is normalized and stored as 'regular'"""
        result = validate_user_type('REGULAR')
        assert result == 'regular'

    def test_validate_external_uppercase(self) -> None:
        """AC: IDP user with user_type='EXTERNAL' is normalized and stored as 'external'"""
        result = validate_user_type('EXTERNAL')
        assert result == 'external'

    def test_validate_external_mixed_case(self) -> None:
        """AC: IDP user with user_type='External' is normalized and stored as 'external'"""
        result = validate_user_type('External')
        assert result == 'external'

    def test_validate_regular_mixed_case(self) -> None:
        """AC: IDP user with user_type='Regular' is normalized and stored as 'regular'"""
        result = validate_user_type('Regular')
        assert result == 'regular'

    def test_validate_with_whitespace(self) -> None:
        """User type with leading/trailing whitespace is normalized"""
        result = validate_user_type('  external  ')
        assert result == 'external'

    # ============================================================================
    # Missing Attribute Tests
    # ============================================================================

    def test_validate_none_defaults_to_regular(self) -> None:
        """AC: IDP user with missing user_type attribute defaults to 'regular'"""
        result = validate_user_type(None)
        assert result == 'regular'

    # ============================================================================
    # Invalid Value Tests
    # ============================================================================

    def test_validate_invalid_string_rejects(self) -> None:
        """AC: IDP user with invalid value (e.g., 'unknown') is rejected with 401"""
        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_user_type('unknown')

        exc = exc_info.value
        assert exc.code == 401
        assert "Invalid user_type attribute from IDP" in exc.message
        assert "Expected 'regular' or 'external', got: unknown" in exc.message

    def test_validate_invalid_guest_rejects(self) -> None:
        """AC: IDP user with invalid value 'guest' is rejected with 401"""
        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_user_type('guest')

        exc = exc_info.value
        assert exc.code == 401
        assert "Expected 'regular' or 'external', got: guest" in exc.message

    def test_validate_empty_string_rejects(self) -> None:
        """Empty string user_type is rejected with 401"""
        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_user_type('')

        exc = exc_info.value
        assert exc.code == 401

    def test_validate_numeric_value_rejects(self) -> None:
        """AC: IDP user with numeric user_type is rejected with 401"""
        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_user_type(123)

        exc = exc_info.value
        assert exc.code == 401
        assert "Expected 'regular' or 'external', got: 123" in exc.message

    def test_validate_array_rejects(self) -> None:
        """AC: IDP user with array user_type is rejected with 401"""
        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_user_type(['regular'])

        exc = exc_info.value
        assert exc.code == 401
        assert "Expected 'regular' or 'external'" in exc.message

    def test_validate_dict_rejects(self) -> None:
        """AC: IDP user with dict user_type is rejected with 401"""
        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_user_type({'type': 'regular'})

        exc = exc_info.value
        assert exc.code == 401
        assert "Expected 'regular' or 'external'" in exc.message

    def test_validate_boolean_rejects(self) -> None:
        """AC: IDP user with boolean user_type is rejected with 401"""
        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_user_type(True)

        exc = exc_info.value
        assert exc.code == 401
        assert "Expected 'regular' or 'external'" in exc.message

    # ============================================================================
    # Error Message Tests
    # ============================================================================

    def test_error_message_includes_actual_value(self) -> None:
        """AC: 401 error message includes the actual invalid value received from IDP"""
        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_user_type('contractor')

        exc = exc_info.value
        assert 'contractor' in exc.message

    def test_error_message_has_help_text(self) -> None:
        """Error response includes help text for IDP configuration"""
        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_user_type('invalid')

        exc = exc_info.value
        assert exc.help is not None
        assert 'administrator' in exc.help.lower()

    # ============================================================================
    # Logging Tests
    # ============================================================================

    @patch('codemie.rest_api.security.user_type_validator.logger')
    def test_invalid_value_is_logged(self, mock_logger) -> None:
        """AC: Authentication rejection is logged with relevant IDP claim details"""
        with pytest.raises(ExtendedHTTPException):
            validate_user_type(
                'invalid_value', idp_context={'provider': 'test', 'source': 'test', 'subject_id_hash': 'abc123'}
            )

        mock_logger.error.assert_called_once()
        log_message = mock_logger.error.call_args[0][0]
        assert 'invalid_value' in log_message.lower()
        assert 'provider=test' in log_message

    @patch('codemie.rest_api.security.user_type_validator.logger')
    def test_invalid_type_is_logged(self, mock_logger) -> None:
        """AC: Invalid type errors are logged for debugging"""
        with pytest.raises(ExtendedHTTPException):
            validate_user_type(123, idp_context={'provider': 'test', 'source': 'test', 'subject_id_hash': 'abc123'})

        mock_logger.error.assert_called_once()
        log_message = mock_logger.error.call_args[0][0]
        assert 'int' in log_message
        assert '123' in log_message
        assert 'provider=test' in log_message

    # ============================================================================
    # Constant Tests
    # ============================================================================

    def test_valid_user_types_constant(self) -> None:
        """Verify VALID_USER_TYPES contains exactly 'regular' and 'external'"""
        assert {'regular', 'external'} == VALID_USER_TYPES
        assert len(VALID_USER_TYPES) == 2

    # ============================================================================
    # Edge Cases
    # ============================================================================

    def test_validate_special_characters_rejects(self) -> None:
        """User type with special characters is rejected"""
        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_user_type('regular!')

        exc = exc_info.value
        assert exc.code == 401

    def test_validate_unicode_characters_rejects(self) -> None:
        """User type with unicode characters is rejected"""
        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_user_type('régular')

        exc = exc_info.value
        assert exc.code == 401
