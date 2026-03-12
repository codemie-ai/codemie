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

import unittest
from unittest.mock import MagicMock, patch

from codemie.rest_api.security.user import User
from codemie.service.assistant_generator_service import PromptGeneratorChain
from langchain_core.language_models import BaseLanguageModel
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel


class TestModel(BaseModel):
    """Test model for structured output"""

    field1: str
    field2: int


class TestPromptGeneratorChain(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures"""
        self.mock_template = MagicMock(spec=PromptTemplate)
        self.mock_llm = MagicMock(spec=BaseLanguageModel)
        self.mock_user = MagicMock(spec=User)
        self.mock_user.current_project = "test-project"
        self.mock_user.is_admin = False

    def test_init(self):
        """Test PromptGeneratorChain initialization"""
        chain = PromptGeneratorChain(self.mock_template, self.mock_llm)

        self.assertEqual(chain._base_template, self.mock_template)
        self.assertEqual(chain._base_llm, self.mock_llm)
        self.assertEqual(chain._chain_input, {})

    @patch('codemie.service.assistant_generator_service.get_llm_by_credentials')
    @patch('codemie.service.assistant_generator_service.llm_service')
    def test_from_prompt_template_with_llm_model(self, mock_llm_service, mock_get_llm):
        """Test from_prompt_template with specified llm_model"""
        mock_get_llm.return_value = self.mock_llm

        chain = PromptGeneratorChain.from_prompt_template(
            self.mock_template, request_id="test-req-id", llm_model="test-model"
        )

        mock_get_llm.assert_called_once_with(llm_model="test-model", request_id="test-req-id")
        self.assertIsInstance(chain, PromptGeneratorChain)
        self.assertEqual(chain._base_template, self.mock_template)
        self.assertEqual(chain._base_llm, self.mock_llm)

    @patch('codemie.service.assistant_generator_service.get_llm_by_credentials')
    @patch('codemie.service.assistant_generator_service.llm_service')
    def test_from_prompt_template_default_llm_model(self, mock_llm_service, mock_get_llm):
        """Test from_prompt_template with default llm_model"""
        mock_llm_service.default_llm_model = "default-model"
        mock_get_llm.return_value = self.mock_llm

        chain = PromptGeneratorChain.from_prompt_template(self.mock_template, request_id="test-req-id")

        mock_get_llm.assert_called_once_with(llm_model="default-model", request_id="test-req-id")
        self.assertIsInstance(chain, PromptGeneratorChain)

    @patch('codemie.service.assistant_generator_service.get_llm_by_credentials')
    def test_from_prompt_template_llm_not_found(self, mock_get_llm):
        """Test from_prompt_template when LLM is not found"""
        mock_get_llm.return_value = None

        with self.assertRaises(RuntimeError) as context:
            PromptGeneratorChain.from_prompt_template(self.mock_template, llm_model="nonexistent-model")

        self.assertIn("llm nonexistent-model model not found", str(context.exception))

    def test_invoke_with_model(self):
        """Test invoke_with_model method"""
        chain = PromptGeneratorChain(self.mock_template, self.mock_llm)

        # Set up mocks
        mock_structured_llm = MagicMock()
        self.mock_llm.with_structured_output.return_value = mock_structured_llm

        mock_combined_chain = MagicMock()
        self.mock_template.__or__.return_value = mock_combined_chain

        expected_result = TestModel(field1="test", field2=42)
        mock_combined_chain.invoke.return_value = expected_result

        # Add some chain input
        chain._chain_input = {"existing": "data"}

        # Test
        result = chain.invoke_with_model(TestModel, {"new": "input"}, config=None, extra_kwarg="value")

        # Assertions
        self.mock_llm.with_structured_output.assert_called_once_with(TestModel)
        self.mock_template.__or__.assert_called_once_with(mock_structured_llm)
        mock_combined_chain.invoke.assert_called_once_with(
            {"existing": "data", "new": "input"}, None, extra_kwarg="value"
        )
        self.assertEqual(result, expected_result)

    @patch('codemie.service.assistant_generator_service.category_service')
    @patch('codemie.service.assistant_generator_service.ASSISTANT_GENERATOR_CATEGORY')
    def test_add_categories_include_true(self, mock_category_template, mock_category_service):
        """Test add_categories when include_categories is True"""
        chain = PromptGeneratorChain(self.mock_template, self.mock_llm)

        # Mock categories
        mock_cat1 = MagicMock()
        mock_cat1.id = "cat1"
        mock_cat1.description = "Category 1"

        mock_cat2 = MagicMock()
        mock_cat2.id = "cat2"
        mock_cat2.description = "Category 2"

        mock_category_service.get_categories.return_value = [mock_cat1, mock_cat2]
        mock_category_template.format.return_value = "formatted_categories"

        # Test
        chain.add_categories(user_categories=["cat1"], include_categories=True)

        # Assertions
        mock_category_service.get_categories.assert_called_once()
        mock_category_template.format.assert_called_once_with(
            include_categories=True,
            categories=[{"id": "cat1", "description": "Category 1"}, {"id": "cat2", "description": "Category 2"}],
            user_categories=["cat1"],
        )
        self.assertEqual(chain._chain_input["categories"], "formatted_categories")

    def test_add_categories_include_false(self):
        """Test add_categories when include_categories is False"""
        chain = PromptGeneratorChain(self.mock_template, self.mock_llm)

        with patch('codemie.service.assistant_generator_service.category_service') as mock_category_service:
            chain.add_categories(user_categories=["cat1"], include_categories=False)

            # Should not call category service
            mock_category_service.get_categories.assert_not_called()
            self.assertNotIn("categories", chain._chain_input)

    def test_transform_toolkits(self):
        """Test _transform_toolkits static method"""
        input_toolkits = [
            {
                "toolkit": "toolkit1",
                "tools": [
                    {"name": "tool1", "label": "Tool 1", "user_description": "Description 1"},
                    {"name": "tool2", "label": "Tool 2", "user_description": None},  # Should be filtered out
                    {"name": "tool3", "label": "Tool 3", "user_description": "Description 3"},
                ],
            },
            {
                "toolkit": "toolkit2",
                "tools": [
                    {"name": "tool4", "label": "Tool 4", "user_description": None},  # All tools filtered
                ],
            },
            {
                "toolkit": "toolkit3",
                "tools": [],  # Empty tools
            },
        ]

        result = PromptGeneratorChain._transform_toolkits(input_toolkits)

        # Should only include toolkit1 with tools that have descriptions
        expected = [
            {
                "toolkit": "toolkit1",
                "tools": [
                    {"name": "tool1", "label": "Tool 1", "description": "Description 1"},
                    {"name": "tool3", "label": "Tool 3", "description": "Description 3"},
                ],
            }
        ]

        self.assertEqual(result, expected)

    @patch('codemie.service.assistant_generator_service.ToolsInfoService')
    def test_add_toolkits_include_false(self, mock_tools_service):
        """Test add_toolkits when include_tools is False"""
        chain = PromptGeneratorChain(self.mock_template, self.mock_llm)

        chain.add_toolkits(self.mock_user, include_tools=False)

        mock_tools_service.get_tools_info.assert_not_called()
        self.assertNotIn("toolkits", chain._chain_input)

    @patch('codemie.service.assistant_generator_service.ToolsInfoService')
    def test_add_toolkits_without_template(self, mock_tools_service):
        """Test add_toolkits without prompt template"""
        chain = PromptGeneratorChain(self.mock_template, self.mock_llm)

        mock_tools_service.get_tools_info.return_value = [
            {"toolkit": "tk1", "tools": [{"name": "t1", "label": "T1", "user_description": "desc"}]}
        ]

        chain.add_toolkits(self.mock_user, include_tools=True, prompt_template=None)

        mock_tools_service.get_tools_info.assert_called_once_with(user=self.mock_user)
        # Should store transformed toolkits directly
        self.assertEqual(
            chain._chain_input["toolkits"],
            [{"toolkit": "tk1", "tools": [{"name": "t1", "label": "T1", "description": "desc"}]}],
        )

    @patch('codemie.service.assistant_generator_service.ToolsInfoService')
    def test_add_toolkits_with_template(self, mock_tools_service):
        """Test add_toolkits with prompt template"""
        chain = PromptGeneratorChain(self.mock_template, self.mock_llm)

        mock_tools_service.get_tools_info.return_value = [
            {"toolkit": "tk1", "tools": [{"name": "t1", "label": "T1", "user_description": "desc"}]}
        ]

        mock_prompt = MagicMock()
        mock_prompt.format.return_value = "formatted_toolkits"

        chain.add_toolkits(self.mock_user, include_tools=True, prompt_template=mock_prompt)

        mock_tools_service.get_tools_info.assert_called_once_with(user=self.mock_user)
        mock_prompt.format.assert_called_once_with(
            toolkits=[{"toolkit": "tk1", "tools": [{"name": "t1", "label": "T1", "description": "desc"}]}],
            include_tools=True,
            toolkit_aliases={"tk1": ["t1"]},
        )
        self.assertEqual(chain._chain_input["toolkits"], "formatted_toolkits")

    @patch('codemie.service.assistant_generator_service.IndexInfo')
    @patch('codemie.service.assistant_generator_service.REFINE_CONTEXT_PROMPT_TEMPLATE')
    def test_add_context_fetches_all_datasources(self, mock_context_template, mock_index_info):
        """Test add_context fetches ALL available datasources for the user/project"""
        chain = PromptGeneratorChain(self.mock_template, self.mock_llm)

        # Mock indexes - returns all available datasources
        idx1 = MagicMock()
        idx1.repo_name = "datasource1"
        idx1.index_type = "code"
        idx1.description = "Code datasource"

        idx2 = MagicMock()
        idx2.repo_name = "datasource2"
        idx2.index_type = "knowledge_base"
        idx2.description = None  # Test with None description

        mock_index_info.filter_for_user.return_value = [idx1, idx2]
        mock_context_template.format.return_value = "formatted_context"

        # Call add_context with ANY context parameter (now ignored)
        chain.add_context(self.mock_user, "project", [])

        # Should fetch ALL available datasources
        mock_index_info.filter_for_user.assert_called_once_with(self.mock_user, "project")

        # Should format with all datasources and handle None description
        mock_context_template.format.assert_called_once_with(
            include_context=True,
            context=[
                {"repo_name": "datasource1", "index_type": "code", "description": "Code datasource"},
                {"repo_name": "datasource2", "index_type": "knowledge_base", "description": "No description available"},
            ],
            current_datasources=[],
        )
        self.assertEqual(chain._chain_input["context"], "formatted_context")

    @patch('codemie.service.assistant_generator_service.IndexInfo')
    @patch('codemie.service.assistant_generator_service.REFINE_CONTEXT_PROMPT_TEMPLATE')
    def test_add_context_with_project(self, mock_context_template, mock_index_info):
        """Test add_context with specified project"""
        chain = PromptGeneratorChain(self.mock_template, self.mock_llm)

        # Mock indexes - now fetches ALL available datasources for the project
        idx1 = MagicMock()
        idx1.repo_name = "repo1"
        idx1.index_type = "type1"
        idx1.description = "desc1"

        idx2 = MagicMock()
        idx2.repo_name = "repo2"
        idx2.index_type = "type2"
        idx2.description = "desc2"

        mock_index_info.filter_for_user.return_value = [idx1, idx2]
        mock_context_template.format.return_value = "formatted_context"

        # Test - context parameter is now ignored (kept for API consistency)
        chain.add_context(self.mock_user, "specific-project", None)

        # Assertions - should use filter_for_user with the specified project
        mock_index_info.filter_for_user.assert_called_once_with(self.mock_user, "specific-project")
        mock_context_template.format.assert_called_once_with(
            include_context=True,
            context=[
                {"repo_name": "repo1", "index_type": "type1", "description": "desc1"},
                {"repo_name": "repo2", "index_type": "type2", "description": "desc2"},
            ],
            current_datasources=[],
        )
        self.assertEqual(chain._chain_input["context"], "formatted_context")

    @patch('codemie.service.assistant_generator_service.IndexInfo')
    @patch('codemie.service.assistant_generator_service.REFINE_CONTEXT_PROMPT_TEMPLATE')
    def test_add_context_without_project(self, mock_context_template, mock_index_info):
        """Test add_context without specified project (uses user's current project)"""
        chain = PromptGeneratorChain(self.mock_template, self.mock_llm)

        # Mock indexes
        idx1 = MagicMock()
        idx1.repo_name = "repo1"
        idx1.index_type = "type1"
        idx1.description = "desc1"

        mock_index_info.filter_for_user.return_value = [idx1]
        mock_context_template.format.return_value = "formatted_context"

        # Test with project=None - should use user's current_project
        chain.add_context(self.mock_user, None, None)

        # Should use user's current_project
        mock_index_info.filter_for_user.assert_called_once_with(self.mock_user, self.mock_user.current_project)
        self.assertEqual(chain._chain_input["context"], "formatted_context")

    def test_integration_full_chain(self):
        """Test full integration of chain building and invocation"""
        chain = PromptGeneratorChain(self.mock_template, self.mock_llm)

        # Set up all mocks
        with (
            patch('codemie.service.assistant_generator_service.category_service') as mock_cat_service,
            patch('codemie.service.assistant_generator_service.ToolsInfoService') as mock_tools_service,
            patch('codemie.service.assistant_generator_service.IndexInfo') as mock_index_info,
            patch('codemie.service.assistant_generator_service.ASSISTANT_GENERATOR_CATEGORY') as mock_cat_template,
            patch('codemie.service.assistant_generator_service.REFINE_TOOLKITS_PROMPT_TEMPLATE') as mock_tools_template,
            patch(
                'codemie.service.assistant_generator_service.REFINE_CONTEXT_PROMPT_TEMPLATE'
            ) as mock_context_template,
        ):
            # Setup category mocks
            mock_cat = MagicMock()
            mock_cat.id = "cat1"
            mock_cat.description = "Category 1"
            mock_cat_service.get_categories.return_value = [mock_cat]
            mock_cat_template.format.return_value = "formatted_categories"

            # Setup toolkit mocks
            mock_tools_service.get_tools_info.return_value = [
                {"toolkit": "tk1", "tools": [{"name": "t1", "label": "T1", "user_description": "desc"}]}
            ]
            mock_tools_template.format.return_value = "formatted_toolkits"

            # Setup context mocks
            idx = MagicMock()
            idx.repo_name = "repo1"
            idx.index_type = "type1"
            idx.description = "desc1"
            mock_index_info.filter_for_user.return_value = [idx]
            mock_context_template.format.return_value = "formatted_context"

            # Setup chain invocation mocks
            mock_structured_llm = MagicMock()
            self.mock_llm.with_structured_output.return_value = mock_structured_llm

            mock_combined_chain = MagicMock()
            self.mock_template.__or__.return_value = mock_combined_chain

            expected_result = TestModel(field1="result", field2=123)
            mock_combined_chain.invoke.return_value = expected_result

            # Build the chain
            chain.add_categories(user_categories=["cat1"], include_categories=True)
            chain.add_toolkits(self.mock_user, include_tools=True, prompt_template=mock_tools_template)
            chain.add_context(self.mock_user, "project", None)

            # Invoke the chain
            result = chain.invoke_with_model(TestModel, {"user_input": "test"}, config=None)

            # Verify chain was built correctly
            self.assertEqual(chain._chain_input["categories"], "formatted_categories")
            self.assertEqual(chain._chain_input["toolkits"], "formatted_toolkits")
            self.assertEqual(chain._chain_input["context"], "formatted_context")

            # Verify invocation
            mock_combined_chain.invoke.assert_called_once()
            invoke_args = mock_combined_chain.invoke.call_args[0][0]
            self.assertEqual(invoke_args["categories"], "formatted_categories")
            self.assertEqual(invoke_args["toolkits"], "formatted_toolkits")
            self.assertEqual(invoke_args["context"], "formatted_context")
            self.assertEqual(invoke_args["user_input"], "test")

            self.assertEqual(result, expected_result)

    def test_transform_toolkits_edge_cases(self):
        """Test _transform_toolkits with edge cases"""
        # Test with missing fields
        input_toolkits = [
            {
                # Missing toolkit name
                "tools": [{"name": "tool1", "label": "Tool 1", "user_description": "desc"}]
            },
            {
                "toolkit": "toolkit2",
                # Missing tools key
            },
            {
                "toolkit": "toolkit3",
                "tools": [
                    # Missing fields in tool
                    {"user_description": "desc"},
                    {"name": "tool2"},  # Missing user_description
                ],
            },
        ]

        result = PromptGeneratorChain._transform_toolkits(input_toolkits)

        expected = [
            {
                "toolkit": "",  # Default empty string for missing toolkit
                "tools": [{"name": "tool1", "label": "Tool 1", "description": "desc"}],
            },
            {"toolkit": "toolkit3", "tools": [{"name": "", "label": "", "description": "desc"}]},
        ]

        self.assertEqual(result, expected)

    def test_chain_state_isolation(self):
        """Test that chain input state is properly isolated between instances"""
        chain1 = PromptGeneratorChain(self.mock_template, self.mock_llm)
        chain2 = PromptGeneratorChain(self.mock_template, self.mock_llm)

        # Modify chain1
        chain1._chain_input["test"] = "value1"

        # chain2 should not be affected
        self.assertEqual(chain1._chain_input, {"test": "value1"})
        self.assertEqual(chain2._chain_input, {})

    @patch('codemie.service.assistant_generator_service.category_service')
    @patch('codemie.service.assistant_generator_service.ASSISTANT_GENERATOR_CATEGORY')
    def test_add_categories_empty_categories(self, mock_category_template, mock_category_service):
        """Test add_categories when category service returns empty list"""
        chain = PromptGeneratorChain(self.mock_template, self.mock_llm)

        mock_category_service.get_categories.return_value = []
        mock_category_template.format.return_value = "formatted_empty_categories"

        chain.add_categories(user_categories=None, include_categories=True)

        mock_category_template.format.assert_called_once_with(
            include_categories=True, categories=[], user_categories=None
        )
        self.assertEqual(chain._chain_input["categories"], "formatted_empty_categories")
