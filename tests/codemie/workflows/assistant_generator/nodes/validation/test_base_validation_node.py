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

"""Tests for BaseValidationNode - LLM access and retry logic."""

from unittest.mock import Mock, patch

import pytest
from pydantic import BaseModel, Field

from codemie.core.exceptions import ExtendedHTTPException
from codemie.workflows.assistant_generator.nodes.validation.base_validation_node import BaseValidationNode


class SampleOutputModel(BaseModel):
    """Sample output model for testing structured LLM output."""

    result: str = Field(description="Test result")
    is_valid: bool = Field(description="Validation flag")


@pytest.fixture
def base_validation_node():
    """Create BaseValidationNode instance."""
    return BaseValidationNode(llm_model="gpt-4o-mini", request_id="test-request-123")


class TestBaseValidationNodeInitialization:
    """Test BaseValidationNode initialization and configuration."""

    def test_init_with_all_params(self):
        """Test initialization with all parameters."""
        # Act
        node = BaseValidationNode(llm_model="gpt-4o-mini", request_id="test-123")

        # Assert
        assert node.llm_model == "gpt-4o-mini"
        assert node.request_id == "test-123"
        assert node._llm is None
        assert BaseValidationNode.DEFAULT_TEMPERATURE == 0.0
        assert BaseValidationNode.DEFAULT_STREAMING is False


class TestBaseValidationNodeLLMProperty:
    """Test lazy-loading LLM property."""

    @patch('codemie.workflows.assistant_generator.nodes.validation.base_validation_node.get_llm_by_credentials')
    def test_llm_property_lazy_loads_and_caches(self, mock_get_llm, base_validation_node, mock_llm):
        """Test that LLM is lazy-loaded on first access and cached."""
        # Arrange
        mock_get_llm.return_value = mock_llm

        # Act - access LLM multiple times
        result1 = base_validation_node.llm
        result2 = base_validation_node.llm

        # Assert - loaded once, cached for subsequent calls
        assert result1 is result2 is mock_llm
        mock_get_llm.assert_called_once_with(
            llm_model="gpt-4o-mini",
            temperature=0.0,
            streaming=False,
            request_id="test-request-123",
        )

    @patch('codemie.workflows.assistant_generator.nodes.validation.base_validation_node.get_llm_by_credentials')
    def test_llm_property_raises_on_initialization_failure(self, mock_get_llm, base_validation_node):
        """Test that LLM initialization failure raises RuntimeError."""
        # Arrange
        mock_get_llm.side_effect = Exception("LLM initialization failed")

        # Act & Assert
        with pytest.raises(RuntimeError, match="Cannot initialize LLM model gpt-4o-mini"):
            _ = base_validation_node.llm


class TestBaseValidationNodeInvokeLLM:
    """Test LLM invocation with structured output."""

    def test_invoke_llm_success(self, base_validation_node, mock_llm):
        """Test successful LLM invocation with structured output."""
        # Arrange
        base_validation_node._llm = mock_llm
        expected_output = SampleOutputModel(result="test result", is_valid=True)

        mock_structured_llm = Mock()
        mock_structured_llm.invoke.return_value = expected_output
        mock_llm.with_structured_output.return_value = mock_structured_llm

        # Act
        result = base_validation_node._invoke_llm("test prompt", SampleOutputModel)

        # Assert
        assert result == expected_output
        mock_llm.with_structured_output.assert_called_once_with(SampleOutputModel)
        mock_structured_llm.invoke.assert_called_once_with("test prompt")


class TestBaseValidationNodeInvokeWithRetry:
    """Test public invoke_llm_with_retry method."""

    def test_invoke_llm_with_retry_success(self, base_validation_node, mock_llm):
        """Test successful invocation via public method."""
        # Arrange
        base_validation_node._llm = mock_llm
        expected_output = SampleOutputModel(result="test", is_valid=True)

        mock_structured_llm = Mock()
        mock_structured_llm.invoke.return_value = expected_output
        mock_llm.with_structured_output.return_value = mock_structured_llm

        # Act
        result = base_validation_node.invoke_llm_with_retry("test prompt", SampleOutputModel)

        # Assert
        assert result == expected_output
        assert isinstance(result, SampleOutputModel)
        assert result.result == "test"
        assert result.is_valid is True

    def test_invoke_llm_with_retry_raises_extended_http_exception_on_failure(self, base_validation_node, mock_llm):
        """Test that invoke_llm_with_retry raises ExtendedHTTPException after retries exhausted."""
        # Arrange
        base_validation_node._llm = mock_llm

        mock_structured_llm = Mock()
        mock_structured_llm.invoke.side_effect = Exception("LLM service down")
        mock_llm.with_structured_output.return_value = mock_structured_llm

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            base_validation_node.invoke_llm_with_retry("prompt", SampleOutputModel)

        # Verify exception details
        exception = exc_info.value
        assert exception.code == 500
        assert "LLM validation service unavailable" in exception.message
        assert "Failed to get response from LLM after" in exception.details
        assert "Please try again later" in exception.help


class TestBaseValidationNodeIntegration:
    """Integration tests for complete workflows."""

    @patch('codemie.workflows.assistant_generator.nodes.validation.base_validation_node.get_llm_by_credentials')
    def test_full_workflow_from_init_to_invocation(self, mock_get_llm, mock_llm):
        """Test complete workflow from initialization to successful invocation."""
        # Arrange
        node = BaseValidationNode(llm_model="gpt-4", request_id="integration-test")

        mock_structured_llm = Mock()
        expected_output = SampleOutputModel(result="integration test", is_valid=True)
        mock_structured_llm.invoke.return_value = expected_output
        mock_llm.with_structured_output.return_value = mock_structured_llm
        mock_get_llm.return_value = mock_llm

        # Act
        result = node.invoke_llm_with_retry("integration test prompt", SampleOutputModel)

        # Assert
        assert result == expected_output
        mock_get_llm.assert_called_once_with(
            llm_model="gpt-4",
            temperature=0.0,
            streaming=False,
            request_id="integration-test",
        )

    @patch('codemie.workflows.assistant_generator.nodes.validation.base_validation_node.get_llm_by_credentials')
    def test_multiple_invocations_reuse_cached_llm(self, mock_get_llm, mock_llm):
        """Test that multiple invocations reuse the same cached LLM instance."""
        # Arrange
        node = BaseValidationNode(llm_model="gpt-4")

        mock_structured_llm = Mock()
        mock_structured_llm.invoke.side_effect = [
            SampleOutputModel(result="first", is_valid=True),
            SampleOutputModel(result="second", is_valid=False),
        ]
        mock_llm.with_structured_output.return_value = mock_structured_llm
        mock_get_llm.return_value = mock_llm

        # Act - invoke multiple times
        result1 = node.invoke_llm_with_retry("prompt 1", SampleOutputModel)
        result2 = node.invoke_llm_with_retry("prompt 2", SampleOutputModel)

        # Assert - LLM initialized only once, but invoked twice
        assert result1.result == "first"
        assert result2.result == "second"
        mock_get_llm.assert_called_once()
        assert mock_structured_llm.invoke.call_count == 2
