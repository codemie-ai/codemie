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

"""Tests for AzureDevOpsTestPlanConfig field mapping and validation."""

import pytest

from codemie_tools.azure_devops.test_plan.models import AzureDevOpsTestPlanConfig


@pytest.mark.parametrize(
    "config_data,expected_org_url,expected_token",
    [
        # Direct organization_url configuration
        (
            {"organization_url": "https://dev.azure.com/my-org", "project": "my-project", "token": "my-token"},
            "https://dev.azure.com/my-org",
            "my-token",
        ),
        # Building organization_url from url + organization
        (
            {"url": "https://dev.azure.com", "organization": "my-org", "project": "my-project", "token": "my-token"},
            "https://dev.azure.com/my-org",
            "my-token",
        ),
        # Building organization_url from base_url + organization
        (
            {
                "base_url": "https://dev.azure.com",
                "organization": "my-org",
                "project": "my-project",
                "token": "my-token",
            },
            "https://dev.azure.com/my-org",
            "my-token",
        ),
        # URL with trailing slash
        (
            {"url": "https://dev.azure.com/", "organization": "my-org", "project": "my-project", "token": "my-token"},
            "https://dev.azure.com/my-org",
            "my-token",
        ),
        # access_token alias
        (
            {
                "organization_url": "https://dev.azure.com/my-org",
                "project": "my-project",
                "access_token": "my-access-token",
            },
            "https://dev.azure.com/my-org",
            "my-access-token",
        ),
        # Combined mapping: url+organization and access_token
        (
            {
                "url": "https://dev.azure.com",
                "organization": "my-org",
                "project": "my-project",
                "access_token": "my-token",
            },
            "https://dev.azure.com/my-org",
            "my-token",
        ),
        # organization_url takes priority over url+organization
        (
            {
                "organization_url": "https://dev.azure.com/priority-org",
                "url": "https://dev.azure.com",
                "organization": "ignored-org",
                "project": "my-project",
                "token": "my-token",
            },
            "https://dev.azure.com/priority-org",
            "my-token",
        ),
        # DB format simulation
        (
            {
                "base_url": "https://dev.azure.com",
                "organization": "my-org",
                "project": "my-project",
                "access_token": "my-pat-token",
                "limit": 5,
            },
            "https://dev.azure.com/my-org",
            "my-pat-token",
        ),
    ],
    ids=[
        "direct_organization_url",
        "url_organization_mapping",
        "base_url_organization_mapping",
        "url_with_trailing_slash",
        "access_token_alias",
        "combined_mapping",
        "organization_url_priority",
        "db_format_simulation",
    ],
)
def test_config_mapping_success(config_data, expected_org_url, expected_token):
    """Test successful config mapping from various input formats."""
    config = AzureDevOpsTestPlanConfig(**config_data)
    assert config.organization_url == expected_org_url
    assert config.project == "my-project"
    assert config.token == expected_token


def test_limit_default_value():
    """Test that limit has correct default value."""
    config = AzureDevOpsTestPlanConfig(
        organization_url="https://dev.azure.com/my-org", project="my-project", token="my-token"
    )
    assert config.limit == 5


def test_limit_custom_value():
    """Test custom limit value."""
    config = AzureDevOpsTestPlanConfig(
        organization_url="https://dev.azure.com/my-org", project="my-project", token="my-token", limit=10
    )
    assert config.limit == 10
