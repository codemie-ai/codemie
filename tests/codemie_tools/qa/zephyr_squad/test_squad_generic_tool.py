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
from unittest.mock import patch, MagicMock

from codemie_tools.qa.zephyr_squad.tools import ZephyrSquadGenericTool
from codemie_tools.qa.zephyr_squad.models import ZephyrSquadConfig


def test_execute_no_config():
    tool = ZephyrSquadGenericTool()

    with pytest.raises(ValueError):
        tool.execute(method="GET", relative_path="path")


@patch("codemie_tools.qa.zephyr_squad.api_wrapper.ZephyrRestAPI.request")
def test_execute_valid_config(mock_make_request):
    mock_make_request.return_value = MagicMock(content="test_content")

    config = ZephyrSquadConfig(account_id="test_account_id", access_key="test_access_key", secret_key="test_secret_key")
    tool = ZephyrSquadGenericTool(config=config)

    result = tool.execute(method="GET", relative_path="path")

    assert result == "test_content"
