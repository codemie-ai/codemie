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

"""Security tests for the marketplace-management router (EXECUTION-PLAN 8.1, 8.2, 8.5).

Three separate holes, one audience:

* **8.1 / F-18, F-19, S11, S12, R6** — ``GET /entities/{kind}/{eid}`` answered
  with ``assistant.model_dump(mode="json")``: all 54 columns, including
  ``system_prompt``, ``system_prompt_history``, the ``toolkits`` and
  ``mcp_servers`` structures the publish gate scans *for inline credentials*,
  and prompt-variable defaults the canonical ``/v1/assistants/{id}`` route
  masks. The route is open to every admin **or maintainer**
  (``authentication.py`` ``admin_access_only``), so that is a tenant-wide
  credential and prompt-history disclosure behind one drawer click.

* **8.2 / F-24, F-75, S14, S15** — an unhandled exception reached
  ``main.handle_exceptions``, which puts the last 2 KB of
  ``traceback.format_exc()`` into the ``error.message`` field of the 500 body
  whenever the process is not running locally. Container paths, line numbers
  and internal symbol names, returned to the client.

* **8.5 / F-37, R1** — neither ``POST /audit/run`` (282 ES round trips + 282
  inserts) nor ``POST /{eid}/assess`` (a billed LLM call) carried any rate
  limit, while the platform limiter has been mounted since EPMCDME-10160.

These drive the **real** ``codemie.rest_api.main.app`` through ``TestClient``
— real routing, real router-level dependency stack, real middleware and real
exception handlers — following the arrangement established by
``test_marketplace_management_contract.py``. Only identity and the data layer
are substituted.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Iterator

import pytest
from tests.codemie.marketplace_fixtures.mounted_app import mounted_marketplace_app
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

BASE = "/v1/marketplace-management"
ENTITY_ID = "leaky-1"
DETAIL = f"{BASE}/entities/assistants/{ENTITY_ID}"
ASSESS = f"{BASE}/entities/assistants/{ENTITY_ID}/assess"
OVERVIEW = f"{BASE}/entities/assistants/overview"
AUDIT_RUN = f"{BASE}/entities/assistants/audit/run"

# Sentinels planted in the structures the audit proved carry secrets. A test
# that asserts on key *names* alone would pass against a projection that
# renamed the field and kept the value, so every assertion below is made
# against the raw response text as well.
TOOLKIT_SECRET = "tk-inline-credential-MUST-NOT-LEAK"
MCP_SECRET = "mcp-inline-credential-MUST-NOT-LEAK"
SENSITIVE_VAR_DEFAULT = "prompt-variable-default-MUST-NOT-LEAK"
OLD_PROMPT = "a-superseded-system-prompt-MUST-NOT-LEAK"
CURRENT_PROMPT = "the-current-system-prompt-MUST-NOT-LEAK"


# ---------------------------------------------------------------------------
# App / identity fixtures (mirrors test_marketplace_management_contract.py)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app() -> Iterator[FastAPI]:
    """The production ASGI app, with the marketplace-management router mounted."""
    yield from mounted_marketplace_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    saved = dict(app.dependency_overrides)
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


def _make_user(*, admin: bool = True) -> User:
    user = User(
        id="admin-1",
        username="admin",
        name="Admin",
        email="admin@codemie.ai",
        project_names=["demo"],
    )
    user.is_admin = admin
    user.is_maintainer = not admin
    return user


@pytest.fixture
def admin_client(app: FastAPI, client: TestClient) -> TestClient:
    user = _make_user(admin=True)

    async def _fake_authenticate(request: Request) -> User:
        request.state.user = user
        return user

    app.dependency_overrides[authenticate] = _fake_authenticate
    return client


@pytest.fixture(autouse=True)
def live_rate_limiter() -> Iterator[None]:
    """Arm the limiter for this module, and hand back an empty budget after.

    ``tests/codemie/rest_api/routers/conftest.py`` sets
    ``limiter.enabled = False`` at import time for the whole routers package —
    a decision made for the local-auth tests, which would otherwise 429 each
    other. It also means no test in this directory can observe a rate limit at
    all, so the two below arm it for their own duration and put it back.

    ``main.app`` is a session-wide singleton, so the storage is reset on both
    edges: a test that burns an endpoint's allowance would otherwise 429 an
    unrelated test that runs afterwards (``test_marketplace_management_
    contract.py`` posts to ``/audit/run`` too).
    """
    from codemie.rest_api.rate_limit import limiter

    was_enabled = limiter.enabled
    limiter.enabled = True
    limiter.reset()
    try:
        yield
    finally:
        limiter.reset()
        limiter.enabled = was_enabled


# ---------------------------------------------------------------------------
# 8.1 — the detail projection (F-18, F-19)
# ---------------------------------------------------------------------------


def _leaky_assistant():
    """An assistant carrying exactly the payloads F-18 says the route dumped."""
    from codemie.rest_api.models.assistant import (
        Assistant,
        MCPServerDetails,
        PromptVariable,
        SystemPromptHistory,
        ToolDetails,
        ToolKitDetails,
    )
    from codemie.rest_api.models.settings import CredentialValues, SettingsBase
    from codemie_tools.base.models import CredentialTypes

    inline_credential = SettingsBase(
        project_name="demo",
        alias="git-pat",
        credential_type=CredentialTypes.GIT,
        credential_values=[CredentialValues(key="token", value=TOOLKIT_SECRET)],
    )
    return Assistant(
        id=ENTITY_ID,
        name="Leaky Assistant",
        description="An assistant whose row carries secrets.",
        system_prompt=CURRENT_PROMPT,
        system_prompt_history=[SystemPromptHistory(system_prompt=OLD_PROMPT, date=datetime.now(UTC))],
        toolkits=[
            ToolKitDetails(
                toolkit="git",
                tools=[ToolDetails(name="git_read", settings=inline_credential)],
            )
        ],
        mcp_servers=[MCPServerDetails(name="secrets-mcp", arguments=f"--token={MCP_SECRET}")],
        prompt_variables=[
            PromptVariable(
                key="API_KEY",
                description="Downstream API key",
                default_value=SENSITIVE_VAR_DEFAULT,
                is_sensitive=True,
            ),
            PromptVariable(key="REGION", default_value="eu-west-1", is_sensitive=False),
        ],
        categories=["Engineering"],
        creator="submitter@example.com",
        project="demo",
        is_global=True,
        marketplace_status="PENDING_REVIEW",
        created_date=datetime.now(UTC),
    )


@pytest.fixture
def leaky_detail(monkeypatch: pytest.MonkeyPatch):
    """Serve the secret-bearing assistant from the detail route's only read."""
    from codemie.rest_api.routers import marketplace_management as mm

    assistant = _leaky_assistant()
    monkeypatch.setattr(mm.Assistant, "get_by_id", staticmethod(lambda aid: assistant))
    # Stub audit enrichment so verdict fields degrade to null rather than
    # surfacing MagicMock values (which fail Pydantic validation) from the
    # session-wide get_engine mock in tests/conftest.py.
    monkeypatch.setattr(mm, "_review_verdict", lambda aid: {})
    monkeypatch.setattr(mm, "_review_polish_checks", lambda aid: None)
    return assistant


def test_detail_does_not_leak_prompts_or_credentials(admin_client: TestClient, leaky_detail) -> None:
    """The review drawer must not be a credential exfiltration endpoint (F-18).

    Audience is admin **or maintainer**, for every assistant in the tenant
    including the private ones. Nothing on this route needs the raw toolkit
    settings, the MCP arguments or the superseded prompts, so none of them may
    cross the wire.
    """
    response = admin_client.get(DETAIL)

    assert response.status_code == 200, response.text
    body = response.json()

    for forbidden_key in ("system_prompt", "system_prompt_history", "toolkits", "mcp_servers"):
        assert forbidden_key not in body, f"{forbidden_key!r} is still projected: {sorted(body)}"

    text = response.text
    for secret in (TOOLKIT_SECRET, MCP_SECRET, SENSITIVE_VAR_DEFAULT, OLD_PROMPT, CURRENT_PROMPT):
        assert secret not in text, f"{secret!r} crossed the wire"


def test_detail_masks_sensitive_prompt_variables(admin_client: TestClient, leaky_detail) -> None:
    """Masking must match the canonical assistant route, value for value (F-19).

    `/v1/assistants/id/{id}` runs `_mask_sensitive_prompt_variables`, which
    replaces the default of any `is_sensitive` variable with
    `SENSITIVE_VALUE_MASK` and leaves the rest alone. The moderation route
    reads the same rows for a wider audience and skipped it entirely.
    """
    from codemie.core.constants import SENSITIVE_VALUE_MASK

    response = admin_client.get(DETAIL)

    assert response.status_code == 200, response.text
    variables = {var["key"]: var for var in response.json()["prompt_variables"]}

    assert variables["API_KEY"]["default_value"] == SENSITIVE_VALUE_MASK
    assert variables["API_KEY"]["is_sensitive"] is True
    # A non-sensitive variable is untouched: masking everything would be a
    # different bug, not a fix.
    assert variables["REGION"]["default_value"] == "eu-west-1"


def test_detail_response_model_declares_the_projection(app: FastAPI) -> None:
    """Phase 4 pinned this route's `response_model`; it must not lie (8.1 NOTE).

    Returning a projection while the schema still advertises the full
    `Assistant` publishes 54 fields the endpoint no longer answers with, and
    the generated frontend types would keep promising the secrets.
    """
    schema = app.openapi()
    operation = schema["paths"][f"{BASE}/entities/{{kind}}/{{eid}}"]["get"]
    ref = operation["responses"]["200"]["content"]["application/json"]["schema"]
    target = ref.get("$ref") or next(
        (o["$ref"] for o in ref.get("allOf", []) + ref.get("anyOf", []) if "$ref" in o), None
    )
    assert target, f"the detail route publishes no modelled response: {ref}"

    model = schema["components"]["schemas"][target.rsplit("/", 1)[-1]]
    declared = set(model["properties"])
    assert not (
        declared & {"system_prompt", "system_prompt_history", "toolkits", "mcp_servers"}
    ), f"the published schema still advertises the dumped columns: {sorted(declared)}"


# ---------------------------------------------------------------------------
# 8.2 — no stack traces, no raw exception text (F-24, F-75)
# ---------------------------------------------------------------------------


@pytest.fixture
def deployed_not_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the request the way a deployed pod runs it.

    `main.handle_exceptions` re-raises when `config.is_local`, and `pytest.ini`
    sets `ENV=local` — so the *default* test environment cannot observe the
    behaviour F-24 documents on the live stack. Pin ENV to a deployed value so
    the assertion is about production, not about the test harness.
    """
    from codemie.configs import config

    monkeypatch.setattr(config, "ENV", "test-deployment")
    assert not config.is_local


def test_5xx_body_has_no_traceback(
    admin_client: TestClient, deployed_not_local: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 500 tells the client a correlation id, not where the code lives (F-24).

    Forced failure rather than a real one so the assertion stays about the
    error *envelope*: whatever blows up, the body must carry neither a Python
    traceback nor an internal path, and must carry something support can join
    against the server log.
    """
    from codemie.rest_api.routers import marketplace_management as mm

    def _boom(kind):
        raise RuntimeError(f"connection to {'psql://user:pw@db:5432'} failed")

    monkeypatch.setattr(mm, "_overview_payload", _boom)

    response = admin_client.get(OVERVIEW)

    assert response.status_code == 500, response.text
    text = response.text
    assert "Traceback" not in text, text[:400]
    assert "/app/src/" not in text, text[:400]
    assert "marketplace_management.py" not in text, text[:400]
    assert "psql://user:pw@db:5432" not in text, text[:400]

    # A 500 with no handle is unactionable: the operator must be able to find
    # the suppressed detail in the log.
    body = response.json()
    blob = json.dumps(body)
    assert "correlation_id" in blob, body


# ---------------------------------------------------------------------------
# 8.5 — rate limits (F-37, R1)
# ---------------------------------------------------------------------------


# Enough rapid calls that any sane per-minute allowance is exhausted, few
# enough that the test stays sub-second. Deliberately does not restate the
# shipped limit: the assertion is "there is a brake", not "the brake is 5".
_BURST = 30


def test_audit_run_is_rate_limited(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """N+1 rapid audit starts must be refused, not queued (F-37, R1).

    A full audit is 282 ES round trips plus 282 inserts. A stuck UI retry loop
    or one impatient admin is a one-click self-DoS on the shared threadpool
    every synchronous route in the product draws from.
    """
    from codemie.rest_api.routers import marketplace_management as mm

    started = {
        "is_running": True,
        "run_id": "run-abc",
        "started_at": 1234.0,
        "finished_at": None,
        "error": None,
    }
    monkeypatch.setattr(mm.AUDIT_CACHE, "start_audit", lambda kind: started)

    statuses = [admin_client.post(AUDIT_RUN).status_code for _ in range(_BURST)]

    assert statuses[0] == 202, statuses
    assert 429 in statuses, f"no rate limit on POST /audit/run: {statuses}"
    # Once refused, it stays refused for the rest of the window.
    assert statuses[statuses.index(429) :] == [429] * (_BURST - statuses.index(429)), statuses


def test_assess_is_rate_limited(admin_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """`/assess` bills a provider per call, so it needs the same brake (F-37)."""
    from codemie.rest_api.routers import marketplace_management as mm
    from codemie.service.marketplace_management.models import (
        AssessmentFields,
        AssessmentResponse,
    )

    fields = AssessmentFields.model_construct()
    monkeypatch.setattr(
        mm.LLMAssessmentService,
        "assess_assistant",
        staticmethod(
            lambda assistant_id, user, llm_model=None: AssessmentResponse.model_construct(
                assistant_id=assistant_id, assessment=fields
            )
        ),
    )

    statuses = [admin_client.post(ASSESS, json={}).status_code for _ in range(_BURST)]

    assert statuses[0] == 200, statuses
    assert 429 in statuses, f"no rate limit on POST /{{eid}}/assess: {statuses}"
