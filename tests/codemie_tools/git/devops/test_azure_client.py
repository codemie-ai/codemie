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

from typing import Dict, Any

from codemie_tools.git.azure_devops.client import AzureDevOpsClient


def test_init_credentials():
    configs: Dict[str, Any] = {
        "repo_link": "https://tag-test.visualstudio.com/_git/MyProject",
        "organization_url": "org",
        "project": "proj",
        "repository_id": "repo",
        "token": "some-token",
    }

    credentials = AzureDevOpsClient.init_credentials(configs=configs)

    assert credentials.organization_url == "org"
    assert credentials.project == "proj"
    assert credentials.base_branch == "main"
    assert credentials.repository_id == "repo"
    assert credentials.token == "some-token"
