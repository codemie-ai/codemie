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

from codemie_tools.base.models import ToolMetadata
from .models import AWSConfig

AWS_TOOL = ToolMetadata(
    name="AWS",
    description="""
    Tool for interacting with AWS (Amazon Web Services) API using boto3 low-level client.
    Accepts a JSON object containing: 'service', 'method_name', and 'method_arguments'.

    Capabilities:
    - Access to all AWS services through boto3 client interface
    - Execute any AWS API method supported by boto3
    - Manage AWS resources (EC2, S3, Lambda, IAM, RDS, etc.)
    - Retrieve information from AWS environment
    - Perform CRUD operations on AWS resources

    Usage:
    Provide a JSON query with:
    - service: AWS service name (e.g., 'ec2', 'iam', 's3', 'lambda')
    - method_name: The boto3 client method to call
    - method_arguments: Dictionary of method parameters

    Examples:
    - Get IAM user: {"service": "iam", "method_name": "get_user", "method_arguments": {}}
    - List S3 buckets: {"service": "s3", "method_name": "list_buckets", "method_arguments": {}}
    - Describe EC2 instances: {"service": "ec2", "method_name": "describe_instances", "method_arguments": {"InstanceIds": ["i-xxx"]}}
    """.strip(),
    label="AWS",
    user_description="""
    Provides access to the AWS (Amazon Web Services) API, allowing for management and interaction
    with various AWS resources and services. This tool enables the AI assistant to perform a wide
    range of operations within the AWS cloud environment.

    Before using it, it is necessary to add a new integration for the tool by providing:
    1. Alias (A friendly name for the AWS account)
    2. AWS Region
    3. AWS Access Key ID
    4. AWS Secret Access Key

    Usage Note:
    Use this tool when you need to manage AWS resources, deploy services, or retrieve information
    from your AWS environment.
    """.strip(),
    settings_config=True,
    config_class=AWSConfig,
)
