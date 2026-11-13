# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""The assistant rollback endpoint passes the disabled MCP report through to its response."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.mcp_restore import DisabledMcpResponse, DisabledMcpServer
from codemie.rest_api.routers.assistant import versions
from codemie.rest_api.security.user import User
from codemie.service.mcp.access_control import CatalogLookupError

_VERSIONS = "codemie.rest_api.routers.assistant.versions"


def test_rollback_returns_disabled_mcp_report() -> None:
    report = [DisabledMcpServer(name="srv", mcp_config_id="cfg-1", reason="inactive")]
    new_config = MagicMock()
    new_config.version_number = 5
    user = User(id="user-1", username="u", name="U")

    with (
        patch(f"{_VERSIONS}.get_assistant_by_id_or_raise", return_value=MagicMock()),
        patch(f"{_VERSIONS}.check_user_can_access_assistant"),
        patch(f"{_VERSIONS}._validate_remote_entities_and_raise"),
        patch(f"{_VERSIONS}._track_assistant_management_metric"),
        patch(
            "codemie.service.assistant.assistant_version_service.AssistantVersionService.rollback_to_version",
            return_value=(new_config, report),
        ),
    ):
        result = versions.rollback_assistant_version(assistant_id="a-1", version_number=2, user=user)

    assert isinstance(result, DisabledMcpResponse)
    assert result.disabled_mcp_servers == report
    assert "version 5" in result.message
    assert "srv" in result.message


def test_rollback_without_disabled_servers_keeps_plain_message() -> None:
    new_config = MagicMock()
    new_config.version_number = 5
    user = User(id="user-1", username="u", name="U")

    with (
        patch(f"{_VERSIONS}.get_assistant_by_id_or_raise", return_value=MagicMock()),
        patch(f"{_VERSIONS}.check_user_can_access_assistant"),
        patch(f"{_VERSIONS}._validate_remote_entities_and_raise"),
        patch(f"{_VERSIONS}._track_assistant_management_metric"),
        patch(
            "codemie.service.assistant.assistant_version_service.AssistantVersionService.rollback_to_version",
            return_value=(new_config, []),
        ),
    ):
        result = versions.rollback_assistant_version(assistant_id="a-1", version_number=2, user=user)

    assert result.message == "Rolled back to version 2. Created new version 5."


def test_rollback_catalogue_read_failure_maps_to_503() -> None:
    user = User(id="user-1", username="u", name="U")

    with (
        patch(f"{_VERSIONS}.get_assistant_by_id_or_raise", return_value=MagicMock()),
        patch(f"{_VERSIONS}.check_user_can_access_assistant"),
        patch(f"{_VERSIONS}._validate_remote_entities_and_raise"),
        patch(f"{_VERSIONS}._track_assistant_management_metric"),
        patch(
            "codemie.service.assistant.assistant_version_service.AssistantVersionService.rollback_to_version",
            side_effect=CatalogLookupError("db down"),
        ),
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            versions.rollback_assistant_version(assistant_id="a-1", version_number=2, user=user)

    assert exc_info.value.code == 503
