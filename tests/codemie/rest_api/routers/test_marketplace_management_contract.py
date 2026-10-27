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

"""HTTP contract tests for the marketplace-management router (EXECUTION-PLAN 1.1).

Why this file exists (F-40, F-41, F-42):

``test_marketplace_management_router.py`` imports the handler functions and
calls them directly, so ``Depends(authenticate)``, ``Depends(admin_access_only)``
and ``Depends(resolve_kind)`` never execute, and neither do URL routing, path
binding or HTTP status codes. Those tests would pass unchanged against a router
that 404s on every URL — which is exactly the state
``GET /entities/{kind}/config`` is in.

These tests drive the **real** ``codemie.rest_api.main.app`` through
``TestClient``: the real routing table, the real router-level dependency stack
and the real exception handlers. Only two things are substituted:

* identity — ``authenticate`` is overridden to inject a ``User`` (there is no
  IDP in a unit-test process). The authorisation decision itself
  (``admin_access_only``) still runs for real, which is the point of
  ``test_non_admin_is_403``;
* data — ``AUDIT_CACHE``/``get_adapter`` are stubbed because ``PG_URL`` in
  ``pytest.ini`` deliberately points at a dead port.

They are characterisation tests: they pin today's observable contract so the
Phase 2+ fixes cannot silently change it.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import MagicMock

import httpx
import pytest
from tests.codemie.marketplace_fixtures.mounted_app import mounted_marketplace_app
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.engine import Engine, make_url
from sqlmodel import create_engine

from codemie.rest_api.routers import marketplace_management as mm
from codemie.rest_api.routers.marketplace_management import _apply_gate_result_filter
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.marketplace_management.models import GateResultFilter, PendingOrderBy

_ROUTER_MOD = "codemie.rest_api.routers.marketplace_management"

BASE = "/v1/marketplace-management"
OVERVIEW = f"{BASE}/entities/assistants/overview"
UNKNOWN_KIND_OVERVIEW = f"{BASE}/entities/bogus/overview"
AUDIT_RUN = f"{BASE}/entities/assistants/audit/run"
AUDIT_STATUS = f"{BASE}/entities/assistants/audit/status"
UNRELATED_ASYNC_ROUTE = "/openapi.json"


# ---------------------------------------------------------------------------
# App / client fixtures — the REAL app, not a hand-assembled one
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app() -> Iterator[FastAPI]:
    """The production ASGI app, with the marketplace-management router mounted."""
    yield from mounted_marketplace_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """TestClient over the real app.

    Deliberately not used as a context manager: entering it would run the
    application lifespan (DB/ES connections), which is neither available nor
    relevant to an HTTP contract assertion.
    """
    saved = dict(app.dependency_overrides)
    yield TestClient(app, raise_server_exceptions=False)
    # `main.app` is a process-wide singleton shared with every other test
    # module in the session — restore, do not clear.
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


# ---------------------------------------------------------------------------
# Identity fixtures — authz itself is NOT stubbed
# ---------------------------------------------------------------------------


def _make_user(*, admin: bool) -> User:
    user = User(
        id="admin-1" if admin else "regular-1",
        username="admin" if admin else "regular",
        name="Admin" if admin else "Regular",
        email="admin@codemie.ai" if admin else "regular@codemie.ai",
        project_names=["demo"],
    )
    # `User.resolve_is_admin` force-promotes every user when ENV=local, which
    # pytest.ini sets. Pin the flags explicitly so the fixture means what its
    # name says regardless of environment.
    user.is_admin = admin
    user.is_maintainer = False
    assert user.is_admin_or_maintainer is admin
    return user


def _override_identity(app: FastAPI, user: User) -> None:
    """Inject *user* the way `authenticate` does, leaving authz untouched.

    `admin_access_only` reads `request.state.user`, so the override has to set
    it — otherwise the test would exercise an AttributeError, not the guard.
    """

    async def _fake_authenticate(request: Request) -> User:
        request.state.user = user
        return user

    app.dependency_overrides[authenticate] = _fake_authenticate


@pytest.fixture
def admin_client(app: FastAPI, client: TestClient) -> TestClient:
    _override_identity(app, _make_user(admin=True))
    return client


@pytest.fixture
def non_admin_client(app: FastAPI, client: TestClient) -> TestClient:
    _override_identity(app, _make_user(admin=False))
    return client


# ---------------------------------------------------------------------------
# Data fixture — PG_URL points at a dead port by design
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_audit_data(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Stub the audit cache + adapter the overview handler reads."""
    from codemie.rest_api.routers import marketplace_management as mm

    result = MagicMock()
    result.total = 3
    result.took_seconds = 0.05
    result.bucket_distribution = {"VERIFIED": 1, "COMMUNITY": 1, "DELETE": 1}
    result.bucket_usage_retention = {}
    result.per_rule_hits = {}
    result.purpose_cluster_id = {}
    result.cluster_size = {}
    result.cluster_canonical = {}
    result.verdicts = {}
    result.usage_tiers = {}

    cache = MagicMock()
    cache.get_or_run.return_value = result
    cache.latest_run_id.return_value = "run-abc"
    # The shape `AuditCache.start_audit` actually returns (it answers with
    # `audit_status(kind)`), not an invented `{"status": "started"}` — the
    # route now declares `response_model=AuditStatusResponse`, so a stub that
    # lies about the shape is a stub that hides a contract break.
    cache.start_audit.return_value = {
        "is_running": True,
        "run_id": "run-abc",
        "started_at": 1234.0,
        "finished_at": None,
        "error": None,
    }

    monkeypatch.setattr(mm, "AUDIT_CACHE", cache)
    monkeypatch.setattr(mm, "get_adapter", MagicMock(return_value=MagicMock(list_records=lambda: [])))
    monkeypatch.setattr(mm, "_last_run_meta", lambda kind: (None, False))
    return cache


@pytest.fixture
def stub_pending_kpi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the review-queue read `GET /overview` performs (EXECUTION-PLAN 7.3).

    `_pending_kpi` counts PENDING_REVIEW rows in Postgres, and `pytest.ini`
    points `PG_URL` at a dead port on purpose. Every other DB reader the
    overview handler touches is already stubbed by `stub_audit_data` /
    `real_audit_data`; this is the same arrangement for the one 7.3 added.

    Tests that assert *about* the KPI use the SQL-backed fixtures instead and
    must not take this.
    """
    from codemie.rest_api.routers import marketplace_management as mm

    monkeypatch.setattr(
        mm._queue_service,
        "pending_stats",
        lambda: {"count": 0, "waiting_over_7d": 0, "avg_wait_days": 0.0},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_overview_returns_200_for_admin(
    admin_client: TestClient, stub_audit_data: MagicMock, stub_pending_kpi: None
) -> None:
    """An admin gets 200 and a bucket rollup from the real URL."""
    response = admin_client.get(OVERVIEW)

    assert response.status_code == 200, response.text
    assert "buckets" in response.json()


def test_unauthenticated_is_401(client: TestClient) -> None:
    """No credentials -> 401.

    The suite has never asserted this (F-42): every existing router test calls
    the handler directly, so the router-level `Depends(authenticate)` never
    ran. No dependency override here — the real auth stack answers.
    """
    response = client.get(OVERVIEW)

    assert response.status_code == 401, response.text


def test_non_admin_is_403(non_admin_client: TestClient) -> None:
    """An authenticated user who is neither admin nor maintainer -> 403.

    `admin_access_only` is NOT overridden; this exercises the real guard.
    """
    response = non_admin_client.get(OVERVIEW)

    assert response.status_code == 403, response.text


def test_unknown_kind_is_400(admin_client: TestClient) -> None:
    """A misspelt `{kind}` segment must 400, not fall back to a default."""
    response = admin_client.get(UNKNOWN_KIND_OVERVIEW)

    assert response.status_code == 400, response.text


def test_audit_run_returns_202(admin_client: TestClient, stub_audit_data: MagicMock) -> None:
    """POST /audit/run is 202 Accepted, not 200 (F-40).

    The endpoint hands the work to a background thread and returns
    immediately; the deleted live suite asserted 200 and could never pass.
    """
    response = admin_client.post(AUDIT_RUN)

    assert response.status_code == 202, response.text


# ---------------------------------------------------------------------------
# Concurrency (EXECUTION-PLAN 2.2, F-08 / R1)
# ---------------------------------------------------------------------------

# Long enough that a read queued behind the load is unmistakably slow, short
# enough that the test stays sub-10s.
_AUDIT_SECONDS = 3.0
# Above anyio's default thread limiter (40), so the shared threadpool is
# genuinely drained rather than merely busy — the F-08 load condition.
_CONCURRENT_READS = 50
_FAST = 1.0


@pytest.mark.asyncio
async def test_reads_stay_fast_during_a_running_audit(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """A running audit must not take the rest of the API down with it (F-08).

    Measured on the live stack: with 50 concurrent `GET /overview` in flight
    during an audit, every *synchronous* route in the product timed out
    (`/v1/assistants` and `/pending` both `000 @40s`) while an `async def`
    route answered in 10ms. Sync handlers are dispatched into anyio's shared
    threadpool (40 tokens); 50 slow reads drain it and everything else queues
    behind them, which presents as "half the API is down".

    So: hold an audit open, saturate the pool with slow Overview reads, and
    require that a marketplace read which does no IO at all (`GET
    /audit/status` — atomic dict lookups) still answers, alongside an
    unrelated async route as the control.
    """
    from codemie.rest_api.routers import marketplace_management as mm
    from codemie.service.marketplace_management.audit_service import AuditCache
    from codemie.service.marketplace_management.models import EntityKind

    _override_identity(app, _make_user(admin=True))

    audit_done = threading.Event()

    def slow_run(kind, **kwargs):
        """Stand-in for a real audit run: minutes of engine + ES work.

        Takes **kwargs so it keeps standing in as `run_audit` grows keyword
        arguments (it gained `run_id`, `started_ts` and `trigger` when audit
        coordination moved into the database).
        """
        audit_done.wait(timeout=_AUDIT_SECONDS)
        return None

    cache = AuditCache()
    cache.run_audit = slow_run  # type: ignore[method-assign]
    # This test is about threadpool starvation, not persistence, so every seam
    # that reaches PostgreSQL is stubbed. `_hydrate_latest_run` and
    # `_fill_status_from_db` would otherwise stamp `_run_ids` and the typed
    # `/audit/status` response with MagicMock columns and 500 the route.
    cache._hydrate_latest_run = lambda kind: None  # type: ignore[method-assign]
    cache._fill_status_from_db = lambda kind, status: status  # type: ignore[method-assign]
    # ...and `_claim_run`, which is the one that mattered (BLOCKER-CI-1).
    #
    # `start_audit` claims the run by INSERTing a `RUNNING` row into
    # `mm_audit_run`; the row is released by `_persist`, at the end of
    # `run_audit`. `run_audit` is replaced above by `slow_run`, so nothing here
    # can ever release a claim this test opens -- it is a leak by construction,
    # not a leak by accident.
    #
    # That was harmless only while the comment this replaces was true ("the
    # session is a MagicMock here"). It stopped being true whenever
    # `tests/codemie/marketplace_fixtures/` had been collected first, because
    # its engine binding used to outlive its package: the claim then became a
    # real `RUNNING` row holding `uq_mm_audit_run_active`, and the next module
    # to audit ASSISTANT anywhere in the run was refused with 409 /
    # AuditAlreadyRunningError. Stubbing the claim makes this module unable to
    # contend for it under any binding, which is the honest statement of what
    # it needs: no claim at all.
    #
    # The claim itself is covered where it belongs, against a real migrated
    # PostgreSQL: `service/marketplace_management/test_audit_service_async.py`
    # and `rest_api/routers/test_marketplace_approval_negative.py`.
    claimed: list[str] = []

    def _claim_without_a_database(kind, **kwargs) -> tuple[str, float]:
        run_id = f"contract-threadpool-{len(claimed)}"
        claimed.append(run_id)
        return run_id, time.time()

    cache._claim_run = _claim_without_a_database  # type: ignore[method-assign]
    monkeypatch.setattr(mm, "AUDIT_CACHE", cache)

    def slow_list_records() -> list:
        """Entity listing during an audit — it competes with the audit's own
        DB/ES traffic, which is what makes an Overview read slow enough to
        hold its worker thread."""
        audit_done.wait(timeout=_AUDIT_SECONDS)
        return []

    monkeypatch.setattr(mm, "get_adapter", lambda kind: SimpleNamespace(list_records=slow_list_records))
    monkeypatch.setattr(mm, "_last_run_meta", lambda kind: (None, False))

    cache.start_audit(EntityKind.ASSISTANT)
    loop = asyncio.get_running_loop()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://contract-test") as client:
            # Warm the control route so its first-call cost is not measured.
            await client.get(UNRELATED_ASYNC_ROUTE)

            load = [asyncio.create_task(client.get(OVERVIEW)) for _ in range(_CONCURRENT_READS)]
            # Let the load actually claim the threadpool before measuring.
            await asyncio.sleep(0.5)

            began = loop.time()
            status_response = await client.get(AUDIT_STATUS)
            marketplace_read = loop.time() - began

            began = loop.time()
            control_response = await client.get(UNRELATED_ASYNC_ROUTE)
            unrelated_route = loop.time() - began

            audit_done.set()
            await asyncio.gather(*load, return_exceptions=True)
    finally:
        audit_done.set()

    assert status_response.status_code == 200, status_response.text
    assert control_response.status_code == 200
    assert marketplace_read < _FAST, (
        f"GET /audit/status took {marketplace_read:.2f}s while {_CONCURRENT_READS} "
        "Overview reads were in flight - the marketplace router still drains the "
        "shared threadpool"
    )
    assert (
        unrelated_route < _FAST
    ), f"an unrelated async route took {unrelated_route:.2f}s - the event loop itself is blocked"


# ---------------------------------------------------------------------------
# Kind guard (EXECUTION-PLAN 2.3, F-24)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("op", ["overview", "facets", "cleanup"])
def test_workflows_reads_are_501_not_500(admin_client: TestClient, op: str) -> None:
    """The workflow kind is routable but unimplemented — say so, don't crash (F-24).

    `resolve_kind` accepts `workflows`, so the URL binds and the handler runs
    against an adapter that does not exist yet. Today that surfaces as a 500
    whose *response body* carries the full Python traceback: a crash and an
    information-disclosure bug in one. `_reject_non_assistant` already exists
    and already returns the right 501; the three read handlers just never
    called it.
    """
    response = admin_client.get(f"{BASE}/entities/workflows/{op}")

    assert response.status_code == 501, response.text
    assert "Traceback" not in response.text


# ---------------------------------------------------------------------------
# /config route ordering (EXECUTION-PLAN 2.4, F-12)
# ---------------------------------------------------------------------------

CONFIG = f"{BASE}/entities/assistants/config"
ENTITY_DETAIL = f"{BASE}/entities/assistants/a-real-id"


def test_get_config_returns_the_config(admin_client: TestClient) -> None:
    """`GET /entities/{kind}/config` must reach the config handler (F-12).

    `GET /entities/{kind}/{eid}` was declared first, so Starlette matched it
    first and `config` was swallowed as an entity id — the endpoint answered
    `404 {"detail": "Assistant 'config' not found"}` and the Settings page
    had never once loaded.
    """
    response = admin_client.get(CONFIG)

    assert response.status_code == 200, response.text
    assert "gates" in response.json()


def test_entity_detail_still_resolves_a_real_id(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reordering the routes must not shadow the `{eid}` catch-all itself.

    Pairs with `test_get_config_returns_the_config`: the fix moves two static
    routes above a dynamic one, and this pins that every *other* id still
    binds to the detail handler.
    """
    from codemie.rest_api.routers import marketplace_management as mm

    # A real `Assistant`, not a `model_dump` stub: EXECUTION-PLAN 8.1 replaced
    # the raw dump with an explicit review projection
    # (`EntityReviewDetail`), so the handler now reads the row's fields rather
    # than whatever a dict happens to contain. `description` and
    # `system_prompt` are the other two required fields.
    stub = mm.Assistant(
        id="a-real-id",
        name="Real",
        description="A real assistant.",
        system_prompt="You are a real assistant.",
        marketplace_status="APPROVED",
    )
    monkeypatch.setattr(mm.Assistant, "get_by_id", staticmethod(lambda aid: stub))
    # _review_verdict / _review_polish_checks call get_session(), which the
    # session-level conftest mock replaces with a MagicMock engine. That causes
    # Pydantic to reject non-string MagicMock values. Stub them out: this test
    # only verifies route resolution, not enrichment.
    monkeypatch.setattr(mm, "_review_verdict", lambda aid: {})
    monkeypatch.setattr(mm, "_review_polish_checks", lambda aid: None)
    monkeypatch.setattr(mm, "_review_override", lambda assistant: {})

    response = admin_client.get(ENTITY_DETAIL)

    assert response.status_code == 200, response.text
    assert response.json()["id"] == "a-real-id"


def test_put_config_accepts_a_gates_patch(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """An admin edit of one gate threshold must be applied, not 400ed (F-11).

    `update_config` merges the patch into the current config and re-validates
    the whole thing through `from_dict`. Because the *shipped* similarity
    weights summed to 1.10, that validation failed on every request — so no
    admin could ever change any setting, whatever they patched.
    """
    from codemie.rest_api.routers import marketplace_management as mm

    # Stub the DynamicConfigService write — this test asserts the merge/validate
    # logic, not the persistence backend (which is unavailable in unit tests).
    monkeypatch.setattr(mm.DynamicConfigService, "set", staticmethod(lambda *a, **k: None))

    before = admin_client.get(CONFIG)
    assert before.status_code == 200, before.text
    original_gates = before.json()["gates"]

    patched_gates = {**original_gates, "description_min": 111}
    try:
        response = admin_client.put(CONFIG, json={"gates": patched_gates})

        assert response.status_code == 200, response.text
        assert response.json()["config"]["gates"]["description_min"] == 111
    finally:
        # AUDIT_CACHE._configs is process-wide state shared with every other
        # test in the session — put it back.
        admin_client.put(CONFIG, json={"gates": original_gates})


def test_put_config_refuses_usage_tiers_until_they_are_persisted(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """usage_tiers has no stored reader, so acknowledging it would be a silent no-op (ok: true, nothing changed)."""
    from codemie.rest_api.routers import marketplace_management as mm

    writes: list[str] = []
    monkeypatch.setattr(mm.DynamicConfigService, "set", staticmethod(lambda key, *a, **k: writes.append(key)))

    current = admin_client.get(CONFIG).json()
    response = admin_client.put(CONFIG, json={"usage_tiers": current["usage_tiers"]})

    assert response.status_code == 400, response.text
    assert "usage_tiers" in response.json()["detail"]
    assert writes == []


def test_put_config_records_a_threshold_event_per_persisted_section(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The write goes through the same hook as the declarations router, so it leaves an audit event."""
    from codemie.rest_api.routers import marketplace_management as mm
    from codemie.service.activity.activity_models import MarketplaceModerationEvent

    monkeypatch.setattr(mm.DynamicConfigService, "set", staticmethod(lambda *a, **k: None))
    hook_calls: list[tuple] = []

    async def _hook(component_id, event_type, actor_id, settings=None):
        hook_calls.append((component_id, event_type, settings))

    monkeypatch.setattr("codemie.rest_api.routers.customer_config._handle_threshold_hook", _hook)

    original_gates = admin_client.get(CONFIG).json()["gates"]
    try:
        response = admin_client.put(CONFIG, json={"gates": {**original_gates, "description_min": 112}})

        assert response.status_code == 200, response.text
        assert [(c[0], c[1]) for c in hook_calls] == [
            ("marketplaceCoreGates", MarketplaceModerationEvent.THRESHOLD_UPDATED)
        ]
        assert hook_calls[0][2]["descriptionMin"] == 112
    finally:
        admin_client.put(CONFIG, json={"gates": original_gates})


# ---------------------------------------------------------------------------
# GET /pending (EXECUTION-PLAN 2.6, F-09)
# ---------------------------------------------------------------------------

PENDING = f"{BASE}/entities/assistants/pending"


def test_compute_gate_findings_runs_the_registry_against_current_data() -> None:
    """The shared per-assistant helper both endpoints call is the single source of truth."""
    from codemie.configs.marketplace_management import get_marketplace_management_config
    from codemie.core.models import CreatedByUser
    from codemie.rest_api.models.assistant import Assistant
    from codemie.rest_api.routers.marketplace_management import _compute_gate_findings
    from codemie.service.marketplace_management.validator.config import Config as GateConfig

    cfg = GateConfig.from_marketplace_management(get_marketplace_management_config("assistants"))
    assistant = Assistant(
        id="a-1",
        name="Bot",
        description="A little helper for the team, sort of.",
        system_prompt="short",
        categories=["engineering"],
        project="demo",
        creator="u1",
        created_by=CreatedByUser(id="u1", username="u1", name="U1"),
        is_global=False,
        marketplace_status="PENDING_REVIEW",
        shared=True,
    )

    findings = _compute_gate_findings(assistant, cfg)

    assert "name_length" in [f.rule for f in findings]


def test_pending_projects_the_creator_and_the_timestamp(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The moderation queue must survive a row that has a creator (F-09).

    Every real pending row carries `created_by` as a `CreatedByUser` object;
    the handler fed it straight into `PendingRow.submitted_by: Optional[str]`,
    so the endpoint 500ed on any non-empty queue. `submitted_at` was never
    populated at all, so the UI's Submitted column was permanently blank.
    """
    from datetime import datetime, timezone

    from codemie.core.models import CreatedByUser
    from codemie.rest_api.routers import marketplace_management as mm

    submitted = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    assistant = SimpleNamespace(
        id="a-1",
        name="Pending One",
        project="demo",
        marketplace_status="PENDING_REVIEW",
        created_by=CreatedByUser(id="u1", username="alice", name="Alice"),
        marketplace_submitted_at=submitted,
        update_date=None,
        date=None,
        categories=["ml", "devops"],
        # Gate-relevant fields `_compute_gate_findings` -> `assistant_to_record`
        # reads (EPMCDME-14234): `system_prompt="short"` trips the CRITICAL
        # `system_prompt_length` gate so the live-recompute path is exercised.
        creator="u1",
        description="A description long enough to clear the min length gate.",
        system_prompt="short",
        updated_date=None,
        created_date=None,
        nested_assistants=[],
        prompt_variables=[],
        agent_mode=None,
        toolkits=[],
        mcp_servers=[],
        skill_ids=[],
        context=[],
        icon_url=None,
        is_global=False,
        shared=True,
        unique_users_count=0,
        unique_likes_count=0,
        unique_dislikes_count=0,
        clone_count=0,
        slug=None,
        llm_model_type=None,
        is_react=None,
        conversation_starters=[],
        plan_prompt=None,
    )
    # `list_pending` returns (page, total) and does its own ORDER BY / LIMIT in
    # SQL since EXECUTION-PLAN 5.6; the router no longer slices in Python.
    monkeypatch.setattr(mm._queue_service, "list_pending", lambda page, page_size, **_: ([assistant], 1))

    response = admin_client.get(PENDING)

    assert response.status_code == 200, response.text
    row = response.json()["items"][0]
    assert row["submitted_by"] == "Alice"
    assert row["submitted_at"] == submitted.timestamp()
    assert row["categories"] == ["ml", "devops"]
    assert "system_prompt_length" in [f["rule"] for f in row["gate_findings"]]


def test_one_bad_row_does_not_blank_the_pending_page(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A gate-recompute failure on one row must not take down the other rows."""
    from codemie.rest_api.routers import marketplace_management as mm

    good = SimpleNamespace(
        id="a-good",
        name="Good",
        project="demo",
        marketplace_status="PENDING_REVIEW",
        created_by=None,
        marketplace_submitted_at=None,
        update_date=None,
        date=None,
        categories=[],
    )
    bad = SimpleNamespace(
        id="a-bad",
        name="Bad",
        project="demo",
        marketplace_status="PENDING_REVIEW",
        created_by=None,
        marketplace_submitted_at=None,
        update_date=None,
        date=None,
        categories=[],
    )
    monkeypatch.setattr(mm._queue_service, "list_pending", lambda page, page_size, **_: ([good, bad], 2))
    monkeypatch.setattr(mm._queue_service, "latest_submissions", lambda ids: {})

    real = mm._compute_gate_findings

    def flaky(assistant, cfg):
        if assistant.id == "a-bad":
            raise RuntimeError("boom")
        return real(assistant, cfg)

    monkeypatch.setattr(mm, "_compute_gate_findings", flaky)

    response = admin_client.get(PENDING)

    assert response.status_code == 200, response.text
    items = {row["assistant_id"]: row for row in response.json()["items"]}
    assert items["a-bad"]["gate_findings"] == []
    assert isinstance(items["a-good"]["gate_findings"], list)


def test_a_bad_gate_config_degrades_the_page_instead_of_500ing(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A misconfigured gate config (e.g. a bad `PUT /config` edit) must not 500
    the whole pending page (CR-001) -- it should degrade the same way one bad
    row already does, not take every row down with it.
    """
    from codemie.rest_api.routers import marketplace_management as mm
    from codemie.service.marketplace_management.validator.config import Config as GateConfig

    good = SimpleNamespace(
        id="a-1",
        name="Good",
        project="demo",
        marketplace_status="PENDING_REVIEW",
        created_by=None,
        marketplace_submitted_at=None,
        update_date=None,
        date=None,
        categories=[],
    )
    monkeypatch.setattr(mm._queue_service, "list_pending", lambda page, page_size, **_: ([good], 1))
    monkeypatch.setattr(mm._queue_service, "latest_submissions", lambda ids: {})

    def broken(mm_cfg):
        raise ValueError("bad credential_patterns")

    monkeypatch.setattr(GateConfig, "from_marketplace_management", classmethod(lambda cls, mm_cfg: broken(mm_cfg)))

    response = admin_client.get(PENDING)

    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["gate_findings"] == []


def test_pending_page_size_is_capped_below_the_generic_200(admin_client: TestClient) -> None:
    """`/pending` recomputes all eight gates -- including a regex credential
    scan -- synchronously per row, on one threadpool worker (CR-002). Unlike
    the cheap read-only `/cleanup` and `/audit/history` pages, which only do a
    SQL read, its `page_size` ceiling must be tighter than the generic 200 so
    one request can't hold a worker for 200 sequential gate recomputes.
    """
    response = admin_client.get(PENDING, params={"page_size": 200})
    assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# _apply_gate_result_filter unit tests (EPMCDME-14628)
# ---------------------------------------------------------------------------


def _row(aid: str, *severities: str) -> dict:
    return {
        "assistant_id": aid,
        "gate_findings": [{"severity": s, "rule": "r", "message": "m", "detail": {}} for s in severities],
    }


def test_apply_gate_result_none_returns_all():
    rows = [_row("a"), _row("b", "CRITICAL")]
    assert _apply_gate_result_filter(rows, None) is rows


def test_apply_gate_result_all_clear():
    rows = [_row("clean"), _row("crit", "CRITICAL"), _row("opt", "OPTIONAL")]
    result = _apply_gate_result_filter(rows, GateResultFilter.all_clear)
    assert [r["assistant_id"] for r in result] == ["clean"]


def test_apply_gate_result_has_critical():
    rows = [_row("clean"), _row("crit", "CRITICAL"), _row("opt", "OPTIONAL")]
    result = _apply_gate_result_filter(rows, GateResultFilter.has_critical)
    assert [r["assistant_id"] for r in result] == ["crit"]


def test_apply_gate_result_has_optional_only():
    rows = [_row("clean"), _row("crit", "CRITICAL"), _row("opt", "OPTIONAL"), _row("both", "CRITICAL", "OPTIONAL")]
    result = _apply_gate_result_filter(rows, GateResultFilter.has_optional_only)
    assert [r["assistant_id"] for r in result] == ["opt"]


def test_apply_gate_result_all_clear_excludes_gate_recompute_failed_rows():
    """A row whose gate recompute failed (gate_recompute_failed=True) must not appear in
    all_clear results even if gate_findings is empty — the reviewer cannot know
    whether the assistant is clean or whether the evaluation was simply unavailable
    (CR-002, EPMCDME-14628).
    """
    failed_row = {"assistant_id": "failed", "gate_findings": [], "gate_recompute_failed": True}
    clean_row = {"assistant_id": "clean", "gate_findings": [], "gate_recompute_failed": False}
    result = _apply_gate_result_filter([failed_row, clean_row], GateResultFilter.all_clear)
    assert [r["assistant_id"] for r in result] == ["clean"]


def test_gate_result_has_critical_filter_applied_at_http_layer(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HTTP GET /pending?gate_result=has_critical must return only rows with at least
    one CRITICAL finding — verifying the _apply_gate_result_filter call is wired
    at the HTTP layer and not just in unit helpers (CR-003, EPMCDME-14628).
    Removing the filter call at the end of _pending_payload would cause this test
    to fail.
    """
    from codemie.rest_api.routers import marketplace_management as mm

    crit = SimpleNamespace(
        id="a-crit",
        name="Critical Bot",
        project="demo",
        marketplace_status="PENDING_REVIEW",
        created_by=None,
        marketplace_submitted_at=None,
        update_date=None,
        date=None,
        categories=[],
    )
    clean = SimpleNamespace(
        id="a-clean",
        name="Clean Bot",
        project="demo",
        marketplace_status="PENDING_REVIEW",
        created_by=None,
        marketplace_submitted_at=None,
        update_date=None,
        date=None,
        categories=[],
    )

    monkeypatch.setattr(mm._queue_service, "list_pending", lambda page, page_size, **_: ([crit, clean], 2))
    monkeypatch.setattr(mm._queue_service, "latest_submissions", lambda ids: {})

    def fake_findings(assistant, cfg):
        if assistant.id == "a-crit":
            return [
                SimpleNamespace(
                    to_dict=lambda: {
                        "severity": "CRITICAL",
                        "rule": "test_rule",
                        "message": "m",
                        "detail": {},
                    }
                )
            ]
        return []

    monkeypatch.setattr(mm, "_compute_gate_findings", fake_findings)

    resp = admin_client.get(PENDING + "?gate_result=has_critical")

    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["assistant_id"] == "a-crit"


# ---------------------------------------------------------------------------
# Param-forwarding contract tests (EPMCDME-14628)
# ---------------------------------------------------------------------------


def test_category_and_order_by_forwarded_to_list_pending(monkeypatch: pytest.MonkeyPatch, admin_client: TestClient):
    captured = {}

    def fake(*args, category=None, order_by=PendingOrderBy.submitted_asc, **kw):
        captured.update(category=category, order_by=order_by)
        return [], 0

    monkeypatch.setattr(mm._queue_service, "list_pending", fake)
    resp = admin_client.get(PENDING + "?category=ai&order_by=name_asc")
    assert resp.status_code == 200, resp.text
    assert captured["category"] == "ai"
    assert captured["order_by"] == PendingOrderBy.name_asc


def test_invalid_gate_result_returns_422(admin_client: TestClient):
    resp = admin_client.get(PENDING + "?gate_result=not_a_value")
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# GET /overview aggregates (EXECUTION-PLAN 3.1, F-06)
# ---------------------------------------------------------------------------

FACETS = f"{BASE}/entities/assistants/facets"
CLEANUP = f"{BASE}/entities/assistants/cleanup"


def _record(rid: str, **overrides) -> SimpleNamespace:
    """A stand-in for an AssistantRecord as `get_adapter().list_records()` returns it."""
    fields = {
        "id": rid,
        "name": f"assistant {rid}",
        "description": "a description",
        "creator": "someone",
        "mcp_servers": [],
        "categories": [],
        "unique_users_count": 0,
        # F-58: the Cleanup table has a Likes column and `AssistantRecord`
        # carries this, so the stand-in must carry it too -- otherwise the
        # contract test cannot tell "dropped by the row builder" from
        # "absent from the record".
        "unique_likes_count": 0,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _audit_fixture():
    """A small but *realistic* audit result: real Verdict/enum objects, not mocks.

    MagicMock attributes answer to anything, so a mock-built result cannot tell
    a correct aggregation from a wrong one -- `v.reason.value` returns a Mock
    either way. These are the real dataclasses the engine produces, so the
    counts below are the counts the endpoint must reproduce.
    """
    from codemie.service.marketplace_management.validator.models import (
        Bucket,
        CleanupReason,
        UsageTier,
        Verdict,
        VerdictKind,
    )

    verdicts = {
        "a-1": Verdict(
            kind=VerdictKind.CANDIDATE_FOR_CLEANUP,
            reason=CleanupReason.INVALID,
            bucket=Bucket.DELETE,
            bucket_reason="invalid_metadata",
        ),
        "a-2": Verdict(
            kind=VerdictKind.CANDIDATE_FOR_CLEANUP,
            reason=CleanupReason.INVALID,
            bucket=Bucket.DELETE,
            bucket_reason="invalid_metadata",
        ),
        "a-3": Verdict(
            kind=VerdictKind.REVIEW,
            reason=CleanupReason.DUPLICATE,
            bucket=Bucket.COMMUNITY,
            bucket_reason="duplicate_of_canonical",
            match_score=0.91,
            # The structured "keep this instead" refs the Cleanup table
            # renders as the "canonical:" hint (F-15).
            canonical_id="a-4",
            canonical_name="the canonical one",
            canonical_users=500,
        ),
        # No reason at all -- must NOT appear in any cleanup_reasons bucket.
        "a-4": Verdict(kind=VerdictKind.KEEP, bucket=Bucket.VERIFIED, bucket_reason="hot_adoption"),
    }
    usage_tiers = {
        "a-1": UsageTier.DEAD,
        "a-2": UsageTier.COLD,
        "a-3": UsageTier.WARM,
        "a-4": UsageTier.HOT,
    }
    result = SimpleNamespace(
        total=len(verdicts),
        took_seconds=0.05,
        bucket_distribution={"VERIFIED": 1, "COMMUNITY": 1, "DELETE": 2},
        bucket_usage_retention={},
        # Rule ids `gates.py` really emits -- `description_min` was a config
        # key, never a rule, so no engine run could have produced it.
        per_rule_hits={"description_length": ["a-1", "a-2"], "description_not_equal_name": ["a-1"]},
        purpose_cluster_id={},
        cluster_size={},
        cluster_canonical={},
        verdicts=verdicts,
        usage_tiers=usage_tiers,
        gate_results={},
        similarity_results={},
    )
    likes = {"a-1": 7, "a-2": 0, "a-3": 2, "a-4": 41}
    records = [_record(rid, unique_likes_count=likes[rid]) for rid in verdicts]
    return result, records


@pytest.fixture
def real_audit_data(monkeypatch: pytest.MonkeyPatch):
    """Wire `_audit_fixture` into the router the same way `stub_audit_data` does."""
    from codemie.rest_api.routers import marketplace_management as mm

    result, records = _audit_fixture()
    cache = MagicMock()
    cache.get_or_run.return_value = result
    cache.latest_run_id.return_value = "run-abc"

    monkeypatch.setattr(mm, "AUDIT_CACHE", cache)
    monkeypatch.setattr(mm, "get_adapter", MagicMock(return_value=MagicMock(list_records=lambda: records)))
    monkeypatch.setattr(mm, "_last_run_meta", lambda kind: (None, False))
    return result


# ---------------------------------------------------------------------------
# SQL-backed fixtures (EXECUTION-PLAN 5.4) — /cleanup and /facets read Postgres
# ---------------------------------------------------------------------------
#
# `/cleanup` and `/facets` no longer read an in-process `AuditResult`: they run
# bounded SQL against the run `latest_succeeded_run_id` pins. A `MagicMock`
# audit cache can no longer stand in for their data, and should not — the whole
# point of the step is that a restart stops emptying them (F-07, F-61).
#
# The database is migrated by the real alembic chain, not by
# `SQLModel.metadata.create_all`: the schema these reads run against is the one
# `n1a2u3d4i5t6` builds, including the NOT NULL and CHECK constraints a
# create_all-from-the-ORM schema would silently reproduce from the ORM's own
# beliefs. Each module run creates its own database and drops it in teardown.

_ADMIN_PG_URL = os.getenv("MM_TEST_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")
_PG_SCHEMA = "codemie"
_ALEMBIC_DIR = Path(__file__).resolve().parents[4] / "src" / "external" / "alembic"

_PG_UNREACHABLE = """\
Cannot reach PostgreSQL at {url}.

`/cleanup` and `/facets` are served from SQL; asserting their contract without a
database would assert nothing, so these tests skip when the database is unavailable. Start the
dev stack:

    cd codemie && docker compose up -d postgres

or point this file at another instance with MM_TEST_PG_URL.

Underlying error: {err}
"""


def _alembic_upgrade(url: str) -> None:
    env = dict(os.environ)
    env["PG_URL"] = url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_ALEMBIC_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"alembic upgrade head failed ({result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )


@pytest.fixture(scope="module")
def sql_engine() -> Iterator[Engine]:
    """A throwaway database migrated to `head`, dropped on teardown."""
    admin_url = make_url(_ADMIN_PG_URL)
    db_name = f"mm_contract_test_{uuid.uuid4().hex[:10]}"
    admin = None
    try:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
        with admin.connect() as conn:
            conn.execute(text(f'create database "{db_name}"'))
    except Exception as exc:  # noqa: BLE001 - reported verbatim
        pytest.skip(_PG_UNREACHABLE.format(url=admin_url.render_as_string(hide_password=True), err=exc))

    target = admin_url.set(database=db_name)
    try:
        setup = create_engine(target, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
        with setup.connect() as conn:
            conn.execute(text(f"create schema if not exists {_PG_SCHEMA}"))
            conn.execute(text("create extension if not exists vector"))
        setup.dispose()
        _alembic_upgrade(target.render_as_string(hide_password=False))

        app_engine = create_engine(
            target,
            pool_pre_ping=True,
            connect_args={"options": f"-c search_path={_PG_SCHEMA},public"},
        )
        yield app_engine
        app_engine.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'drop database if exists "{db_name}" with (force)'))
        admin.dispose()


def _insert_assistant(
    conn,
    aid: str,
    *,
    name: str,
    likes: int,
    users: int,
    description: str = "d",
    marketplace_status: str = "APPROVED",
    is_global: bool = True,
) -> None:
    conn.execute(
        text(
            "insert into codemie.assistants "
            "(id, name, description, system_prompt, project, shared, is_react, created_date, "
            " creator, is_global, categories, marketplace_status, unique_users_count, "
            " unique_likes_count, unique_dislikes_count, version_count) "
            "values (:id, :n, :d, 'p', 'demo', true, true, now(), 'someone', :g, "
            "        '[]'::jsonb, :ms, :u, :l, 0, 1)"
        ),
        {"id": aid, "n": name, "d": description, "u": users, "l": likes, "g": is_global, "ms": marketplace_status},
    )


def _insert_run(conn, run_id: str) -> None:
    conn.execute(
        text(
            "insert into codemie.mm_audit_run "
            "(id, entity_kind, started_ts, finished_ts, started_at, finished_at, status, "
            " schema_version, trigger, records_processed, records_failed, total_records, "
            " engine_took_seconds, aggregates) "
            "values (:id, 'ASSISTANT', 1.0, 2.0, now(), now(), 'SUCCEEDED', 2, 'MANUAL', "
            "        0, 0, 4, 0.05, '{}'::jsonb)"
        ),
        {"id": run_id},
    )


def _insert_verdict(conn, run_id: str, aid: str, **cols) -> None:
    payload = {
        "audit_run_id": run_id,
        "assistant_id": aid,
        "bucket": None,
        "bucket_reason": None,
        "verdict_kind": None,
        "cleanup_reason": None,
        "usage_tier": None,
        "users_count": 0,
        "canonical_id": None,
        "canonical_name": None,
        "canonical_users": None,
        "purpose_cluster_id": None,
        "match_score": None,
        "detail": None,
        "critical_count": 0,
        "optional_count": 0,
    }
    payload.update(cols)
    columns = ", ".join(payload)
    binds = ", ".join(f":{k}" for k in payload)
    conn.execute(text(f"insert into codemie.mm_audit_verdict ({columns}) values ({binds})"), payload)


def _insert_finding(conn, run_id: str, aid: str, rule: str, severity: str) -> None:
    conn.execute(
        text(
            "insert into codemie.mm_audit_finding (audit_run_id, assistant_id, rule, severity, message) "
            "values (:r, :a, :rule, :sev, 'seeded')"
        ),
        {"r": run_id, "a": aid, "rule": rule, "sev": severity},
    )


def _point_app_at(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from codemie.clients import postgres as pg

    monkeypatch.setattr(pg.PostgresClient, "get_engine", classmethod(lambda cls: engine))


@pytest.fixture
def sql_audit_data(sql_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """`_audit_fixture()`, written to Postgres instead of held in a mock.

    Same four entities, same verdicts, same rule hits, so the assertions the
    in-memory version made about counts and flat row fields still mean what they
    meant — only the storage moved.
    """
    run_id = "run-abc"
    with sql_engine.connect() as conn:
        _insert_run(conn, run_id)
        for aid, name, likes in (
            ("a-1", "assistant a-1", 7),
            ("a-2", "assistant a-2", 0),
            ("a-3", "assistant a-3", 2),
            ("a-4", "the canonical one", 41),
        ):
            _insert_assistant(conn, aid, name=name, likes=likes, users=0)
        _insert_verdict(
            conn,
            run_id,
            "a-1",
            bucket="DELETE",
            bucket_reason="invalid_metadata",
            verdict_kind="CANDIDATE_FOR_CLEANUP",
            cleanup_reason="invalid",
            usage_tier="DEAD",
            critical_count=1,
            optional_count=1,
        )
        _insert_verdict(
            conn,
            run_id,
            "a-2",
            bucket="DELETE",
            bucket_reason="invalid_metadata",
            verdict_kind="CANDIDATE_FOR_CLEANUP",
            cleanup_reason="invalid",
            usage_tier="COLD",
            critical_count=1,
        )
        _insert_verdict(
            conn,
            run_id,
            "a-3",
            bucket="COMMUNITY",
            bucket_reason="duplicate_of_canonical",
            verdict_kind="REVIEW",
            cleanup_reason="duplicate",
            usage_tier="WARM",
            match_score=0.91,
            canonical_id="a-4",
            canonical_name="the canonical one",
            canonical_users=500,
        )
        _insert_verdict(
            conn,
            run_id,
            "a-4",
            bucket="VERIFIED",
            bucket_reason="hot_adoption",
            verdict_kind="KEEP",
            usage_tier="HOT",
        )
        _insert_finding(conn, run_id, "a-1", "description_length", "CRITICAL")
        _insert_finding(conn, run_id, "a-2", "description_length", "CRITICAL")
        _insert_finding(conn, run_id, "a-1", "description_not_equal_name", "OPTIONAL")
        conn.commit()

    _point_app_at(sql_engine, monkeypatch)
    yield run_id

    with sql_engine.connect() as conn:
        conn.execute(text("delete from codemie.mm_audit_run"))
        conn.execute(text("delete from codemie.assistants"))
        conn.commit()


# --- the 5.4 pagination assertion -------------------------------------------

_BIG_RUN_ROWS = 300
# A SELECT that returns more than this is reading the catalogue, not the page.
_BOUNDED_ROWS = 50


@pytest.fixture
def big_sql_run(sql_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A run with 300 DELETE-bucket verdicts, to page through."""
    run_id = "run-big"
    with sql_engine.connect() as conn:
        _insert_run(conn, run_id)
        for i in range(_BIG_RUN_ROWS):
            aid = f"big-{i:04d}"
            _insert_assistant(conn, aid, name=f"assistant {i}", likes=i, users=i)
            _insert_verdict(
                conn,
                run_id,
                aid,
                bucket="DELETE",
                bucket_reason="invalid_metadata",
                verdict_kind="CANDIDATE_FOR_CLEANUP",
                cleanup_reason="invalid",
                usage_tier="DEAD",
                critical_count=2,
            )
        conn.commit()

    _point_app_at(sql_engine, monkeypatch)
    yield run_id

    with sql_engine.connect() as conn:
        conn.execute(text("delete from codemie.mm_audit_run"))
        conn.execute(text("delete from codemie.assistants"))
        conn.commit()


def test_cleanup_paginates_in_sql(admin_client: TestClient, sql_engine: Engine, big_sql_run: str) -> None:
    """A page must cost a page, not the catalogue (F-61).

    The handler used to ask the audit cache for every verdict, ask the adapter
    for every record, build a dict per row and slice the list — so `?page_size=10`
    materialised 300 rows to return 10, and the cost of the Cleanup table grew
    with the marketplace rather than with the page.

    The listener below watches every statement the request issues. Any SELECT
    returning more than `_BOUNDED_ROWS` rows is, by construction, reading more
    than the page asked for.
    """
    oversized: list = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        if not statement.lstrip().upper().startswith(("SELECT", "WITH")):
            return
        rowcount = cursor.rowcount
        if rowcount is not None and rowcount > _BOUNDED_ROWS:
            oversized.append((rowcount, " ".join(statement.split())[:160]))

    event.listen(sql_engine, "after_cursor_execute", _record)
    try:
        response = admin_client.get(CLEANUP, params={"page_size": 10, "page": 1})
    finally:
        event.remove(sql_engine, "after_cursor_execute", _record)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == _BIG_RUN_ROWS, f"total is {body['total']}, expected {_BIG_RUN_ROWS}"
    assert len(body["items"]) == 10
    assert body["page"] == 1 and body["page_size"] == 10
    assert not oversized, "the cleanup page read more than the page: " + "; ".join(
        f"{n} rows from {sql}" for n, sql in oversized
    )


def test_cleanup_pages_do_not_overlap(admin_client: TestClient, big_sql_run: str) -> None:
    """Paging in SQL needs a total order, or rows repeat and rows vanish."""
    first = admin_client.get(CLEANUP, params={"page_size": 10, "page": 1}).json()
    second = admin_client.get(CLEANUP, params={"page_size": 10, "page": 2}).json()

    ids_1 = [row["id"] for row in first["items"]]
    ids_2 = [row["id"] for row in second["items"]]
    assert len(ids_1) == len(ids_2) == 10
    assert not set(ids_1) & set(ids_2), "page 1 and page 2 returned overlapping rows"


@pytest.fixture
def withdrawn_sql_run(sql_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A DELETE-bucket run where one flagged assistant was unpublished afterward.

    Mirrors what `bulk_unpublish` -> `queue_service.withdraw` actually does: it
    mutates `assistants.marketplace_status` to WITHDRAWN, not the audit
    snapshot, so a stale row would otherwise keep showing in the Cleanup queue.
    """
    run_id = "run-withdrawn"
    with sql_engine.connect() as conn:
        _insert_run(conn, run_id)
        _insert_assistant(conn, "still-live", name="still live", likes=0, users=0)
        _insert_assistant(
            conn,
            "withdrawn-1",
            name="withdrawn one",
            likes=0,
            users=0,
            marketplace_status="WITHDRAWN",
            is_global=False,
        )
        for aid in ("still-live", "withdrawn-1"):
            _insert_verdict(
                conn,
                run_id,
                aid,
                bucket="DELETE",
                bucket_reason="invalid_metadata",
                verdict_kind="CANDIDATE_FOR_CLEANUP",
                cleanup_reason="invalid",
                usage_tier="DEAD",
                critical_count=1,
            )
        conn.commit()

    _point_app_at(sql_engine, monkeypatch)
    yield run_id

    with sql_engine.connect() as conn:
        conn.execute(text("delete from codemie.mm_audit_run"))
        conn.execute(text("delete from codemie.assistants"))
        conn.commit()


def test_cleanup_excludes_assistants_withdrawn_since_the_audit_ran(
    admin_client: TestClient, withdrawn_sql_run: str
) -> None:
    """Unpublishing an assistant must drop it from the Cleanup queue immediately.

    `bulk_unpublish` / `withdraw_pending` mutate live `assistants` state, not
    `mm_audit_verdict` (the audit snapshot the Cleanup queue reads). Without a
    live-status filter, a withdrawn assistant keeps showing as a DELETE
    candidate until the next audit run completes, which reads to an admin as
    "bulk-unpublish did nothing."
    """
    body = admin_client.get(CLEANUP, params={"bucket": "DELETE"}).json()
    ids = [row["id"] for row in body["items"]]
    assert ids == ["still-live"], ids
    assert body["total"] == 1


@pytest.fixture
def rejected_sql_run(sql_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A DELETE-bucket run where one flagged assistant was rejected afterward.

    Mirrors what the reject decision (`queue_service.decide` with
    ReviewDecision.REJECT) actually does: it mutates
    `assistants.marketplace_status` to REJECTED, not the audit snapshot, so a
    stale row would otherwise keep showing in the Cleanup queue (MR !4070
    review comment #24).
    """
    run_id = "run-rejected"
    with sql_engine.connect() as conn:
        _insert_run(conn, run_id)
        _insert_assistant(conn, "still-live-2", name="still live 2", likes=0, users=0)
        _insert_assistant(
            conn,
            "rejected-1",
            name="rejected one",
            likes=0,
            users=0,
            marketplace_status="REJECTED",
            is_global=False,
        )
        for aid in ("still-live-2", "rejected-1"):
            _insert_verdict(
                conn,
                run_id,
                aid,
                bucket="DELETE",
                bucket_reason="invalid_metadata",
                verdict_kind="CANDIDATE_FOR_CLEANUP",
                cleanup_reason="invalid",
                usage_tier="DEAD",
                critical_count=1,
            )
        conn.commit()

    _point_app_at(sql_engine, monkeypatch)
    yield run_id

    with sql_engine.connect() as conn:
        conn.execute(text("delete from codemie.mm_audit_run"))
        conn.execute(text("delete from codemie.assistants"))
        conn.commit()


def test_cleanup_excludes_assistants_rejected_since_the_audit_ran(
    admin_client: TestClient, rejected_sql_run: str
) -> None:
    """Rejecting an assistant must also drop it from the Cleanup queue immediately.

    Comment #24: the live-status filter used to only exclude WITHDRAWN, so a
    rejected assistant kept showing as a DELETE candidate until the next
    audit run.
    """
    body = admin_client.get(CLEANUP, params={"bucket": "DELETE"}).json()
    ids = [row["id"] for row in body["items"]]
    assert ids == ["still-live-2"], ids
    assert body["total"] == 1


def test_catalogue_facet_scope_matches_the_adapter(
    admin_client: TestClient, sql_engine: Engine, sql_audit_data: str
) -> None:
    """The MCP / category facets count the same catalogue the adapter lists.

    They are counted over `assistants` and not over the run, so the dropdowns
    are populated before the first audit — which is how the in-memory path
    behaved, because it read the adapter's records rather than the
    `AuditResult`. A private draft must stay out of both.
    """
    with sql_engine.connect() as conn:
        conn.execute(
            text(
                "insert into codemie.assistants "
                "(id, name, description, system_prompt, project, shared, is_react, created_date, "
                " creator, is_global, categories, marketplace_status, version_count) "
                "values ('private-1', 'private', 'd', 'p', 'demo', true, true, now(), 'someone', "
                "        false, '[\"SecretCategory\"]'::jsonb, 'DRAFT', 1)"
            )
        )
        conn.execute(text("update codemie.assistants set categories = '[\"Shared\"]'::jsonb where id = 'a-1'"))
        conn.commit()

    body = admin_client.get(FACETS).json()
    values = [item["value"] for item in body["categories"]]
    assert "Shared" in values
    assert "SecretCategory" not in values, "a private DRAFT leaked into the category facet"


def test_overview_emits_cleanup_reasons(admin_client: TestClient, real_audit_data, stub_pending_kpi: None) -> None:
    """`GET /overview` must report *why* entities were flagged (F-06).

    This is the user-reported bug: the Overview page renders a "flagged by
    reason" breakdown from `overview.cleanup_reasons`, and the endpoint never
    emitted that key -- so every flagged assistant showed with an empty reason
    while `mm_audit_verdict.cleanup_reason` held thousands of rows.

    The three clauses matter independently: the key must exist; its vocabulary
    must be `CleanupReason` (not the free-form `bucket_reason` slug, and not
    the FE's nine invented SCREAMING_SNAKE codes); and the counts must add up
    to the verdicts that actually carry a reason -- no double counting, and no
    silent inclusion of the reasonless ones.
    """
    from codemie.service.marketplace_management.validator.models import CleanupReason

    response = admin_client.get(OVERVIEW)

    assert response.status_code == 200, response.text
    body = response.json()
    assert "cleanup_reasons" in body, sorted(body)

    reasons = body["cleanup_reasons"]
    allowed = {member.value for member in CleanupReason}
    assert set(reasons) <= allowed, f"unknown reason codes: {set(reasons) - allowed}"

    expected = sum(1 for v in real_audit_data.verdicts.values() if v.reason)
    assert sum(reasons.values()) == expected
    assert reasons == {"invalid": 2, "duplicate": 1}


def test_overview_emits_usage_tiers_and_verdict_kinds(
    admin_client: TestClient, real_audit_data, stub_pending_kpi: None
) -> None:
    """`usage_tiers` and `verdicts` are declared by the UI contract too (F-59).

    `OverviewResponse` in the frontend types already declares all three as
    `Record<string, number>`; two of them were as absent as `cleanup_reasons`.
    (`pending` was the fourth; EXECUTION-PLAN 7.3 added it, and
    `test_overview_emits_pending_block` is what holds it.)
    """
    response = admin_client.get(OVERVIEW)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["usage_tiers"] == {"DEAD": 1, "COLD": 1, "WARM": 1, "HOT": 1}
    assert body["verdicts"] == {"CANDIDATE_FOR_CLEANUP": 2, "REVIEW": 1, "KEEP": 1}


# ---------------------------------------------------------------------------
# GET /facets (EXECUTION-PLAN 3.3, F-33 / F-68)
# ---------------------------------------------------------------------------

DOCUMENTED_FACET_KEYS = {
    "mcps",
    "categories",
    "clusters",
    "reasons",
    "tiers",
    "buckets",
    "rules",
}


def test_facets_include_reason_tier_bucket_rule(admin_client: TestClient, sql_audit_data) -> None:
    """Every Cleanup filter needs a facet, and every facet must be filterable.

    Two failures in one endpoint. `/facets` served three of the seven
    vocabularies the Cleanup filter bar offers (F-33), so the reason, tier,
    bucket and rule dropdowns had to be hardcoded in the frontend -- which is
    how they came to offer nine reason codes of which exactly one was
    reachable (F-68).

    The round-trip clause is the one that closes F-68: for every value the
    dropdown offers, the filter it feeds must return rows. A facet the filter
    rejects is worse than a missing facet -- it renders as an empty table with
    no explanation.
    """
    response = admin_client.get(FACETS)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (
        set(body) == DOCUMENTED_FACET_KEYS
    ), f"missing: {DOCUMENTED_FACET_KEYS - set(body)}; unexpected: {set(body) - DOCUMENTED_FACET_KEYS}"

    reason_values = [item["value"] for item in body["reasons"]]
    assert reason_values, "no reason facet values to filter by"
    for value in reason_values:
        # `bucket=` explicitly clears the DELETE default -- a reason is
        # orthogonal to the surface bucket, and `duplicate` lives on a
        # COMMUNITY row here.
        filtered = admin_client.get(CLEANUP, params={"bucket": "", "reason": value})
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()["total"] > 0, f"facet reason {value!r} filters to nothing"


def test_facet_tiers_buckets_and_rules_are_filterable_too(admin_client: TestClient, sql_audit_data) -> None:
    """The same round-trip guarantee for the other three new facets.

    Split from the test above so a regression names which vocabulary drifted.

    tier/rule are checked against each real bucket explicitly rather than
    `bucket=""` so a regression names which specific bucket lost the value,
    instead of only "somewhere in the unfiltered read."
    """
    body = admin_client.get(FACETS).json()

    bucket_values = [item["value"] for item in body["buckets"]]
    assert bucket_values, "no buckets facet values"
    for value in bucket_values:
        filtered = admin_client.get(CLEANUP, params={"bucket": value})
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()["total"] > 0, f"facet buckets value {value!r} filters to nothing"

    for facet, param in (("tiers", "tier"), ("rules", "rule")):
        values = [item["value"] for item in body[facet]]
        assert values, f"no {facet} facet values"
        for value in values:
            totals = [
                admin_client.get(CLEANUP, params={"bucket": bucket, param: value}).json()["total"]
                for bucket in ("VERIFIED", "COMMUNITY", "DELETE")
            ]
            assert sum(totals) > 0, f"facet {facet} value {value!r} filters to nothing in any bucket"


# ---------------------------------------------------------------------------
# GET /cleanup row shape (EXECUTION-PLAN 3.5, F-15, F-58)
# ---------------------------------------------------------------------------


def test_cleanup_row_is_flat_for_the_frontend(admin_client: TestClient, sql_audit_data) -> None:
    """Every field the Cleanup table renders must be readable off the row itself.

    F-15: the row builder nested the verdict (`{"verdict": {"kind": ...,
    "reason": ..., "canonical_name": ...}}`) while `MarketplaceCleanupPage`
    reads `item.verdict_kind`, `item.cleanup_reason` and `item.canonical_name`.
    Result: the "Decision" column rendered an em-dash on every row, and the
    reason sub-label and the canonical hint never rendered at all.

    F-58: `unique_likes_count` is on `AssistantRecord` and populated by
    `entity_adapter`, but the row builder dropped it -- so "Likes" was
    permanently empty.

    There is no nested `verdict` object: the flat fields are the whole contract.
    """
    response = admin_client.get(CLEANUP, params={"bucket": ""})
    assert response.status_code == 200, response.text
    items = {row["id"]: row for row in response.json()["items"]}
    assert items, "fixture must produce cleanup rows"

    # a-4 (VERIFIED) is included in the unfiltered read, covering its
    # flat-field shape alongside the other buckets.
    assert "a-4" in items

    for row in items.values():
        # Present on every row, even when the value is None.
        for field in (
            "verdict_kind",
            "cleanup_reason",
            "canonical_id",
            "canonical_name",
            "canonical_users",
            "unique_likes_count",
        ):
            assert field in row, f"{field} missing from cleanup row {row['id']}"
        assert "verdict" not in row

    # a-1 is CANDIDATE_FOR_CLEANUP / invalid, and carries 7 likes.
    assert items["a-1"]["verdict_kind"] == "CANDIDATE_FOR_CLEANUP"
    assert items["a-1"]["cleanup_reason"] == "invalid"
    assert items["a-1"]["unique_likes_count"] == 7

    # a-3 is the row with a canonical target and a match score.
    assert items["a-3"]["verdict_kind"] == "REVIEW"
    assert items["a-3"]["cleanup_reason"] == "duplicate"
    assert items["a-3"]["canonical_id"] == "a-4"
    assert items["a-3"]["canonical_name"] == "the canonical one"
    assert items["a-3"]["canonical_users"] == 500

    # a-4 has no reason at all -- the flat fields must be explicit nulls, not
    # absent keys, so the frontend's `?? '—'` fallbacks stay meaningful.
    assert items["a-4"]["verdict_kind"] == "KEEP"
    assert items["a-4"]["cleanup_reason"] is None
    assert items["a-4"]["canonical_name"] is None


# ---------------------------------------------------------------------------
# OpenAPI contract (EXECUTION-PLAN 4.1, F-47 / F-15 / F-31 / F-59)
# ---------------------------------------------------------------------------

# The schema FastAPI emits for a handler annotated `-> dict[str, Any]`: an
# object that promises nothing. A client generator turns it into `unknown`,
# which is how the frontend came to hand-maintain a parallel set of types that
# had already drifted from the payload (F-47).
UNTYPED_OBJECT_SCHEMA = {"type": "object", "additionalProperties": True}


def _marketplace_success_schemas(app: FastAPI) -> dict[str, dict]:
    """`{"GET /path -> 200": <json schema>}` for every marketplace 2xx response."""
    spec = app.openapi()
    out: dict[str, dict] = {}
    for path, operations in spec["paths"].items():
        if "marketplace-management" not in path:
            continue
        for method, operation in operations.items():
            for code, response in (operation.get("responses") or {}).items():
                if not code.startswith("2"):
                    continue
                schema = (response.get("content") or {}).get("application/json", {}).get("schema")
                if schema is None:
                    continue
                out[f"{method.upper()} {path} -> {code}"] = schema
    return out


def test_no_untyped_routes(app: FastAPI) -> None:
    """Every marketplace route must declare a real response schema (F-47).

    Handlers annotated `-> dict[str, Any]` make FastAPI publish
    `{"type": "object", "additionalProperties": true}` — a 200 that promises
    nothing. That is what let `/overview` ship without `cleanup_reasons`
    (F-06), `/cleanup` nest the verdict the table reads flat (F-15) and
    `/pending` omit `submitted_at` (F-31) with no build-time signal anywhere.

    Asserted over the *whole* marketplace surface, not a sampled route: the
    point of the step is that the class of bug becomes impossible, and a
    single untyped route re-opens it for whatever it serves.
    """
    schemas = _marketplace_success_schemas(app)
    assert schemas, "no marketplace-management operations found in the OpenAPI document"

    untyped = sorted(
        k
        for k, v in schemas.items()
        # FastAPI decorates the inferred schema with an auto-generated
        # `title`; the promise-nothing shape underneath is what matters.
        if {key: value for key, value in v.items() if key != "title"} == UNTYPED_OBJECT_SCHEMA
    )
    assert not untyped, f"{len(untyped)} untyped marketplace responses: {untyped}"

    # The phase exit criterion, as an assertion: nothing under a marketplace
    # 2xx may carry `additionalProperties` at all — a model declared
    # `extra="allow"` is an open object wearing a schema's clothes.
    open_ended = sorted(k for k, v in schemas.items() if "additionalProperties" in str(v))
    assert not open_ended, f"open-ended marketplace responses: {open_ended}"


# ---------------------------------------------------------------------------
# Filter-parameter typing (EXECUTION-PLAN 4.2, F-48)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("param", ["bucket", "tier", "reason", "rule"])
def test_bad_filter_value_is_422(admin_client: TestClient, sql_audit_data, param: str) -> None:
    """A value outside the filter's vocabulary must be rejected, not ignored (F-48).

    Declared `Optional[str]`, every one of these filters accepted any string
    and quietly matched nothing, so `?bucket=BOGUS` — and, far more often, a
    typo or a stale bookmark — returned `200 {"total": 0}`. An empty table is
    indistinguishable from "no assistants are in that bucket", which is how a
    filter that never matched anything survived in the UI (F-68's sibling).
    """
    response = admin_client.get(CLEANUP, params={param: "BOGUS"})

    assert response.status_code == 422, response.text


def test_empty_bucket_still_means_no_bucket_filter(admin_client: TestClient, sql_audit_data) -> None:
    """`?bucket=` keeps its "clear the default" meaning after enum-typing.

    The default is `DELETE`, and the UI clears it by sending an empty string.
    An enum that rejects `""` would 422 the "All buckets" view — the fix must
    reject `BOGUS` without breaking the one non-member value that is part of
    the contract.

    "No bucket filter" now means every bucket, VERIFIED included: fixture has
    a-1/a-2 DELETE, a-3 COMMUNITY, a-4 VERIFIED, so total is 4.
    """
    response = admin_client.get(CLEANUP, params={"bucket": ""})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 4
    assert "a-4" in [item["id"] for item in body["items"]]


def test_verified_bucket_included_in_all(admin_client: TestClient, sql_audit_data) -> None:
    """`bucket=ALL` (and its `bucket=` alias) surfaces every bucket, VERIFIED included.

    An admin who explicitly asks for the VERIFIED bucket alone still gets
    only that.
    """
    all_response = admin_client.get(CLEANUP, params={"bucket": "ALL"})
    assert all_response.status_code == 200, all_response.text
    assert "a-4" in [item["id"] for item in all_response.json()["items"]]

    verified_response = admin_client.get(CLEANUP, params={"bucket": "VERIFIED"})
    assert verified_response.status_code == 200, verified_response.text
    verified_ids = [item["id"] for item in verified_response.json()["items"]]
    assert verified_ids == ["a-4"]


def test_gate_rule_enum_matches_the_gates() -> None:
    """`GateRule` must stay the exact vocabulary `gates.py` emits.

    The `rule` filter and the `rules` facet are both typed `GateRule` now, so
    the enum is the single source of truth for what an admin can filter by. If
    a new gate lands and the enum is not extended, `/facets` 500s on the first
    audit that hits it — this test turns that into a red unit test at the
    moment the gate is added instead.
    """
    import inspect
    import re

    from codemie.service.marketplace_management.validator import gates
    from codemie.service.marketplace_management.validator.models import GateRule

    emitted = set(re.findall(r'rule="([^"]+)"', inspect.getsource(gates)))
    declared = {member.value for member in GateRule}

    assert emitted, "found no rule= literals in gates.py — did the pattern change?"
    assert emitted <= declared, f"gates emit rules GateRule does not declare: {emitted - declared}"
    assert declared <= emitted, f"GateRule declares rules no gate emits: {declared - emitted}"


# ---------------------------------------------------------------------------
# EXECUTION-PLAN 6.5 — the audit actor is the authenticated user (F-14, F-38, S7)
# ---------------------------------------------------------------------------
#
# Every moderation endpoint used to take `actor` as a client-supplied free-text
# body field defaulting to "admin"/"owner", and that string is what reached the
# audit trail (`mm_marketplace_event.actor`). An audit trail whose author is the
# person being audited is worse than no audit trail: any admin — or anyone who
# reaches an admin session — could sign a takedown as somebody else.
#
# The identity of a decision must come from `Depends(authenticate)` and from
# nowhere else. These two tests pin that from the outside: one sends a spoofed
# actor and requires it to leave no trace, the other sends no actor at all and
# requires the real user to be recorded anyway.

SPOOFED_ACTOR = "someone.else@example.com"


def _seed_pending(engine: Engine, assistant_id: str) -> None:
    """One assistant sitting in the review queue, version 0.

    `assistants.marketplace_decided_by` carries a FK to `users`, so the
    authenticated admin has to exist as a row before a decision can name it —
    which is itself part of why the audit actor cannot be free text.
    """
    with engine.connect() as conn:
        conn.execute(
            text(
                "insert into codemie.users (id, username, email, name, user_type, is_admin) "
                "values ('admin-1', 'admin', 'admin@codemie.ai', 'Admin', 'regular', true) "
                "on conflict (id) do nothing"
            )
        )
        conn.execute(
            text(
                "insert into codemie.assistants "
                "(id, name, description, system_prompt, project, shared, is_react, "
                " created_date, creator, is_global, categories, marketplace_status, "
                " marketplace_version, unique_users_count, unique_likes_count, "
                " unique_dislikes_count, version_count) "
                "values (:id, :n, :desc, :prompt, 'demo', true, true, "
                "        now(), 'seed-creator', false, '[\"productivity\"]'::jsonb, 'PENDING_REVIEW', "
                "        0, 3, 1, 0, 1)"
            ),
            {
                "id": assistant_id,
                "n": f"Assistant {assistant_id}",
                # Gates that must pass: description_min=100, system_prompt_min=200,
                # at_least_one_category, substance (prompt >= 500 OR has capability).
                "desc": "A seeded assistant used in contract tests. " + "x" * 60,
                "prompt": "You are a seeded assistant used in contract tests. " + "x" * 460,
            },
        )
        conn.commit()


@pytest.fixture
def pending_entity(sql_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A PENDING_REVIEW assistant, with the app pointed at the real database."""
    from codemie.service.platform.platform_indexing_service import PlatformIndexingService

    # Approve reindexes; the index is a projection, not the subject of this
    # test, and reaching a real Elasticsearch/embedding endpoint from a unit
    # test would make it slow and non-hermetic.
    monkeypatch.setattr(
        PlatformIndexingService,
        "index_single_assistant",
        staticmethod(lambda *a, **k: None),
        raising=False,
    )
    monkeypatch.setattr(
        PlatformIndexingService,
        "remove_single_assistant",
        staticmethod(lambda *a, **k: None),
        raising=False,
    )

    assistant_id = f"actor-{uuid.uuid4().hex[:8]}"
    _seed_pending(sql_engine, assistant_id)
    _point_app_at(sql_engine, monkeypatch)
    yield assistant_id

    with sql_engine.connect() as conn:
        conn.execute(text("delete from codemie.mm_review_decision where entity_id = :i"), {"i": assistant_id})
        conn.execute(
            text("delete from codemie.activity_events where entity_id = :i and domain = 'marketplace'"),
            {"i": assistant_id},
        )
        conn.execute(text("delete from codemie.assistants where id = :i"), {"i": assistant_id})
        conn.commit()


def _decision_row(engine: Engine, entity_id: str) -> dict:
    with engine.connect() as conn:
        return dict(
            conn.execute(
                text("select * from codemie.mm_review_decision where entity_id = :i"),
                {"i": entity_id},
            )
            .mappings()
            .one()
        )


def _event_rows(engine: Engine, entity_id: str) -> list[dict]:
    with engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                text(
                    # Phase 9.3 moved the moderation audit trail from
                    # mm_marketplace_event to activity_events, where the actor is
                    # a real user id rather than the client-supplied free text
                    # that F-14 was about.
                    "select event_type, actor_id, attributes from codemie.activity_events "
                    " where entity_id = :i and domain = 'marketplace' order by created_at"
                ),
                {"i": entity_id},
            ).mappings()
        ]


@pytest.fixture
def activity_events_enabled(monkeypatch):
    """Turn on the platform activity feed for the duration of a test.

    `activity_event_repository.insert` returns None immediately when
    `config.ACTIVITY_EVENTS_ENABLED` is False, which is the shipped default. The
    moderation audit trail that must hold unconditionally is
    `mm_review_decision`, written inside the CAS transaction; `activity_events`
    is the platform-wide feed on top of it. These tests cover the feed, so they
    must enable it explicitly rather than pass vacuously when it is off.
    """
    from codemie.configs import config as _config

    monkeypatch.setattr(_config, "ACTIVITY_EVENTS_ENABLED", True, raising=False)
    yield


def test_actor_cannot_be_spoofed(
    admin_client: TestClient,
    sql_engine: Engine,
    pending_entity: str,
    activity_events_enabled,
) -> None:
    """A body-supplied `actor` must not reach the audit trail (F-14, F-38, S7).

    The authenticated user here is `admin-1` / `admin@codemie.ai`. The request
    claims to be someone else entirely. Every persisted trace of the decision
    must name the authenticated user, and the attacker-controlled string must
    appear in none of them.
    """
    response = admin_client.post(
        f"{BASE}/entities/assistants/pending/{pending_entity}/approve",
        json={"actor": SPOOFED_ACTOR},
    )
    assert response.status_code == 200, response.text

    row = _decision_row(sql_engine, pending_entity)
    assert row["decided_by_id"] == "admin-1", row
    assert row["decided_by_email"] == "admin@codemie.ai", row
    assert SPOOFED_ACTOR not in json.dumps(
        row, default=str
    ), f"the request body's actor leaked into the persisted decision: {row}"

    events = _event_rows(sql_engine, pending_entity)
    assert events, (
        "the decision recorded no marketplace activity event. NOTE: "
        "activity_event_repository.insert no-ops when config.ACTIVITY_EVENTS_ENABLED "
        "is False (its default), so this fixture enables it — see the "
        "activity_events_enabled fixture."
    )
    assert SPOOFED_ACTOR not in json.dumps(
        events, default=str
    ), f"the request body's actor leaked into the audit trail: {events}"
    assert events[-1]["actor_id"] == "admin-1", events


def test_actor_omitted_still_records_the_real_user(
    admin_client: TestClient,
    sql_engine: Engine,
    pending_entity: str,
    activity_events_enabled,
) -> None:
    """No `actor` in the body at all still attributes the decision correctly.

    Deleting the field must not degrade the audit trail to the old `"admin"`
    placeholder — the identity comes from `Depends(authenticate)`, so an empty
    body is fully attributable.
    """
    response = admin_client.post(
        f"{BASE}/entities/assistants/pending/{pending_entity}/approve",
        json={},
    )
    assert response.status_code == 200, response.text

    row = _decision_row(sql_engine, pending_entity)
    assert row["decided_by_id"] == "admin-1", row

    events = _event_rows(sql_engine, pending_entity)
    assert events[-1]["actor_id"] == "admin-1", f"audit trail recorded a placeholder instead of the real user: {events}"


# ---------------------------------------------------------------------------
# GET /overview -- the pending KPI (EXECUTION-PLAN 7.3, F-59)
# ---------------------------------------------------------------------------


def test_overview_emits_pending_block(
    admin_client: TestClient, sql_engine: Engine, sql_audit_data: str, real_audit_data
) -> None:
    """`overview.pending` is the review-queue KPI, and it must agree with `/pending`.

    The Overview page has always had a "Pending review" tile. It read
    `overview.pending.count` -- a field the API never emitted -- so the tile was
    permanently blank and the only way to learn the size of the queue was to
    open the queue (F-59). The frontend has since been moved onto `/pending`'s
    own `total`, which fixed the tile but left the KPI missing from the payload
    that documents it.

    Two properties are asserted, not one:

    * the block carries all three keys (`count`, `waiting_over_7d`,
      `avg_wait_days`) -- a partial block is a tile that renders `undefined`;
    * `count` equals `GET /pending`'s `total`. Two endpoints counting the same
      queue with two different predicates is exactly how a dashboard comes to
      disagree with the page it links to. Both must read
      `marketplace_status = 'PENDING_REVIEW'`.

    The age arithmetic uses `marketplace_submitted_at` (`s1u2b3m4t5d6`): one row
    submitted 10 days ago (over the 7-day line), one 3 days ago (under it), and
    one that predates the column entirely -- it counts toward the queue but
    cannot contribute an age it does not have.
    """
    with sql_engine.connect() as conn:
        for aid, days_ago in (("pend-old", 10), ("pend-new", 3), ("pend-null", None)):
            conn.execute(
                text(
                    "insert into codemie.assistants "
                    "(id, name, description, system_prompt, project, shared, is_react, "
                    " created_date, creator, is_global, categories, marketplace_status, "
                    " marketplace_submitted_at, version_count) "
                    "values (:id, :id, 'd', 'p', 'demo', true, true, now(), 'someone', false, "
                    "        '[]'::jsonb, 'PENDING_REVIEW', "
                    "        case when :d is null then null "
                    "             else now() - make_interval(days => :d) end, 1)"
                ),
                {"id": aid, "d": days_ago},
            )
        conn.commit()

    overview = admin_client.get(OVERVIEW)
    pending = admin_client.get(PENDING)

    assert overview.status_code == 200, overview.text
    assert pending.status_code == 200, pending.text

    block = overview.json().get("pending")
    assert block is not None, sorted(overview.json())
    assert set(block) == {"count", "waiting_over_7d", "avg_wait_days"}, sorted(block)

    assert block["count"] == pending.json()["total"] == 3
    assert block["waiting_over_7d"] == 1, "the 10-day-old submission is over the line"
    assert block["avg_wait_days"] == pytest.approx(
        6.5, abs=0.05
    ), "the average is taken over the rows that carry a submission time"


def test_overview_pending_block_is_typed_not_a_loose_dict(app: FastAPI) -> None:
    """`pending` is part of `OverviewResponse`, so the published schema declares it.

    Phase 4 gave every marketplace route a real `response_model`; returning the
    new KPI as an untyped `dict` entry would leave it out of `openapi.json` and
    out of the generated frontend types, which is how the FE came to read a
    field the backend had never promised in the first place.
    """
    schema = app.openapi()
    overview = schema["components"]["schemas"]["OverviewResponse"]
    assert "pending" in overview["properties"], sorted(overview["properties"])

    ref = overview["properties"]["pending"]
    target = ref.get("$ref") or next(
        (option["$ref"] for option in ref.get("allOf", []) + ref.get("anyOf", []) if "$ref" in option),
        None,
    )
    assert target, f"pending is not a modelled object: {ref}"
    block = schema["components"]["schemas"][target.rsplit("/", 1)[-1]]
    assert set(block["properties"]) == {"count", "waiting_over_7d", "avg_wait_days"}


def test_adapter_scope_excludes_private_approved_rows(sql_engine: Engine, sql_audit_data: str) -> None:
    """`list_records` lists the marketplace, and a private row is not the marketplace.

    EXECUTION-PLAN 7.2. `APPROVED` used to be enough on its own to pull a row
    into the moderation surface, which is why all 282 blanket-approved
    assistants -- 63 of them private -- appeared there (F-20). The migration
    fixes the rows that exist; this predicate is what stops the state recurring,
    because `APPROVED + is_global false` is not a state any transition produces
    and should not be a state any reader honours.

    `PENDING_REVIEW` and `REJECTED` stay in scope while private: both are
    mid-workflow by definition, and a reviewer has to be able to see them.
    """
    from codemie.service.marketplace_management.entity_adapter import AssistantAdapter

    with sql_engine.connect() as conn:
        for aid, is_global, status in (
            ("scope-private-approved", False, "APPROVED"),
            ("scope-private-pending", False, "PENDING_REVIEW"),
            ("scope-private-rejected", False, "REJECTED"),
            ("scope-private-draft", False, "DRAFT"),
            ("scope-public", True, "APPROVED"),
        ):
            conn.execute(
                text(
                    "insert into codemie.assistants "
                    "(id, name, description, system_prompt, project, shared, is_react, "
                    " created_date, creator, is_global, categories, marketplace_status, "
                    " version_count) "
                    "values (:id, :id, 'd', 'p', 'demo', true, true, now(), 'someone', "
                    "        :g, '[]'::jsonb, :s, 1)"
                ),
                {"id": aid, "g": is_global, "s": status},
            )
        conn.commit()

    listed = {r.id for r in AssistantAdapter().list_records() if r.id.startswith("scope-")}

    assert "scope-private-approved" not in listed, "a private APPROVED row is still listed on the moderation surface"
    assert listed == {"scope-private-pending", "scope-private-rejected", "scope-public"}


# ---------------------------------------------------------------------------
# GET /overview -- `total` is a live count, not the last run's row count (12.3)
# ---------------------------------------------------------------------------
#
# `total` was `AuditResult.total`, i.e. `mm_audit_run.total_records` of the
# newest servable run. That is a *snapshot* taken whenever the last audit
# happened, and the tile it feeds is labelled "Assistants in marketplace" --
# a question about now. Phase 11 watched it read 220 while Postgres held 219;
# it self-corrected on the next audit, which is precisely what makes the class
# of bug hard to catch by looking.
#
# The handler already holds the live answer: `_overview_payload` calls
# `get_adapter(kind).list_records()` for the MCP/category rollups, and that is
# the same catalogue the audit counts. So `total` becomes `len(records)` at no
# extra query cost, and it is right before the first audit has ever run --
# where the snapshot reported 0 for a catalogue of 282.
#
# The snapshot is not deleted, it is renamed. `buckets`, `gate_hits`,
# `cleanup_reasons`, `verdicts` and `usage_tiers` are all counted over the
# audited population, so anything computing a percentage needs that population's
# size as the denominator. `audited_total` is that number, next to
# `last_run_at`, which is when it was true.


@pytest.fixture
def drifted_audit_data(monkeypatch: pytest.MonkeyPatch):
    """A snapshot that disagrees with the catalogue, exactly as Phase 11 saw.

    The run counted 220; the catalogue holds 4. Any handler that serves `total`
    from the run reports 220 for a marketplace with 4 things in it.
    """
    from codemie.rest_api.routers import marketplace_management as mm

    result, records = _audit_fixture()
    result.total = 220
    cache = MagicMock()
    cache.get_or_run.return_value = result
    cache.latest_run_id.return_value = "run-abc"

    monkeypatch.setattr(mm, "AUDIT_CACHE", cache)
    monkeypatch.setattr(mm, "get_adapter", MagicMock(return_value=MagicMock(list_records=lambda: records)))
    monkeypatch.setattr(mm, "_last_run_meta", lambda kind: ("2026-08-20T10:00:00Z", True))
    return result, records


def test_overview_total_is_the_live_catalogue_not_the_last_run(
    admin_client: TestClient, drifted_audit_data, stub_pending_kpi: None
) -> None:
    """`total` counts what is in the marketplace now (F-53 sibling, 12.3)."""
    _result, records = drifted_audit_data

    response = admin_client.get(OVERVIEW)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] != 220, (
        "`total` is still the audit snapshot's row count -- it reports a number "
        "that was true whenever the last audit ran, under a label that claims to "
        "describe the present"
    )
    assert body["total"] == len(records) == 4


def test_overview_keeps_the_audited_population_under_an_honest_name(
    admin_client: TestClient, drifted_audit_data, stub_pending_kpi: None
) -> None:
    """The snapshot survives as `audited_total`, beside the time it was taken.

    `buckets`, `gate_hits` and the reason/verdict/tier rollups are all counted
    over the audited population. Dropping its size would leave every percentage
    the UI computes without a correct denominator; keeping it as `total` was the
    defect. It is kept, and named for what it is.
    """
    response = admin_client.get(OVERVIEW)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["audited_total"] == 220
    assert body["last_run_at"] == "2026-08-20T10:00:00Z", (
        "`audited_total` without the timestamp it belongs to is the same " "unlabelled staleness in a new field"
    )


def test_overview_total_is_right_before_the_first_audit(
    admin_client: TestClient, stub_pending_kpi: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A catalogue with rows and no audit reports its rows, not zero.

    This is the same defect at the other extreme: on a fresh install the
    snapshot is the empty placeholder (`total=0`) while the catalogue is full,
    so the Overview told the admin the marketplace was empty.
    """
    from codemie.rest_api.routers import marketplace_management as mm

    records = [_record(f"a-{n}") for n in range(7)]
    empty = SimpleNamespace(
        total=0,
        took_seconds=0.0,
        bucket_distribution={"VERIFIED": 0, "COMMUNITY": 0, "DELETE": 0},
        bucket_usage_retention={},
        per_rule_hits={},
        purpose_cluster_id={},
        cluster_size={},
        cluster_canonical={},
        verdicts={},
        usage_tiers={},
        gate_results={},
        similarity_results={},
    )
    cache = MagicMock()
    cache.get_or_run.return_value = empty
    cache.latest_run_id.return_value = None
    monkeypatch.setattr(mm, "AUDIT_CACHE", cache)
    monkeypatch.setattr(mm, "get_adapter", MagicMock(return_value=MagicMock(list_records=lambda: records)))
    monkeypatch.setattr(mm, "_last_run_meta", lambda kind: (None, False))

    body = admin_client.get(OVERVIEW).json()

    assert body["total"] == 7
    assert body["audited_total"] == 0
    assert body["has_ever_run"] is False
