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

"""Unit tests for BaseIdp class."""

import pytest
from fastapi import Request
from typing import Optional

from codemie.rest_api.security.idp.base import BaseIdp
from codemie.rest_api.security.user import User


class ConcreteIdp(BaseIdp):
    """Concrete implementation of BaseIdp for testing."""

    def __init__(self, user: Optional[User] = None, session_cookie: str = "test_session"):
        self._user = user or User(id="test_id")
        self._session_cookie = session_cookie

    async def authenticate(self, request: Request, auth_header: str | None = None) -> User:
        return self._user

    def get_session_cookie(self) -> str:
        return self._session_cookie


class TestBaseIdp:
    """Test cases for BaseIdp class."""

    def test_cannot_instantiate_abstract_class(self):
        """Test that BaseIdp cannot be instantiated directly."""
        with pytest.raises(TypeError, match=r"Can't instantiate abstract class BaseIdp"):
            BaseIdp()

    def test_concrete_implementation_creation(self):
        """Test that concrete implementation can be created."""
        idp = ConcreteIdp()
        assert isinstance(idp, BaseIdp)
        assert isinstance(idp, ConcreteIdp)

    @pytest.mark.asyncio
    async def test_authenticate_method(self, mock_request):
        """Test authenticate method returns correct user."""
        test_user = User(id="test123", username="test_user", name="Test User", roles=["user"])
        idp = ConcreteIdp(user=test_user)

        user = await idp.authenticate(mock_request)
        assert isinstance(user, User)
        assert user.id == "test123"
        assert user.username == "test_user"
        assert user.name == "Test User"
        assert user.roles == ["user"]

    def test_get_session_cookie_method(self):
        """Test get_session_cookie method returns correct cookie name."""
        session_cookie = "custom_session"
        idp = ConcreteIdp(session_cookie=session_cookie)

        assert idp.get_session_cookie() == session_cookie


@pytest.fixture
def mock_request():
    """Fixture to create a mock request object."""
    return Request({"type": "http", "method": "GET", "headers": []})
