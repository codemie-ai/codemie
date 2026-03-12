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
from unittest.mock import patch

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.provider import ProviderService
from codemie.rest_api.models.provider import Provider, CreateProviderRequest, UpdateProviderRequest
from codemie.rest_api.security.user import User
from .mock_provider_data import MOCK_PROVIDER_DATA


@pytest.fixture
def user():
    return User(id="test_user")


def mock_es_index_results():
    return {
        "hits": {
            "hits": [
                {"_source": MOCK_PROVIDER_DATA},
            ]
        }
    }


@pytest.fixture
def mock_provider():
    return Provider(**MOCK_PROVIDER_DATA)


@patch("codemie.rest_api.models.provider.Provider.get_all")
def test_index(mock_get_all, user, mock_provider):
    mock_get_all.return_value = [mock_provider]

    result = ProviderService.index(user=user)

    assert isinstance(result[0], Provider)
    assert result[0].name == "provider"
    assert result[0].service_location_url == "http://path1.com/"
    assert result[0].configuration.auth_type.value == "Bearer"


@patch("codemie.rest_api.models.provider.Provider.find_by_id")
def test_get(mock_find_by_id, mock_provider, user):
    mock_find_by_id.return_value = mock_provider

    result = ProviderService.get(user=user, provider_id="test_id")

    mock_find_by_id.assert_called_once_with("test_id")
    assert isinstance(result, Provider)
    assert result.name == "provider"
    assert result.service_location_url == "http://path1.com/"
    assert result.configuration.auth_type.value == "Bearer"


@patch("codemie.rest_api.models.provider.Provider.find_by_id")
def test_get_not_found(mock_find_by_id, mock_provider, user):
    mock_find_by_id.return_value = None

    with pytest.raises(ExtendedHTTPException) as exc:
        ProviderService.get(user=user, provider_id="test_id")

    mock_find_by_id.assert_called_once_with("test_id")
    assert exc.value.code == 404
    assert exc.value.message == "Not found"
    assert exc.value.details == "The provider with ID [test_id] could not be found in the system."


@patch("codemie.rest_api.models.provider.Provider.check_name_is_unique")
@patch("codemie.rest_api.models.provider.Provider.save")
def test_create(mock_save, mock_check_name, user):
    mock_check_name.return_value = True
    request = CreateProviderRequest(**MOCK_PROVIDER_DATA)
    result = ProviderService.create(user=user, request=request)

    mock_save.assert_called_once()
    assert isinstance(result, Provider)
    assert result.name == "provider"


@patch("codemie.rest_api.models.provider.Provider.check_name_is_unique")
@patch("codemie.rest_api.models.provider.Provider.save")
def test_create_name_non_unique(mock_save, mock_check_name, user):
    mock_check_name.return_value = False

    with pytest.raises(ExtendedHTTPException):
        request = CreateProviderRequest(**MOCK_PROVIDER_DATA)
        ProviderService.create(user=user, request=request)


@patch("codemie.rest_api.models.provider.Provider.check_name_is_unique")
@patch("codemie.rest_api.models.provider.Provider.find_by_id")
@patch("codemie.rest_api.models.provider.Provider.update")
def test_update(mock_update, mock_find_by_id, mock_check_name, mock_provider, user):
    mock_check_name.return_value = True
    mock_find_by_id.return_value = mock_provider
    request = UpdateProviderRequest(name="new_name")

    result = ProviderService.update(user=user, provider_id="test_id", request=request)

    mock_find_by_id.assert_called_once_with("test_id")
    mock_update.assert_called_once()
    assert isinstance(result, Provider)
    assert result.name == "new_name"


@patch("codemie.rest_api.models.provider.Provider.find_by_id")
@patch("codemie.rest_api.models.provider.Provider.delete")
def test_delete(mock_delete, mock_find_by_id, mock_provider, user):
    mock_find_by_id.return_value = mock_provider

    result = ProviderService.delete(user, provider_id="test_id")

    assert result is True
    mock_find_by_id.assert_called_once_with("test_id")
    mock_delete.assert_called_once()
