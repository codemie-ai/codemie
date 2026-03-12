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
from fastapi import status

from codemie.rest_api.routers.utils import (
    raise_access_denied,
    raise_unprocessable_entity,
    raise_not_found,
    ExtendedHTTPException,
)


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
