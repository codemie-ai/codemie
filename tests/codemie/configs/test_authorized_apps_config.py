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
import yaml
from unittest.mock import patch

from codemie.configs.authorized_apps_config import AuthorizedApplicationsConfig, AuthorizedApplication


@pytest.fixture
def mock_config_data():
    return {
        "authorized_applications": [
            {"name": "test-app", "public_key_url": "http://key.com/key.pem", "allowed_resources": ["datasource"]},
            {"name": "test-app-2", "public_key_path": "/key.pem", "allowed_resources": ["datasource"]},
        ]
    }


@pytest.mark.parametrize(
    "test_case,params,expect_error",
    [
        (
            "key_url_present",
            {
                "name": "test-app",
                "public_key_url": "http://public_keys.com/key.pem",
                "public_key_path": None,
                "allowed_resources": ["datasource"],
            },
            False,
        ),
        (
            "key_path_present",
            {
                "name": "test-app",
                "public_key_url": None,
                "public_key_path": "/apps-key.pem",
                "allowed_resources": ["datasource"],
            },
            False,
        ),
        (
            "url_path_present",
            {
                "name": "test-app",
                "public_key_url": "http://public_keys.com/key.pem",
                "public_key_path": "/apps-key.pem",
                "allowed_resources": ["datasource"],
            },
            True,
        ),
        (
            "url_path_missing",
            {"name": "test-app", "public_key_url": None, "public_key_path": None, "allowed_resources": ["datasource"]},
            True,
        ),
    ],
)
def test_authorized_app_validations(test_case, params, expect_error):
    if expect_error:
        with pytest.raises(ValueError):
            AuthorizedApplication(**params)
    else:
        app = AuthorizedApplication(**params)
        assert app


@patch("requests.get")
def test_authorized_app_public_key_url(mock_get_key, mock_config_data):
    app = AuthorizedApplication(
        name="test-app", public_key_url="http://key.com/key.pem", allowed_resources=["datasource"]
    )
    mock_get_key.return_value.content = b"public_key_content"

    assert app.get_public_key(x_request_id="test-request-id") == b"public_key_content"
    mock_get_key.assert_called_once_with("http://key.com/key.pem", params={"request_id": "test-request-id"})


@patch("pathlib.Path.read_bytes")
def test_authorized_app_public_key_path(mock_read_bytes, mock_config_data):
    app = AuthorizedApplication(name="test-app", public_key_path="keys/app_key.pem", allowed_resources=["datasource"])
    mock_read_bytes.return_value = b"public_key_content"

    assert app.get_public_key(x_request_id="test-request-id") == b"public_key_content"


@patch("yaml.safe_load")
def test_authorized_apps_config(mock_load_data, mock_config_data):
    mock_load_data.return_value = mock_config_data
    config = AuthorizedApplicationsConfig()

    assert len(config.applications) == 2
    assert isinstance(config.applications[0], AuthorizedApplication)
    assert config.applications[0].name == "test-app"
    assert config.applications[0].public_key_url == "http://key.com/key.pem"
    assert config.applications[0].allowed_resources == ["datasource"]


@patch("yaml.safe_load")
def test_authorized_apps_config_invalid_yaml(mock_load_data):
    mock_config = ["array"]
    mock_load_data.return_value = mock_config

    with pytest.raises(ValueError):
        AuthorizedApplicationsConfig()


@patch("yaml.safe_load")
def test_authorized_apps_config_yaml_error(mock_load_data):
    mock_load_data.side_effect = yaml.YAMLError

    with pytest.raises(ValueError):
        AuthorizedApplicationsConfig()


@patch("yaml.safe_load")
def test_applications_names(mock_load_data, mock_config_data):
    mock_load_data.return_value = mock_config_data
    config = AuthorizedApplicationsConfig()

    assert config.applications_names == ["test-app", "test-app-2"]


@patch("yaml.safe_load")
def test_find_by_name(mock_load_data, mock_config_data):
    mock_load_data.return_value = mock_config_data
    config = AuthorizedApplicationsConfig()

    app = config.find_by_name("test-app")
    assert app is not None
    assert app.name == "test-app"

    app = config.find_by_name("non-existent-app")
    assert app is None
