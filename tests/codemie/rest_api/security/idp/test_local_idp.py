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
from fastapi import Request
from codemie.rest_api.security.idp.local import LocalIdp, USER_ID_HEADER
from codemie.rest_api.security.user import User


@pytest.fixture
def local_idp():
    return LocalIdp()


def test_get_session_cookie(local_idp):
    """Test that get_session_cookie returns empty string"""
    assert local_idp.get_session_cookie() == ""


@pytest.mark.asyncio
async def test_authenticate_with_header():
    """Test authentication with user-id header"""
    idp = LocalIdp()
    test_user_id = "test_user"

    # Create mock request with user-id header
    headers = {USER_ID_HEADER: test_user_id}
    request = Request(
        scope={"type": "http", "method": "GET", "headers": [[k.encode(), v.encode()] for k, v in headers.items()]}
    )

    user = await idp.authenticate(request)
    assert isinstance(user, User)
    assert user.id == test_user_id
    assert user.username == test_user_id
    assert user.name == test_user_id


@pytest.mark.asyncio
async def test_authenticate_with_explicit_user_id():
    """Test authentication with explicitly provided user_id"""
    idp = LocalIdp()
    test_user_id = "explicit_user"

    # Create mock request with user-id header
    headers = {USER_ID_HEADER: test_user_id}
    request = Request(
        scope={"type": "http", "method": "GET", "headers": [[k.encode(), v.encode()] for k, v in headers.items()]}
    )

    user = await idp.authenticate(request)
    assert isinstance(user, User)
    assert user.id == test_user_id
    assert user.username == test_user_id
    assert user.name == test_user_id
