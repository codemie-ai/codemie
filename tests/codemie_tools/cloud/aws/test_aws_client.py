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

from unittest.mock import Mock, patch, MagicMock

import pytest
from langchain_core.tools import ToolException

from codemie_tools.cloud.aws.aws_client import AWSClient


@pytest.fixture
def aws_client():
    """Create a test AWS client."""
    return AWSClient(
        region="us-east-1",
        access_key_id="AKIAIOSFODNN7EXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    )


@pytest.fixture
def aws_client_with_session():
    """Create a test AWS client with session token."""
    return AWSClient(
        region="us-east-1",
        access_key_id="AKIAIOSFODNN7EXAMPLE",
        secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        session_token="FakeSessionTokenForTestingPurposes123",
    )


class TestAWSClient:
    def test_client_initialization(self, aws_client):
        """Test that client initializes with correct credentials."""
        assert aws_client.region == "us-east-1"
        assert aws_client.access_key_id == "AKIAIOSFODNN7EXAMPLE"
        assert aws_client.secret_access_key == "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

    def test_client_with_session_initialization(self, aws_client_with_session):
        """Test that client initializes with correct credentials."""
        assert aws_client_with_session.region == "us-east-1"
        assert aws_client_with_session.access_key_id == "AKIAIOSFODNN7EXAMPLE"
        assert aws_client_with_session.secret_access_key == "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        assert aws_client_with_session.session_token == "FakeSessionTokenForTestingPurposes123"

    @patch('codemie_tools.cloud.aws.aws_client.boto3.client')
    def test_get_client_success(self, mock_boto_client, aws_client):
        """Test getting a boto3 client for a service."""
        mock_client = Mock()
        mock_boto_client.return_value = mock_client

        client = aws_client.get_client("iam")

        assert client == mock_client
        mock_boto_client.assert_called_once()
        call_args = mock_boto_client.call_args
        assert call_args[0][0] == "iam"
        assert call_args[1]["aws_access_key_id"] == "AKIAIOSFODNN7EXAMPLE"

    @patch('codemie_tools.cloud.aws.aws_client.boto3.client')
    def test_get_client_with_session_success(self, mock_boto_client, aws_client_with_session):
        mock_client = Mock()
        mock_boto_client.return_value = mock_client

        client = aws_client_with_session.get_client("iam")

        assert client == mock_client
        mock_boto_client.assert_called_once()
        call_args = mock_boto_client.call_args
        assert call_args[0][0] == "iam"
        assert call_args[1]["aws_session_token"] == "FakeSessionTokenForTestingPurposes123"

    @patch('codemie_tools.cloud.aws.aws_client.boto3.client')
    def test_get_client_failure(self, mock_boto_client, aws_client):
        """Test handling client creation failure."""
        mock_boto_client.side_effect = Exception("Invalid credentials")

        with pytest.raises(ToolException) as exc_info:
            aws_client.get_client("s3")

        assert "Failed to create AWS client" in str(exc_info.value)
        assert "s3" in str(exc_info.value)

    @patch('codemie_tools.cloud.aws.aws_client.boto3.client')
    def test_execute_method_success(self, mock_boto_client, aws_client):
        """Test successfully executing a method."""
        mock_client = MagicMock()
        mock_client.list_buckets.return_value = {"Buckets": []}
        mock_boto_client.return_value = mock_client

        response = aws_client.execute_method(service="s3", method_name="list_buckets", method_arguments={})

        assert response == {"Buckets": []}
        mock_client.list_buckets.assert_called_once_with()

    @patch('codemie_tools.cloud.aws.aws_client.boto3.client')
    def test_execute_method_with_arguments(self, mock_boto_client, aws_client):
        """Test executing a method with arguments."""
        mock_client = MagicMock()
        mock_client.describe_instances.return_value = {"Reservations": []}
        mock_boto_client.return_value = mock_client

        response = aws_client.execute_method(
            service="ec2", method_name="describe_instances", method_arguments={"InstanceIds": ["i-1234567890abcdef0"]}
        )

        assert response == {"Reservations": []}
        mock_client.describe_instances.assert_called_once_with(InstanceIds=["i-1234567890abcdef0"])

    @patch('codemie_tools.cloud.aws.aws_client.boto3.client')
    def test_execute_method_nonexistent(self, mock_boto_client, aws_client):
        """Test executing a method that doesn't exist."""
        mock_client = MagicMock(spec=[])  # Empty spec means no methods
        mock_boto_client.return_value = mock_client

        with pytest.raises(ToolException) as exc_info:
            aws_client.execute_method(service="iam", method_name="nonexistent_method", method_arguments={})

        assert "does not exist" in str(exc_info.value)
        assert "nonexistent_method" in str(exc_info.value)

    @patch('codemie_tools.cloud.aws.aws_client.boto3.client')
    def test_execute_method_exception(self, mock_boto_client, aws_client):
        """Test handling method execution exception."""
        mock_client = MagicMock()
        mock_client.get_user.side_effect = Exception("Access denied")
        mock_boto_client.return_value = mock_client

        with pytest.raises(ToolException) as exc_info:
            aws_client.execute_method(service="iam", method_name="get_user", method_arguments={})

        assert "Failed to execute" in str(exc_info.value)
        assert "iam.get_user" in str(exc_info.value)

    @patch('codemie_tools.cloud.aws.aws_client.boto3.client')
    def test_health_check_success(self, mock_boto_client, aws_client):
        """Test successful health check."""
        mock_client = MagicMock()
        mock_client.get_caller_identity.return_value = {
            "UserId": "AIDAEXAMPLEID",
            "Account": "123456789012",
            "Arn": "arn:aws:sts::123456789012:assumed-role/MyRole/MySession",
        }
        mock_boto_client.return_value = mock_client

        # Should not raise exception
        aws_client.health_check()

        mock_client.get_caller_identity.assert_called_once()

    @patch('codemie_tools.cloud.aws.aws_client.boto3.client')
    def test_health_check_failure(self, mock_boto_client, aws_client):
        """Test health check with invalid credentials."""
        mock_boto_client.side_effect = Exception("Invalid credentials")

        with pytest.raises(ToolException):
            aws_client.health_check()
