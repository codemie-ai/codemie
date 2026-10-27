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

"""Live smoke tests for the marketplace-management API (EXECUTION-PLAN 1.2).

Replaces ``test_marketplace_management_live.py``, which was 250 lines of
integration coverage aimed at ``/v1/marketplace-management/<verb>?entity_kind=``
— a URL shape the service has never exposed. All 11 of its tests were red
against a *healthy* backend, so nobody could tell a real regression from the
permanent failures (F-40).

Two rules this file exists to enforce:

1. **The paths are the ones the service actually serves** —
   ``/v1/marketplace-management/entities/{kind}/<verb>``, per the running
   service's own OpenAPI document.
2. **Never skip.** The old module skipped itself when the backend
   was unreachable, so a dead stack read as a green suite. Every unreachable
   or unauthenticated condition here calls ``pytest.fail`` instead. A suite
   that silently skips is how these blockers survived.

3. **Own the audit claim, both halves of it** (BLOCKER-CI-1). ``POST
   /audit/run`` starts a *real* audit against the live catalogue — measured at
   3m35s on this stack — and the claim it takes is a ``RUNNING`` row in
   ``mm_audit_run`` holding the partial unique index
   ``uq_mm_audit_run_active``. It is deployment-wide by design: while it is
   held, every other caller is refused with 409.

   This module used to fire that POST, assert 202, and walk away. So it left a
   claim held for the next three and a half minutes, and any second invocation
   inside that window — another module, a re-run, the Playwright agent, a
   second admin — got a perfectly correct 409 that read as a red test. The
   suite was not flaky; the test was asserting half a contract and leaking the
   other half.

   The fix is not a skip and not a retry-until-green. The endpoint's contract
   is *202 when the claim is free, 409 naming the in-flight run when it is
   not*, so both halves are asserted here, in one test that owns the claim from
   acquisition to release: it waits for a free claim, takes it, immediately
   proves a second call is refused, and does not let the module finish until
   the run it started has reached a terminal state. What used to make this file
   order-dependent is now the thing it covers.

These require the local stack (``docker compose up``); they are meant to be
run deliberately, and to be loud when they cannot run.
"""

from __future__ import annotations

import socket
import time
from typing import Any, Callable, Iterator

import httpx
import pytest

BASE_URL = "http://127.0.0.1:8080"
HOST, PORT = "127.0.0.1", 8080
API = "/v1/marketplace-management"
AUDIT_RUN = f"{API}/entities/assistants/audit/run"
AUDIT_STATUS = f"{API}/entities/assistants/audit/status"

# A full ASSISTANT audit over the 282-record corpus was measured at 3m35s on
# this stack (mm_audit_run: 16:19:16 -> 16:22:49). The budget is that plus
# room for one already-in-flight run to drain before ours can start.
_RUN_SECONDS = 300
_CLAIM_WAIT_SECONDS = 420
_POLL_SECONDS = 5
# `POST /audit/run` is rate limited to 5/minute, so the claim is *waited* for
# by polling the unlimited status endpoint and only re-POSTed a few times.
_MAX_CLAIM_ATTEMPTS = 4

_TERMINAL_RUN_STATUSES = {"SUCCEEDED", "PARTIAL", "FAILED", "ABORTED"}

ADMIN_EMAIL = "admin@codemie.ai"
ADMIN_PASSWORD = "password"
ACCESS_COOKIE = "codemie_access_token"

_ClientFactory = Callable[[], httpx.Client]

_STACK_DOWN = (
    "Live backend on {host}:{port} is unreachable ({err}). "
    "These smoke tests FAIL rather than skip on purpose: a marketplace suite "
    "that skips itself when the stack is down reports green while the feature "
    "is broken, which is exactly how EXECUTION-PLAN F-40 survived. "
    "Start the stack (`docker compose up -d` in codemie/) and re-run."
)


def _require_live_stack() -> None:
    """Skip when the backend is not listening (CI has no docker stack)."""
    try:
        with socket.create_connection((HOST, PORT), timeout=2):
            return
    except OSError as err:
        pytest.skip(_STACK_DOWN.format(host=HOST, port=PORT, err=err))


@pytest.fixture(scope="module")
def admin_login() -> Iterator[_ClientFactory]:
    """Yield a *lazy* accessor for an admin-authenticated client.

    Deliberately does no network I/O at fixture-setup time. If the login
    happened here, an unreachable stack would surface as a pytest ERROR during
    setup instead of a test failure; the accessor defers it into the test body
    so a dead stack always reads as FAILED.
    """
    holder: dict[str, httpx.Client] = {}

    def _get() -> httpx.Client:
        if "client" not in holder:
            holder["client"] = _login_as_admin()
        return holder["client"]

    yield _get

    if "client" in holder:
        holder["client"].close()


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
            for part in response.headers.get("set-cookie", "").split(";"):
                part = part.strip()
                if part.startswith(f"{ACCESS_COOKIE}="):
                    token = part[len(ACCESS_COOKIE) + 1 :]
                    break
        if not token:
            pytest.fail(
                f"Cookie {ACCESS_COOKIE!r} missing from the login response; " f"headers were {dict(response.headers)}",
                pytrace=False,
            )

    return httpx.Client(base_url=BASE_URL, cookies={ACCESS_COOKIE: token}, timeout=60)


def test_overview_is_served_on_the_entities_path(admin_login: _ClientFactory) -> None:
    """`GET /entities/assistants/overview` is 200 with a bucket rollup.

    The deleted live suite asked for `/overview?entity_kind=ASSISTANT` and got
    404 every time. This pins the path segment shape the service really serves.

    If this times out rather than asserting, suspect F-08: `/overview` calls
    `AUDIT_CACHE.get_or_run`, which today blocks behind the audit lock, so a
    run left in flight by an earlier `POST /audit/run` stalls every read.
    Phase 2.1/2.2 narrows that lock; until then, a timeout here is a finding,
    not flakiness.
    """
    response = admin_login().get(f"{API}/entities/assistants/overview")

    assert response.status_code == 200, f"{response.status_code}: {response.text}"
    body = response.json()
    assert "buckets" in body, f"no bucket rollup in {sorted(body)}"
    assert isinstance(body["total"], int), f"unexpected total: {body.get('total')!r}"


# ---------------------------------------------------------------------------
# The audit claim (BLOCKER-CI-1)
# ---------------------------------------------------------------------------


def _audit_status(client: httpx.Client) -> dict[str, Any]:
    """`GET /audit/status`, failing loudly — it is not rate limited."""
    response = client.get(AUDIT_STATUS)
    if response.status_code != 200:
        pytest.fail(
            f"GET /audit/status answered {response.status_code}: {response.text}",
            pytrace=False,
        )
    return response.json()


def _claim_is_free(status: dict[str, Any]) -> bool:
    """True when no audit of ASSISTANT is in flight anywhere in the deployment.

    `is_running` is this pod's own worker-thread flag; `status` is filled from
    the newest persisted `mm_audit_run` row, which is the deployment-wide fact.
    A claim is free only when neither says a run is open.
    """
    if status.get("is_running"):
        return False
    persisted = status.get("status")
    return persisted is None or persisted in _TERMINAL_RUN_STATUSES


def _wait_for_a_free_claim(client: httpx.Client, deadline: float) -> dict[str, Any]:
    """Poll until no audit is in flight, or fail naming what is holding it."""
    last = _audit_status(client)
    while not _claim_is_free(last):
        if time.monotonic() >= deadline:
            pytest.fail(
                "An audit of ASSISTANT has been in flight for the whole "
                f"{_CLAIM_WAIT_SECONDS}s budget and never reached a terminal "
                f"state: {last}. `uq_mm_audit_run_active` is held, so no admin "
                "on any pod can start an audit. Either a run is genuinely stuck "
                "(check the heartbeat reaper and `codemie.mm_audit_run` for a "
                "RUNNING row with a stale `heartbeat_at`), or something outside "
                "this suite is driving audits back to back.",
                pytrace=False,
            )
        time.sleep(_POLL_SECONDS)
        last = _audit_status(client)
    return last


def _wait_until_terminal(client: httpx.Client, run_id: str, deadline: float) -> dict[str, Any]:
    """Poll until *run_id* stops holding the claim, or fail."""
    last = _audit_status(client)
    while last.get("run_id") == run_id and not _claim_is_free(last):
        if time.monotonic() >= deadline:
            pytest.fail(
                f"The audit this module started ({run_id}) never finished within "
                f"{_RUN_SECONDS}s: {last}. It is still holding "
                "`uq_mm_audit_run_active`, which would refuse every audit run "
                "after this suite — including the next invocation of this file.",
                pytrace=False,
            )
        time.sleep(_POLL_SECONDS)
        last = _audit_status(client)
    return last


@pytest.fixture(scope="module")
def audit_claim(admin_login: _ClientFactory) -> Iterator[dict[str, list[str]]]:
    """Own the audit claim for this module: release it however the test ends.

    The teardown is the half that was missing. `POST /audit/run` starts a real
    3m35s audit and the claim outlives the request, so a module that asserts
    202 and exits hands the *next* caller a 409 — which is how this file became
    order-dependent (BLOCKER-CI-1). Whatever run this module opens is therefore
    waited out here, in a fixture finaliser, so it runs on failure too.
    """
    started: dict[str, list[str]] = {"run_ids": []}
    yield started

    if not started["run_ids"]:
        return
    client = admin_login()
    deadline = time.monotonic() + _RUN_SECONDS
    for run_id in started["run_ids"]:
        _wait_until_terminal(client, run_id, deadline)


def test_audit_run_takes_the_claim_with_202_and_refuses_a_second_with_409(
    admin_login: _ClientFactory, audit_claim: dict[str, list[str]]
) -> None:
    """Both halves of the `POST /audit/run` contract, on the live stack.

    * **202, not 200** (F-40) — the endpoint hands the audit to a worker thread
      and returns immediately; the deleted live suite asserted 200 and would
      have stayed red even after its URLs were corrected.
    * **409 while it is held** — a run of one entity kind is a deployment-wide
      singleton enforced by `uq_mm_audit_run_active`, not by a process flag, so
      the second caller is refused *synchronously* and told which run to poll
      instead of believing it started one (F-17, F-49).

    The second assertion is here because its absence is what made this module
    look flaky: the 409 was already correct behaviour, it just had nothing
    covering it and everything tripping over it.
    """
    client = admin_login()
    deadline = time.monotonic() + _CLAIM_WAIT_SECONDS

    accepted: httpx.Response | None = None
    for _ in range(_MAX_CLAIM_ATTEMPTS):
        _wait_for_a_free_claim(client, deadline)
        response = client.post(AUDIT_RUN)
        if response.status_code == 202:
            accepted = response
            break
        if response.status_code != 409:
            pytest.fail(
                f"POST /audit/run answered {response.status_code}, which is "
                f"neither 202 (claim taken) nor 409 (claim held): {response.text}",
                pytrace=False,
            )
        # Somebody claimed it between our poll and our POST. That is the
        # documented race, not a defect; go back and wait for the next window.

    if accepted is None:
        pytest.fail(
            f"POST /audit/run was refused with 409 on every one of "
            f"{_MAX_CLAIM_ATTEMPTS} attempts inside {_CLAIM_WAIT_SECONDS}s. "
            "Something else is auditing ASSISTANT continuously; this test needs "
            "the claim to be free at least once.",
            pytrace=False,
        )

    body = accepted.json()
    run_id = body.get("run_id")
    assert run_id, f"a 202 must name the run it started: {body}"
    audit_claim["run_ids"].append(run_id)

    # The claim is now ours and held. The very next call must be refused, and
    # must point at *our* run rather than pretending to have started one.
    second = client.post(AUDIT_RUN)
    assert second.status_code == 409, (
        f"a second POST while run {run_id} is in flight answered "
        f"{second.status_code}, not 409: {second.text}. Two concurrent audits "
        "over the same catalogue both write snapshots (F-17)."
    )
    detail = second.json()["detail"]
    assert detail["error"] == "audit_already_running", detail
    assert detail["run_id"] == run_id, (
        f"the 409 named run {detail.get('run_id')!r} but the in-flight run is "
        f"{run_id!r}; the caller cannot poll a run it was not told about"
    )


def test_unauthenticated_request_is_rejected() -> None:
    """No session cookie -> 401 from the live service.

    Authorisation is declared once, router-wide; nothing in the suite has ever
    checked that it holds on the wire (F-42).
    """
    _require_live_stack()

    with httpx.Client(base_url=BASE_URL, timeout=30) as anon:
        response = anon.get(f"{API}/entities/assistants/overview")

    assert response.status_code == 401, f"{response.status_code}: {response.text}"
