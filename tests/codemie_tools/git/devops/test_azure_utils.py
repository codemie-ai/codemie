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

from typing import Optional

import pytest

from codemie_tools.git.azure_devops.utils import parse_azure_devops_url


@pytest.mark.parametrize(
    "url, expected_result",
    [
        (
            "https://dev.azure.com/organization/project/_git/repository",
            {
                "organization": "organization",
                "project": "project",
                "repository": "repository",
                "is_legacy_url": False,
                "base_url": "https://dev.azure.com/organization",
            },
        ),
        (
            "https://dev.azure.com/organization/_git/repository",
            {
                "organization": "organization",
                "project": "repository",
                "repository": "repository",
                "is_legacy_url": False,
                "base_url": "https://dev.azure.com/organization",
            },
        ),
        (
            "https://organization.visualstudio.com/project/_git/repository",
            {
                "organization": "organization",
                "project": "project",
                "repository": "repository",
                "is_legacy_url": True,
                "base_url": "https://organization.visualstudio.com",
            },
        ),
        (
            "https://organization.visualstudio.com/_git/repository",
            {
                "organization": "organization",
                "project": "repository",
                "repository": "repository",
                "is_legacy_url": True,
                "base_url": "https://organization.visualstudio.com",
            },
        ),
        ("https://github.com/org/repo", None),
        ("https://dev.azure.com", None),
        ("invalid_url", None),
        ("", None),
    ],
)
def test_parse_azure_devops_url(url: str, expected_result: Optional[dict]):
    result = parse_azure_devops_url(url)
    assert result == expected_result


@pytest.mark.parametrize(
    "url",
    [
        "https://dev.azure.com/org-with-dash/project/_git/repo",
        "https://dev.azure.com/org_with_underscore/project/_git/repo",
        "https://dev.azure.com/orgWithNumbers123/project/_git/repo",
    ],
)
def test_parse_azure_devops_url_special_characters(url: str):
    result = parse_azure_devops_url(url)
    assert result is not None
    assert isinstance(result, dict)
    assert all(key in result for key in ["organization", "project", "repository", "is_legacy_url", "base_url"])
