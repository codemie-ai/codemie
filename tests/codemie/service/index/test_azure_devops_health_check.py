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


from unittest.mock import MagicMock, patch

import pytest

from codemie.core.constants import DatasourceTypes
from codemie.rest_api.models.settings import CredentialTypes
from codemie.service.index.datasource_health_check_service import IndexHealthCheckService

_SERVICE = "codemie.service.index.datasource_health_check_service"

_CASES = [
    (DatasourceTypes.AZURE_DEVOPS_WIKI, "AzureDevOpsWikiDatasourceProcessor"),
    (DatasourceTypes.AZURE_DEVOPS_WORK_ITEM, "AzureDevOpsWorkItemDatasourceProcessor"),
]


def _request(index_type, setting_id="s1"):
    request = MagicMock()
    request.index_type = index_type
    request.project_name = "p1"
    request.setting_id = setting_id
    request.wiql_query = None

    return request


@pytest.fixture(params=_CASES, ids=["wiki", "work_item"])
def case(request):
    return request.param


def _run(index_type, processor_name, credential_type, setting_id="s1", accessible=True):
    user = MagicMock(id="u1")
    setting = MagicMock(credential_type=credential_type)
    with (
        patch(f"{_SERVICE}.SettingsService.get_azure_devops_creds") as get_creds,
        patch(f"{_SERVICE}.Settings.find_by_id", return_value=setting if credential_type else None) as search,
        patch(f"{_SERVICE}.user_can_access_setting", return_value=accessible) as access_check,
        patch(f"{_SERVICE}.{processor_name}") as processor,
    ):
        processor.return_value._check_docs_health.return_value = 3
        response = IndexHealthCheckService.health_check_datasource(_request(index_type, setting_id), user=user)

    return response, get_creds, search, access_check, processor, user, setting


def test_the_selected_integration_is_forwarded(case):
    index_type, processor_name = case
    response, get_creds, _, access_check, _, user, setting = _run(
        index_type, processor_name, CredentialTypes.AZURE_DEVOPS
    )

    assert get_creds.call_args.kwargs["user_id"] == "u1"
    assert get_creds.call_args.kwargs["setting_id"] == "s1"
    access_check.assert_called_once_with(setting, user, "p1")
    assert response.documents_count == 3


def test_unknown_setting_id_is_a_field_error(case):
    index_type, processor_name = case

    response, get_creds, _, access_check, processor, _, _ = _run(index_type, processor_name, None)

    assert response.error.field_error == "setting_id"
    get_creds.assert_not_called()
    access_check.assert_not_called()
    processor.assert_not_called()


def test_wrong_credential_type_is_a_field_error(case):
    index_type, processor_name = case
    response, get_creds, _, access_check, processor, _, _ = _run(index_type, processor_name, CredentialTypes.JIRA)

    assert response.error.field_error == "setting_id"
    get_creds.assert_not_called()
    access_check.assert_not_called()
    processor.assert_not_called()


def test_inaccessible_setting_id_is_a_field_error(case):
    index_type, processor_name = case

    response, get_creds, _, _, processor, _, _ = _run(
        index_type, processor_name, CredentialTypes.AZURE_DEVOPS, accessible=False
    )

    assert response.error.field_error == "setting_id"
    get_creds.assert_not_called()
    processor.assert_not_called()


def test_without_setting_id_nothing_is_looked_up(case):
    index_type, processor_name = case

    response, get_creds, search, access_check, _, _, _ = _run(index_type, processor_name, None, setting_id=None)

    search.assert_not_called()
    access_check.assert_not_called()
    assert get_creds.call_args.kwargs["setting_id"] is None
    assert response.error is None
