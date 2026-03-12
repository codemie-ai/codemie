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

from codemie_tools.cloud.aws.models import AWSConfig, AWSInput


class TestAWSConfig:
    def test_valid_config(self):
        """Test creating a valid AWS configuration."""
        config = AWSConfig(
            region="us-east-1",
            access_key_id="AKIAIOSFODNN7EXAMPLE",
            secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        )
        assert config.region == "us-east-1"
        assert config.access_key_id == "AKIAIOSFODNN7EXAMPLE"
        assert config.secret_access_key == "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

    def test_legacy_credential_keys(self):
        """Test backward compatibility with legacy credential keys."""
        config = AWSConfig(
            aws_region="eu-west-1",
            aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
            aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            aws_session_token="AQoDYXdzEJr//////////wEaCXVzLWVhc3QtMSJGMEQCEH1kT8N6uC1b82j3g2bPsF1RvbA9Jp4x2dN1YlGJ7wK3oFQGd8jJtQ6r9O7P2Qk5L7aG4H3cS2qR1DpMZkQaL4sPZklmQW5hR29mSWxCcnZKTlNjUkZDa1RaeFVpT1ZCSW5oN2t4U3U5MEt2YXhXRXhhbXBsZVNlc3Npb25Ub2tlbkV4YW1wbGUxMjM0NTY3ODkw",
        )
        assert config.region == "eu-west-1"
        assert config.access_key_id == "AKIAIOSFODNN7EXAMPLE"
        assert config.secret_access_key == "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        assert (
            config.session_token
            == "AQoDYXdzEJr//////////wEaCXVzLWVhc3QtMSJGMEQCEH1kT8N6uC1b82j3g2bPsF1RvbA9Jp4x2dN1YlGJ7wK3oFQGd8jJtQ6r9O7P2Qk5L7aG4H3cS2qR1DpMZkQaL4sPZklmQW5hR29mSWxCcnZKTlNjUkZDa1RaeFVpT1ZCSW5oN2t4U3U5MEt2YXhXRXhhbXBsZVNlc3Npb25Ub2tlbkV4YW1wbGUxMjM0NTY3ODkw"
        )

    def test_config_field_metadata(self):
        """Test that config fields have correct metadata."""
        schema = AWSConfig.model_json_schema()

        # Check region field
        assert "region" in schema["properties"]
        assert schema["properties"]["region"]["description"]

        # Check sensitive fields are marked
        assert "access_key_id" in schema["properties"]
        assert schema["properties"]["access_key_id"]["sensitive"] is True
        assert schema["properties"]["secret_access_key"]["sensitive"] is True


class TestAWSInput:
    def test_valid_dict_query(self):
        """Test creating AWS input with valid dictionary query."""
        aws_input = AWSInput(query={"service": "ec2", "method_name": "describe_instances", "method_arguments": {}})
        assert isinstance(aws_input.query, dict)
        assert aws_input.query["service"] == "ec2"

    def test_valid_string_query(self):
        """Test creating AWS input with valid JSON string query."""
        aws_input = AWSInput(query='{"service": "iam", "method_name": "get_user", "method_arguments": {}}')
        assert isinstance(aws_input.query, str)

    def test_query_with_arguments(self):
        """Test AWS input with method arguments."""
        aws_input = AWSInput(
            query={
                "service": "ec2",
                "method_name": "describe_instances",
                "method_arguments": {"InstanceIds": ["i-1234567890abcdef0"]},
            }
        )
        assert aws_input.query["method_arguments"]["InstanceIds"]
