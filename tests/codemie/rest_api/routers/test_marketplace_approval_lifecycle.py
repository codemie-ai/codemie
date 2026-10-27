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

"""The approval lifecycle, server side (EXECUTION-PLAN 10.4, spec §1).

Traces F-01, F-02, F-09, F-84, F-87.

This module owns the five steps of the happy path whose assertion is **a row or
a status code**, per the layering rule in spec §5:

    step 1  create               -> the DRAFT row exists and is not global
    step 2  publish              -> PENDING_REVIEW *and* is_global=false
    step 3  /pending lists it    -> the queue contains *this* id
    step 7a Postgres visible     -> APPROVED *and* is_global=true after approve
    step 7b Elasticsearch        -> the projected document appears

Steps 4, 5, 6 and 7c — "does the reviewer see the row, can the reviewer act on
it, does the assistant show up in the marketplace UI" — are the browser's
question and live in ``codemie-ui/e2e/marketplace-approval-lifecycle.spec.ts``.
Nothing is asserted in both places: this file never opens a page, and that file
never re-derives a status code the API already answered.

Why live HTTP and real SQL rather than a TestClient with a mocked session:

  * the defect this chain guards (F-01) was a *config-conditional* write —
    ``MARKETPLACE_APPROVAL_GATE_ENABLED`` decides whether publish stamps
    APPROVED or submits for review. A test that constructs its own app and
    patches the flag proves what the code does under the patch, not what the
    deployed process does under its ``.env``;
  * F-09 was a pydantic type error at the response boundary that only fires on
    a *non-empty* queue. Serialisation is the thing under test, so the response
    has to travel over the wire;
  * F-87 is a cross-store disagreement (219 rows in Postgres, 18 documents in
    Elasticsearch). Only the two live stores can disagree.

**Never skip.** Every unreachable precondition — dead stack, missing category,
an `.env` this module cannot edit — calls ``pytest.fail``. A moderation suite
that skips itself when the feature is off reports green while the feature is
broken, which is precisely how F-01 survived eighty-six passing tests.

**The gate is armed by this module, not by the operator.** The three steps that
assert *about* the approval gate need it on in the container, and used to need
somebody to have turned it on beforehand — so under the shipped default this
file was red and `pytest tests/` had no green configuration at all. The
``armed_gate`` fixture below now sets ``MARKETPLACE_APPROVAL_GATE_ENABLED=true``
in ``.env``, restarts the container, and restores both afterwards. See the long
comment above it for why the flag is set *there* and not with ``monkeypatch``.

**Ordering is deliberate.** The tests run top to bottom against one submission
created by the module-scoped ``submission`` fixture; each is a stage of one
lifecycle, and a stage cannot assert on state its predecessor did not create.
They are named ``test_step<N>_`` so the transcript reads as the chain. The
fixture removes the assistant, its moderation ledger rows and its Elasticsearch
document in teardown, so the module can run repeatedly against the same
database.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

import httpx
import pytest

BASE_URL = "http://127.0.0.1:8080"
HOST, PORT = "127.0.0.1", 8080
MM_API = "/v1/marketplace-management/entities/assistants"

# Opt-in: these tests drive the developer's running stack and rewrite its `.env`
# (the `armed_gate` fixture), so they must not run just because something happens
# to be listening on 8080. `RUN_LIVE_STACK_TESTS=1 pytest -m live_stack ...` runs them.
pytestmark = [
    pytest.mark.live_stack,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_STACK_TESTS") != "1",
        reason="live-stack lifecycle tests are opt-in: set RUN_LIVE_STACK_TESTS=1",
    ),
]

ADMIN_EMAIL = "admin@codemie.ai"
ADMIN_PASSWORD = "password"
ACCESS_COOKIE = "codemie_access_token"

ES_BASE = "http://localhost:9200"
ES_INDEX = "marketplace_assistants"

# `psql -d codemie` fails at connect with `FATAL: database "codemie" does not
# exist`, which reads downstream as "no rows" rather than "wrong database"
# (F-96). The database is `postgres`; `codemie` is the schema.
PG_CONTAINER = "postgres"
SEARCH_PATH = "set search_path to codemie;"

SEED_PROJECT = "codemie"
SEED_LLM_MODEL = "claude-opus-4-6-20260205"

# Step 7b: indexing after approve is fire-and-forget and every exception is
# swallowed (`queue_service.py`), so the document may lag the row. Generous on
# purpose — a tight bound here would report F-87 as flake.
ES_DEADLINE_SECONDS = 30.0
ES_POLL_SECONDS = 0.5

_STACK_DOWN = (
    "Live backend on {host}:{port} is unreachable ({err}). "
    "These lifecycle tests FAIL rather than skip on purpose: a suite that skips "
    "itself when the stack is down reports green while the approval gate is "
    "broken, which is how F-01 and F-40 survived. "
    "Start the stack (`docker compose up -d` in codemie/) and re-run."
)

GATE_DISARMED = (
    "Publishing {aid} left marketplace_status={status!r} (is_global={is_global}), "
    "but the approval lifecycle requires 'PENDING_REVIEW'.\n\n"
    "MARKETPLACE_APPROVAL_GATE_ENABLED is off in the RUNNING container even though "
    "{env} says it is on, so publish stamped APPROVED immediately: the pending queue "
    "stays empty and every moderation test silently becomes a test of nothing (F-01). "
    "The process is running a configuration its own .env no longer describes; restart "
    "it so the two agree:\n\n"
    "    docker restart {container}\n\n"
    "Asserted rather than skipped on purpose: a skipped moderation test is "
    "indistinguishable from a passing one on a dashboard."
)


# ---------------------------------------------------------------------------
# The gate this module needs, armed by this module (EXECUTION-PLAN 13.2 item 2)
# ---------------------------------------------------------------------------
#
# Steps 2, 3 and 7a are assertions ABOUT the approval gate, so they need it on.
# They used to need it on *ambiently* — somebody had to have edited `.env` and
# restarted the container before the suite ran — which meant `pytest tests/` had
# no configuration in which it was green: under the shipped default
# (`MARKETPLACE_APPROVAL_GATE_ENABLED=false`, `config.py:713`) three of these
# five failed, and a suite that is red in its own default configuration cannot
# gate CI.
#
# Skipping them was never an option and still is not: F-01 survived eighty-six
# passing tests precisely because the moderation suite was indistinguishable
# from green while the feature was off. So the module arms the flag it needs
# and puts it back afterwards.
#
# Arming it means arming it in the CONTAINER, not in this process. These tests
# assert what the deployed process does under its own `.env` (module docstring
# above), and `monkeypatch.setattr(config, ...)` in the test runner changes
# nothing about a server in another container — that patch would prove what the
# code does under the patch, which is exactly the substitution F-01 hid behind.
# So the fixture rewrites the one `.env` line in place — never appending a
# second assignment of the same key — restarts the container, waits until a
# login actually succeeds, and restores the original file and restarts again in
# teardown, whether the tests passed or not.
#
# The in-process publish tests do the opposite and patch `config` directly
# (`test_marketplace_approval_negative.py:424`); `pytest.ini` pins the flag off
# for them so a developer's edited `.env` cannot swap which branch they take.
# Both halves are now explicit, and neither depends on ambient state.

# tests/codemie/rest_api/routers/<this file> -> the repo checkout root.
ENV_FILE = Path(__file__).resolve().parents[4] / ".env"
GATE_KEY = "MARKETPLACE_APPROVAL_GATE_ENABLED"
CODEMIE_CONTAINER = "codemie"

# How long the container may take to answer a login after a restart. Measured at
# 6-12s on the dev stack; generous so a slow machine reads as slow, not as red.
RESTART_DEADLINE_SECONDS = 120.0
RESTART_POLL_SECONDS = 2.0

ARM_COMMAND = ("sed -i '' 's/^{key}=.*/{key}=true/' {env} && docker restart {container}").format(
    key=GATE_KEY, env=ENV_FILE, container=CODEMIE_CONTAINER
)

_GATE_LINE = re.compile(rf"^{GATE_KEY}=(?P<value>.*)$", re.MULTILINE)

_NO_ENV_FILE = """\
{env} does not exist, so this module cannot arm the approval gate it needs.

Steps 2, 3 and 7a assert what the deployed container does when
{key} is on. Create the file, set the flag and restart:

    {command}

Failed rather than skipped on purpose (F-40).
"""

_NO_GATE_LINE = """\
{env} carries no `{key}=` line, so this module cannot arm the gate in place —
and it will not append a second one, because a file with two assignments of the
same key is a configuration nobody can reason about.

Add the line, then:

    {command}

Failed rather than skipped on purpose (F-40).
"""

_RESTART_FAILED = """\
Could not restart the `{container}` container to arm the approval gate.

Steps 2, 3 and 7a assert what the deployed container does when {key} is on, and
this module sets that flag itself rather than depending on whatever the operator
last left in `.env`. Do it by hand and re-run:

    {command}

Underlying error: {err}

Failed rather than skipped on purpose (F-40).
"""

_RESTART_TIMED_OUT = """\
The `{container}` container did not answer a login within {deadline:.0f}s of the
restart that arms the approval gate (last status: {status}).

The flag is set in {env}; the container is the thing that did not come back.
Check `docker logs {container}`, then:

    docker restart {container}

Failed rather than skipped on purpose (F-40).
"""


def _read_gate() -> tuple[str, str]:
    """`(whole file, current value)` — failing loudly rather than guessing."""
    if not ENV_FILE.exists():
        pytest.fail(
            _NO_ENV_FILE.format(env=ENV_FILE, key=GATE_KEY, command=ARM_COMMAND),
            pytrace=False,
        )
    text = ENV_FILE.read_text()
    match = _GATE_LINE.search(text)
    if match is None:
        pytest.fail(
            _NO_GATE_LINE.format(env=ENV_FILE, key=GATE_KEY, command=ARM_COMMAND),
            pytrace=False,
        )
    return text, match.group("value").strip()


def _write_gate(text: str, value: str) -> None:
    """Rewrite the single `KEY=` line, leaving every other byte alone."""
    updated, count = _GATE_LINE.subn(f"{GATE_KEY}={value}", text, count=1)
    assert count == 1, f"expected exactly one {GATE_KEY} line, rewrote {count}"
    ENV_FILE.write_text(updated)


def _restart_container() -> None:
    """`docker restart`, then wait until a login really succeeds."""
    try:
        result = subprocess.run(
            ["docker", "restart", CODEMIE_CONTAINER],
            capture_output=True,
            text=True,
            timeout=RESTART_DEADLINE_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as err:
        pytest.fail(
            _RESTART_FAILED.format(container=CODEMIE_CONTAINER, key=GATE_KEY, command=ARM_COMMAND, err=err),
            pytrace=False,
        )
    if result.returncode != 0:
        pytest.fail(
            _RESTART_FAILED.format(
                container=CODEMIE_CONTAINER,
                key=GATE_KEY,
                command=ARM_COMMAND,
                err=f"exit {result.returncode}: {result.stderr.strip()}",
            ),
            pytrace=False,
        )

    # `docker restart` returns once the process was started, not once uvicorn
    # has imported the app — so readiness is a real login, never a sleep.
    deadline = time.monotonic() + RESTART_DEADLINE_SECONDS
    status: Any = "no attempt"
    while time.monotonic() < deadline:
        try:
            with httpx.Client(base_url=BASE_URL, timeout=10) as probe:
                response = probe.post(
                    "/v1/local-auth/login",
                    json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                )
            status = response.status_code
            if status == 200:
                return
        except httpx.HTTPError as err:  # pragma: no cover - timing-dependent
            status = repr(err)
        time.sleep(RESTART_POLL_SECONDS)

    pytest.fail(
        _RESTART_TIMED_OUT.format(
            container=CODEMIE_CONTAINER,
            deadline=RESTART_DEADLINE_SECONDS,
            status=status,
            env=ENV_FILE,
        ),
        pytrace=False,
    )


# ---------------------------------------------------------------------------
# Live-stack plumbing
# ---------------------------------------------------------------------------


def _require_live_stack() -> None:
    """Skip when the backend is not listening (CI has no docker stack)."""
    try:
        with socket.create_connection((HOST, PORT), timeout=2):
            return
    except OSError as err:
        pytest.skip(_STACK_DOWN.format(host=HOST, port=PORT, err=err))


def _login_as_admin() -> httpx.Client:
    """Log in as the seeded super-admin, failing loudly at every step."""
    _require_live_stack()

    with httpx.Client(base_url=BASE_URL, timeout=30) as login_client:
        try:
            response = login_client.post(
                "/v1/local-auth/login",
                json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            )
        except httpx.HTTPError as err:  # pragma: no cover - network-dependent
            pytest.fail(f"Admin login request failed: {err!r}", pytrace=False)

        if response.status_code != 200:
            pytest.fail(
                f"Admin login failed ({response.status_code}): {response.text}",
                pytrace=False,
            )

        token = response.cookies.get(ACCESS_COOKIE)
        if token is None:
            body = response.json() if response.text else {}
            token = body.get("access_token") or body.get(ACCESS_COOKIE)
        if not token:
            pytest.fail(
                f"Cookie {ACCESS_COOKIE!r} missing from the login response; " f"headers were {dict(response.headers)}",
                pytrace=False,
            )

    return httpx.Client(base_url=BASE_URL, cookies={ACCESS_COOKIE: token}, timeout=60)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def pg_rows(sql: str) -> list[list[str]]:
    """One statement against schema ``codemie``; a row is a line, a column a ``|``.

    Errors are surfaced, never swallowed: a helper that returns ``[]`` on a
    connection failure turns "the database rejected us" into "the row is gone".
    """
    proc = subprocess.run(
        [
            "docker",
            "exec",
            PG_CONTAINER,
            "psql",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-A",
            "-t",
            "-q",
            "-c",
            SEARCH_PATH,
            "-c",
            sql,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"psql failed for: {sql}\n{proc.stderr}\n"
            f"(container {PG_CONTAINER!r}, database 'postgres', schema 'codemie' — "
            "note `psql -d codemie` does not work, the database is `postgres`)",
            pytrace=False,
        )
    return [line.split("|") for line in proc.stdout.splitlines() if line.strip()]


def pg_scalar(sql: str) -> str | None:
    rows = pg_rows(sql)
    return rows[0][0] if rows else None


def assistant_row(aid: str) -> dict[str, Any] | None:
    """The persisted moderation state of one assistant, straight from the table."""
    rows = pg_rows(
        "select coalesce(marketplace_status,''), is_global, "
        "coalesce(marketplace_submitted_at::text,'') "
        f"from assistants where id = {_sql_literal(aid)};"
    )
    if not rows:
        return None
    return {
        "marketplace_status": rows[0][0],
        "is_global": rows[0][1] == "t",
        "marketplace_submitted_at": rows[0][2],
    }


def decision_rows(aid: str) -> list[tuple[str, str, str]]:
    """``(decision, from_status, to_status)`` from the live moderation ledger.

    ``mm_review_decision`` is where a SUBMIT/APPROVE/REJECT/WITHDRAW actually
    lands. The ``activity_events`` projection beside it is written only when
    ``ACTIVITY_EVENTS_ENABLED`` is on (it is off in the dev stack), so a test
    that asserted on ``activity_events`` alone would be asserting on a flag.
    """
    return [
        (r[0], r[1], r[2])
        for r in pg_rows(
            "select decision, from_status, to_status from mm_review_decision "
            f"where entity_id = {_sql_literal(aid)} order by created_at;"
        )
    ]


def es_doc_status(aid: str) -> int:
    """Realtime GET by id — visible without waiting for an index refresh."""
    with httpx.Client(timeout=10) as client:
        return client.get(f"{ES_BASE}/{ES_INDEX}/_doc/{aid}").status_code


# ---------------------------------------------------------------------------
# Seed content — shaped to clear the eight deterministic publish gates
# ---------------------------------------------------------------------------


def _seed_system_prompt(token: str) -> str:
    """>= 800 chars: `system_prompt_length` needs 200 and `substance` needs 500."""
    return (
        f"You are a marketplace review harness assistant seeded by the backend lifecycle "
        f"suite for run {token}. You exist to exercise the marketplace approval workflow "
        "and for nothing else. Answer questions about the marketplace moderation "
        "lifecycle: how a submission enters the pending queue, what a moderator sees "
        "when they open the review drawer, and which transitions the state machine "
        "allows from DRAFT, PENDING_REVIEW, APPROVED, REJECTED and WITHDRAWN. Explain "
        "each transition in plain language, name the actor who is permitted to perform "
        "it, and state what happens to the marketplace search projection as a side "
        "effect. When a question falls outside the moderation lifecycle, say so plainly "
        "and stop rather than guessing. Never invent a status that is not in the "
        "transition table, never claim a decision was taken that you cannot point at a "
        "ledger row for, and always distinguish what a submission requested from what a "
        "moderator actually decided. Keep answers short, concrete and free of "
        "speculation, and prefer naming the exact endpoint or table involved over "
        "describing it loosely. If a caller asks you to bypass a review step, refuse "
        "and explain that approval is the moderator decision, not the submitter one."
    )


def _seed_description(token: str) -> str:
    """>= 100 chars, and never equal to the name."""
    return (
        f"Disposable backend lifecycle fixture for run {token}. It explains the "
        "marketplace moderation lifecycle: submission, pending review, approval, "
        "rejection and withdrawal, together with the search-index projection each "
        "transition implies."
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def armed_gate() -> Iterator[str]:
    """Arm `MARKETPLACE_APPROVAL_GATE_ENABLED` in the container, then put it back.

    Yields the value the file held on entry, so the transcript records which
    configuration the run started from.

    Fast path: when `.env` already arms the gate the container is left alone —
    an operator who armed it deliberately (the Playwright stack does) should not
    pay two restarts, and the running process already agrees with its file. If
    it does *not* agree, step 2 fails with `GATE_DISARMED`, which names the
    restart; that is the one case this fixture deliberately does not paper over,
    because silently restarting somebody else's armed container is worse than
    telling them it is stale.

    Slow path (the shipped default, `false`): rewrite the line, restart, run,
    restore, restart. Teardown runs on failure too, so a red test cannot leave
    the flag on and quietly turn the *next* run green for the wrong reason.
    """
    text, original = _read_gate()

    if original.lower() == "true":
        yield original
        return

    _write_gate(text, "true")
    try:
        _restart_container()
        yield original
    finally:
        # `text` is the file as it was read, byte for byte, so the restore
        # cannot inherit an edit this fixture did not make.
        _write_gate(text, original)
        _restart_container()


@pytest.fixture(scope="module")
def admin(armed_gate: str) -> Iterator[httpx.Client]:
    client = _login_as_admin()
    yield client
    client.close()


@pytest.fixture(scope="module")
def category_id(admin: httpx.Client) -> str:
    """A real category id — `at_least_one_category` needs an id, not a label."""
    response = admin.get("/v1/assistants/categories/list")
    if response.status_code != 200:
        pytest.fail(
            f"GET /v1/assistants/categories/list answered {response.status_code}: "
            f"{response.text}. The publish gate needs a real category id, so the "
            "lifecycle cannot start.",
            pytrace=False,
        )
    categories = response.json().get("categories") or []
    if not categories:
        pytest.fail(
            "No assistant categories exist; the at_least_one_category publish gate " "cannot be satisfied.",
            pytrace=False,
        )
    return categories[0]["id"]


@pytest.fixture(scope="module")
def submission(admin: httpx.Client) -> Iterator[dict[str, str]]:
    """One assistant, carried through the whole chain, removed from three stores.

    The name carries a per-run token so every SQL check is
    ``where name like 'E2E PY-<token>%'`` and never
    ``order by created_date desc limit 1``, which races with the Playwright
    suite seeding into the same database (spec §4.1).
    """
    token = f"PY-{uuid.uuid4().hex[:8]}"
    state = {
        "token": token,
        # `name_length` caps the name at 60 characters.
        "name": f"E2E {token} lifecycle",
        "description": _seed_description(token),
        "system_prompt": _seed_system_prompt(token),
        "id": "",
    }
    yield state

    aid = state["id"]
    if aid:
        admin.delete(f"/v1/assistants/{aid}")
        pg_rows(f"delete from mm_review_decision where entity_id = {_sql_literal(aid)};")
        pg_rows(f"delete from activity_events where entity_id = {_sql_literal(aid)};")
        with httpx.Client(timeout=10) as client:
            client.delete(f"{ES_BASE}/{ES_INDEX}/_doc/{aid}")


# ---------------------------------------------------------------------------
# Step 1 — create
# ---------------------------------------------------------------------------


def test_step1_create_lands_as_a_private_draft(admin: httpx.Client, submission: dict[str, str]) -> None:
    """`POST /v1/assistants` writes exactly one row, private and out of scope.

    The UI create flow is deliberately not used: the assistant editor is a large
    multi-step form with nothing to do with the approval gate, and driving it
    would make the whole chain hostage to unrelated form regressions (spec §1
    step 1).

    **Resolution of the deviation this test used to record.** Until 12.3 the
    column carried the blanket server default `'APPROVED'` (`5df1d2e53fbf:29`,
    finding F-53) and the ORM repeated it, so every newly created private
    assistant was stamped with the review state machine's TERMINAL state. Phase
    7.2 defended with the catalogue predicate rather than the default, and this
    test asserted only that containment. `d3r4a5f6t7d8` fixes the source: the
    column and the ORM both default to `DRAFT`.

    NOT NULL, which spec §1 step 1 originally asked for. `_SUBMITTABLE_STATUSES`
    is ('DRAFT', 'REJECTED', 'WITHDRAWN') and the SUBMIT compare-and-set guards
    on `marketplace_status = ANY(:allowed)`; NULL equals nothing in SQL, so a
    NULL row could never be submitted and step 2 below would 409 on the very
    first publish.

    Both claims are asserted, because they fail independently: the row is a
    DRAFT, *and* it does not satisfy `_CATALOGUE_SCOPE`
    (`is_global = true OR marketplace_status IN ('PENDING_REVIEW','REJECTED')`),
    so it cannot reach any moderation surface. The containment stays asserted
    even though the default is fixed -- it is what protects the surface if some
    other writer ever produces a private APPROVED row again.
    """
    response = admin.post(
        "/v1/assistants",
        json={
            "name": submission["name"],
            "description": submission["description"],
            "system_prompt": submission["system_prompt"],
            "project": SEED_PROJECT,
            "llm_model_type": SEED_LLM_MODEL,
        },
    )
    assert response.status_code == 200, f"{response.status_code}: {response.text}"

    # The id key in the create response is `assistantId`, not `id`.
    aid = response.json().get("assistantId")
    assert aid, f"no assistantId in the create response: {response.text}"
    submission["id"] = aid

    rows = pg_rows(
        "select id, is_global, coalesce(marketplace_status,'<null>'), "
        # `_CATALOGUE_SCOPE` verbatim: the predicate `AssistantAdapter.list_records`
        # and the SQL facet reads share.
        "(is_global = true or marketplace_status in ('PENDING_REVIEW','REJECTED')) "
        "from assistants "
        f"where name = {_sql_literal(submission['name'])};"
    )
    assert len(rows) == 1, f"expected exactly one row named {submission['name']!r}, got {rows}"
    assert rows[0][0] == aid, f"row id {rows[0][0]} is not the created id {aid}"
    assert rows[0][1] == "f", "a freshly created assistant must not be global"
    assert rows[0][2] != "PENDING_REVIEW", (
        "creating an assistant enqueued it for review; only an explicit publish may "
        "put a row into the moderation queue"
    )
    assert rows[0][2] == "DRAFT", (
        f"a never-submitted assistant was persisted as {rows[0][2]!r}. 'APPROVED' is "
        "F-53 back: the review state machine's terminal state stamped on a row nobody "
        "reviewed (EXECUTION-PLAN 12.3, migration d3r4a5f6t7d8)"
    )
    assert rows[0][3] == "f", (
        f"the new row satisfies the moderation catalogue predicate with "
        f"marketplace_status={rows[0][2]!r} / is_global={rows[0][1]!r}. A private draft "
        "that reaches the moderation surfaces is F-53 escaping the containment Phase "
        "7.2 chose for it."
    )

    # And the same claim from the outside: the queue does not know about it.
    queue = admin.get(f"{MM_API}/pending", params={"page": 1, "page_size": 100}).json()
    assert aid not in [
        item["assistant_id"] for item in queue["items"]
    ], f"{aid} is listed as pending immediately after creation, before any publish"


# ---------------------------------------------------------------------------
# Step 2 — publish is a submission, not a publication
# ---------------------------------------------------------------------------


def test_step2_publish_submits_for_review_and_does_not_go_global(
    admin: httpx.Client, submission: dict[str, str], category_id: str
) -> None:
    """The F-01 acceptance criterion: publish writes PENDING_REVIEW, not APPROVED.

    `assistant.py` used to write ``is_global = True`` and
    ``marketplace_status = "APPROVED"`` unconditionally, so the pending queue had
    no producer and the entire moderation feature was unreachable. Both halves
    are asserted: the status alone would still pass if the row went global.
    """
    aid = submission["id"]
    assert aid, "step 1 did not create an assistant; the chain cannot continue"

    response = admin.post(
        f"/v1/assistants/{aid}/marketplace/publish",
        json={
            "categories": [category_id],
            # Waives the *advisory* LLM opinion only. The eight deterministic
            # gates still run and a CRITICAL finding is still a 422 — the seed
            # content above is written to pass them.
            "ignore_recommendations": True,
        },
    )
    assert response.status_code == 200, f"{response.status_code}: {response.text}"
    body = response.json()
    assert (
        body.get("marketplace_status") == "PENDING_REVIEW"
    ), f"publish answered {body.get('marketplace_status')!r}: {response.text}"

    row = assistant_row(aid)
    assert row is not None, f"assistant {aid} vanished from codemie.assistants after publish"
    if row["marketplace_status"] != "PENDING_REVIEW":
        pytest.fail(
            GATE_DISARMED.format(
                aid=aid,
                status=row["marketplace_status"],
                is_global=row["is_global"],
                env=ENV_FILE,
                container=CODEMIE_CONTAINER,
            ),
            pytrace=False,
        )
    assert row["is_global"] is False, (
        "publish set is_global=true: a submission that is already visible to every "
        "user has not been reviewed, it has been published (F-01/F-02)"
    )
    assert row["marketplace_submitted_at"], (
        "marketplace_submitted_at is empty — the Pending page renders it as a column "
        "and would show an em-dash for every row (F-09)"
    )

    decisions = decision_rows(aid)
    assert [d[0] for d in decisions] == ["SUBMIT"], f"expected exactly one SUBMIT decision for {aid}, got {decisions}"


# ---------------------------------------------------------------------------
# Step 3 — the queue lists it
# ---------------------------------------------------------------------------


def test_step3_pending_queue_lists_this_submission(admin: httpx.Client, submission: dict[str, str]) -> None:
    """`GET /pending` is 200 on a non-empty queue and contains *this* id.

    F-09 was a pydantic type error at the response boundary: the router passed
    the ``CreatedByUser`` model where ``PendingRow.submitted_by`` declares
    ``str``, so the first real row turned the endpoint into a 500. It only fires
    on a non-empty queue, which is why this test seeds its own row rather than
    reading the ambient one (there is none).
    """
    aid = submission["id"]
    response = admin.get(f"{MM_API}/pending", params={"page": 1, "page_size": 100})
    assert response.status_code == 200, f"{response.status_code}: {response.text}"

    body = response.json()
    ids = [item["assistant_id"] for item in body["items"]]
    assert aid in ids, (
        f"the queue does not contain {aid}. Asserting on this id rather than on "
        f"'at least one row' is the point: the ambient queue would satisfy a count. "
        f"Queue held {len(ids)} ids, total={body.get('total')}"
    )

    mine = next(item for item in body["items"] if item["assistant_id"] == aid)
    assert mine["name"] == submission["name"], f"queue row carries {mine['name']!r}"
    assert isinstance(mine["submitted_by"], str) and mine["submitted_by"].strip(), (
        f"submitted_by is {mine['submitted_by']!r}; it must be a rendered name, and it "
        "must be a str — passing the CreatedByUser model here is F-09"
    )
    assert isinstance(mine["submitted_at"], (int, float)) and mine["submitted_at"] > 0, (
        f"submitted_at is {mine['submitted_at']!r}; the Pending page renders it as a "
        "timestamp column and shows an em-dash when it is missing"
    )

    # Independent check: the endpoint reads the table, not a cache.
    pending_in_db = int(pg_scalar("select count(*) from assistants where marketplace_status='PENDING_REVIEW';") or 0)
    assert body["total"] == pending_in_db, (
        f"the endpoint reports total={body['total']} while the table holds " f"{pending_in_db} PENDING_REVIEW rows"
    )


# ---------------------------------------------------------------------------
# Step 7a — approve publishes it, in Postgres
# ---------------------------------------------------------------------------


def test_step7a_approve_publishes_the_row_in_postgres(admin: httpx.Client, submission: dict[str, str]) -> None:
    """Approve moves the terminal transition where it belongs: the queue service.

    Both halves again — ``APPROVED`` with ``is_global=false`` would be an
    assistant that was approved and never published, and the marketplace listing
    reads ``is_global``.
    """
    aid = submission["id"]
    response = admin.post(
        f"{MM_API}/pending/{aid}/approve",
        json={"reason": "approved by the backend lifecycle test"},
    )
    assert response.status_code == 200, f"{response.status_code}: {response.text}"

    row = assistant_row(aid)
    assert row is not None, f"assistant {aid} vanished after approve"
    assert (
        row["marketplace_status"] == "APPROVED"
    ), f"marketplace_status is {row['marketplace_status']!r} after a 200 approve"
    assert row["is_global"] is True, (
        "is_global is still false after approve — the assistant was approved but never " "actually published to anyone"
    )

    decisions = decision_rows(aid)
    approvals = [d for d in decisions if d[0] == "APPROVE"]
    assert len(approvals) == 1, (
        f"expected exactly one APPROVE decision row for {aid}, got {decisions}. More "
        "than one means the compare-and-set let two writers through."
    )
    assert approvals[0][1] == "PENDING_REVIEW" and approvals[0][2] == "APPROVED", (
        f"the APPROVE decision records the transition {approvals[0][1]} -> "
        f"{approvals[0][2]}, which is not the one that happened"
    )
    assert [d[0] for d in decisions] == [
        "SUBMIT",
        "APPROVE",
    ], f"the ledger for {aid} should read SUBMIT then APPROVE, it reads {decisions}"

    # No longer in the queue it was read from.
    queue = admin.get(f"{MM_API}/pending", params={"page": 1, "page_size": 100}).json()
    assert aid not in [
        item["assistant_id"] for item in queue["items"]
    ], f"{aid} is still listed as pending after being approved"


# ---------------------------------------------------------------------------
# Step 7b — and in Elasticsearch
# ---------------------------------------------------------------------------


@pytest.mark.live_index
def test_step7b_elasticsearch_document_appears(submission: dict[str, str]) -> None:
    """The approved assistant is projected into the marketplace search index.

    Indexing after approve is fire-and-forget and *every* exception is caught and
    logged, so approve answers 200 whether or not the document was written. The
    deadline below is deliberately generous, and the failure message names the
    finding, because a red here is a product defect and not a slow test.
    """
    aid = submission["id"]
    deadline = time.monotonic() + ES_DEADLINE_SECONDS
    status = 0
    while time.monotonic() < deadline:
        status = es_doc_status(aid)
        if status == 200:
            break
        time.sleep(ES_POLL_SECONDS)

    assert status == 200, (
        f"indexed document never appeared for {aid} within {ES_DEADLINE_SECONDS:.0f}s "
        f"(last status {status}) — this is F-87, not a test bug. Postgres says the row "
        "is APPROVED and is_global=true; the marketplace search index disagrees, and "
        "approve reported success either way."
    )

    with httpx.Client(timeout=10) as client:
        doc = client.get(f"{ES_BASE}/{ES_INDEX}/_doc/{aid}").json()
    # The projection nests the entity fields under `metadata`; `_source` itself
    # holds the embedding payload (`text`, `metadata`, `vector`).
    metadata = (doc.get("_source") or {}).get("metadata") or {}
    assert metadata.get("id") == aid, f"the indexed document is not this assistant: {json.dumps(metadata)[:300]}"
    assert metadata.get("name") == submission["name"], (
        f"the indexed document carries the name {metadata.get('name')!r}, not " f"{submission['name']!r}"
    )
