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

from unittest.mock import patch

import pytest
from langchain_core.tools import ToolException

from codemie_tools.cloud.aws.models import AWSConfig
from codemie_tools.cloud.aws.tools import GenericAWSTool


@pytest.fixture
def aws_config():
    """Create a test AWS configuration."""
    return AWSConfig(
        region="us-east-1",
        access_key_id="AKIAIOSFODNN7EXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    )


@pytest.fixture
def aws_tool(aws_config):
    """Create a test AWS tool instance."""
    return GenericAWSTool(config=aws_config)


class TestGenericAWSTool:
    def test_tool_initialization(self, aws_tool):
        """Test that tool initializes correctly with client."""
        assert aws_tool.name == "AWS"
        assert aws_tool.client is not None
        assert aws_tool.config.region == "us-east-1"

    def test_execute_with_dict_query(self, aws_tool):
        """Test executing AWS operation with dictionary query."""
        with patch.object(aws_tool.client, 'execute_method') as mock_execute:
            mock_execute.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}

            query = {"service": "iam", "method_name": "get_user", "method_arguments": {}}

            result = aws_tool.execute(query=query)

            assert isinstance(result, str)
            mock_execute.assert_called_once_with(service="iam", method_name="get_user", method_arguments={})

    def test_execute_with_json_string_query(self, aws_tool):
        """Test executing AWS operation with JSON string query."""
        with patch.object(aws_tool.client, 'execute_method') as mock_execute:
            mock_execute.return_value = {"Buckets": []}

            query = '{"service": "s3", "method_name": "list_buckets", "method_arguments": {}}'

            result = aws_tool.execute(query=query)

            assert isinstance(result, str)
            mock_execute.assert_called_once_with(service="s3", method_name="list_buckets", method_arguments={})

    def test_execute_with_markdown_code_block(self, aws_tool):
        """Test executing with query wrapped in markdown code block."""
        with patch.object(aws_tool.client, 'execute_method') as mock_execute:
            mock_execute.return_value = {"User": {"UserName": "test"}}

            query = '```json\n{"service": "iam", "method_name": "get_user", "method_arguments": {}}\n```'

            result = aws_tool.execute(query=query)

            assert isinstance(result, str)
            mock_execute.assert_called_once()

    def test_execute_with_method_arguments(self, aws_tool):
        """Test executing with method arguments."""
        with patch.object(aws_tool.client, 'execute_method') as mock_execute:
            mock_execute.return_value = {"Reservations": []}

            query = {
                "service": "ec2",
                "method_name": "describe_instances",
                "method_arguments": {"InstanceIds": ["i-1234567890abcdef0"]},
            }

            aws_tool.execute(query=query)

            mock_execute.assert_called_once_with(
                service="ec2",
                method_name="describe_instances",
                method_arguments={"InstanceIds": ["i-1234567890abcdef0"]},
            )

    def test_execute_invalid_json(self, aws_tool):
        """Test executing with invalid JSON string."""
        with pytest.raises(ToolException) as exc_info:
            aws_tool.execute(query='{"invalid": json}')

        assert "Invalid JSON format" in str(exc_info.value)

    def test_execute_missing_service_key(self, aws_tool):
        """Test executing without service key."""
        with pytest.raises(ToolException) as exc_info:
            aws_tool.execute(query={"method_name": "get_user"})

        assert "'service' key is missing" in str(exc_info.value)

    def test_execute_invalid_query_type(self, aws_tool):
        """Test executing with invalid query type."""
        with pytest.raises(ToolException) as exc_info:
            aws_tool.execute(query=123)

        assert "optional_args must be a JSON string or dict" in str(exc_info.value)

    def test_execute_client_exception(self, aws_tool):
        """Test handling client exceptions."""
        with patch.object(aws_tool.client, 'execute_method') as mock_execute:
            mock_execute.side_effect = Exception("AWS error")

            query = {"service": "iam", "method_name": "get_user", "method_arguments": {}}

            with pytest.raises(ToolException) as exc_info:
                aws_tool.execute(query=query)

            assert "AWS tool execution failed" in str(exc_info.value)

    def test_healthcheck(self, aws_tool):
        """Test health check functionality."""
        with patch.object(aws_tool.client, 'health_check') as mock_health:
            mock_health.return_value = None

            aws_tool._healthcheck()

            mock_health.assert_called_once()

    def test_healthcheck_failure(self, aws_tool):
        """Test health check when service is unavailable."""
        with patch.object(aws_tool.client, 'health_check') as mock_health:
            mock_health.side_effect = RuntimeError("Connection failed")

            with pytest.raises(RuntimeError):
                aws_tool._healthcheck()
