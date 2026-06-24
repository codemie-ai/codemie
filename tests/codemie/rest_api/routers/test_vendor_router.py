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

import pytest
from unittest.mock import patch, MagicMock

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.vendor import Entities, Vendor
from codemie.rest_api.routers.vendor import import_vendor_entities, unimport_vendor_entity
from codemie.rest_api.security.user import User
from codemie.service.aws_bedrock.exceptions import (
    AgentcoreEndpointNotFoundError,
    AgentcoreEndpointValidationError,
    EntityAccessDenied,
    EntityNotFound,
)


@patch("codemie.rest_api.routers.vendor.get_service_or_404")
def test_unimport_vendor_entity_delegates_to_unimport_entity(mock_get_service):
    """Test unimport_vendor_entity calls service.unimport_entity and returns success."""
    mock_service = MagicMock()
    mock_get_service.return_value = mock_service

    user = MagicMock(spec=User)

    result = unimport_vendor_entity(Vendor.AWS, Entities.AWS_AGENTCORE_RUNTIMES, "entity-123", user)

    mock_get_service.assert_called_once_with(Vendor.AWS, Entities.AWS_AGENTCORE_RUNTIMES)
    mock_service.unimport_entity.assert_called_once_with("entity-123", user)
    assert result == {"success": True}


@patch("codemie.rest_api.routers.vendor.get_service_or_404")
def test_unimport_vendor_entity_converts_entity_not_found_to_404(mock_get_service):
    """Test unimport_vendor_entity converts EntityNotFound to HTTP 404."""
    mock_service = MagicMock()
    mock_service.unimport_entity.side_effect = EntityNotFound("agent", "missing-id")
    mock_get_service.return_value = mock_service

    user = MagicMock(spec=User)

    with pytest.raises(ExtendedHTTPException) as exc_info:
        unimport_vendor_entity(Vendor.AWS, Entities.AWS_AGENTS, "missing-id", user)

    assert exc_info.value.code == 404


@patch("codemie.rest_api.routers.vendor.get_service_or_404")
def test_unimport_vendor_entity_converts_entity_access_denied_to_403(mock_get_service):
    """Test unimport_vendor_entity converts EntityAccessDenied to HTTP 403."""
    mock_service = MagicMock()
    mock_service.unimport_entity.side_effect = EntityAccessDenied()
    mock_get_service.return_value = mock_service

    user = MagicMock(spec=User)

    with pytest.raises(ExtendedHTTPException) as exc_info:
        unimport_vendor_entity(Vendor.AWS, Entities.AWS_GUARDRAILS, "entity-id", user)

    assert exc_info.value.code == 403


# --- import_vendor_entities: service raises AgentcoreEndpointImportError ---


def _make_user():
    return MagicMock(spec=User)


@patch("codemie.rest_api.routers.vendor.get_service_or_404")
def test_import_vendor_entities_not_found_raises_404(mock_get_service):
    """Service raises AgentcoreEndpointNotFoundError → router returns HTTP 404."""
    mock_service = MagicMock()
    mock_service.import_entities.side_effect = AgentcoreEndpointNotFoundError("Runtime endpoint not found")
    mock_get_service.return_value = mock_service

    with pytest.raises(ExtendedHTTPException) as exc_info:
        import_vendor_entities(
            origin=Vendor.AWS,
            entity=Entities.AWS_AGENTCORE_RUNTIMES,
            body=[
                {
                    "setting_id": "s1",
                    "id": "r1",
                    "agentcoreRuntimeEndpointName": "nonexistent-ep",
                    "configuration_json": "{}",
                }
            ],
            user=_make_user(),
        )

    assert exc_info.value.code == 404


@patch("codemie.rest_api.routers.vendor.get_service_or_404")
def test_import_vendor_entities_validation_error_raises_400(mock_get_service):
    """Service raises AgentcoreEndpointValidationError → router returns HTTP 400."""
    mock_service = MagicMock()
    mock_service.import_entities.side_effect = AgentcoreEndpointValidationError("Invalid JSON template: ...")
    mock_get_service.return_value = mock_service

    with pytest.raises(ExtendedHTTPException) as exc_info:
        import_vendor_entities(
            origin=Vendor.AWS,
            entity=Entities.AWS_AGENTCORE_RUNTIMES,
            body=[
                {"setting_id": "s1", "id": "r1", "agentcoreRuntimeEndpointName": "ep1", "configuration_json": "{bad"}
            ],
            user=_make_user(),
        )

    assert exc_info.value.code == 400


@patch("codemie.rest_api.routers.vendor.get_service_or_404")
def test_import_vendor_entities_success_returns_summary(mock_get_service):
    """Service returns a success list → router returns HTTP 200 with the summary."""
    mock_service = MagicMock()
    mock_service.import_entities.return_value = [
        {"runtimeId": "r1", "endpointName": "ep1", "aiRunId": "uuid-1"},
    ]
    mock_get_service.return_value = mock_service

    result = import_vendor_entities(
        origin=Vendor.AWS,
        entity=Entities.AWS_AGENTCORE_RUNTIMES,
        body=[{"setting_id": "s1", "id": "r1", "agentcoreRuntimeEndpointName": "ep1", "configuration_json": "{}"}],
        user=_make_user(),
    )

    assert result == {"summary": [{"runtimeId": "r1", "endpointName": "ep1", "aiRunId": "uuid-1"}]}
