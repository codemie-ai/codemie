# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Request

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User


def _make_request(headers: dict[str, str]) -> Request:
    encoded = [[k.lower().encode(), v.encode()] for k, v in headers.items()]
    return Request(scope={"type": "http", "method": "GET", "headers": encoded})


@pytest.fixture
def fake_user() -> User:
    return User(
        id="u1",
        username="u1",
        email="u1@test.example",
        name="u1",
        roles=[],
        project_names=[],
        admin_project_names=[],
        knowledge_bases=[],
        is_admin=False,
        is_maintainer=False,
        picture="",
        auth_token="raw-bearer-token",
    )


@pytest.fixture
def inner_idp(fake_user) -> AsyncMock:
    inner = MagicMock()
    inner.authenticate = AsyncMock(return_value=fake_user)
    inner.get_session_cookie = MagicMock(return_value="inner-session")
    return inner


@pytest.fixture
def validator_mock() -> AsyncMock:
    v = MagicMock()
    v.validate = AsyncMock(return_value={"sub": "u1"})
    return v


@pytest.fixture
def wrapped_idp(inner_idp, validator_mock):
    from codemie.rest_api.security.idp.jwks_validating import JwksValidatingIdp

    return JwksValidatingIdp(inner=inner_idp, validator=validator_mock)


class TestJwksValidatingIdp:
    @pytest.mark.asyncio
    async def test_calls_validator_then_inner(self, wrapped_idp, inner_idp, validator_mock):
        request = _make_request({"Authorization": "Bearer raw-bearer-token"})

        await wrapped_idp.authenticate(request)

        validator_mock.validate.assert_awaited_once_with("raw-bearer-token")
        inner_idp.authenticate.assert_awaited_once_with(request)

    @pytest.mark.asyncio
    async def test_short_circuits_on_validation_failure(self, wrapped_idp, inner_idp, validator_mock):
        validator_mock.validate.side_effect = ExtendedHTTPException(
            code=401, message="Token validation failed", details="bad sig"
        )
        request = _make_request({"Authorization": "Bearer raw-bearer-token"})

        with pytest.raises(ExtendedHTTPException):
            await wrapped_idp.authenticate(request)

        inner_idp.authenticate.assert_not_called()

    @pytest.mark.asyncio
    async def test_records_the_verified_token_for_hosted_applications(self, wrapped_idp):
        request = _make_request({"Authorization": "Bearer raw-bearer-token"})

        await wrapped_idp.authenticate(request)

        assert getattr(request.state, "hosted_verified_token") == ({"sub": "u1"}, "raw-bearer-token")

    @pytest.mark.asyncio
    async def test_records_no_verified_token_after_a_failed_validation(self, wrapped_idp, validator_mock):
        from codemie.rest_api.security.idp.jwks_validating import _AuthenticationError

        validator_mock.validate.side_effect = _AuthenticationError("bad")
        request = _make_request({"Authorization": "Bearer raw-bearer-token"})

        with pytest.raises(ExtendedHTTPException):
            await wrapped_idp.authenticate(request)

        assert getattr(request.state, "hosted_verified_token", None) is None

    @pytest.mark.asyncio
    async def test_propagates_inner_user(self, wrapped_idp, fake_user):
        request = _make_request({"Authorization": "Bearer raw-bearer-token"})

        result = await wrapped_idp.authenticate(request)

        assert result is fake_user

    @pytest.mark.asyncio
    async def test_missing_bearer_header_raises_401(self, wrapped_idp, inner_idp, validator_mock):
        request = _make_request({})  # no Authorization

        with pytest.raises(ExtendedHTTPException) as exc_info:
            await wrapped_idp.authenticate(request)

        assert exc_info.value.code == 401
        validator_mock.validate.assert_not_called()
        inner_idp.authenticate.assert_not_called()

    def test_get_session_cookie_delegates_to_inner(self, wrapped_idp, inner_idp):
        result = wrapped_idp.get_session_cookie()
        assert result == "inner-session"
        inner_idp.get_session_cookie.assert_called_once()

    @pytest.mark.asyncio
    async def test_injects_access_token_header(self, wrapped_idp):
        """After JWKS validation the token must appear as x-auth-request-access-token."""
        request = _make_request({"Authorization": "Bearer raw-bearer-token"})

        await wrapped_idp.authenticate(request)

        # The header must be reachable via request.headers (mutable-in-place update)
        assert request.headers.get("x-auth-request-access-token") == "raw-bearer-token"

    @pytest.mark.asyncio
    async def test_inject_replaces_existing_access_token_header(self, wrapped_idp):
        """Pre-existing x-auth-request-access-token is replaced, not duplicated."""
        request = _make_request(
            {
                "Authorization": "Bearer new-token",
                "x-auth-request-access-token": "old-token",
            }
        )

        await wrapped_idp.authenticate(request)

        values = [v.decode() for k, v in request.scope["headers"] if k.lower() == b"x-auth-request-access-token"]
        assert values == ["new-token"]
