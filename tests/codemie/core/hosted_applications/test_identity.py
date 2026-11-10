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

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import jwt
import pytest

from codemie.core.hosted_applications import identity
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.identity import (
    ABSOLUTE_AUTH_SECONDS,
    VERIFIED_TOKEN_STATE,
    VerifiedIdentityContext,
    bind_identity,
    record_platform_identity,
)
from codemie.core.hosted_applications.settings import IdentityAuthority

NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
AUTHORITY = IdentityAuthority(5)
CLAIMS = {
    "iss": "any-issuer",
    "sub": "immutable-user",
    "iat": int(NOW.timestamp()) - 5,
    "exp": int(NOW.timestamp()) + 600,
    "sid": "same-login",
}


@pytest.fixture(autouse=True)
def fresh_warning_state():
    # Warning state is module-wide; xdist workers and tests must not share it.
    identity._warned_at.clear()
    yield
    identity._warned_at.clear()


def bind(claims=None, *, authority=AUTHORITY, principal="user-1", kind="jwks", digest=b"a" * 32):
    return bind_identity(
        {**CLAIMS, **(claims or {})},
        authority=authority,
        principal_id=principal,
        authentication_kind=kind,
        credential_digest=digest,
        now=NOW,
    )


def test_verified_identity_carries_the_principal_without_organization_or_issuer():
    context = bind()
    assert isinstance(context, VerifiedIdentityContext)
    assert not hasattr(context, "org_id")
    assert not hasattr(context, "issuer")
    assert context.principal_id == "user-1"
    assert context.expires_at > NOW
    assert "credential_digest" not in repr(context)


def test_principal_is_the_authenticated_user_not_the_token_subject():
    context = bind({"sub": "something-else", "iss": "any-issuer", "aud": "any-client"})
    assert context.principal_id == "user-1"


def _digest(material):
    return hmac.new(b"s" * 32, json.dumps(material, separators=(",", ":")).encode(), hashlib.sha256).digest()


def test_binding_digest_covers_the_principal_and_the_session_id():
    context = bind()
    assert context.binding_digest(b"s" * 32) == _digest(["user-1", "same-login"])


def test_binding_digest_falls_back_to_the_credential_digest_without_a_session_id():
    context = bind({"sid": None})
    assert context.binding_digest(b"s" * 32) == _digest(["user-1", (b"a" * 32).hex()])


def test_binding_digest_ignores_the_authority():
    assert bind(authority=IdentityAuthority(5)).binding_digest(b"s" * 32) == bind().binding_digest(b"s" * 32)


def test_binding_digest_differs_for_another_principal_or_session():
    base = bind().binding_digest(b"s" * 32)
    assert bind(principal="user-2").binding_digest(b"s" * 32) != base
    assert bind({"sid": "other-login"}).binding_digest(b"s" * 32) != base


@pytest.mark.parametrize("principal", ["", "a\x00b", "x" * 256, None], ids=["empty", "nul", "long", "none"])
def test_bad_principal_is_refused(principal):
    with pytest.raises(HostedApplicationError):
        bind(principal=principal)


def test_claims_without_sub_or_audience_still_bind():
    context = bind_identity(
        {"iat": CLAIMS["iat"], "exp": CLAIMS["exp"]},
        authority=AUTHORITY,
        principal_id="user-1",
        authentication_kind="local_jwt",
        credential_digest=b"a" * 32,
        now=NOW,
    )
    assert context.sid is None


@pytest.mark.parametrize(
    "changes",
    [
        {"exp": None},
        {"iat": None},
        {"iat": True},
        {"exp": int(NOW.timestamp()) - 1},
        {"iat": int(NOW.timestamp()) + 6},
        {"iat": "123"},
    ],
)
def test_identity_denies_unbound_or_bad_claims(changes):
    with pytest.raises(HostedApplicationError):
        bind(changes)


def test_forwarded_token_binds_per_token_and_takes_nothing_from_its_claims():
    """CR-004: an unverified forwarded token supplies no sid, iat or auth_time; only its bytes bind."""
    context = identity.bind_forwarded_identity(principal_id="user-1", credential_digest=b"a" * 32, now=NOW)
    assert (context.authentication_kind, context.sid, context.auth_time) == ("platform_jwt", None, None)
    assert context.expiry_verified is False
    assert context.issued_at == NOW
    assert context.expires_at == NOW + timedelta(seconds=ABSOLUTE_AUTH_SECONDS)
    assert context.binding_digest(b"s" * 32) == _digest(["user-1", (b"a" * 32).hex()])


@pytest.mark.parametrize("principal", ["", "a\x00b", "x" * 256, None], ids=["empty", "nul", "long", "none"])
def test_forwarded_token_still_refuses_a_bad_principal(principal):
    with pytest.raises(HostedApplicationError):
        identity.bind_forwarded_identity(principal_id=principal, credential_digest=b"a" * 32, now=NOW)


def test_token_the_platform_checked_keeps_its_own_expiry():
    context = bind()
    assert context.expiry_verified is True
    assert context.expires_at == NOW + timedelta(seconds=600)


@pytest.mark.parametrize(
    ("leeway", "ahead", "binds"),
    [(5, 6, False), (30, 20, True), (30, 31, False)],
    ids=["default-too-far", "wide-leeway-within", "wide-leeway-beyond"],
)
def test_future_iat_uses_the_authority_leeway(leeway, ahead, binds):
    claims = {"iat": int(NOW.timestamp()) + ahead}
    authority = IdentityAuthority(leeway)
    if binds:
        assert bind(claims, authority=authority).issued_at > NOW
    else:
        with pytest.raises(HostedApplicationError):
            bind(claims, authority=authority)


def test_tokens_without_sid_bind_per_token():
    no_sid = {"sid": None}
    first = bind(no_sid, digest=b"a" * 32)
    second = bind(no_sid, digest=b"b" * 32)
    assert first.binding_digest(b"s" * 32) != second.binding_digest(b"s" * 32)
    same_a = bind({"sid": "s1"}, digest=b"a" * 32)
    same_b = bind({"sid": "s1"}, digest=b"b" * 32)
    assert same_a.binding_digest(b"s" * 32) == same_b.binding_digest(b"s" * 32)


def request_for(settings, headers=None):
    hosted = SimpleNamespace(settings=settings)
    return SimpleNamespace(
        scope={"app": SimpleNamespace(state=SimpleNamespace(hosted_applications=hosted))},
        state=SimpleNamespace(),
        headers=headers or {},
    )


def running(headers=None):
    return request_for(SimpleNamespace(enabled=True, authority=AUTHORITY), headers)


def access_token(**changes):
    now = int(datetime.now(timezone.utc).timestamp())
    claims = {"sub": "token-subject", "iat": now - 5, "exp": now + 600, "sid": "same-login"}
    claims = {**claims, **changes}
    claims = {key: value for key, value in claims.items() if value is not ...}
    # The signing key is irrelevant: this path does not verify the signature, the platform did.
    return jwt.encode(claims, "k" * 32, algorithm="HS256")


def test_verifier_seam_binds_only_while_hosted_applications_run():
    now = int(datetime.now(timezone.utc).timestamp())
    claims = {"sub": "immutable-user", "iat": now - 5, "exp": now + 600}
    verified = (claims, "signed-token")

    request = running()
    record_platform_identity(request, "immutable-user", provider="local", verified=verified)
    assert request.state.hosted_identity.principal_id == "immutable-user"

    for settings in (SimpleNamespace(enabled=False, authority=AUTHORITY), None):
        stopped = request_for(settings)
        record_platform_identity(stopped, "immutable-user", provider="local", verified=verified)
        assert stopped.state.hosted_identity is None


def test_local_sign_in_binds_the_validated_local_jwt():
    token = access_token()
    request = running()
    record_platform_identity(
        request, "user-1", provider="local", verified=(jwt.decode(token, options={"verify_signature": False}), token)
    )
    context = request.state.hosted_identity
    assert (context.authentication_kind, context.principal_id) == ("local_jwt", "user-1")
    assert context.credential_digest == hashlib.sha256(token.encode()).digest()


def test_jwks_wrapper_token_is_preferred():
    wrapped, other = access_token(sid="wrapped"), access_token(sid="other")
    request = running({"x-auth-request-access-token": other})
    setattr(request.state, VERIFIED_TOKEN_STATE, (jwt.decode(wrapped, options={"verify_signature": False}), wrapped))
    record_platform_identity(request, "user-1", provider="oidc")
    context = request.state.hosted_identity
    assert (context.authentication_kind, context.sid) == ("jwks", "wrapped")
    assert context.credential_digest == hashlib.sha256(wrapped.encode()).digest()


def test_keycloak_reads_the_forwarded_access_token():
    token = access_token()
    request = running({"x-auth-request-access-token": token})
    record_platform_identity(request, "user-1", provider="keycloak")
    context = request.state.hosted_identity
    assert (context.authentication_kind, context.principal_id) == ("platform_jwt", "user-1")
    assert context.credential_digest == hashlib.sha256(token.encode()).digest()


@pytest.mark.parametrize("provider", ["keycloak", "oidc", "entraid-oidc"])
def test_forwarded_token_claims_never_feed_revocation_or_session_bounds(provider):
    """CR-004: the platform did not verify this token here, so a caller who controls the header chooses nothing."""
    chosen = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
    token = access_token(sid="chosen-sid", iat=chosen, auth_time=chosen, exp=chosen + 600)
    request = running({"x-auth-request-access-token": token, "authorization": "Bearer " + token})
    before = datetime.now(timezone.utc)
    record_platform_identity(request, "user-1", provider=provider)
    context = request.state.hosted_identity
    assert context.sid is None and context.auth_time is None
    assert before <= context.issued_at <= datetime.now(timezone.utc)
    assert context.binding_digest(b"s" * 32) == _digest(["user-1", hashlib.sha256(token.encode()).hexdigest()])


def test_two_forwarded_tokens_with_the_same_sid_bind_separately():
    first, second = access_token(sid="same-login"), access_token(sid="same-login", jti="other")
    contexts = []
    for token in (first, second):
        request = running({"x-auth-request-access-token": token})
        record_platform_identity(request, "user-1", provider="keycloak")
        contexts.append(request.state.hosted_identity.binding_digest(b"s" * 32))
    assert contexts[0] != contexts[1]


def expired_claims():
    issued = int((datetime.now(timezone.utc) - timedelta(days=3)).timestamp())
    return {"iat": issued, "exp": issued + 600, "auth_time": issued}


@pytest.mark.parametrize("provider", ["keycloak", "oidc", "entraid-oidc"])
def test_expired_forwarded_token_binds_like_the_platform_accepts_it(provider):
    token = access_token(**expired_claims())
    headers = {"x-auth-request-access-token": token, "authorization": "Bearer " + token}
    request = running(headers)
    record_platform_identity(request, "user-1", provider=provider)
    context = request.state.hosted_identity
    assert (context.authentication_kind, context.expiry_verified) == ("platform_jwt", False)
    assert context.expires_at > datetime.now(timezone.utc)


@pytest.mark.parametrize("path", ["local", "jwks"])
def test_expired_token_from_a_checking_platform_path_records_nothing(path):
    token = access_token(**expired_claims())
    claims = jwt.decode(token, options={"verify_signature": False})
    request = running({"x-auth-request-access-token": access_token()})
    if path == "local":
        record_platform_identity(request, "user-1", provider="local", verified=(claims, token))
    else:
        setattr(request.state, VERIFIED_TOKEN_STATE, (claims, token))
        record_platform_identity(request, "user-1", provider="keycloak")
    assert request.state.hosted_identity is None


@pytest.mark.parametrize("provider", ["oidc", "entraid-oidc"])
@pytest.mark.parametrize("prefix", ["Bearer ", "bearer ", "BEARER "])
def test_oidc_reads_the_authorization_bearer_token(provider, prefix):
    expected = access_token(sid="from-authorization")
    request = running(
        {
            "authorization": prefix + expected + " ",
            "x-auth-request-access-token": access_token(sid="from-forwarded-header"),
        }
    )
    record_platform_identity(request, "user-1", provider=provider)
    assert request.state.hosted_identity.credential_digest == hashlib.sha256(expected.encode()).digest()


@pytest.mark.parametrize(
    "token",
    [
        None,
        "",
        "not-a-jwt",
        "a.b.c",
    ],
    ids=["none", "empty", "not-a-jwt", "three-dots"],
)
def test_unusable_token_records_nothing(token):
    request = running({} if token is None else {"x-auth-request-access-token": token})
    request.state.hosted_identity = "stale"
    record_platform_identity(request, "user-1", provider="keycloak")
    assert request.state.hosted_identity is None


@pytest.mark.parametrize("settings", [SimpleNamespace(enabled=False, authority=AUTHORITY), None], ids=["off", "none"])
def test_nothing_is_recorded_while_hosted_applications_are_off(settings):
    request = request_for(settings, {"x-auth-request-access-token": access_token()})
    request.state.hosted_identity = "stale"
    record_platform_identity(request, "user-1", provider="keycloak")
    assert request.state.hosted_identity is None


def test_failure_warning_is_rate_limited_per_cause(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(identity, "monotonic", lambda: clock["now"])
    secret = access_token()

    def attempt(headers):
        record_platform_identity(running(headers), "user-1", provider="keycloak")

    # The application logger does not propagate to the root logger, so caplog cannot see it.
    with patch("codemie.configs.logger.logger") as logger:
        attempt({})
        attempt({})
        assert logger.warning.call_count == 1
        attempt({"x-auth-request-access-token": "not-a-jwt"})
        assert logger.warning.call_count == 2
        clock["now"] = 601.0
        attempt({})
        assert logger.warning.call_count == 3
    messages = [call.args[0] for call in logger.warning.call_args_list]
    assert messages[0] == "Hosted applications found no usable sign-in token: cause=missing IDP_PROVIDER=keycloak"
    assert "cause=not_jwt" in messages[1]
    assert not any("not-a-jwt" in message or secret in message for message in messages)


def wrapped(request, **changes):
    token = access_token(**changes)
    setattr(request.state, VERIFIED_TOKEN_STATE, (jwt.decode(token, options={"verify_signature": False}), token))
    return request


@pytest.mark.parametrize(
    ("make", "cause"),
    [
        (lambda: wrapped(running(), **expired_claims()), "expired"),
        (lambda: wrapped(running(), iat=int(datetime.now(timezone.utc).timestamp()) + 60), "future_iat"),
        (lambda: wrapped(running(), iat="soon"), "bad_claims"),
        (lambda: wrapped(running(), sid=""), "bad_sid"),
    ],
    # A verified token past its exp and a clock that runs ahead are the causes production sees most.
    ids=["expired", "issued-in-future", "bad-claims", "bad-sid"],
)
def test_token_that_does_not_bind_warns_with_its_cause(make, cause):
    request = make()
    with patch("codemie.configs.logger.logger") as logger:
        record_platform_identity(request, "user-1", provider="keycloak")
    assert request.state.hosted_identity is None
    logger.warning.assert_called_once_with(
        f"Hosted applications found no usable sign-in token: cause={cause} IDP_PROVIDER=keycloak"
    )
