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

import pytest

from codemie_tools.git.toolkit import GitToolkit
from codemie_tools.git.utils import TYPE_GITHUB


@pytest.mark.parametrize(
    "domain_only_repo_link",
    [
        "https://github.com",
        "https://gitlab.com",
        "https://bitbucket.org",
        "https://dev.azure.com",
    ],
)
def test_git_integration_domain_only_repo_link(domain_only_repo_link):
    """Test that domain-only URLs return False."""
    configs = {
        "repo_type": TYPE_GITHUB,
        "repo_link": domain_only_repo_link,
    }

    result = GitToolkit.git_integration_healthcheck(configs)
    assert result[0] is False
    assert result[1] == "Testing the connection requires the full repository URL"
