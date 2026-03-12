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
Tests for custom headers producer service (migrated to enterprise.litellm.llm_factory).
"""

from unittest.mock import patch

from codemie.enterprise.litellm.llm_factory import (
    generate_litellm_headers_from_context as generate_headers_from_litellm_context,
)
from codemie.rest_api.models.settings import LiteLLMContext, LiteLLMCredentials
from codemie.configs import config

# Header constant (defined locally since it's not exported from enterprise)
LITELLM_TAGS_HEADER = "x-litellm-tags"


class TestGenerateHeadersFromLiteLLMContext:
    """Test suite for generate_headers_from_litellm_context function."""

    def test_generate_headers_with_valid_context_and_project(self):
        """Test header generation with valid context and current project."""
        # Arrange
        with patch.object(config, 'LITE_LLM_PROJECTS_TO_TAGS_LIST', "test-project"):
            with patch.object(config, 'LITE_LLM_TAGS_HEADER_VALUE', "default"):
                credentials = LiteLLMCredentials(api_key="test-key", url="http://test.com")
                context = LiteLLMContext(credentials=credentials, current_project="test-project")

                # Act
                result = generate_headers_from_litellm_context(context)

                # Assert
                expected_headers = {LITELLM_TAGS_HEADER: "test-project"}
                assert result == expected_headers

    def test_generate_headers_with_context_but_no_project(self):
        """Test header generation with context but no current project."""
        # Arrange
        credentials = LiteLLMCredentials(api_key="test-key", url="http://test.com")
        context = LiteLLMContext(credentials=credentials, current_project="")

        # Act
        result = generate_headers_from_litellm_context(context)

        # Assert
        expected_headers = {LITELLM_TAGS_HEADER: config.LITE_LLM_TAGS_HEADER_VALUE}
        assert result == expected_headers

    def test_generate_headers_with_none_context(self):
        """Test header generation with None context."""
        # Act
        result = generate_headers_from_litellm_context(None)

        # Assert
        expected_headers = {LITELLM_TAGS_HEADER: config.LITE_LLM_TAGS_HEADER_VALUE}
        assert result == expected_headers

    def test_generate_headers_with_special_characters_in_project_name(self):
        """Test header generation with special characters in project name."""
        # Arrange
        project_name = "project-with-special@chars#123"
        with patch.object(config, 'LITE_LLM_PROJECTS_TO_TAGS_LIST', project_name):
            with patch.object(config, 'LITE_LLM_TAGS_HEADER_VALUE', "default"):
                credentials = LiteLLMCredentials(api_key="test-key", url="http://test.com")
                context = LiteLLMContext(credentials=credentials, current_project=project_name)

                # Act
                result = generate_headers_from_litellm_context(context)

                # Assert
                expected_headers = {LITELLM_TAGS_HEADER: project_name}
                assert result == expected_headers

    def test_generate_headers_with_context_without_credentials(self):
        """Test header generation with context but no credentials."""
        # Arrange
        with patch.object(config, 'LITE_LLM_PROJECTS_TO_TAGS_LIST', "test-project"):
            with patch.object(config, 'LITE_LLM_TAGS_HEADER_VALUE', "default"):
                context = LiteLLMContext(credentials=None, current_project="test-project")

                # Act
                result = generate_headers_from_litellm_context(context)

                # Assert
                expected_headers = {LITELLM_TAGS_HEADER: "test-project"}
                assert result == expected_headers
