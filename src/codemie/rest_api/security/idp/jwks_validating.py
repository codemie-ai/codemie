# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

from __future__ import annotations

from typing import Any, Protocol

from fastapi import Request

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.idp.base import BaseIdp
from codemie.rest_api.security.user import User


_BEARER_PREFIX = "bearer "
# oauth2-proxy injects the validated access token under this header.
# After JWKS signature verification we inject it ourselves so the inner
# enterprise IDP can extract claims from it without re-verifying.
_ACCESS_TOKEN_HEADER = b"x-auth-request-access-token"

try:
    from codemie_enterprise.idp.utils import AuthenticationError as _AuthenticationError  # type: ignore[assignment,misc]
except ImportError:

    class _AuthenticationError(Exception):  # noqa: N818
        """Fallback sentinel used only when codemie_enterprise is absent.

        Deliberately a dedicated subclass rather than bare ``Exception`` so that
        ``except _AuthenticationError`` in :meth:`authenticate` never degrades
        into a catch-all. In practice this branch is unreachable at runtime: the
        validator itself comes from the enterprise package, so a real
        ``AuthenticationError`` can only exist when the import above succeeds.
        """


class _TokenValidator(Protocol):
    """Structural protocol satisfied by enterprise TokenSignatureValidator."""

    async def validate(self, token: str) -> dict[str, Any]: ...


class JwksValidatingIdp(BaseIdp):
    """Composes signature validation in front of an existing IDP.

    Step 1: extract the bearer token from `Authorization`.
    Step 2: cryptographically verify it via TokenSignatureValidator.
    Step 3: inject the verified token as `x-auth-request-access-token` so the
            inner enterprise IDP can read claims without repeating verification.
    Step 4: delegate claim extraction / User construction to the inner IDP.

    The inner IDP's behaviour (claim mapping, user_type checks, exception
    translation) is preserved unchanged.
    """

    validates_token = True

    def __init__(self, inner: BaseIdp, validator: _TokenValidator | None) -> None:
        self._inner = inner
        # ``validator`` is None when the JWKS runtime could not be built (empty
        # JWKS_TRUSTED_ISSUERS, invalid JSON, or missing enterprise package). We
        # still construct the wrapper so cookie-only paths (e.g. logout) keep
        # working, but authenticate() fails closed with a 401.
        self._validator = validator

    def get_session_cookie(self) -> str:
        return self._inner.get_session_cookie()

    async def authenticate(self, request: Request) -> User:
        if self._validator is None:
            raise ExtendedHTTPException(
                code=401,
                message="Token validation unavailable",
                details="JWKS signature validation is enabled but not correctly configured",
            )
        token = self._extract_bearer(request)
        try:
            claims = await self._validator.validate(token)
        except _AuthenticationError as e:
            raise ExtendedHTTPException(
                code=401,
                message="Token validation failed",
                details="Invalid or expired credentials",
            ) from e
        self._inject_access_token_header(request, token)
        from codemie.core.hosted_applications.identity import VERIFIED_TOKEN_STATE

        setattr(request.state, VERIFIED_TOKEN_STATE, (claims, token))
        user = await self._inner.authenticate(request)
        user.tenant_id = self._extract_tenant_id(claims)
        return user

    @staticmethod
    def _extract_tenant_id(claims: dict) -> str | None:
        """Extract a tenant identifier from validated JWT claims.

        Tries standard claim names in priority order:
          - `tid`             (Microsoft Entra ID / Azure AD)
          - `realm_access`    (Keycloak — uses the realm name as tenant)
          - `tenant_id`       (generic custom claim)
        """
        if tid := claims.get("tid"):
            return str(tid)
        realm_access = claims.get("realm_access")
        if isinstance(realm_access, dict) and (realm := realm_access.get("realm")):
            return str(realm)
        if tenant_id := claims.get("tenant_id"):
            return str(tenant_id)
        return None

    @staticmethod
    def _inject_access_token_header(request: Request, token: str) -> None:
        """Inject the validated token into the request scope so inner IDPs can read it.

        Starlette's Headers object holds a *direct reference* to scope["headers"],
        so we mutate the list in place (pop + append) rather than replacing it.
        Replacing the list would leave any already-constructed Headers instance
        pointing at the old object and seeing no change.
        """
        headers: list[tuple[bytes, bytes]] = request.scope["headers"]
        for i in range(len(headers) - 1, -1, -1):
            if headers[i][0].lower() == _ACCESS_TOKEN_HEADER:
                headers.pop(i)
        headers.append((_ACCESS_TOKEN_HEADER, token.encode()))

    @staticmethod
    def _extract_bearer(request: Request) -> str:
        auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
        if not auth_header:
            raise ExtendedHTTPException(
                code=401,
                message="Authentication failed",
                details="Missing Authorization header",
            )
        if not auth_header.lower().startswith(_BEARER_PREFIX):
            raise ExtendedHTTPException(
                code=401,
                message="Authentication failed",
                details="Authorization header must use Bearer scheme",
            )
        return auth_header[len(_BEARER_PREFIX) :].strip()
