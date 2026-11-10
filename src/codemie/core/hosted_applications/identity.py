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

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from time import monotonic

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.settings import IdentityAuthority

# oauth2-proxy forwards the user's Keycloak access token in this header; the Keycloak provider reads it.
FORWARDED_ACCESS_TOKEN_HEADER = "x-auth-request-access-token"
# request.state attribute where the JWKS wrapper stores (claims, token) of the token it verified.
VERIFIED_TOKEN_STATE = "hosted_verified_token"
WARNING_INTERVAL_SECONDS = 600
# The longest a hosted sign-in lasts without a new request the platform accepts.
ABSOLUTE_AUTH_SECONDS = 86400
_warned_at: dict[str, float] = {}


def _is_compact_jwt(jwt, token: str) -> bool:
    """Only proves the header carries a JWT; its claims are never used (see bind_forwarded_identity).

    The structure is checked without decoding claims: a valid JOSE header, an attached payload segment
    (``b64`` is not ``false``) and a payload that is a JSON object.
    """
    try:
        header = jwt.get_unverified_header(token)
        if header.get("b64", True) is False:
            return False
        # get_unverified_header has already proved exactly three base64url segments.
        payload = json.loads(jwt.utils.base64url_decode(token.split(".")[1]))
    except (jwt.PyJWTError, ValueError, RecursionError):
        return False
    return isinstance(payload, dict)


@dataclass(frozen=True)
class VerifiedIdentityContext:
    principal_id: str
    authentication_kind: str
    issued_at: datetime
    expires_at: datetime
    credential_digest: bytes = field(repr=False)
    sid: str | None = field(default=None, repr=False)
    auth_time: datetime | None = None
    # False when the platform accepted the token without checking its exp, as it does for a token
    # that oauth2-proxy forwards; hosted applications then follow the platform's session, not the token.
    expiry_verified: bool = True

    def binding_digest(self, secret: bytes) -> bytes:
        material = [self.principal_id, self.sid or self.credential_digest.hex()]
        return hmac.new(secret, json.dumps(material, separators=(",", ":")).encode(), hashlib.sha256).digest()


@dataclass(frozen=True)
class PrincipalRevocation:
    """Sign-out cutoff for a person the platform has just signed out, on every device.

    It carries no SID, so revocation advances the durable per-principal cutoff and revokes every content
    context of the principal. It can only remove access; nothing accepts it as a signed-in identity.
    """

    principal_id: str
    sid: None = None


class _Unbound(ValueError):
    """Why claims do not bind; the cause goes to the warning log and never carries token content."""

    def __init__(self, cause: str):
        super().__init__(cause)
        self.cause = cause


def _claim_time(value) -> datetime:
    if type(value) not in (int, float):
        raise _Unbound("bad_claims")
    return datetime.fromtimestamp(value, timezone.utc)


def _check_principal(principal_id, credential_digest) -> None:
    if (
        not isinstance(principal_id, str)
        or not principal_id
        or len(principal_id) > 255
        or any(ord(c) < 32 or ord(c) == 127 for c in principal_id)
        or len(credential_digest) != 32
    ):
        raise _Unbound("bad_principal")


def _check_claims(claims, principal_id, credential_digest, now, leeway):
    issued = _claim_time(claims["iat"])
    expires = _claim_time(claims["exp"])
    sid = claims.get("sid")
    auth_time = _claim_time(claims["auth_time"]) if "auth_time" in claims else None
    _check_principal(principal_id, credential_digest)
    if expires <= issued:
        raise _Unbound("bad_claims")
    if issued > now + leeway or (auth_time is not None and auth_time > now + leeway):
        raise _Unbound("future_iat")
    if sid is not None and (not isinstance(sid, str) or not sid or len(sid) > 255):
        raise _Unbound("bad_sid")
    if expires <= now:
        raise _Unbound("expired")
    return issued, expires, sid, auth_time


def _unbound() -> HostedApplicationError:
    return HostedApplicationError("AUTHENTICATION_REQUIRED", "A supported, verified CodeMie login is required", 401)


def bind_identity(
    claims: dict,
    *,
    authority: IdentityAuthority,
    principal_id: str,
    authentication_kind: str,
    credential_digest: bytes,
    now: datetime | None = None,
) -> VerifiedIdentityContext:
    """Bind the claims of a token whose signature and exp the platform verified (the local JWT, the JWKS wrapper).

    This function does not verify JWTs itself. The principal is the user the platform authenticated, never the
    token subject. The deployment's identity authority supplies the clock-skew leeway. Only verified claims may
    feed the session id (``sid``), ``iat`` and ``auth_time`` that revocation and session bounds rely on.
    """
    now = now or datetime.now(timezone.utc)
    leeway = timedelta(seconds=authority.leeway_seconds)
    try:
        issued, expires, sid, auth_time = _check_claims(claims, principal_id, credential_digest, now, leeway)
        return VerifiedIdentityContext(
            principal_id, authentication_kind, issued, expires, credential_digest, sid, auth_time, True
        )
    except (KeyError, ValueError, TypeError, OverflowError, OSError) as exc:
        raise _unbound() from exc


def bind_forwarded_identity(
    *, principal_id: str, credential_digest: bytes, now: datetime | None = None
) -> VerifiedIdentityContext:
    """Bind a token the platform accepted without verifying it here (``platform_jwt``: oauth2-proxy forwards it).

    Nothing is taken from its claims: whoever controls the header could choose them. The session binds to the
    token itself (no ``sid``), starts when the platform accepted this request and, like the platform session,
    lasts at most ``ABSOLUTE_AUTH_SECONDS`` without another accepted request. Sign-out ends it through the
    principal-level cutoff, which needs no ``sid``.
    """
    now = now or datetime.now(timezone.utc)
    try:
        _check_principal(principal_id, credential_digest)
    except (ValueError, TypeError) as exc:
        raise _unbound() from exc
    expires = now + timedelta(seconds=ABSOLUTE_AUTH_SECONDS)
    return VerifiedIdentityContext(principal_id, "platform_jwt", now, expires, credential_digest, None, None, False)


def _authority(request) -> IdentityAuthority | None:
    """The identity authority while hosted applications are on, else None."""
    scope = getattr(request, "scope", None)
    app = scope.get("app") if isinstance(scope, dict) else None
    settings = getattr(getattr(getattr(app, "state", None), "hosted_applications", None), "settings", None)
    if not getattr(settings, "enabled", False) or not isinstance(settings.authority, IdentityAuthority):
        return None
    return settings.authority


def platform_token(request, provider: str) -> str | None:
    """The access token that the given sign-in provider accepted for this request, if the request carries one."""
    if provider == "keycloak":
        return request.headers.get(FORWARDED_ACCESS_TOKEN_HEADER)
    if provider in ("oidc", "entraid-oidc"):
        value = request.headers.get("authorization") or ""
        if value[:7].lower() == "bearer ":
            return value[7:].strip()
    return None


def _warn(cause: str, provider: str) -> None:
    """Log an identity failure at most once per interval and cause; the log never holds token content."""
    now = monotonic()
    if now - _warned_at.get(cause, float("-inf")) < WARNING_INTERVAL_SECONDS:
        return
    _warned_at[cause] = now
    from codemie.configs.logger import logger

    logger.warning(f"Hosted applications found no usable sign-in token: cause={cause} IDP_PROVIDER={provider}")


def _record(request, claims: dict | None, principal_id, kind: str, token: str) -> None:
    authority = _authority(request)
    if authority is None:
        return
    digest = hashlib.sha256(token.encode()).digest()
    if claims is None:
        request.state.hosted_identity = bind_forwarded_identity(principal_id=principal_id, credential_digest=digest)
        return
    request.state.hosted_identity = bind_identity(
        claims, authority=authority, principal_id=principal_id, authentication_kind=kind, credential_digest=digest
    )


def record_platform_identity(request, user_id: str, *, provider: str, verified: tuple[dict, str] | None = None) -> None:
    """Record the identity of the user the platform has just authenticated.

    Call only after authentication succeeded. ``verified`` holds the claims and token of the validated local
    JWT. Otherwise the token comes from the JWKS wrapper, or from the provider's own request header. The local
    JWT and the JWKS wrapper verified the token, so its claims bind (``sid``, ``iat``, ``auth_time``, ``exp``).
    The provider's own header is accepted by the platform without verification here, so only the token's bytes
    bind and none of its claims (``bind_forwarded_identity``). Nothing is recorded while hosted applications are
    off; hosted endpoints then fail closed, and legacy authentication does not change.
    """
    request.state.hosted_identity = None
    if _authority(request) is None:
        return
    # The validator image shares this package but not PyJWT, and never reaches this function.
    import jwt

    claims, token, kind = None, None, "local_jwt"
    if verified is not None:
        claims, token = verified
    elif (wrapped := getattr(request.state, VERIFIED_TOKEN_STATE, None)) is not None:
        (claims, token), kind = wrapped, "jwks"
    else:
        kind = "platform_jwt"
        token = platform_token(request, provider)
        if not token:
            return _warn("missing", provider)
        if not _is_compact_jwt(jwt, token):
            return _warn("not_jwt", provider)
    try:
        _record(request, claims, user_id, kind, token)
    except HostedApplicationError as exc:
        _warn(exc.__cause__.cause if isinstance(exc.__cause__, _Unbound) else "bad_claims", provider)
