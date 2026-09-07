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

from codemie.core.constants import DatasourceTypes
from codemie.datasource.exceptions import ConnectionException, UnauthorizedException
from codemie.datasource.loader.xwiki_loader import XWikiLoader
from codemie.service.index.datasource_health_check_service import IndexHealthCheckService

_SERVICE = "codemie.service.index.datasource_health_check_service"


def _request(space="KB", setting_id="setting-1"):
    request = MagicMock()
    request.index_type = DatasourceTypes.XWIKI
    request.project_name = "p1"
    request.space = space
    request.wiki = "xwiki"
    request.setting_id = setting_id
    return request


def _processor_with_stats(**stats):
    processor = MagicMock()
    processor._fetch_remote_stats.return_value = {
        XWikiLoader.DOCUMENTS_COUNT_KEY: stats.get("count", 0),
        "truncated": stats.get("truncated", False),
    }
    return processor


def test_returns_the_real_page_count():
    with (
        patch(f"{_SERVICE}.SettingsService.get_xwiki_creds"),
        patch(f"{_SERVICE}.XWikiDatasourceProcessor", return_value=_processor_with_stats(count=6)),
    ):
        response = IndexHealthCheckService.health_check_datasource(_request(), "u1")
    assert response.documents_count == 6
    assert response.error is None


def test_truncation_is_reported_as_not_healthy():
    processor = _processor_with_stats(count=5000, truncated=True)
    with (
        patch(f"{_SERVICE}.SettingsService.get_xwiki_creds"),
        patch(f"{_SERVICE}.XWikiDatasourceProcessor", return_value=processor),
    ):
        response = IndexHealthCheckService.health_check_datasource(_request(), "u1")
    assert response.documents_count == 5000
    assert response.error is not None
    assert response.error.field_error == "space"


def test_connection_failure_points_at_the_url_field():
    """A 404 on /rest almost always means the base URL carries or omits the /xwiki prefix."""
    processor = MagicMock()
    processor._fetch_remote_stats.side_effect = ConnectionException("xWiki", "HTTP 404 for /rest/wikis")
    with (
        patch(f"{_SERVICE}.SettingsService.get_xwiki_creds"),
        patch(f"{_SERVICE}.XWikiDatasourceProcessor", return_value=processor),
    ):
        response = IndexHealthCheckService.health_check_datasource(_request(), "u1")
    assert response.error is not None
    assert "404" in response.error.details
    assert response.error.field_error == "url"
    assert "/xwiki/rest" in response.error.help


def test_auth_failure_becomes_an_error_message():
    with (
        patch(f"{_SERVICE}.SettingsService.get_xwiki_creds"),
        patch(
            f"{_SERVICE}.XWikiDatasourceProcessor",
            side_effect=UnauthorizedException(datasource_type="xWiki"),
        ),
    ):
        response = IndexHealthCheckService.health_check_datasource(_request(), "u1")
    assert response.error is not None


def test_missing_space_is_rejected_with_a_field_error():
    response = IndexHealthCheckService.health_check_datasource(_request(space=""), "u1")
    assert response.error.field_error == "space"


def test_invalid_field_for_xwiki_is_space():
    assert IndexHealthCheckService.get_invalid_field(DatasourceTypes.XWIKI) == "space"


def test_unresolved_integration_is_a_field_error_not_a_500():
    """When no xWiki integration resolves, get_xwiki_creds returns None. The form must get a
    field error pointing at the integration picker, not a None flowing into the processor and
    surfacing as an AttributeError -> HTTP 500. Fails before the guard: the processor is built."""
    with (
        patch(f"{_SERVICE}.SettingsService.get_xwiki_creds", return_value=None),
        patch(f"{_SERVICE}.XWikiDatasourceProcessor") as processor,
    ):
        response = IndexHealthCheckService.health_check_datasource(_request(), "u1")
    processor.assert_not_called()
    assert response.error is not None
    assert response.error.field_error == "setting_id"


def test_the_selected_integration_is_the_one_checked():
    """Without setting_id the check validates whatever integration the project resolves by
    default, so a wrong base URL in the selected integration passes. Caught in live e2e."""
    with (
        patch(f"{_SERVICE}.SettingsService.get_xwiki_creds") as get_creds,
        patch(f"{_SERVICE}.XWikiDatasourceProcessor", return_value=_processor_with_stats(count=1)),
    ):
        IndexHealthCheckService.health_check_datasource(_request(setting_id="setting-42"), "u1")
    assert get_creds.call_args.kwargs["setting_id"] == "setting-42"
