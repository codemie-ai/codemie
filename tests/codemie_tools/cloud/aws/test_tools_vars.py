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

from codemie_tools.cloud.aws.models import AWSConfig
from codemie_tools.cloud.aws.tools_vars import AWS_TOOL


class TestAWSToolMetadata:
    def test_tool_name(self):
        """Test that AWS tool has correct name."""
        assert AWS_TOOL.name == "AWS"

    def test_tool_description(self):
        """Test that AWS tool has description."""
        assert AWS_TOOL.description is not None
        assert len(AWS_TOOL.description) > 0
        assert "boto3" in AWS_TOOL.description.lower()

    def test_tool_label(self):
        """Test that AWS tool has correct label."""
        assert AWS_TOOL.label == "AWS"

    def test_tool_user_description(self):
        """Test that AWS tool has user description."""
        assert AWS_TOOL.user_description is not None
        assert len(AWS_TOOL.user_description) > 0

    def test_tool_settings_config(self):
        """Test that AWS tool requires settings configuration."""
        assert AWS_TOOL.settings_config is True

    def test_tool_config_class(self):
        """Test that AWS tool has correct config class."""
        assert AWS_TOOL.config_class == AWSConfig
