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

"""Hosted endpoints use the existing authenticator plus a verified credential context."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from fastapi import Depends, Request

from codemie.configs import config
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.identity import VerifiedIdentityContext
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

if TYPE_CHECKING:
    from codemie.core.hosted_applications.settings import HostedSettings

UNVERIFIED_REQUEST = "The browser request could not be verified"


class HostedActor(NamedTuple):
    """The platform user who calls a management route, and the hosted sign-in recorded on the request.

    ``identity`` is None when no verified sign-in was recorded; management transactions then refuse writes.
    """

    user: User
    identity: VerifiedIdentityContext | None

    @property
    def principal_id(self) -> str:
        return self.user.id


def verified_context(request: Request, user: User) -> VerifiedIdentityContext | None:
    """The verified sign-in of an internal (not external) user, or None when it cannot use the feature."""
    context = getattr(request.state, "hosted_identity", None)
    if (
        not isinstance(context, VerifiedIdentityContext)
        or context.principal_id != user.id
        or user.user_type == config.EXTERNAL_USER_TYPE
        or request.headers.get("x-bind-key") is not None
        or request.headers.get("user-id") is not None
    ):
        return None
    return context


def account_not_supported() -> HostedApplicationError:
    # 403, not 401: signing in again with the same account or method would not help, and the UI must
    # not treat it as an expired session.
    return HostedApplicationError(
        "ACCOUNT_NOT_SUPPORTED", "Applications are available to internal CodeMie accounts only", 403
    )


def require_verified_context(request: Request, user: User) -> VerifiedIdentityContext:
    context = verified_context(request, user)
    if context is None:
        raise account_not_supported()
    return context


async def hosted_identity(request: Request, user: User = Depends(authenticate)) -> VerifiedIdentityContext:
    """Gateway and session routes only: the verified hosted sign-in itself."""
    return require_verified_context(request, user)


async def hosted_actor(request: Request, user: User = Depends(authenticate)) -> HostedActor:
    """Management routes: the platform user, plus the recorded sign-in that the transaction checks for logout."""
    if user.user_type == config.EXTERNAL_USER_TYPE:
        raise account_not_supported()
    return HostedActor(user, verified_context(request, user))


def guard_mutation(
    request: Request, settings: HostedSettings, *, mime: str | tuple[str, ...] = "application/json"
) -> None:
    """``mime`` is the exact media type, or a tuple of exact media types, that the route accepts."""
    critical = {"origin", "content-type", "authorization", "cookie", "sec-fetch-site", "sec-fetch-mode"}
    names = [key.decode("ascii").lower() for key, _ in request.scope["headers"]]
    if any(names.count(name) > 1 for name in critical):
        raise HostedApplicationError("CSRF_REJECTED", UNVERIFIED_REQUEST, 403)
    content_type = request.headers.get("content-type", "").lower()
    if mime == "application/json":
        allowed = (mime, mime + "; charset=utf-8")
    else:
        allowed = (mime,) if isinstance(mime, str) else mime
    if content_type not in allowed:
        raise HostedApplicationError("UNSUPPORTED_MEDIA_TYPE", "Unsupported request content type", 415)
    cookie = request.headers.get("cookie")
    origin = request.headers.get("origin")
    if not cookie and not origin:
        if not request.headers.get("authorization", "").startswith("Bearer "):
            raise HostedApplicationError("CSRF_REJECTED", UNVERIFIED_REQUEST, 403)
        return
    if (
        origin not in settings.ui_origins
        or request.headers.get("sec-fetch-site") not in ("same-origin", "same-site", "cross-site")
        or request.headers.get("sec-fetch-mode") not in ("cors", "same-origin")
    ):
        raise HostedApplicationError("CSRF_REJECTED", UNVERIFIED_REQUEST, 403)
