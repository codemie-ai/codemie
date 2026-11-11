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

"""Run attribute query parameters on the usage analytics routes: parsed, forwarded, and rejected with 422."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.analytics import usage
from codemie.rest_api.routers.analytics._common import AnalyticsQueryParams
from codemie.rest_api.security.user import User

SERVICE = "codemie.rest_api.routers.analytics.usage.AnalyticsService"
TABULAR = {
    "data": {"columns": [], "rows": []},
    "metadata": {"timestamp": "t", "data_as_of": "t", "filters_applied": {}, "execution_time_ms": 1.0},
}


@pytest.fixture
def user():
    user = MagicMock(spec=User)
    user.id = "user-1"
    return user


async def _call(endpoint: str, user, attributes: str | None, **extra):
    return await getattr(usage, endpoint)(
        user=user, params=AnalyticsQueryParams(), client_source=None, attributes=attributes, **extra
    )


FILTERED_ENDPOINTS = [
    ("get_assistants_chats", {}),
    ("get_assistants_requests", {"assistant_id": None}),
    ("get_workflows", {}),
    ("get_workflows_executions", {}),
    ("get_attributes_usage", {"attribute_key": "team"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint, extra", FILTERED_ENDPOINTS)
async def test_attributes_filter_is_parsed_and_forwarded(endpoint, extra, user):
    with patch(SERVICE) as service_class:
        service_method = AsyncMock(return_value=TABULAR)
        setattr(service_class.return_value, endpoint, service_method)

        response = await _call(endpoint, user, '{"team": "core"}', **extra)

    assert response.status_code == status.HTTP_200_OK
    assert service_method.call_args.kwargs["attributes"] == {"team": "core"}


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint, extra", FILTERED_ENDPOINTS)
@pytest.mark.parametrize("raw", ["not json", '["team=core"]', '{"Team": "core"}', '{"team": "' + "v" * 129 + '"}'])
async def test_invalid_attributes_filter_returns_422(endpoint, extra, raw, user):
    with patch(SERVICE) as service_class:
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _call(endpoint, user, raw, **extra)

    assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert exc_info.value.message == "Invalid attributes filter"
    assert not service_class.return_value.method_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("attribute_key", ["Team", "team=core", "codemie.user"])
async def test_attributes_usage_rejects_invalid_key_with_422(attribute_key, user):
    with patch(SERVICE) as service_class:
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _call("get_attributes_usage", user, None, attribute_key=attribute_key)

    assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert exc_info.value.message == "Invalid attribute key"
    assert not service_class.return_value.method_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("attribute_key", [None, ""])
async def test_attributes_usage_rejects_missing_key_with_422(attribute_key, user):
    with patch(SERVICE) as service_class:
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await _call("get_attributes_usage", user, None, attribute_key=attribute_key)

    assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert exc_info.value.message == "Missing attribute key"
    assert exc_info.value.details == "attribute_key is required"
    assert not service_class.return_value.method_calls


@pytest.mark.asyncio
async def test_attributes_usage_forwards_the_validated_key(user):
    with patch(SERVICE) as service_class:
        service_class.return_value.get_attributes_usage = AsyncMock(return_value=TABULAR)

        await _call("get_attributes_usage", user, None, attribute_key="com.epam.gen_ai.service.work_item.id")

    assert service_class.return_value.get_attributes_usage.call_args.args[0] == "com.epam.gen_ai.service.work_item.id"
