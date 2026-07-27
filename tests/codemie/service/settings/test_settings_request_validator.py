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

"""Tests for validate_datasource_type_for_scheduler in settings_request_validator."""

import pytest
from unittest.mock import Mock

from fastapi import status

from unittest.mock import MagicMock

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings import SettingRequest
from codemie.service.settings.settings_request_validator import (
    validate_datasource_type_for_scheduler,
    validate_timezone_value,
    UNSUPPORTED_SCHEDULER_DATASOURCE_TYPES,
)


def _make_datasource(index_type: str, ds_id: str = "ds-123") -> Mock:
    ds = Mock()
    ds.index_type = index_type
    ds.id = ds_id
    return ds


@pytest.mark.parametrize(
    "index_type",
    [
        "knowledge_base_file",
        "knowledge_base_sharepoint",
    ],
)
def test_validate_datasource_type_for_scheduler_rejects_unsupported(index_type):
    """Unsupported datasource types must raise 422."""
    datasource = _make_datasource(index_type)

    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_datasource_type_for_scheduler(datasource)

    assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "does not support triggering by schedule" in exc_info.value.message
    assert index_type in exc_info.value.details


@pytest.mark.parametrize(
    "index_type",
    [
        "code",
        "summary",
        "chunk-summary",
        "knowledge_base_confluence",
        "knowledge_base_jira",
        "knowledge_base_xray",
        "knowledge_base_azure_devops_wiki",
        "knowledge_base_azure_devops_work_item",
        "llm_routing_google",
    ],
)
def test_validate_datasource_type_for_scheduler_accepts_supported(index_type):
    """Supported datasource types must not raise."""
    validate_datasource_type_for_scheduler(_make_datasource(index_type))  # should not raise


def test_unsupported_scheduler_datasource_types_is_frozenset():
    """Constant must be immutable (frozenset)."""
    assert isinstance(UNSUPPORTED_SCHEDULER_DATASOURCE_TYPES, frozenset)


@pytest.mark.parametrize(
    "index_type",
    [
        "knowledge_base_file",
        "knowledge_base_sharepoint",
    ],
)
def test_unsupported_scheduler_datasource_types_contains(index_type):
    """Each unsupported type must be present in the constant."""
    assert index_type in UNSUPPORTED_SCHEDULER_DATASOURCE_TYPES


def _make_request_with_timezone(tz_value):
    cred = MagicMock()
    cred.key = "timezone"
    cred.value = tz_value
    req = MagicMock(spec=SettingRequest)
    req.credential_values = [cred]
    return req


def _make_request_without_timezone():
    req = MagicMock(spec=SettingRequest)
    req.credential_values = []
    return req


@pytest.mark.parametrize("tz", ["UTC", "Europe/Warsaw", "America/New_York"])
def test_validate_timezone_value_valid(tz):
    validate_timezone_value(_make_request_with_timezone(tz))


def test_validate_timezone_value_absent_is_allowed():
    validate_timezone_value(_make_request_without_timezone())


@pytest.mark.parametrize("tz", ["UTC+2", "Bad/Zone", "not_a_timezone", ""])
def test_validate_timezone_value_invalid(tz):
    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_timezone_value(_make_request_with_timezone(tz))
    assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
