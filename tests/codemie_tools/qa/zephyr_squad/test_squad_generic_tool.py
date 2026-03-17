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

from codemie_tools.qa.zephyr_squad.tools import (
    ZephyrSquadGenericTool,
    ZEPHYR_SQUAD_HEALTHCHECK_URL,
)
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


@patch("codemie_tools.qa.zephyr_squad.api_wrapper.ZephyrRestAPI.request")
def test_healthcheck_success(mock_make_request):
    mock_make_request.return_value = MagicMock(
        content=b'{"baseUrl": "https://jira.example.com", "version": "8.20.0", "buildNumber": 820000}'
    )

    config = ZephyrSquadConfig(account_id="test_account_id", access_key="test_access_key", secret_key="test_secret_key")
    tool = ZephyrSquadGenericTool(config=config)

    tool._healthcheck()

    mock_make_request.assert_called_once_with(
        path=ZEPHYR_SQUAD_HEALTHCHECK_URL, method="GET", json={}, headers={"Content-Type": "application/json"}
    )


@patch("codemie_tools.qa.zephyr_squad.api_wrapper.ZephyrRestAPI.request")
def test_healthcheck_failure_html_response(mock_make_request):
    mock_make_request.return_value = MagicMock(content=b"<html><body>Login required</body></html>")

    config = ZephyrSquadConfig(account_id="test_account_id", access_key="test_access_key", secret_key="test_secret_key")
    tool = ZephyrSquadGenericTool(config=config)

    with pytest.raises(AssertionError, match="Access denied"):
        tool._healthcheck()


@patch("codemie_tools.qa.zephyr_squad.api_wrapper.ZephyrRestAPI.request")
def test_healthcheck_failure_missing_expected_fields(mock_make_request):
    mock_make_request.return_value = MagicMock(content=b'{"message": "Authentication failed"}')

    config = ZephyrSquadConfig(account_id="test_account_id", access_key="test_access_key", secret_key="test_secret_key")
    tool = ZephyrSquadGenericTool(config=config)

    with pytest.raises(AssertionError, match="Access denied"):
        tool._healthcheck()
