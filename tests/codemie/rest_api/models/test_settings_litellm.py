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
Tests for LiteLLM-related models in settings module.
"""

import pytest
from pydantic import ValidationError

from codemie.rest_api.models.settings import LiteLLMCredentials, LiteLLMContext


class TestLiteLLMCredentials:
    """Test suite for LiteLLMCredentials model."""

    def test_create_valid_credentials(self):
        """Test creating LiteLLMCredentials with valid data."""
        # Arrange & Act
        credentials = LiteLLMCredentials(api_key="test-api-key-123", url="https://api.litellm.com")

        # Assert
        assert credentials.api_key == "test-api-key-123"
        assert credentials.url == "https://api.litellm.com"

    def test_create_credentials_missing_required_fields(self):
        """Test creating LiteLLMCredentials without required fields."""
        # Test missing api_key
        with pytest.raises(ValidationError) as exc_info:
            LiteLLMCredentials(url="https://api.litellm.com")

        assert "api_key" in str(exc_info.value)
        assert "Field required" in str(exc_info.value)

        # Test missing url
        with pytest.raises(ValidationError) as exc_info:
            LiteLLMCredentials(api_key="test-key")

        assert "url" in str(exc_info.value)
        assert "Field required" in str(exc_info.value)

    def test_credentials_serialization(self):
        """Test serialization of LiteLLMCredentials."""
        # Arrange
        credentials = LiteLLMCredentials(api_key="test-key", url="https://api.litellm.com")

        # Act
        serialized = credentials.model_dump()

        # Assert
        expected = {"api_key": "test-key", "url": "https://api.litellm.com"}
        assert serialized == expected


class TestLiteLLMContext:
    """Test suite for LiteLLMContext model."""

    def test_create_valid_context_with_credentials(self):
        """Test creating LiteLLMContext with valid credentials."""
        # Arrange
        credentials = LiteLLMCredentials(api_key="test-key", url="https://api.litellm.com")

        # Act
        context = LiteLLMContext(credentials=credentials, current_project="test-project")

        # Assert
        assert context.credentials == credentials
        assert context.current_project == "test-project"

    def test_create_context_without_credentials(self):
        """Test creating LiteLLMContext without credentials."""
        # Act
        context = LiteLLMContext(credentials=None, current_project="test-project")

        # Assert
        assert context.credentials is None
        assert context.current_project == "test-project"

    def test_create_context_missing_required_fields(self):
        """Test creating LiteLLMContext without required fields."""
        # Test missing current_project
        with pytest.raises(ValidationError) as exc_info:
            LiteLLMContext(credentials=None)

        assert "current_project" in str(exc_info.value)
        assert "Field required" in str(exc_info.value)

        # Test with None for current_project (should fail since it expects str)
        credentials = LiteLLMCredentials(api_key="key", url="https://test.com")
        with pytest.raises(ValidationError) as exc_info:
            LiteLLMContext(credentials=credentials, current_project=None)

        assert "current_project" in str(exc_info.value)

    def test_context_serialization(self):
        """Test serialization of LiteLLMContext."""
        # Arrange
        credentials = LiteLLMCredentials(api_key="key", url="https://test.com")
        context = LiteLLMContext(credentials=credentials, current_project="test-project")

        # Act
        serialized = context.model_dump()

        # Assert
        expected = {"credentials": {"api_key": "key", "url": "https://test.com"}, "current_project": "test-project"}
        assert serialized == expected
