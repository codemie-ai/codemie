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

"""Evidence routing on the four ingest routes (EPMCDME-16440, spec 4, 5, 6, 6b, 6c, 8).

The real ingestor runs over a recording connection; only the destination resolver is replaced (it is
tested in test_cli_analytics_destination.py), except where the real Personal destination is exercised.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

import codemie.rest_api.routers.cli_analytics as ingest_router
import codemie.rest_api.routers.cli_analytics_ingest_route as route_module
from codemie.configs import config
from codemie.core.models import Application
from codemie.repository.cli_analytics.factory import CliAnalyticsStorage
from codemie.repository.cli_analytics.ports import TelemetryStorageUnavailableError
from codemie.repository.cli_analytics.postgres import ingestor as ingestor_module
from codemie.repository.cli_analytics.postgres.ingestor import PostgresTelemetryIngestor
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.analytics.cli_classification import classify, policy_version
from codemie.service.analytics.cli_analytics_project_gate import DestinationDecision
from codemie.service.analytics.cli_analytics_project_gate import resolve_destination as real_resolve
from tests.codemie.repository.cli_analytics.postgres.test_ingestor import FakeEngine
from tests.codemie.service.analytics.test_cli_classification import encode_org
from tests.codemie.repository.cli_analytics.support import hook_event_builders as hb
from tests.codemie.repository.cli_analytics.support import otlp_builders as b

GATE = "codemie.service.analytics.cli_analytics_project_gate"
BASE = "/v1/analytics/cli-analytics"
T = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
SID, OTHER_SID = "sess-1", "sess-2"
EMAIL = "dev@example.com"
ORGS = ["Acme"]
VERSION = policy_version(ORGS)
DEST = DestinationDecision(
    subscription_type="corporate",
    client_subscription_type="corporate",
    client_policy_version=VERSION,
    project="dest-proj",
    project_type="shared",
    project_origin="linked_folder",
)
ROUTES = ["logs", "metrics", "traces", "hooks"]


def _payload(route: str, sid: str, project: str | None = None) -> tuple[str, bytes, str]:
    """(path, body, content type) of a one-record request of that route for one session."""
    resource = {"service.name": "claude-code"}
    if route == "logs":
        attrs = {"event.name": "api_request", "session.id": sid, "event.sequence": 1, "cost_usd": 0.5}
        body = b.logs_request(resource, [b.log_record(T, attrs)]).SerializeToString()
    elif route == "metrics":
        point = b.number_point(T, 30, {"session.id": sid}, start=T)
        body = b.metrics_request(resource, [b.sum_metric("claude_code.active_time.total", [point])]).SerializeToString()
    elif route == "traces":
        span = b.span(
            "claude_code.llm_request",
            trace_id=b"\x01" * 16,
            span_id=b"\x02" * 8,
            start=T,
            end=T,
            attrs={"session.id": sid},
        )
        body = b.traces_request(resource, [span]).SerializeToString()
    else:
        event = hb.minimal("agent.tool.end", sid, "2026-09-29T10:59:41.512Z")
        if project is not None:
            event["codemie_project_name"] = project
        return f"{BASE}/event-hooks", json.dumps(event).encode(), "application/x-ndjson"
    return f"{BASE}/{route}", body, "application/x-protobuf"


def _evidence_headers(**overrides: str) -> dict[str, str]:
    headers = {
        "X-CodeMie-Session-Id": SID,
        "X-CodeMie-Organization": "Acme",
        "X-CodeMie-Subscription-Type": "corporate",
        "X-CodeMie-Policy-Version": VERSION,
    }
    headers.update(overrides)
    return headers


@dataclass
class Env:
    client: TestClient
    engine: FakeEngine
    resolve: AsyncMock
    settings: dict
    provision: AsyncMock
    user: User

    def post(self, route: str, headers: dict | None = None, sid: str = SID, project: str | None = None):
        path, body, content_type = _payload(route, sid, project)
        return self.client.post(path, content=body, headers={"Content-Type": content_type, **(headers or {})})

    def sql(self, needle: str) -> list[tuple]:
        return self.engine.conn.sql(needle)


@pytest.fixture
def env():
    engine = FakeEngine()
    storage = CliAnalyticsStorage(reader=None, ingestor=PostgresTelemetryIngestor(engine))  # type: ignore[arg-type]
    user = User(id="u-1", email=EMAIL)

    def authenticate_as(request: Request) -> User:
        request.state.user = user
        return user

    app = FastAPI()
    app.include_router(ingest_router.router)
    app.dependency_overrides[authenticate] = authenticate_as
    settings = {"corporateOrganizations": "\n".join(ORGS)}
    customer_config = MagicMock()
    customer_config.is_feature_enabled.return_value = True
    customer_config.get_feature_setting.side_effect = lambda _f, key, default=None: settings.get(key, default)
    resolve = AsyncMock(return_value=DEST)
    provision = AsyncMock(return_value=True)

    @asynccontextmanager
    async def session():
        yield object()

    async def legacy_gate(_explicit):  # the legacy project gate has its own tests; it accepts here
        return None

    with (
        patch.object(ingest_router, "get_cli_analytics_storage", return_value=storage),
        patch.object(ingest_router, "build_ingest_gate", return_value=legacy_gate),
        patch.object(ingest_router, "customer_config", customer_config),
        patch.object(route_module, "customer_config", customer_config),
        patch(f"{GATE}.resolve_destination", resolve),
        patch(f"{GATE}.get_async_session", session),
        patch(f"{GATE}.personal_project_service.ensure_personal_project_async", provision),
        patch.object(config, "ENABLE_USER_MANAGEMENT", False),
    ):
        yield Env(TestClient(app), engine, resolve, settings, provision, user)


def _reason(response, route: str) -> str:
    """The discard reason a 200 answer carries."""
    assert response.status_code == 200, response.text
    if route == "hooks":
        return response.json().get("reason", "")
    response_type, _ = ingestor_module._OTLP_RESPONSES[route]
    return response_type.FromString(response.content).partial_success.error_message


def _writes(env: Env) -> list[str]:
    """The statements that change data (the lock and the binding lookup are reads)."""
    return [
        sql for sql, _ in env.engine.conn.statements if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]


def _projects_stored(env: Env, table: str) -> set:
    return {
        v for args in env.sql(f"INSERT INTO {table}") for column in args if isinstance(column, list) for v in column
    }


@pytest.mark.parametrize("route", ROUTES)
def test_a_stale_policy_answers_409_and_writes_nothing(env, route):
    stale = _evidence_headers(**{"X-CodeMie-Policy-Version": "old", "X-CodeMie-Subscription-Type": "personal"})

    response = env.post(route, stale)

    assert response.status_code == 409
    assert response.json() == {"reason": "stale_policy", "policy_version": VERSION}
    assert env.engine.conn.statements == []
    env.resolve.assert_not_awaited()


@pytest.mark.parametrize("route", ROUTES)
def test_a_session_boundary_violation_wins_over_a_stale_policy(env, route):
    """Spec 4 order: boundary, classification, stale, binding. A request breaking both is a final discard."""
    stale = _evidence_headers(**{"X-CodeMie-Policy-Version": "old", "X-CodeMie-Subscription-Type": "personal"})

    response = env.post(route, stale, sid=OTHER_SID)

    assert _reason(response, route) == "evidence_session_conflict"
    assert env.engine.conn.statements == []


@pytest.mark.parametrize("route", ROUTES)
def test_an_evidence_request_without_a_sender_email_is_a_final_discard_not_a_500(env, route):
    env.user.email = ""

    response = env.post(route, _evidence_headers())

    assert _reason(response, route) == "session_owner_conflict"
    assert _writes(env) == []
    env.resolve.assert_not_awaited()
    env.provision.assert_not_awaited()


@pytest.mark.parametrize("route", ROUTES)
def test_a_matching_classification_is_not_stale_even_when_the_version_differs(env, route):
    response = env.post(route, _evidence_headers(**{"X-CodeMie-Policy-Version": "old"}))

    assert response.status_code == 200
    env.resolve.assert_awaited_once()


@pytest.mark.parametrize("route", ROUTES)
def test_personal_sessions_are_stored_without_separate_enablement(env, route):
    headers = _evidence_headers(**{"X-CodeMie-Organization": "Elsewhere", "X-CodeMie-Subscription-Type": "personal"})
    personal = Application(
        id=EMAIL,
        name=EMAIL,
        project_type=Application.ProjectType.PERSONAL,
        created_by=env.user.id,
        cli_analytics_enabled=False,
    )
    lookup = AsyncMock(return_value=personal)
    with (
        patch(f"{GATE}.resolve_destination", real_resolve),
        patch(f"{GATE}.application_repository.aget_active_by_name_case_insensitive", lookup),
    ):
        response = env.post(route, headers)

    assert response.status_code == 200, response.text
    binding_args = env.sql("INSERT INTO session_dims")[0]
    assert binding_args[1:4] == (EMAIL, "personal", "personal")
    assert binding_args[5] == [EMAIL]
    assert _writes(env)
    env.provision.assert_not_awaited()
    env.resolve.assert_not_awaited()


@pytest.mark.parametrize("route", ROUTES)
def test_a_user_with_no_project_gets_a_retryable_503_and_nothing_is_written(env, route):
    env.user.project_names = []
    env.user.default_project = None

    with patch(f"{GATE}.resolve_destination", real_resolve):
        response = env.post(route, _evidence_headers())

    assert response.status_code == 503
    assert response.json()["detail"] == "default_project_unavailable"
    assert _writes(env) == []
    env.provision.assert_not_awaited()


def _with_org(env: Env, listed: list[str], client_type: str, **overrides) -> dict:
    """Headers of a non-routed request whose client claims `client_type` against the list `listed`."""
    env.settings["corporateOrganizations"] = "\n".join(listed)
    headers = _evidence_headers(
        **{"X-CodeMie-Policy-Version": policy_version(listed), "X-CodeMie-Subscription-Type": client_type}
    )
    del headers["X-CodeMie-Organization"]
    headers.update(overrides)
    return headers


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    ("org", "listed", "expected"),
    [
        ("株式会社テスト組織", ["株式会社テスト組織"], "corporate"),
        ("  ТОВ Ромашка ", ["тов ромашка"], "corporate"),  # the existing strip/casefold applies after decoding
        ("Ак" * 128, ["Acme"], "personal"),
        ("Org 😀𝒳", ["Acme"], "personal"),
        ("100%25 Org", ["100%25 org"], "corporate"),
    ],
)
def test_an_encoded_organization_is_decoded_before_the_existing_classification(env, route, org, listed, expected):
    headers = _with_org(env, listed, expected, **{"X-CodeMie-Organization-Encoded": encode_org(org)})

    assert env.post(route, headers).status_code == 200
    assert env.resolve.await_args.kwargs["subscription_type"] == expected


@pytest.mark.parametrize("route", ROUTES)
def test_the_plain_organization_header_is_never_percent_decoded(env, route):
    headers = _with_org(env, ["100% org"], "personal")  # "100%25 Org" would match only if it were percent-decoded
    headers["X-CodeMie-Organization"] = "100%25 Org"

    assert env.post(route, headers).status_code == 200
    assert env.resolve.await_args.kwargs["subscription_type"] == "personal"


def test_a_latin1_plain_organization_header_is_read_as_before():
    """Starlette decodes header bytes as Latin-1 (the test client re-encodes header values, so this goes to the
    function with raw bytes, as the server hands them over)."""
    headers = Headers(raw=[(b"x-codemie-organization", "Société".encode("latin-1"))])

    organization = route_module._organization(headers, False, "corporate")

    assert organization == "Société"
    assert classify(False, organization, ["société"]) == "corporate"


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("plain", ["Acme", "Other", ""])
def test_both_organization_headers_are_a_conflict_even_when_equal(env, route, plain):
    headers = _with_org(env, ["Acme"], "corporate")
    headers.update({"X-CodeMie-Organization": plain, "X-CodeMie-Organization-Encoded": encode_org("Acme")})

    response = env.post(route, headers)

    assert (response.status_code, response.json()["detail"]) == (400, "organization_conflict")
    assert env.engine.conn.statements == []
    env.resolve.assert_not_awaited()


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "value",
    [
        "",
        "utf8-b64u.v2:QWNtZQ",
        "utf8-b64u.v1:QWNtZQ==",
        "utf8-b64u.v1:_w",
        encode_org("  "),
        encode_org("a\x00"),
        encode_org("a" * 257),
    ],
)
def test_a_malformed_encoded_organization_is_a_400_with_nothing_read_or_written(env, route, value):
    response = env.post(route, _with_org(env, ["Acme"], "corporate", **{"X-CodeMie-Organization-Encoded": value}))

    assert (response.status_code, response.json()["detail"]) == (400, "organization_invalid")
    assert env.engine.conn.statements == []
    env.resolve.assert_not_awaited()
    env.provision.assert_not_awaited()


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("claim", ["personal", "corporate"])
@pytest.mark.parametrize("plain", [None, "", "   "])
def test_a_claimed_class_without_any_organization_is_an_incomplete_envelope_not_unknown(env, route, claim, plain):
    """A proxy stripped the encoded header, or an old CLI could not represent the name: never stored as Unknown."""
    headers = _with_org(env, ["Acme"], claim)
    if plain is not None:
        headers["X-CodeMie-Organization"] = plain

    response = env.post(route, headers)

    assert (response.status_code, response.json()["detail"]) == (400, "organization_required")
    assert env.engine.conn.statements == []
    env.resolve.assert_not_awaited()
    env.provision.assert_not_awaited()


@pytest.mark.parametrize("route", ROUTES)
def test_a_stale_client_without_an_organization_is_still_an_incomplete_envelope(env, route):
    headers = _with_org(env, ["Acme"], "personal", **{"X-CodeMie-Policy-Version": "old"})

    assert env.post(route, headers).json()["detail"] == "organization_required"


@pytest.mark.parametrize("route", ROUTES)
def test_codemie_routing_and_an_unknown_claim_need_no_organization(env, route):
    routed = _with_org(env, ["Acme"], "codemie", **{"X-CodeMie-Routed-Via-CodeMie": "true"})
    assert env.post(route, routed).status_code == 200
    assert env.resolve.await_args.kwargs["subscription_type"] == "codemie"

    unknown = _with_org(env, ["Acme"], "unknown")
    assert env.post(route, unknown).status_code == 200
    assert env.resolve.await_args.kwargs["subscription_type"] == "unknown"


@pytest.mark.parametrize("route", ROUTES)
def test_the_encoded_header_alone_makes_an_evidence_request(env, route):
    response = env.post(route, {"X-CodeMie-Organization-Encoded": encode_org("Acme")})

    assert (response.status_code, response.json()["detail"]) == (400, "session_id_required")


@pytest.mark.parametrize("route", ROUTES)
def test_differing_session_ids_discard_the_request_before_any_resolution(env, route):
    response = env.post(route, _evidence_headers(), sid=OTHER_SID)

    assert _reason(response, route) == "evidence_session_conflict"
    assert env.engine.conn.statements == []
    env.resolve.assert_not_awaited()


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("session_header", [None, "", "  "])
def test_an_evidence_request_without_a_session_id_is_a_400_the_cli_keeps_not_a_final_discard(
    env, route, session_header
):
    """A missing or blank header is a malformed envelope: 400 (the CLI leaves the range in its spool; a 200 discard
    would delete it), unlike a session id that really differs, which stays the final `evidence_session_conflict`."""
    headers = _evidence_headers()
    del headers["X-CodeMie-Session-Id"]
    if session_header is not None:
        headers["X-CodeMie-Session-Id"] = session_header

    response = env.post(route, headers)

    assert response.status_code == 400
    assert response.json()["detail"] == "session_id_required"
    assert env.engine.conn.statements == []  # nothing read, locked or written
    env.resolve.assert_not_awaited()
    env.provision.assert_not_awaited()


@pytest.mark.parametrize("route", ROUTES)
def test_a_repo_remote_alone_is_an_evidence_request_and_needs_the_session_id(env, route):
    response = env.post(route, {"X-CodeMie-Repo-Remote": "git@h:o/r"})

    assert response.status_code == 400
    assert response.json()["detail"] == "session_id_required"
    assert env.engine.conn.statements == []


@pytest.mark.parametrize("route", ROUTES)
def test_the_client_and_repository_headers_reach_the_resolver(env, route):
    headers = _evidence_headers(**{"X-CodeMie-Repo-Remote": "git@h:o/r", "X-CodeMie-Subscription-Type": "Corporate"})

    assert env.post(route, headers).status_code == 200

    kwargs = env.resolve.await_args.kwargs
    assert (kwargs["repo_remote"], kwargs["client_subscription_type"], kwargs["client_policy_version"]) == (
        "git@h:o/r",
        "corporate",  # normalised like the classification, as the stored client column is
        VERSION,
    )


@pytest.mark.parametrize("route", ROUTES)
def test_without_a_repo_remote_header_the_resolver_gets_none(env, route):
    env.post(route, _evidence_headers())

    assert env.resolve.await_args.kwargs["repo_remote"] is None


@pytest.mark.parametrize("route", ROUTES)
def test_rebinding_stores_only_the_destination_project(env, route):
    headers = _evidence_headers(**{"X-CodeMie-Project": "hdr-proj", "X-CodeMie-Project-Source": "linked_folder"})

    response = env.post(route, headers, project="hdr-proj")

    assert response.status_code == 200
    assert env.resolve.await_args.kwargs["header_project"] == "hdr-proj"
    assert env.resolve.await_args.kwargs["project_source"] == "linked_folder"
    ((sessions, owner, kind, origin, note, projects, *contract),) = env.sql("INSERT INTO session_dims")
    assert (sessions, owner, kind, origin, note, projects) == (
        [SID],
        EMAIL,
        "corporate",
        "linked_folder",
        "",
        ["dest-proj"],
    )
    assert contract == [None, None, None]  # no publication contract yet: stored as NULL
    if route == "hooks":
        stored = _projects_stored(env, "hook_events")
        assert "dest-proj" in stored and "hdr-proj" not in stored


def test_a_body_project_that_contradicts_the_header_is_a_context_conflict(env):
    headers = _evidence_headers(**{"X-CodeMie-Project": "hdr-proj", "X-CodeMie-Project-Source": "profile"})

    response = env.post("hooks", headers, project="another")

    assert _reason(response, "hooks") == "project_context_conflict"
    assert env.engine.conn.statements == []
    env.resolve.assert_not_awaited()


@pytest.mark.parametrize("route", ROUTES)
def test_a_resolver_discard_is_a_final_200_with_its_reason(env, route):
    env.resolve.return_value = DestinationDecision(subscription_type="corporate", reason="cli_analytics_disabled")

    assert _reason(env.post(route, _evidence_headers()), route) == "cli_analytics_disabled"
    assert _writes(env) == []


@pytest.mark.parametrize("route", ["logs", "metrics", "traces"])
def test_every_otlp_route_hands_the_authenticated_sender_to_the_ingestor_even_for_legacy_requests(env, route):
    env.engine.conn.bindings = [
        {
            "session_id": SID,
            "owner_email": "someone-else@example.com",
            "project_name": "p",
            "subscription_type": "corporate",
            "project_origin": "",
            "route_note": "",
            "legacy_project": None,
        }
    ]

    response = env.post(route)  # no evidence header: legacy

    assert _reason(response, route) == "session_owner_conflict"
    assert env.sql("INSERT INTO session_dims") == []


@pytest.mark.parametrize("route", ROUTES)
def test_a_legacy_only_session_is_unchanged_and_never_bound(env, route):
    response = env.post(route, {"X-CodeMie-Project": "hdr-proj"})

    assert response.status_code in (200,)  # the legacy gate answers on its own policy; see the gate tests
    assert env.sql("INSERT INTO session_dims") == []
    env.resolve.assert_not_awaited()


def test_the_response_of_a_discard_names_the_hook_shape(env):
    response = env.post("hooks", _evidence_headers(), sid=OTHER_SID)

    assert response.json() == {
        "accepted": 0,
        "dropped": 1,
        "reason": "evidence_session_conflict",
        "other_sessions_dropped": 0,
    }


# ── the destination re-checked under the lock (mandatory concern 2 and 5) ─────────────────────


def _gate(project_names: list[str] | None = None, **kw):
    from codemie.service.analytics.cli_analytics_project_gate import build_evidence_gate

    defaults = {
        "header_project": None,
        "project_source": None,
        "repo_remote": None,
        "subscription_type": "corporate",
    }
    user = User(id="u-1", email=EMAIL, **({"project_names": project_names} if project_names is not None else {}))
    return build_evidence_gate(user, **{**defaults, **kw})


def _binding(project: str, kind: str = "corporate"):
    from codemie.repository.cli_analytics.ports import SessionBinding

    return SessionBinding(project, kind)


def _app(name: str, **kw) -> Application:
    return Application(id=name, name=name, cli_analytics_enabled=True, **kw)


@pytest.fixture
def lookup():
    apps: dict[str, Application] = {}

    @asynccontextmanager
    async def session():
        yield object()

    with (
        patch(f"{GATE}.get_async_session", session),
        patch(
            f"{GATE}.application_repository.aget_active_by_name_case_insensitive",
            AsyncMock(side_effect=lambda _s, name: apps.get(name.lower())),
        ),
        patch(f"{GATE}.user_project_repository.aget_by_user_and_project", new_callable=AsyncMock),
        patch.object(config, "ENABLE_USER_MANAGEMENT", False),
    ):
        yield apps


@pytest.mark.asyncio
async def test_a_bound_shared_project_is_rechecked_for_existence(lookup):
    with pytest.raises(TelemetryStorageUnavailableError, match="session_project_unavailable"):
        await _gate().validate_destination(_binding("gone"))


@pytest.mark.asyncio
@pytest.mark.parametrize("env_name", ["local", "production"])  # ENV=local makes every user an admin
async def test_a_bound_shared_project_must_still_be_enabled(lookup, env_name):
    lookup["bound"] = Application(id="b", name="bound", cli_analytics_enabled=False)

    with patch.object(config, "ENV", env_name):
        # access is established explicitly (membership), so the disabled switch is what decides
        assert await _gate(project_names=["bound"]).validate_destination(_binding("bound")) == "cli_analytics_disabled"


@pytest.mark.asyncio
async def test_a_bound_shared_project_without_access_is_unavailable(lookup):
    lookup["bound"] = Application(id="b", name="bound", cli_analytics_enabled=False)

    with patch.object(config, "ENV", "production"):
        with pytest.raises(TelemetryStorageUnavailableError, match="session_project_unavailable"):
            await _gate(project_names=["other"]).validate_destination(_binding("bound"))


@pytest.mark.asyncio
async def test_a_bound_shared_project_that_is_enabled_and_accessible_passes(lookup):
    lookup["bound"] = _app("bound")
    with patch.object(User, "has_access_to_application", lambda self, name: True):
        assert await _gate().validate_destination(_binding("bound")) is None


@pytest.mark.asyncio
async def test_a_bound_personal_session_still_needs_ownership_without_separate_enablement(lookup):
    personal = Application(
        id="p", name=EMAIL, project_type=Application.ProjectType.PERSONAL, created_by="u-1", cli_analytics_enabled=False
    )
    lookup[EMAIL] = personal
    with patch.object(User, "has_access_to_application", lambda self, name: True):
        assert await _gate().validate_destination(_binding(EMAIL, "personal")) is None
        personal.created_by = "someone-else"
        with pytest.raises(TelemetryStorageUnavailableError, match="session_project_unavailable"):
            await _gate().validate_destination(_binding(EMAIL, "personal"))


# ── closing coverage (AC1, AC15, AC25) ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("routed", "org", "orgs", "expected"),
    [
        ("true", "Elsewhere", ["Acme"], "codemie"),  # CodeMie routing wins, org outside the list
        ("true", "Acme", ["Acme"], "codemie"),  # ... and inside it
        (None, "Acme", [], "unknown"),  # empty list
        (None, None, ["Acme"], "unknown"),  # missing org
        (None, "acme", ["Acme"], "corporate"),  # match
        (None, "Elsewhere", ["Acme"], "personal"),  # non-match
    ],
)
def test_the_classification_matrix_reaches_the_stored_binding(env, routed, org, orgs, expected):
    env.settings["corporateOrganizations"] = "\n".join(orgs)
    headers = {"X-CodeMie-Session-Id": SID}
    if routed:
        headers["X-CodeMie-Routed-Via-CodeMie"] = routed
    if org:
        headers["X-CodeMie-Organization"] = org

    assert env.post("metrics", headers).status_code == 200

    assert env.resolve.await_args.kwargs["subscription_type"] == expected


@pytest.mark.parametrize("route", ROUTES)
def test_the_authenticated_user_is_the_owner_of_the_binding(env, route):
    env.post(route, _evidence_headers())

    assert env.sql("INSERT INTO session_dims")[0][1] == EMAIL


# ── the organization is client-observed evidence (review 2505289, AC7/AC11) ──────────────────


@pytest.mark.parametrize("route", ROUTES)
def test_a_session_the_cli_withheld_the_organization_for_is_unknown(env, route):
    """What the paired CLI sends for a stale saved OAuth login next to an API key, cloud flag or foreign gateway:
    no organization and `unknown`. The backend classifies Unknown, it does not ask for or guess an organization."""
    headers = _with_org(env, ["Acme"], "unknown")

    assert env.post(route, headers).status_code == 200
    assert env.resolve.await_args.kwargs["subscription_type"] == "unknown"


@pytest.mark.parametrize("route", ROUTES)
def test_codemie_routing_wins_over_any_organization_and_over_the_client_claim(env, route):
    headers = _evidence_headers(
        **{
            "X-CodeMie-Organization": "Elsewhere",
            "X-CodeMie-Subscription-Type": "personal",
            "X-CodeMie-Routed-Via-CodeMie": "true",
        }
    )

    assert env.post(route, headers).status_code == 200
    assert env.resolve.await_args.kwargs["subscription_type"] == "codemie"


@pytest.mark.parametrize("route", ROUTES)
def test_the_clients_subscription_claim_is_never_the_classification(env, route):
    """The claim only detects a lost organization or a stale policy; the class is derived from routing + organization."""
    headers = _evidence_headers(**{"X-CodeMie-Organization": "Elsewhere", "X-CodeMie-Subscription-Type": "corporate"})

    env.post(route, headers)

    assert env.resolve.await_args.kwargs["subscription_type"] == "personal"
