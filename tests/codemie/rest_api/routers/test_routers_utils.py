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
from datetime import datetime, timezone

import pytest
from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.utils import raise_access_denied, raise_unprocessable_entity, raise_not_found
from codemie.utils.datetime_utils import get_timestamp_bounds
from codemie.rest_api.models.conversation import GeneratedMessage


def test_raise_access_denied():
    action = "delete"

    with pytest.raises(ExtendedHTTPException) as exc_info:
        raise_access_denied(action)

    exception = exc_info.value
    assert exception.code == status.HTTP_401_UNAUTHORIZED
    assert exception.message == "Access denied"
    assert exception.details == "You do not have the necessary permissions to delete this entity."
    assert "Please ensure you have the correct role or permissions" in exception.help
    assert "contact your system administrator" in exception.help


def test_raise_unprocessable_entity():
    action = "create"
    resource = "user"
    original_exception = ValueError("Invalid email format")

    with pytest.raises(ExtendedHTTPException) as exc_info:
        raise_unprocessable_entity(action, resource, original_exception)

    exception = exc_info.value
    assert exception.code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert exception.message == "Failed to create a user"
    assert exception.details == "An error occurred while trying to create a user: Invalid email format"
    assert "Please check your request format" in exception.help
    assert "contact support" in exception.help
    assert exc_info.value.__cause__ == original_exception


def test_raise_not_found():
    resource_id = "user123"
    resource_type = "User"

    with pytest.raises(ExtendedHTTPException) as exc_info:
        raise_not_found(resource_id, resource_type)

    exception = exc_info.value
    assert exception.code == status.HTTP_404_NOT_FOUND
    assert exception.message == "User not found"
    assert exception.details == "The User with ID [user123] could not be found in the system."
    assert exception.help == "Please ensure the specified ID is correct"


def test_get_timestamp_bounds_empty():
    assert get_timestamp_bounds([]) == (None, None)
    assert get_timestamp_bounds(None) == (None, None)


def test_get_timestamp_bounds_single_message():
    dt = datetime(2023, 1, 1, 12, 0, 0)
    dt_utc = dt.replace(tzinfo=timezone.utc)
    history = [GeneratedMessage(message="msg", role="User", date=dt)]
    assert get_timestamp_bounds(history) == (dt_utc, dt_utc)


def test_get_timestamp_bounds_multiple_messages():
    dt1 = datetime(2023, 1, 1, 12, 0, 0)
    dt2 = datetime(2023, 1, 1, 12, 1, 0)
    dt3 = datetime(2023, 1, 1, 12, 5, 0)
    history = [
        GeneratedMessage(message="msg1", role="User", date=dt1),
        GeneratedMessage(message="msg2", role="Assistant", date=dt2),
        GeneratedMessage(message="msg3", role="User", date=dt3),
    ]
    assert get_timestamp_bounds(history) == (
        dt1.replace(tzinfo=timezone.utc),
        dt3.replace(tzinfo=timezone.utc),
    )


def test_get_timestamp_bounds_messages_without_date():
    dt = datetime(2023, 1, 1, 12, 0, 0)
    history = [
        GeneratedMessage(message="msg1", role="User", date=None),
        GeneratedMessage(message="msg2", role="Assistant", date=dt),
        GeneratedMessage(message="msg3", role="User"),
    ]
    assert get_timestamp_bounds(history) == (dt.replace(tzinfo=timezone.utc), dt.replace(tzinfo=timezone.utc))


def test_get_timestamp_bounds_mixed_naive_and_aware_utc():
    dt_naive = datetime(2023, 1, 1, 10, 0, 0)
    dt_aware = datetime(2023, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
    history = [
        GeneratedMessage(message="a", role="User", date=dt_naive),
        GeneratedMessage(message="b", role="Assistant", date=dt_aware),
    ]
    assert get_timestamp_bounds(history) == (
        dt_naive.replace(tzinfo=timezone.utc),
        dt_aware,
    )


def test_get_timestamp_bounds_all_messages_without_date():
    history = [
        GeneratedMessage(message="msg1", role="User", date=None),
        GeneratedMessage(message="msg3", role="User"),
    ]
    assert get_timestamp_bounds(history) == (None, None)


def test_get_timestamp_bounds_dict_messages():
    """Dict-based messages (raw JSONB-decoded) are handled via dict key access, not getattr."""
    dt1 = datetime(2023, 6, 1, 8, 0, 0, tzinfo=timezone.utc)
    dt2 = datetime(2023, 6, 1, 9, 0, 0, tzinfo=timezone.utc)
    dt3 = datetime(2023, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    history = [
        {"role": "User", "message": "m1", "date": dt2},
        {"role": "Assistant", "message": "m2", "date": dt1},
        {"role": "User", "message": "m3", "date": dt3},
        {"role": "User", "message": "m4"},  # no "date" key — should be skipped
    ]
    assert get_timestamp_bounds(history) == (dt1, dt3)


def test_get_timestamp_bounds_dict_messages_no_dates():
    """Dict-based messages with no date values return (None, None)."""
    history = [
        {"role": "User", "message": "m1"},
        {"role": "Assistant", "message": "m2", "date": None},
    ]
    assert get_timestamp_bounds(history) == (None, None)
