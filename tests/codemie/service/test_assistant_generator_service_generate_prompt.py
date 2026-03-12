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
Tests for AssistantGeneratorService.generate_assistant_prompt method.
"""

import unittest
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.assistant_generator import PromptGeneratorResponse
from codemie.rest_api.security.user import User
from codemie.service.assistant_generator_service import AssistantGeneratorService, PromptDetails


class TestGenerateAssistantPrompt(unittest.TestCase):
    """Test suite for generate_assistant_prompt method"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_user = MagicMock(spec=User)
        self.mock_user.id = "user-123"
        self.mock_user.username = "test@example.com"
        self.mock_user.current_project = "test-project"
        self.mock_user.is_admin = False

    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_generate_new_prompt_with_text_no_existing(self, mock_chain_class, mock_send_metric):
        """Test generating a new prompt from text with no existing prompt"""
        # Setup mocks
        mock_chain = MagicMock()
        mock_chain_class.from_prompt_template.return_value = mock_chain

        mock_response = PromptDetails(
            system_prompt="## Instructions\nYou are a helpful coding assistant.\n\n## Steps to Follow\n1. Understand user requirements\n2. Provide clear solutions\n\n## Constraints\n- Be concise\n- Use best practices"
        )
        mock_chain.invoke_with_model.return_value = mock_response

        # Execute
        result = AssistantGeneratorService.generate_assistant_prompt(
            user=self.mock_user, text="Help users write Python code", existing_prompt=None, llm_model="test-model"
        )

        # Assertions
        self.assertIsInstance(result, PromptGeneratorResponse)
        self.assertEqual(result.system_prompt, mock_response.system_prompt)
        self.assertIn("## Instructions", result.system_prompt)

        # Verify chain was built correctly
        mock_chain_class.from_prompt_template.assert_called_once()
        # Note: project is None when not explicitly provided, user.current_project is only used internally
        mock_chain.add_context.assert_called_once_with(self.mock_user, None, None)
        mock_chain.add_prompt_refine_instructions.assert_called_once_with("Help users write Python code")

        # Verify metrics sent
        mock_send_metric.assert_called()
        metric_calls = [call[1]["name"] for call in mock_send_metric.call_args_list]
        self.assertIn("codemie_prompt_generator_total", metric_calls)

    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_refine_prompt_with_user_instructions(self, mock_chain_class, mock_send_metric):
        """Test refining an existing prompt with user instructions"""
        # Setup mocks
        mock_chain = MagicMock()
        mock_chain_class.from_prompt_template.return_value = mock_chain

        existing_prompt = "You are a code helper."
        refined_prompt = "## Instructions\nYou are a professional code assistant specializing in Python.\n\n## Steps to Follow\n1. Analyze user code\n2. Provide detailed explanations\n\n## Constraints\n- Use industry standards"

        mock_response = PromptDetails(system_prompt=refined_prompt)
        mock_chain.invoke_with_model.return_value = mock_response

        # Execute
        result = AssistantGeneratorService.generate_assistant_prompt(
            user=self.mock_user,
            text="Make it more professional and add Python focus",
            existing_prompt=existing_prompt,
            llm_model="test-model",
        )

        # Assertions
        self.assertIsInstance(result, PromptGeneratorResponse)
        self.assertEqual(result.system_prompt, refined_prompt)

        # Verify the existing prompt was passed correctly
        invoke_call_args = mock_chain.invoke_with_model.call_args[0]
        self.assertEqual(invoke_call_args[0], PromptDetails)

        # Verify refine instructions include the user's text
        mock_chain.add_prompt_refine_instructions.assert_called_once_with(
            "Make it more professional and add Python focus"
        )

    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_automatic_quality_review_mode(self, mock_chain_class, mock_send_metric):
        """Test automatic quality review when text is None"""
        # Setup mocks
        mock_chain = MagicMock()
        mock_chain_class.from_prompt_template.return_value = mock_chain

        existing_prompt = "You help with stuff"  # Poor quality prompt
        improved_prompt = "## Instructions\nYou are a helpful assistant.\n\n## Steps to Follow\n1. Listen to user needs\n2. Provide accurate information\n\n## Constraints\n- Be clear and concise"

        mock_response = PromptDetails(system_prompt=improved_prompt)
        mock_chain.invoke_with_model.return_value = mock_response

        # Execute with text=None to trigger automatic quality review
        result = AssistantGeneratorService.generate_assistant_prompt(
            user=self.mock_user, text=None, existing_prompt=existing_prompt, llm_model="test-model"
        )

        # Assertions
        self.assertIsInstance(result, PromptGeneratorResponse)
        self.assertEqual(result.system_prompt, improved_prompt)

        # Verify automatic review mode was triggered (text=None)
        mock_chain.add_prompt_refine_instructions.assert_called_once_with(None)

    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_empty_string_text_triggers_automatic_review(self, mock_chain_class, mock_send_metric):
        """Test that empty string for text triggers automatic quality review"""
        # Setup mocks
        mock_chain = MagicMock()
        mock_chain_class.from_prompt_template.return_value = mock_chain

        mock_response = PromptDetails(system_prompt="Improved prompt")
        mock_chain.invoke_with_model.return_value = mock_response

        # Execute with empty string
        AssistantGeneratorService.generate_assistant_prompt(
            user=self.mock_user, text="", existing_prompt="Some prompt", llm_model="test-model"
        )

        # Verify empty string was passed (template handles empty string logic)
        mock_chain.add_prompt_refine_instructions.assert_called_once_with("")

    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    @patch("codemie.service.assistant_generator_service.IndexInfo")
    def test_datasource_context_included(self, mock_index_info, mock_chain_class, mock_send_metric):
        """Test that datasource context is fetched and included"""
        # Setup mocks
        mock_chain = MagicMock()
        mock_chain_class.from_prompt_template.return_value = mock_chain

        # Mock datasources
        idx1 = MagicMock()
        idx1.repo_name = "python-repo"
        idx1.index_type = "code"
        idx1.description = "Python code repository"

        idx2 = MagicMock()
        idx2.repo_name = "docs-kb"
        idx2.index_type = "knowledge_base"
        idx2.description = "Documentation knowledge base"

        mock_index_info.filter_for_user.return_value = [idx1, idx2]

        mock_response = PromptDetails(system_prompt="Generated prompt with datasources")
        mock_chain.invoke_with_model.return_value = mock_response

        # Execute - pass project explicitly to test datasource fetching
        result = AssistantGeneratorService.generate_assistant_prompt(
            user=self.mock_user,
            text="Create a coding assistant",
            existing_prompt=None,
            project="test-project",
            llm_model="test-model",
        )

        # Assertions
        self.assertIsInstance(result, PromptGeneratorResponse)

        # Verify datasources were fetched for the specified project
        mock_chain.add_context.assert_called_once_with(self.mock_user, "test-project", None)

    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_with_custom_project(self, mock_chain_class, mock_send_metric):
        """Test generating prompt with a custom project instead of user's current project"""
        # Setup mocks
        mock_chain = MagicMock()
        mock_chain_class.from_prompt_template.return_value = mock_chain

        mock_response = PromptDetails(system_prompt="Generated prompt")
        mock_chain.invoke_with_model.return_value = mock_response

        # Execute with custom project
        result = AssistantGeneratorService.generate_assistant_prompt(
            user=self.mock_user,
            text="Create assistant",
            existing_prompt=None,
            project="custom-project",
            llm_model="test-model",
        )

        # Assertions
        self.assertIsInstance(result, PromptGeneratorResponse)

        # Verify custom project was used
        mock_chain.add_context.assert_called_once_with(self.mock_user, "custom-project", None)

    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_both_text_and_existing_prompt_none(self, mock_chain_class, mock_send_metric):
        """Test generating prompt when both text and existing_prompt are None"""
        # Setup mocks
        mock_chain = MagicMock()
        mock_chain_class.from_prompt_template.return_value = mock_chain

        mock_response = PromptDetails(system_prompt="Default minimal prompt")
        mock_chain.invoke_with_model.return_value = mock_response

        # Execute with both None
        result = AssistantGeneratorService.generate_assistant_prompt(
            user=self.mock_user, text=None, existing_prompt=None, llm_model="test-model"
        )

        # Assertions
        self.assertIsInstance(result, PromptGeneratorResponse)
        self.assertEqual(result.system_prompt, "Default minimal prompt")

        # Verify call was made with appropriate defaults
        invoke_args = mock_chain.invoke_with_model.call_args[0]
        self.assertEqual(invoke_args[0], PromptDetails)

    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_with_request_id(self, mock_chain_class, mock_send_metric):
        """Test that request_id is passed to chain correctly"""
        # Setup mocks
        mock_chain = MagicMock()
        mock_chain_class.from_prompt_template.return_value = mock_chain

        mock_response = PromptDetails(system_prompt="Generated prompt")
        mock_chain.invoke_with_model.return_value = mock_response

        request_id = "req-uuid-12345"

        # Execute
        result = AssistantGeneratorService.generate_assistant_prompt(
            user=self.mock_user, text="Create assistant", existing_prompt=None, request_id=request_id
        )

        # Assertions
        self.assertIsInstance(result, PromptGeneratorResponse)

        # Verify request_id was passed to chain creation
        # from_prompt_template(template, request_id, llm_model)
        call_args = mock_chain_class.from_prompt_template.call_args
        self.assertEqual(call_args[0][1], request_id)  # Second positional argument

    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_error_handling_chain_failure(self, mock_chain_class, mock_send_metric):
        """Test error handling when chain invocation fails"""
        # Setup mocks to raise an exception
        mock_chain = MagicMock()
        mock_chain_class.from_prompt_template.return_value = mock_chain
        mock_chain.invoke_with_model.side_effect = Exception("LLM invocation failed")

        # Execute and expect exception
        with pytest.raises(ExtendedHTTPException) as exc_info:
            AssistantGeneratorService.generate_assistant_prompt(
                user=self.mock_user, text="Create assistant", existing_prompt=None
            )

        # Assertions
        self.assertEqual(exc_info.value.code, 500)
        self.assertIn("Failed to generate/refine system prompt", exc_info.value.message)

        # Verify error metric was sent
        error_metric_calls = [call for call in mock_send_metric.call_args_list if "error" in call[1]["name"].lower()]
        self.assertGreater(len(error_metric_calls), 0)

    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    @patch("codemie.service.assistant_generator_service.logger")
    def test_logging_output(self, mock_logger, mock_chain_class, mock_send_metric):
        """Test that appropriate logging occurs"""
        # Setup mocks
        mock_chain = MagicMock()
        mock_chain_class.from_prompt_template.return_value = mock_chain

        mock_response = PromptDetails(system_prompt="A" * 1000)  # Long prompt for length check
        mock_chain.invoke_with_model.return_value = mock_response

        # Execute
        result = AssistantGeneratorService.generate_assistant_prompt(
            user=self.mock_user, text="Create assistant", existing_prompt="Old prompt"
        )

        # Assertions
        self.assertIsInstance(result, PromptGeneratorResponse)

        # Check that logging occurred with correct parameters
        mock_logger.info.assert_called_once()
        log_message = mock_logger.info.call_args[0][0]
        self.assertIn("Generated/refined prompt", log_message)
        self.assertIn("had_existing=True", log_message)  # existing_prompt provided
        self.assertIn("had_instructions=True", log_message)  # text provided
        self.assertIn("prompt_length=1000", log_message)

    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_metrics_tracking_success(self, mock_chain_class, mock_send_metric):
        """Test that success metrics are tracked correctly"""
        # Setup mocks
        mock_chain = MagicMock()
        mock_chain_class.from_prompt_template.return_value = mock_chain

        mock_response = PromptDetails(system_prompt="Generated prompt")
        mock_chain.invoke_with_model.return_value = mock_response

        # Execute
        AssistantGeneratorService.generate_assistant_prompt(
            user=self.mock_user, text="Create assistant", existing_prompt=None, llm_model="gpt-4"
        )

        # Verify success metric was sent
        self.assertTrue(mock_send_metric.called)

        # Find the success metric call
        success_call = None
        for call in mock_send_metric.call_args_list:
            if call[1].get("name") == "codemie_prompt_generator_total":
                success_call = call
                break

        self.assertIsNotNone(success_call, "codemie_prompt_generator_total metric should be sent")
        self.assertIn("attributes", success_call[1])
        self.assertIn("llm_model", success_call[1]["attributes"])

    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_with_default_llm_model(self, mock_chain_class, mock_send_metric):
        """Test that default LLM model is used when not specified"""
        # Setup mocks
        mock_chain = MagicMock()
        mock_chain_class.from_prompt_template.return_value = mock_chain

        mock_response = PromptDetails(system_prompt="Generated prompt")
        mock_chain.invoke_with_model.return_value = mock_response

        # Execute without specifying llm_model
        result = AssistantGeneratorService.generate_assistant_prompt(
            user=self.mock_user, text="Create assistant", existing_prompt=None
        )

        # Assertions
        self.assertIsInstance(result, PromptGeneratorResponse)

        # Verify chain was created (default model will be used by from_prompt_template)
        mock_chain_class.from_prompt_template.assert_called_once()

    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_long_existing_prompt_preserved(self, mock_chain_class, mock_send_metric):
        """Test that long existing prompts are properly handled"""
        # Setup mocks
        mock_chain = MagicMock()
        mock_chain_class.from_prompt_template.return_value = mock_chain

        # Long existing prompt with multiple sections
        existing_prompt = (
            """## Instructions
You are a highly specialized assistant for enterprise software development.

## Steps to Follow
1. Analyze requirements thoroughly
2. Design scalable solutions
3. Consider security implications
4. Document all decisions
5. Implement with best practices

## Constraints
- Must follow company coding standards
- Security-first approach
- Performance optimization required
- Comprehensive error handling
"""
            * 5
        )  # Repeat to make it longer

        # Refined prompt should preserve structure
        mock_response = PromptDetails(system_prompt=existing_prompt)  # In this case, keep it as-is
        mock_chain.invoke_with_model.return_value = mock_response

        # Execute
        result = AssistantGeneratorService.generate_assistant_prompt(
            user=self.mock_user,
            text=None,
            existing_prompt=existing_prompt,  # Automatic quality review
        )

        # Assertions
        self.assertIsInstance(result, PromptGeneratorResponse)
        self.assertEqual(len(result.system_prompt), len(existing_prompt))

    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.PromptGeneratorChain")
    def test_special_characters_in_prompt(self, mock_chain_class, mock_send_metric):
        """Test handling of special characters in prompts"""
        # Setup mocks
        mock_chain = MagicMock()
        mock_chain_class.from_prompt_template.return_value = mock_chain

        text_with_special_chars = "Create assistant with {placeholders}, [brackets], and \"quotes\""
        existing_with_special_chars = "Existing prompt with $pecial ch@rs & symb0ls"

        mock_response = PromptDetails(system_prompt="Refined prompt with special handling")
        mock_chain.invoke_with_model.return_value = mock_response

        # Execute
        result = AssistantGeneratorService.generate_assistant_prompt(
            user=self.mock_user, text=text_with_special_chars, existing_prompt=existing_with_special_chars
        )

        # Assertions
        self.assertIsInstance(result, PromptGeneratorResponse)

        # Verify special characters were passed through correctly
        mock_chain.add_prompt_refine_instructions.assert_called_once_with(text_with_special_chars)


class TestGenerateAssistantPromptIntegration(unittest.TestCase):
    """Integration tests for generate_assistant_prompt with real templates"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_user = MagicMock(spec=User)
        self.mock_user.id = "user-123"
        self.mock_user.username = "test@example.com"
        self.mock_user.current_project = "test-project"

    @patch("codemie.service.assistant_generator_service.send_log_metric")
    @patch("codemie.service.assistant_generator_service.REFINE_CONTEXT_PROMPT_TEMPLATE")
    @patch("codemie.service.assistant_generator_service.IndexInfo")
    @patch("codemie.service.assistant_generator_service.get_llm_by_credentials")
    def test_template_rendering_with_context(
        self, mock_get_llm, mock_index_info, mock_context_template, mock_send_metric
    ):
        """Test that templates render correctly with datasource context"""
        # Setup LLM mock
        mock_llm = MagicMock()
        mock_structured_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured_llm
        mock_get_llm.return_value = mock_llm

        # Setup datasources
        idx1 = MagicMock()
        idx1.repo_name = "code-repo"
        idx1.index_type = "code"
        idx1.description = "Main code repository"
        mock_index_info.filter_for_user.return_value = [idx1]

        # Mock context template rendering
        mock_context_template.format.return_value = "Formatted datasource context"

        # Mock the final chain invocation - need to mock the combined chain
        from langchain_core.prompts import PromptTemplate

        mock_prompt_template = MagicMock(spec=PromptTemplate)
        mock_combined_chain = MagicMock()
        mock_prompt_template.__or__.return_value = mock_combined_chain

        # Mock the final chain invocation
        mock_response = PromptDetails(system_prompt="Generated prompt with context")
        mock_combined_chain.invoke.return_value = mock_response

        # Patch the template that will be used
        with patch("codemie.service.assistant_generator_service.PROMPT_REFINE_TEMPLATE", mock_prompt_template):
            # Execute
            result = AssistantGeneratorService.generate_assistant_prompt(
                user=self.mock_user,
                text="Create coding assistant",
                existing_prompt=None,
                project="test-project",
                llm_model="test-model",
            )

        # Assertions
        self.assertIsInstance(result, PromptGeneratorResponse)
        self.assertEqual(result.system_prompt, "Generated prompt with context")

        # Verify datasources were fetched
        mock_index_info.filter_for_user.assert_called_once_with(self.mock_user, "test-project")


if __name__ == "__main__":
    unittest.main()
