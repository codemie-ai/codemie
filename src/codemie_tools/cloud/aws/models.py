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

from typing import Dict, Any, Union

from pydantic import BaseModel, Field, model_validator

from codemie_tools.base.models import CodeMieToolConfig, CredentialTypes, RequiredField


class AWSConfig(CodeMieToolConfig):
    """Configuration for AWS integration."""

    credential_type: CredentialTypes = Field(default=CredentialTypes.AWS, exclude=True, frozen=True)

    region: str = RequiredField(
        description="AWS region (e.g., us-east-1, eu-west-1)", json_schema_extra={"placeholder": "us-east-1"}
    )

    access_key_id: str = RequiredField(
        description="AWS Access Key ID for authentication",
        json_schema_extra={
            "placeholder": "your-aws-access-key-id",
            "sensitive": True,
            "help": "https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html",
        },
    )

    secret_access_key: str = RequiredField(
        description="AWS Secret Access Key for authentication",
        json_schema_extra={
            "placeholder": "your-aws-secret-access-key",
            "sensitive": True,
            "help": "https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html",
        },
    )

    session_token: str | None = Field(
        default=None,
        description="AWS Session Token for temporary credentials",
        json_schema_extra={
            "placeholder": "AQoDYXdzEJr...<remainder of security token>",
            "sensitive": True,
            "help": "https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_temp_use-resources.html",
        },
    )

    @model_validator(mode='before')
    def validate_config(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        """Support legacy credential keys for backward compatibility."""
        # Map legacy keys to new keys
        if "aws_region" in values:
            values["region"] = values.pop("aws_region")
        if "aws_access_key_id" in values:
            values["access_key_id"] = values.pop("aws_access_key_id")
        if "aws_secret_access_key" in values:
            values["secret_access_key"] = values.pop("aws_secret_access_key")
        if "aws_session_token" in values:
            values["session_token"] = values.pop("aws_session_token")

        return values


class AWSInput(BaseModel):
    """Input schema for AWS tool operations."""

    query: Union[str, Dict[str, Any]] = Field(
        description="""
        JSON object containing AWS service operation details with the following structure:
        - 'service': AWS service name (e.g., 'ec2', 'iam', 's3', 'lambda')
        - 'method_name': The API method to call (e.g., 'describe_instances', 'list_buckets')
        - 'method_arguments': Dictionary of arguments for the method (can be empty {})

        Example: {"service": "iam", "method_name": "get_user", "method_arguments": {}}
        Example: {"service": "ec2", "method_name": "describe_instances", "method_arguments": {"InstanceIds": ["i-1234567890abcdef0"]}}

        Note: Must be valid JSON. No comments allowed.
        """
    )
