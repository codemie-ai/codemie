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

"""Unit tests for Xray models."""

import pytest
from pydantic import ValidationError

from codemie_tools.base.models import CredentialTypes
from codemie_tools.qa.xray.models import XrayConfig, XrayGetTestsInput, XrayCreateTestInput, XrayExecuteGraphQLInput


class TestXrayConfig:
    """Test cases for XrayConfig model."""

    def test_credential_type(self):
        """Test that credential_type is set correctly."""
        config = XrayConfig()
        assert config.credential_type == CredentialTypes.XRAY

    def test_credential_type_frozen(self):
        """Test that credential_type cannot be modified."""
        config = XrayConfig()
        with pytest.raises(ValidationError):
            config.credential_type = CredentialTypes.JIRA

    def test_default_values(self):
        """Test default values for optional fields."""
        config = XrayConfig()
        assert config.limit == 100
        assert config.verify_ssl is True

    def test_custom_values(self):
        """Test setting custom values."""
        config = XrayConfig(
            base_url="https://custom.xray.app",
            client_id="custom_client_id",
            client_secret="custom_secret",
            limit=50,
            verify_ssl=False,
        )
        assert config.base_url == "https://custom.xray.app"
        assert config.client_id == "custom_client_id"
        assert config.client_secret == "custom_secret"
        assert config.limit == 50
        assert config.verify_ssl is False

    def test_config_serialization(self):
        """Test that credential_type is excluded from serialization."""
        config = XrayConfig(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")
        config_dict = config.model_dump()
        assert "credential_type" not in config_dict


class TestXrayGetTestsInput:
    """Test cases for XrayGetTestsInput model."""

    def test_required_jql(self):
        """Test that jql is required."""
        with pytest.raises(ValidationError):
            XrayGetTestsInput()

    def test_valid_input(self):
        """Test valid input creation."""
        input_data = XrayGetTestsInput(jql='project = "CALC"')
        assert input_data.jql == 'project = "CALC"'

    def test_jql_examples(self):
        """Test various JQL query formats."""
        jql_queries = [
            'project = "CALC" AND type = Test',
            'key in (CALC-1, CALC-2)',
            'status = "To Do" AND assignee = currentUser()',
        ]
        for jql in jql_queries:
            input_data = XrayGetTestsInput(jql=jql)
            assert input_data.jql == jql


class TestXrayCreateTestInput:
    """Test cases for XrayCreateTestInput model."""

    def test_required_mutation(self):
        """Test that graphql_mutation is required."""
        with pytest.raises(ValidationError):
            XrayCreateTestInput()

    def test_valid_input(self):
        """Test valid input creation."""
        mutation = """
        mutation {
            createTest(
                testType: { name: "Manual" },
                jira: { fields: { summary: "Test", project: { key: "CALC" } } }
            ) {
                test { issueId }
            }
        }
        """
        input_data = XrayCreateTestInput(graphql_mutation=mutation)
        assert input_data.graphql_mutation == mutation

    def test_manual_test_mutation(self):
        """Test manual test mutation format."""
        mutation = """
        mutation {
            createTest(
                testType: { name: "Manual" },
                steps: [{ action: "Test step", result: "Expected result" }],
                jira: { fields: { summary: "Manual Test", project: { key: "CALC" } } }
            ) {
                test { issueId testType { name } }
            }
        }
        """
        input_data = XrayCreateTestInput(graphql_mutation=mutation)
        assert "Manual" in input_data.graphql_mutation
        assert "steps" in input_data.graphql_mutation


class TestXrayExecuteGraphQLInput:
    """Test cases for XrayExecuteGraphQLInput model."""

    def test_required_graphql(self):
        """Test that graphql is required."""
        with pytest.raises(ValidationError):
            XrayExecuteGraphQLInput()

    def test_valid_query(self):
        """Test valid query input."""
        query = """
        query {
            getTests(jql: "project = CALC", limit: 10) {
                results { issueId }
            }
        }
        """
        input_data = XrayExecuteGraphQLInput(graphql=query)
        assert input_data.graphql == query

    def test_valid_mutation(self):
        """Test valid mutation input."""
        mutation = """
        mutation {
            updateTest(issueId: "12345", testType: { name: "Manual" }) {
                test { issueId }
            }
        }
        """
        input_data = XrayExecuteGraphQLInput(graphql=mutation)
        assert input_data.graphql == mutation
        assert "mutation" in input_data.graphql
