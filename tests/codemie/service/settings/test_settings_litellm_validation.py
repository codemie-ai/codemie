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

"""
Tests for LiteLLM validation in settings request validator.
"""

import pytest
from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings import SettingRequest, CredentialTypes, CredentialValues
from codemie.service.settings.settings_request_validator import validate_litellm_request, validate_litellm_api_key


class TestLiteLLMValidation:
    """Test suite for LiteLLM validation functions."""

    def test_validate_litellm_request_with_valid_api_key(self):
        """Test validating LiteLLM request with a valid API key."""
        # Arrange
        request = SettingRequest(
            alias="test-litellm",
            credential_type=CredentialTypes.LITE_LLM,
            credential_values=[
                CredentialValues(key="api_key", value="valid-api-key-123"),
                CredentialValues(key="url", value="https://api.litellm.com"),
            ],
            project_name="test-project",
        )

        # Act & Assert - should not raise any exception
        validate_litellm_request(request)

    def test_validate_litellm_request_with_empty_string_api_key(self):
        """Test validating LiteLLM request with empty string API key."""
        # Arrange
        request = SettingRequest(
            alias="test-litellm",
            credential_type=CredentialTypes.LITE_LLM,
            credential_values=[
                CredentialValues(key="api_key", value=""),
                CredentialValues(key="url", value="https://api.litellm.com"),
            ],
            project_name="test-project",
        )

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_litellm_request(request)

        assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "LiteLLM API key cannot be empty" in exc_info.value.message
        assert "cannot be empty or contain only whitespace" in exc_info.value.details

    def test_validate_litellm_request_with_whitespace_only_api_key(self):
        """Test validating LiteLLM request with whitespace-only API key."""
        # Arrange
        request = SettingRequest(
            alias="test-litellm",
            credential_type=CredentialTypes.LITE_LLM,
            credential_values=[
                CredentialValues(key="api_key", value="   "),
                CredentialValues(key="url", value="https://api.litellm.com"),
            ],
            project_name="test-project",
        )

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_litellm_request(request)

        assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "LiteLLM API key cannot be empty" in exc_info.value.message

    def test_validate_litellm_request_with_missing_api_key(self):
        """Test validating LiteLLM request with missing API key."""
        # Arrange
        request = SettingRequest(
            alias="test-litellm",
            credential_type=CredentialTypes.LITE_LLM,
            credential_values=[CredentialValues(key="url", value="https://api.litellm.com")],
            project_name="test-project",
        )

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_litellm_request(request)

        assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "LiteLLM API key is missing" in exc_info.value.message
        assert "Please provide an API key for the LiteLLM integration" in exc_info.value.details

    def test_validate_litellm_api_key_with_valid_key(self):
        """Test validating API key directly with valid key."""
        # Arrange
        request = SettingRequest(
            alias="test-litellm",
            credential_type=CredentialTypes.LITE_LLM,
            credential_values=[CredentialValues(key="api_key", value="valid-key-123")],
            project_name="test-project",
        )

        # Act
        result = validate_litellm_api_key(request)

        # Assert
        assert result == "valid-key-123"

    def test_validate_litellm_api_key_with_none_value(self):
        """Test validating API key with None value."""
        # Arrange
        request = SettingRequest(
            alias="test-litellm",
            credential_type=CredentialTypes.LITE_LLM,
            credential_values=[CredentialValues(key="api_key", value=None)],
            project_name="test-project",
        )

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_litellm_api_key(request)

        assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "LiteLLM API key cannot be empty" in exc_info.value.message

    def test_validate_litellm_api_key_with_mixed_whitespace(self):
        """Test validating API key with mixed whitespace (tabs, spaces, newlines)."""
        # Arrange
        request = SettingRequest(
            alias="test-litellm",
            credential_type=CredentialTypes.LITE_LLM,
            credential_values=[CredentialValues(key="api_key", value="\t\n  \r\n")],
            project_name="test-project",
        )

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            validate_litellm_api_key(request)

        assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "LiteLLM API key cannot be empty" in exc_info.value.message

    def test_validate_litellm_api_key_with_valid_key_surrounded_by_whitespace(self):
        """Test validating API key with valid key that has surrounding whitespace."""
        # Arrange
        request = SettingRequest(
            alias="test-litellm",
            credential_type=CredentialTypes.LITE_LLM,
            credential_values=[CredentialValues(key="api_key", value="  valid-key-123  ")],
            project_name="test-project",
        )

        # Act
        result = validate_litellm_api_key(request)

        # Assert
        # The function should return the original value, not stripped
        assert result == "  valid-key-123  "
