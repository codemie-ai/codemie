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

from unittest.mock import MagicMock, patch, ANY

import pytest
from requests import HTTPError

from codemie_tools.qa.zephyr_squad.api_wrapper import ZephyrRestAPI


@patch("requests.Session.request")
def test_request_successfull(mock_request):
    mock_request.return_value = MagicMock(value="test_response", status_code=200)
    api = ZephyrRestAPI(account_id="test_account_id", secret_key="test_secret_key", access_key="secret_key")

    response = api.request(method="GET", path="/test_path")

    assert response.value == "test_response"
    mock_request.assert_called_once_with(
        method='GET',
        url='https://prod-api.zephyr4jiracloud.com/connect/public/rest/api/1.0/test_path',
        headers=ANY,
        data=None,
        json=None,
        timeout=75,
        files=None,
    )


@patch("requests.Session.request")
def test_request_failure(mock_request):
    mock_request.return_value = MagicMock(value="test_response", status_code=401)
    api = ZephyrRestAPI(account_id="test_account_id", secret_key="test_secret_key", access_key="secret_key")

    with pytest.raises(HTTPError):
        api.request(method="GET", path="/test_path")


@patch("requests.Session.request")
def test_request_with_custom_headers_and_content_type(mock_request):
    """Test that custom headers are merged with auth headers and content type can be set via headers"""
    mock_request.return_value = MagicMock(value="test_response", status_code=200)
    api = ZephyrRestAPI(account_id="test_account_id", secret_key="test_secret_key", access_key="test_access_key")

    custom_headers = {"X-Custom-Header": "custom_value", "Content-Type": "application/xml"}
    response = api.request(method="POST", path="/test_path", headers=custom_headers)

    assert response.value == "test_response"

    # Verify the request was called with merged headers
    call_args = mock_request.call_args
    headers = call_args.kwargs['headers']

    # Auth headers should be present
    assert 'Authorization' in headers
    assert headers['Authorization'].startswith('JWT ')
    assert headers['zapiAccessKey'] == 'test_access_key'

    # Custom content type should be set
    assert headers['Content-Type'] == 'application/xml'

    # Custom header should be preserved
    assert headers['X-Custom-Header'] == 'custom_value'


@patch("requests.Session.request")
def test_request_without_explicit_content_type(mock_request):
    """Test that no default content type is set when not specified"""
    mock_request.return_value = MagicMock(value="test_response", status_code=200)
    api = ZephyrRestAPI(account_id="test_account_id", secret_key="test_secret_key", access_key="test_access_key")

    response = api.request(method="GET", path="/test_path")

    assert response.value == "test_response"

    # Verify no default content type is set
    call_args = mock_request.call_args
    headers = call_args.kwargs['headers']

    # Auth headers should be present
    assert 'Authorization' in headers
    assert headers['zapiAccessKey'] == 'test_access_key'

    # Content-Type should not be set automatically
    assert 'Content-Type' not in headers
