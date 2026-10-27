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

"""The named negative and corner cases of the marketplace approval chain.

EXECUTION-PLAN 10.5 — implements the **pytest half** of
`review-e2e-coverage-spec.md` §2. Traces F-03, F-04, F-05, F-06, F-08, F-14,
F-17, F-22, F-49, F-83, F-86, F-87.

**Why these cases are here and not in a browser** (spec §5, "THE LAYERING
RULE"): Playwright answers *does the user see it and can the user do it*.
Anything whose assertion is a row, a status code or a race belongs in pytest.
Four of the cases in this file are explicitly **barred** from Playwright,
because in a browser each is slower, flakier and *less conclusive*:

* **§2.5** two admins acting concurrently — two browser contexts cannot
  reliably overlap in the millisecond window that matters, so a real defect
  would read as flake;
* **§2.6** the unpublish + de-index contract — a cross-store side effect; a UI
  assertion would be measuring index latency;
* **§2.7a/c** the authz matrix and actor provenance — the requirement is
  explicitly *not* "the button is hidden";
* **§2.11** audit re-run and lock behaviour — a process-global claim and a
  multi-minute run; a browser test would hang CI.

**Test data** (spec §4). Every test seeds its own rows under a per-module run
token and asserts on **its own id**. There is no assertion on an ambient count
anywhere in this file; where a count is genuinely the subject it is scoped by
`where id = :id` or captured as a before/after delta. `mm_publish_db`
(`conftest.py`) hands out a throwaway, migration-built database and truncates
the four tables it owns after every test; this module cleans the three it adds
(`activity_events`, `mm_audit_run`, `mm_audit_verdict`) itself, before that
teardown runs, so a leaked row cannot survive into another module.

**Known limit, stated rather than papered over — the Elasticsearch half of
§2.6.** These tests cannot assert against a live Elasticsearch, for two
reasons that are worth naming precisely rather than hand-waving:

1. The assistants they seed live in a **throwaway database** (`mm_publish_db`)
   that no Elasticsearch instance has ever seen. There is no document for a
   de-index to remove, so a live `GET /marketplace_assistants/_doc/{id}` would
   assert `404 -> 404` — which passes whether or not the product ever calls
   remove. That is precisely the vacuous shape §2.6 exists to avoid.
2. Creating one first is not available either: indexing goes through the
   embedding service, which in this environment answers
   `AuthenticationError: 401 ... invalid subscription key` (observed:
   `marketplace index attempt 1/2 failed ... 2/2 failed`). A test that has to
   reach a paid third-party endpoint to set up its precondition is not a unit
   test.

So these tests do two separate things and say which is which:

* they assert the **de-index call is made**, on the production call path
  (`queue_service._side_effects` -> `marketplace_index_sync.remove_assistant`
  -> `PlatformIndexingService.remove_single_assistant`) and with this
  assistant's id. That is the real, load-bearing claim, and it is the one that
  was missing entirely — `reject` and `withdraw` had no index call site at all
  (F-04);
* they assert the **document is gone** against an in-process index double that
  is seeded with a document *first*, so "the document existed and then did
  not" is a real state transition rather than an absence that was always true.

The live-Elasticsearch round trip stays an operator check; it is recorded, with
the command, in `impl-10.5-pytest.md`.
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Iterator, Optional
from unittest.mock import patch

import pytest
from tests.codemie.marketplace_fixtures.mounted_app import mounted_marketplace_app
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session

from codemie.configs import config
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

MM = "/v1/marketplace-management/entities/assistants"
PUBLISH = "/v1/assistants/{aid}/marketplace/publish"
OVERVIEW = f"{MM}/overview"
AUDIT_RUN = f"{MM}/audit/run"
AUDIT_STATUS = f"{MM}/audit/status"

# One token per test session process. Every id and every name this module
# writes carries it, so a crashed run leaves a labelled, greppable residue and
# no `like` predicate can ever collide with another module's rows.
RUN = f"mmneg-{uuid.uuid4().hex[:8]}"

# Long enough to clear the `substance` (>= 800 chars) and `system_prompt_length`
# (>= 200) gates without tripping `no_inline_credentials`.
GOOD_PROMPT = (
    "You are a release-notes assistant for a platform engineering group. "
    "You summarise merge requests into human readable release notes, you group "
    "them by component, and you always cite the merge request identifier next "
    "to every bullet you produce. When the user asks for a changelog you answer "
    "with markdown headings per component and a short paragraph explaining the "
    "user visible impact of each change. You never invent a merge request that "
    "was not given to you, and when the input is empty you say so plainly "
    "instead of producing an empty document. "
) * 3
GOOD_DESCRIPTION = (
    "Summarises merge requests into structured, component-grouped release notes "
    "with citations back to every source merge request identifier."
)
# `_validate_reason` demands 20-2000 characters, so a rejection reason in this
# file has to be a real sentence.
GOOD_REASON = "Duplicate of an existing verified assistant; removing it from the queue."

ADMIN_ID = f"{RUN}-admin"
ADMIN_EMAIL = f"{RUN}-admin@codemie.ai"
OWNER_ID = f"{RUN}-owner"
OWNER_EMAIL = f"{RUN}-owner@codemie.ai"
SPOOFED_ACTOR = "attacker@example.com"


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def _user(*, uid: str, email: str, admin: bool, name: str) -> User:
    user = User(id=uid, username=email, name=name, email=email, project_names=["demo"])
    # `User.resolve_is_admin` force-promotes everyone when ENV=local, which
    # pytest.ini sets. Pin both flags so the fixture means what its name says.
    user.is_admin = admin
    user.is_maintainer = False
    assert user.is_admin_or_maintainer is admin
    return user


@pytest.fixture(scope="module")
def app() -> Iterator[FastAPI]:
    """The production ASGI app, with the marketplace-management router mounted."""
    yield from mounted_marketplace_app()


@pytest.fixture
def admin() -> User:
    return _user(uid=ADMIN_ID, email=ADMIN_EMAIL, admin=True, name="Ada Admin")


@pytest.fixture
def owner() -> User:
    """A non-admin who owns the assistant: the §2.7b asymmetry."""
    return _user(uid=OWNER_ID, email=OWNER_EMAIL, admin=False, name="Owen Owner")


@pytest.fixture
def db(mm_publish_db, admin: User, owner: User) -> Iterator[Engine]:
    """The throwaway database, with both principals present as real users.

    `assistants.marketplace_decided_by` and `activity_events.actor_id` both FK
    `users.id`, so a decision cannot name a principal that does not exist -- a
    constraint that is itself part of why the audit actor cannot be free text.

    Cleans the three tables `mm_publish_db` does not own **before** its own
    truncation runs (finalisation is LIFO and this fixture depends on it), so
    an `activity_events` row can never block its `delete from users`.
    """
    with mm_publish_db.connect() as conn:
        for principal in (admin, owner):
            conn.execute(
                text(
                    "insert into codemie.users (id, username, email, name, is_admin, is_active, date) "
                    "values (:i, :u, :e, :n, :a, true, now()) on conflict (id) do nothing"
                ),
                {
                    "i": principal.id,
                    "u": principal.username,
                    "e": principal.email,
                    "n": principal.name,
                    "a": principal.is_admin,
                },
            )
        conn.commit()

    yield mm_publish_db

    with mm_publish_db.connect() as conn:
        conn.execute(text("delete from codemie.activity_events where domain = 'marketplace'"))
        conn.execute(text("delete from codemie.mm_audit_finding"))
        conn.execute(text("delete from codemie.mm_audit_similarity"))
        conn.execute(text("delete from codemie.mm_audit_verdict"))
        conn.execute(text("delete from codemie.mm_audit_run"))
        conn.commit()


# `app.dependency_overrides` is a dict on a process-wide singleton, so an
# identity installed by one fixture is the identity every TestClient in the
# test sees -- including one built by another fixture that thinks it is
# somebody else. Two per-principal client fixtures in one signature therefore
# resolve to whichever ran last, and an authz test written that way passes for
# the wrong reason. (Observed while writing this file: a `403 for a non-admin`
# case was running as the admin.)
#
# So there is exactly ONE client, and the principal is swapped per call through
# `as_user`, which cannot be got wrong by re-ordering a signature.
_ACTING: dict[str, Optional[User]] = {}


@pytest.fixture
def client(app: FastAPI, db: Engine, admin: User) -> Iterator[TestClient]:
    saved = dict(app.dependency_overrides)

    async def _fake_authenticate(request: Request) -> User:
        principal = _ACTING["user"]
        assert principal is not None, "no principal is acting; use as_user(...)"
        request.state.user = principal
        return principal

    _ACTING["user"] = admin
    app.dependency_overrides[authenticate] = _fake_authenticate
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)
    _ACTING.clear()


@contextmanager
def as_user(user: User) -> Iterator[None]:
    """Act as *user* for the duration of the block."""
    previous = _ACTING.get("user")
    _ACTING["user"] = user
    try:
        yield
    finally:
        _ACTING["user"] = previous


@contextmanager
def as_anonymous(app: FastAPI) -> Iterator[None]:
    """Remove the identity override entirely: the real auth stack answers.

    This is the only honest way to assert a 401 -- an override that returns
    `None` would exercise the handler's own None-handling, not authentication.
    """
    saved = app.dependency_overrides.pop(authenticate, None)
    try:
        yield
    finally:
        if saved is not None:
            app.dependency_overrides[authenticate] = saved


@pytest.fixture
def activity_events_enabled(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Turn on the platform activity feed for the duration of a test.

    `activity_event_repository.insert` returns immediately when
    `config.ACTIVITY_EVENTS_ENABLED` is False, which is the shipped default, so
    a test that asserts *about* the feed has to arm it explicitly rather than
    pass vacuously because it is off.
    """
    from codemie.configs import config as _config

    monkeypatch.setattr(_config, "ACTIVITY_EVENTS_ENABLED", True, raising=False)
    yield


# ---------------------------------------------------------------------------
# The search-index double (see the module docstring on the ES half of §2.6)
# ---------------------------------------------------------------------------


class _IndexDouble:
    """Stands in for the `marketplace_assistants` Elasticsearch index.

    Records every call *and* keeps a document set, so a test can assert both
    halves of the §2.6 contract separately: that the product asked for the
    removal, and that a document which really existed really went away.
    """

    def __init__(self) -> None:
        self.docs: set[str] = set()
        self.indexed: list[str] = []
        self.removed: list[tuple[str, Optional[str]]] = []

    def seed_doc(self, assistant_id: str) -> None:
        self.docs.add(assistant_id)

    def index(self, assistant_id: str, user: Any = None, is_update: bool = False) -> None:
        self.indexed.append(assistant_id)
        self.docs.add(assistant_id)

    def remove(self, assistant_id: str, assistant_name: Any = None, user: Any = None) -> None:
        self.removed.append((assistant_id, assistant_name))
        self.docs.discard(assistant_id)


@pytest.fixture
def index_double(monkeypatch: pytest.MonkeyPatch) -> _IndexDouble:
    from codemie.service.platform.platform_indexing_service import PlatformIndexingService

    double = _IndexDouble()
    monkeypatch.setattr(PlatformIndexingService, "index_single_assistant", staticmethod(double.index))
    monkeypatch.setattr(PlatformIndexingService, "remove_single_assistant", staticmethod(double.remove))
    return double


# ---------------------------------------------------------------------------
# Seeding and reading — always by this module's own ids
# ---------------------------------------------------------------------------


def seed(engine: Engine, case: str, *, creator: User, **overrides: Any) -> str:
    """Insert one assistant owned by *creator* and return its id.

    The id and the name both carry the run token (spec §4.1) so every SQL
    predicate in this file is `where id = :id` over a row this test created.
    """
    aid = f"{RUN}-{case}"
    fields: dict[str, Any] = {
        "id": aid,
        "name": f"Release Notes Assistant {aid}",
        "description": GOOD_DESCRIPTION,
        "system_prompt": GOOD_PROMPT,
        "categories": ["engineering"],
        "project": "demo",
        "creator": creator.id,
        "created_by": CreatedByUser(id=creator.id, username=creator.username, name=creator.name),
        "is_global": False,
        "marketplace_status": "DRAFT",
        "shared": True,
    }
    fields.update(overrides)
    with Session(engine) as session:
        session.add(Assistant(**fields))
        session.commit()
    return aid


def row(engine: Engine, aid: str) -> dict[str, Any]:
    with engine.connect() as conn:
        return dict(
            conn.execute(
                text(
                    "select marketplace_status, is_global, is_verified, marketplace_version, "
                    "       marketplace_decided_by, marketplace_decision_reason "
                    "  from codemie.assistants where id = :i"
                ),
                {"i": aid},
            )
            .mappings()
            .one()
        )


def decisions(engine: Engine, aid: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                text(
                    "select decision, from_status, to_status, reason, override_notice, "
                    "       decided_by_id, decided_by_email, result "
                    "  from codemie.mm_review_decision where entity_id = :i order by created_at"
                ),
                {"i": aid},
            ).mappings()
        ]


def events(engine: Engine, aid: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                text(
                    "select event_type, actor_id, attributes from codemie.activity_events "
                    " where entity_id = :i and domain = 'marketplace' order by created_at"
                ),
                {"i": aid},
            ).mappings()
        ]


def publish(client: TestClient, aid: str, *, gate: bool = True, llm: bool = False, **body: Any):
    """POST the publish endpoint with the approval gate armed by default.

    `Ability.can` and `validate_category_ids` are patched because neither is
    the subject of any test here: the first needs a permissions backend, the
    second a categories table, and both would turn every case in this file into
    a test of somebody else's fixture.
    """
    payload: dict[str, Any] = {"categories": ["engineering"]}
    payload.update(body)
    with (
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch(
            "codemie.service.assistant.category_service.category_service.validate_category_ids",
            return_value=None,
        ),
        patch("codemie.service.monitoring.base_monitoring_service.send_log_metric"),
        patch.object(config, "MARKETPLACE_APPROVAL_GATE_ENABLED", gate),
        # The gate is armed only together with marketplace management (`is_approval_gate_armed`).
        patch.object(config, "MARKETPLACE_MANAGEMENT_ENABLED", gate),
        patch.object(config, "MARKETPLACE_LLM_VALIDATION_ON_PUBLISH_ENABLED", llm),
    ):
        return client.post(PUBLISH.format(aid=aid), json=payload)


def submit(client: TestClient, aid: str) -> None:
    """Put a seeded assistant in the queue through the real publish path.

    Always publishes as a non-admin/maintainer variant of whichever principal
    is currently acting: an admin/maintainer publish now skips the review
    queue by design (`assistant.py` `admin_skip_queue`), and this helper's
    whole job is producing a PENDING_REVIEW row for this file's moderation
    tests to act on — not exercising that skip-queue path.
    """
    submitter = _ACTING["user"].model_copy(update={"is_admin": False, "is_maintainer": False})
    with as_user(submitter):
        response = publish(client, aid)
    assert response.status_code == 200, response.text
    assert response.json()["marketplace_status"] == "PENDING_REVIEW", response.text


def approve(client: TestClient, aid: str, **body: Any):
    return client.post(f"{MM}/pending/{aid}/approve", json=body)


def reject(client: TestClient, aid: str, reason: str = GOOD_REASON, **body: Any):
    return client.post(f"{MM}/pending/{aid}/reject", json={"reason": reason, **body})


def withdraw(client: TestClient, aid: str, **body: Any):
    return client.post(f"{MM}/pending/{aid}/withdraw", json=body)


def force_publish(client: TestClient, aid: str, notice: str = GOOD_REASON, **body: Any):
    return client.post(f"{MM}/pending/{aid}/force-publish", json={"override_notice": notice, **body})


# ---------------------------------------------------------------------------
# §2.1 -- publish rejects missing or invalid metadata (F-86)
#
# Three of the four sub-cases never reach a rendered page, which is why they
# are here and not in a browser. The fourth -- the 422 rendering as an
# actionable dialog rather than a raw toast -- is the one Playwright test §2.1
# keeps.
#
# The shared assertion, and the one that matters most: a *rejected* publish
# must not partially mutate. Status, is_global and the CAS version counter are
# all re-read afterwards, because a 422 that still bumped the version would
# leave the next legitimate submission conflicting with a decision that was
# never taken.
# ---------------------------------------------------------------------------


def test_publish_with_no_json_body_is_422_naming_the_field(client, db, admin) -> None:
    """POST with no body at all: 422 naming `categories`, never a 500 (F-86).

    `request: Optional[PublishToMarketplaceRequest] = None` is optional in the
    signature and mandatory in fact -- the handler's first act is to read
    `request.categories`. Unguarded, that is an `AttributeError` and a 500
    whose body carried the traceback: a crash and an information-disclosure
    bug in one response.
    """
    aid = seed(db, "nobody", creator=admin)

    with (
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch.object(config, "MARKETPLACE_APPROVAL_GATE_ENABLED", True),
        # The gate is armed only together with marketplace management (`is_approval_gate_armed`).
        patch.object(config, "MARKETPLACE_MANAGEMENT_ENABLED", True),
    ):
        response = client.post(PUBLISH.format(aid=aid))

    assert response.status_code == 422, response.text
    assert "categories" in response.text.lower(), response.text
    assert "Traceback" not in response.text, response.text

    unchanged = row(db, aid)
    assert unchanged["marketplace_status"] == "DRAFT"
    assert unchanged["is_global"] is False
    assert unchanged["marketplace_version"] == 0
    assert decisions(db, aid) == []


def test_publish_with_no_categories_is_rejected_and_changes_nothing(client, db, admin) -> None:
    """`{"categories": []}`: 4xx naming the field, and no partial mutation."""
    aid = seed(db, "nocats", creator=admin)

    response = publish(client, aid, categories=[])

    assert 400 <= response.status_code < 500, response.text
    assert "categories" in response.text.lower(), response.text

    unchanged = row(db, aid)
    assert unchanged["marketplace_status"] == "DRAFT"
    assert unchanged["is_global"] is False
    assert unchanged["marketplace_version"] == 0
    assert decisions(db, aid) == []


def test_publish_with_an_unknown_category_names_the_invalid_id(client, db, admin) -> None:
    """An id that is not in `categories`: 4xx that names it, and no mutation.

    `validate_category_ids` is the real validator here rather than the stub
    `publish()` installs -- the point of this case is that its refusal reaches
    the client intact.
    """
    from codemie.core.exceptions import ExtendedHTTPException

    aid = seed(db, "badcat", creator=admin)
    bogus = "does-not-exist"

    def _refuse(categories, required=False):
        raise ExtendedHTTPException(
            code=400,
            message=f"Invalid category ids: {bogus}",
            details={"invalid": [bogus]},
            help="Use an id from GET /v1/assistant-categories.",
        )

    with (
        patch("codemie.core.ability.Ability.can", return_value=True),
        patch("codemie.service.monitoring.base_monitoring_service.send_log_metric"),
        patch(
            "codemie.service.assistant.category_service.category_service.validate_category_ids",
            side_effect=_refuse,
        ),
        patch.object(config, "MARKETPLACE_APPROVAL_GATE_ENABLED", True),
        # The gate is armed only together with marketplace management (`is_approval_gate_armed`).
        patch.object(config, "MARKETPLACE_MANAGEMENT_ENABLED", True),
        patch.object(config, "MARKETPLACE_LLM_VALIDATION_ON_PUBLISH_ENABLED", False),
    ):
        response = client.post(PUBLISH.format(aid=aid), json={"categories": [bogus]})

    assert 400 <= response.status_code < 500, response.text
    assert bogus in response.text, response.text
    assert "Traceback" not in response.text, response.text

    unchanged = row(db, aid)
    assert unchanged["marketplace_status"] == "DRAFT"
    assert unchanged["is_global"] is False
    assert unchanged["marketplace_version"] == 0
    assert decisions(db, aid) == []


# ---------------------------------------------------------------------------
# §2.2 -- a duplicate hits the similarity/quality gate, with the LLM stubbed
#
# The LLM is stubbed on purpose: a live model makes the verdict
# non-deterministic and the test slow, and the claim under test is the *gate
# wiring*, not the model's judgement.
# ---------------------------------------------------------------------------


def _non_accept_verdict():
    from codemie.rest_api.models.assistant import QualityValidationResult

    return QualityValidationResult(
        decision="reject",
        reasoning_comment=(
            "This assistant duplicates an existing marketplace entry with the same "
            "purpose and near-identical instructions."
        ),
        recommendations=None,
    )


def test_duplicate_hits_the_quality_gate_and_is_not_published(client, db, admin) -> None:
    """A near-clone of a published assistant is refused, and stays unpublished.

    The count assertion is scoped to the two ids this test created -- never to
    `select count(*) ... where marketplace_status = 'APPROVED'`, which would be
    a statement about whatever else is in the database (spec §4.2).
    """
    original = seed(db, "dup-a", creator=admin, marketplace_status="APPROVED", is_global=True)
    clone = seed(db, "dup-b", creator=admin)

    with patch(
        "codemie.service.assistant_generator_service.AssistantGeneratorService.validate_assistant_for_publish",
        return_value=_non_accept_verdict(),
    ) as stub:
        response = publish(client, clone, llm=True)

    assert response.status_code == 422, response.text
    assert stub.called, "the publish path never consulted the quality validator"
    body = response.json()
    assert body["error"]["details"]["requires_confirmation"] is True, body
    assert body["error"]["details"]["quality_validation"]["decision"] != "accept", body

    # The clone did not publish...
    clone_row = row(db, clone)
    assert clone_row["marketplace_status"] == "DRAFT"
    assert clone_row["is_global"] is False
    assert clone_row["marketplace_version"] == 0
    assert decisions(db, clone) == []
    # ...and the original was not touched by the refusal either.
    assert row(db, original)["marketplace_status"] == "APPROVED"

    with db.connect() as conn:
        approved = conn.execute(
            text(
                "select count(*) from codemie.assistants " " where id = any(:ids) and marketplace_status = 'APPROVED'"
            ),
            {"ids": [original, clone]},
        ).scalar_one()
    assert approved == 1, "exactly one of the pair may be published"


def test_admin_waiver_bypasses_the_llm_gate_and_is_recorded(client, db, admin) -> None:
    """`ignore_recommendations` waives the *advisory* verdict — audibly.

    A silent bypass is an audit hole, so the waiver has to reach the metric as
    `published_with_ignored_recommendations`. The validator must not even be
    consulted once the waiver is honoured, which is what makes it a waiver
    rather than a retry.
    """
    aid = seed(db, "waiver", creator=admin)

    with (
        patch(
            "codemie.service.assistant_generator_service.AssistantGeneratorService.validate_assistant_for_publish",
            return_value=_non_accept_verdict(),
        ) as stub,
        patch("codemie.rest_api.routers.assistant.marketplace._track_assistant_management_metric") as metric,
    ):
        response = publish(client, aid, llm=True, ignore_recommendations=True)

    assert response.status_code == 200, response.text
    assert not stub.called, "the waiver was honoured but the model was billed anyway"
    # admin/maintainer publishes skip the review queue by design (see
    # `assistant.py` `admin_skip_queue`), so this lands on APPROVED, not
    # PENDING_REVIEW -- the waiver itself is the same either way.
    assert row(db, aid)["marketplace_status"] == "APPROVED"

    published_calls = [c for c in metric.call_args_list if c.args[0] == "publish_to_marketplace"]
    assert published_calls, metric.call_args_list
    attributes = published_calls[-1].args[4]
    assert attributes["published_with_ignored_recommendations"] is True, attributes


def test_ignore_recommendations_does_not_skip_validation_for_a_non_admin(client, db, owner) -> None:
    """F-83: the waiver is a waiver, not a skip button, and it belongs to admins.

    A non-admin who sends `ignore_recommendations` against a real finding gets
    a 403 that still **names the failing rules**, so the refusal is actionable
    rather than merely a refusal -- and the deterministic gates run for them
    either way.
    """
    aid = seed(
        db,
        "f83",
        creator=owner,
        name="Bot",  # trips name_length
        description="A little helper for the team, sort of.",  # trips description_length
    )

    with as_user(owner):
        response = publish(client, aid, ignore_recommendations=True)

    assert response.status_code == 403, response.text
    body = response.text
    assert "name_length" in body, body
    assert "description_length" in body, body

    unchanged = row(db, aid)
    assert unchanged["marketplace_status"] == "DRAFT"
    assert unchanged["is_global"] is False
    assert unchanged["marketplace_version"] == 0
    assert decisions(db, aid) == []


# ---------------------------------------------------------------------------
# §2.3a -- the publish-time validator is unavailable or times out (F-22)
#
# The Playwright half (§2.3b) asserts the drawer resolves its spinner to an
# error state. This half asserts the server does not leak a 500 and does not
# half-publish, which is a status code and a row -- pytest's job.
# ---------------------------------------------------------------------------


def test_llm_validator_timeout_is_bounded_and_leaks_no_500(client, db, admin) -> None:
    """A hung quality validator is an upstream failure, not a server crash.

    `_llm_validate_on_marketplace_publish` calls
    `AssistantGeneratorService.validate_assistant_for_publish` with no timeout
    and no try/except, so a `TimeoutError` escaped to the framework's generic
    handler: a 500 whose body carried the traceback. The contract asserted here
    is 503/504 with an actionable message, and -- the part that matters even
    more -- **no state change**, because a publish that failed upstream must be
    retryable rather than half-done.
    """
    aid = seed(db, "llmtimeout", creator=admin)

    with patch(
        "codemie.service.assistant_generator_service.AssistantGeneratorService.validate_assistant_for_publish",
        side_effect=TimeoutError("assistant validation timed out after 30s"),
    ):
        response = publish(client, aid, llm=True)

    assert response.status_code in (503, 504), (
        f"a timed-out upstream validator surfaced as {response.status_code}; "
        "an unavailable dependency is a 503/504, and a 500 tells the admin "
        "nothing they can act on"
    )
    assert "Traceback" not in response.text, response.text

    unchanged = row(db, aid)
    assert unchanged["marketplace_status"] == "DRAFT"
    assert unchanged["is_global"] is False
    assert unchanged["marketplace_version"] == 0
    assert decisions(db, aid) == []


# ---------------------------------------------------------------------------
# §2.4 -- reject with a reason, then the owner re-submits (F-05)
#
# The state machine is a status code plus a ledger row, so it is pytest's. The
# Playwright half of §2.4 is only the button state: `moderation-submit` stays
# disabled until the reason is long enough.
# ---------------------------------------------------------------------------


def test_reject_requires_a_reason_and_a_blank_one_writes_nothing(client, db, admin) -> None:
    """Whitespace is not a reason: 4xx, and the queue item is untouched.

    The second half is the one worth having. A refusal that still moved the row
    -- or still appended to the ledger -- would make the audit trail claim a
    decision that never happened.
    """
    aid = seed(db, "blankreason", creator=admin)
    submit(client, aid)
    before = row(db, aid)

    response = reject(client, aid, reason="   ")

    assert 400 <= response.status_code < 500, response.text
    assert row(db, aid) == before
    assert [d["decision"] for d in decisions(db, aid)] == ["SUBMIT"]


def test_the_same_short_notice_is_refused_identically_by_both_entry_points(client, db, admin) -> None:
    """One override-reason rule, one status code, from either door.

    Comment #12: the 20-2000 char rule was implemented twice and had drifted --
    `POST /assistants/{id}/publish?force_publish=true` answered 422 while the
    admin queue's force-publish answered 400 for the very same input. Both now
    route through `_validate_reason`, so a reviewer cannot learn one contract
    from one endpoint and have the other disagree.
    """
    aid = seed(db, "notice-converge", creator=admin)
    submit(client, aid)

    queue = force_publish(client, aid, notice="too short")

    assert queue.status_code == 422, queue.text
    assert "override_notice" in queue.text

    # Same rule, same refusal, for a rejection reason of the same length.
    rejected = reject(client, aid, reason="too short")

    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["details"]["field"] == "reason"


def test_reject_persists_its_reason_on_the_row_and_in_the_ledger(client, db, admin, index_double) -> None:
    """A rejection carries its reason verbatim into both records that outlive it."""
    aid = seed(db, "rejreason", creator=admin)
    submit(client, aid)

    response = reject(client, aid)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "REJECTED", response.json()

    rejected = row(db, aid)
    assert rejected["marketplace_status"] == "REJECTED"
    assert rejected["marketplace_decision_reason"] == GOOD_REASON

    ledger = decisions(db, aid)
    assert [d["decision"] for d in ledger] == ["SUBMIT", "REJECT"]
    assert ledger[-1]["from_status"] == "PENDING_REVIEW"
    assert ledger[-1]["to_status"] == "REJECTED"
    assert ledger[-1]["reason"] == GOOD_REASON


def test_reject_then_resubmit_returns_to_pending_review(client, db, admin, owner, index_double) -> None:
    """The owner fixes the assistant and publishes again: back into the queue.

    REJECTED is a source state of SUBMIT (`TRANSITIONS`), so re-publishing must
    return the assistant to PENDING_REVIEW -- **not** straight to APPROVED,
    which is what publishing did before the gate was armed and which would let
    a non-admin owner overturn a moderator's decision by clicking Publish
    twice. (An admin/maintainer resubmit *does* now skip straight back to
    APPROVED, by design -- that is the `owner` fixture's job here, distinct
    from `admin`, who plays the moderator.)

    The ledger is the proof: three rows, in order, each naming the state it
    moved from, so the reviewer can see the whole cycle rather than a single
    current status.
    """
    aid = seed(db, "resubmit", creator=owner)
    submit(client, aid)
    assert reject(client, aid).status_code == 200
    assert row(db, aid)["marketplace_status"] == "REJECTED"

    with as_user(owner):
        again = publish(client, aid)

    assert again.status_code == 200, again.text
    assert again.json()["marketplace_status"] == "PENDING_REVIEW", again.text

    resubmitted = row(db, aid)
    assert resubmitted["marketplace_status"] == "PENDING_REVIEW"
    assert resubmitted["is_global"] is False

    ledger = decisions(db, aid)
    assert [d["decision"] for d in ledger] == ["SUBMIT", "REJECT", "SUBMIT"]
    assert ledger[-1]["from_status"] == "REJECTED"
    assert ledger[-1]["to_status"] == "PENDING_REVIEW"

    # And it is visible to the reviewer again -- asserted on this test's own id,
    # never on the size of the queue (spec §4.2).
    queue = client.get(f"{MM}/pending", params={"page_size": 100})
    assert queue.status_code == 200, queue.text
    assert aid in [item["assistant_id"] for item in queue.json()["items"]]


# ---------------------------------------------------------------------------
# §2.5 -- two admins act on the same item concurrently  ⚑ BARRED FROM PLAYWRIGHT
#
# Two browser contexts cannot reliably produce request overlap in the
# milliseconds that matter, and the assertion is about row state and status
# codes rather than pixels: in a browser a real defect would present as flake.
#
# The service-level race is already pinned in
# `service/marketplace_management/test_queue_service_concurrency.py`. What this
# test adds is the **HTTP contract**: that the loser's
# `MarketplaceConflictError` is translated into a 409 the admin UI can render,
# rather than a 500 or -- as the live audit log recorded before the CAS landed
# -- a second 200 for a decision that was silently discarded.
# ---------------------------------------------------------------------------


def _race(client: TestClient, first, second) -> list[int]:
    """Release two moderation requests from a shared barrier; return statuses."""
    barrier = threading.Barrier(2)

    def _fire(call):
        def _run() -> int:
            barrier.wait(timeout=10)
            return call().status_code

        return _run

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_fire(first)), pool.submit(_fire(second))]
        return sorted(f.result(timeout=60) for f in futures)


def test_parallel_approve_and_reject_over_http_is_one_200_and_one_409(client, db, admin, index_double) -> None:
    """Exactly one winner, and a final row no reader can misinterpret.

    Three claims, and the third is the one that made this case a requirement:

    1. `sorted(statuses) == [200, 409]` -- one decision was taken and the other
       admin was *told*.
    2. exactly one row in `mm_review_decision` -- the loser left no trace of
       having mutated anything.
    3. the final row is never `REJECTED` with `is_global = true`. That
       combination is what the live database was found in: a state no single
       operation produces, and one in which a rejected assistant is still
       serving traffic on the marketplace.
    """
    aid = seed(db, "contested", creator=admin)
    submit(client, aid)

    statuses = _race(
        client,
        lambda: approve(client, aid),
        lambda: reject(client, aid),
    )

    assert statuses == [200, 409], (
        f"parallel approve+reject returned {statuses}; two 200s mean one admin's " "decision was silently discarded"
    )

    final = row(db, aid)
    assert final["marketplace_version"] == 2, final  # SUBMIT, then exactly one decision
    assert final["marketplace_status"] in {"APPROVED", "REJECTED"}
    if final["marketplace_status"] == "REJECTED":
        assert final["is_global"] is False, "a rejected assistant is still live on the marketplace"
        assert final["is_verified"] is False, final
    else:
        assert final["is_global"] is True, final
        assert final["is_verified"] is True, final

    ledger = [d for d in decisions(db, aid) if d["decision"] != "SUBMIT"]
    assert len(ledger) == 1, f"{len(ledger)} decisions were logged for one winning transition"
    assert ledger[0]["to_status"] == final["marketplace_status"]


def test_the_loser_is_told_who_won_and_what_the_row_is_now(client, db, admin, index_double) -> None:
    """The 409 body has to be renderable, not a bare refusal.

    An admin whose click lost needs to know the entity moved, what it moved to
    and which version they would have to re-read -- otherwise the only recovery
    is a page reload and a guess.
    """
    aid = seed(db, "loserbody", creator=admin)
    submit(client, aid)
    assert approve(client, aid).status_code == 200

    conflict = reject(client, aid)

    assert conflict.status_code == 409, conflict.text
    detail = conflict.json()["detail"]
    assert detail["error"] == "conflict"
    assert detail["entity_id"] == aid
    assert detail["decision"] == "REJECT"
    assert detail["marketplace_status"] == "APPROVED"
    assert detail["marketplace_version"] == row(db, aid)["marketplace_version"]
    assert detail["allowed_from"] == ["PENDING_REVIEW"]


def test_a_second_approve_does_not_duplicate_the_audit_row(client, db, admin, index_double) -> None:
    """Approving twice is one decision, not two -- §2.5's companion case.

    Silently appending a second APPROVED row would inflate the audit log with
    decisions nobody took; the contract pinned here is 409 on the second call
    and exactly one ledger row either way.
    """
    aid = seed(db, "doubleapprove", creator=admin)
    submit(client, aid)

    first = approve(client, aid)
    second = approve(client, aid)

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    assert [d["decision"] for d in decisions(db, aid)] == ["SUBMIT", "APPROVE"]
    assert row(db, aid)["marketplace_version"] == 2


# ---------------------------------------------------------------------------
# §2.6 -- reject and withdraw must unpublish AND de-index  ⚑ BARRED FROM PLAYWRIGHT
#
# A cross-store side effect: Postgres says private, the search index says gone.
# A UI assertion here would be measuring index latency, not behaviour.
#
# See the module docstring for exactly which half of the Elasticsearch claim
# these tests genuinely cover. In short: the *call* is asserted against the
# production call path, the *document* against a double that is seeded with a
# document first, because the seeded assistants live in a throwaway database no
# Elasticsearch has ever indexed.
# ---------------------------------------------------------------------------


def _make_live(client: TestClient, engine: Engine, aid: str, double: _IndexDouble) -> None:
    """Take a seeded assistant all the way to live: approved, global, indexed."""
    submit(client, aid)
    assert approve(client, aid).status_code == 200
    live = row(engine, aid)
    assert live["is_global"] is True, live
    assert aid in double.docs, "approve did not index the assistant it published"
    double.indexed.clear()


def test_reject_of_a_still_listed_assistant_unpublishes_and_deindexes(client, db, admin, index_double) -> None:
    """Rejection is a takedown, not a relabelling (F-04).

    Before this landed, a moderator clicked Reject, the UI said
    `Assistant rejected.`, and the assistant stayed in the marketplace for
    every user: `queue_service.reject` never assigned `is_global` and made no
    index call at all. That silent failure mode is worse than a loud one, which
    is why both halves are asserted separately here.

    **On the seeded state.** §2.6 as written says "publish X so it is live,
    then reject it". The transition table added since then makes that exact
    sequence unreachable through the API -- REJECT is legal only from
    PENDING_REVIEW, and an APPROVED listing is taken down with WITHDRAW (the
    test below). The state this test seeds is the one that is *actually* out
    there: a row that is `PENDING_REVIEW` and still `is_global = true`,
    produced by the legacy `marketplace_status` default and by every row
    written before the CAS existed. That is precisely the row on which a
    rejection must still take the listing down, so it is seeded directly
    rather than pretended into existence through a transition that would 409.
    """
    aid = seed(db, "rejlisted", creator=admin, marketplace_status="PENDING_REVIEW", is_global=True)
    index_double.seed_doc(aid)

    response = reject(client, aid)

    assert response.status_code == 200, response.text

    # 1. Postgres: unpublished and unverified.
    rejected = row(db, aid)
    assert rejected["marketplace_status"] == "REJECTED"
    assert rejected["is_global"] is False, "a rejected assistant is still on the marketplace"
    assert rejected["is_verified"] is False

    # 2. The de-index call was made, on the production path, for this id.
    assert [call[0] for call in index_double.removed] == [aid], index_double.removed
    assert index_double.indexed == [], "a rejection re-indexed the assistant it took down"

    # 3. The document that existed is gone.
    assert aid not in index_double.docs

    # 4. And the response says so, rather than reporting a clean 200 over a
    #    projection that never landed (F-87).
    assert response.json()["indexing"] == {
        "action": "remove",
        "state": "OK",
        "attempts": 1,
        "error": None,
    }, response.json()


def test_withdraw_of_a_live_assistant_unpublishes_and_deindexes(client, db, admin, index_double) -> None:
    """`withdraw` had the same two omissions as `reject`; it gets the same test."""
    aid = seed(db, "wdlive", creator=admin)
    _make_live(client, db, aid, index_double)

    response = withdraw(client, aid)

    assert response.status_code == 200, response.text

    withdrawn = row(db, aid)
    assert withdrawn["marketplace_status"] == "WITHDRAWN"
    assert withdrawn["is_global"] is False
    assert withdrawn["is_verified"] is False

    assert [call[0] for call in index_double.removed] == [aid], index_double.removed
    assert aid not in index_double.docs
    assert response.json()["indexing"]["action"] == "remove"
    assert response.json()["indexing"]["state"] == "OK"


def test_approve_publishes_and_indexes_the_control_case(client, db, admin, index_double) -> None:
    """The mirror image, so the two tests above cannot pass by doing nothing.

    A de-index assertion built on a spy is only meaningful if the same spy can
    distinguish the opposite transition. Approve must index and must not
    remove.
    """
    aid = seed(db, "apprindex", creator=admin)
    submit(client, aid)

    response = approve(client, aid)

    assert response.status_code == 200, response.text
    approved = row(db, aid)
    assert approved["is_global"] is True
    assert approved["is_verified"] is True
    assert index_double.indexed == [aid], index_double.indexed
    assert index_double.removed == [], index_double.removed
    assert aid in index_double.docs
    assert response.json()["indexing"]["action"] == "index"


def test_a_failed_deindex_is_reported_rather_than_reported_as_success(
    client, db, admin, index_double, monkeypatch
) -> None:
    """F-87's delivery mechanism: "we tried" must not look like "it worked".

    219 assistants carried `is_global = true` while the index held 19
    documents, and no response, metric or status anywhere ever said so, because
    every index call site swallowed its exception and answered 200 regardless.
    The decision still stands -- the index is a projection, not the record --
    but the body has to admit what happened.
    """
    from codemie.service.platform.platform_indexing_service import PlatformIndexingService

    aid = seed(db, "deindexfail", creator=admin)
    _make_live(client, db, aid, index_double)

    def _explode(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("elasticsearch is unreachable")

    monkeypatch.setattr(PlatformIndexingService, "remove_single_assistant", staticmethod(_explode))

    response = withdraw(client, aid)

    assert response.status_code == 200, response.text
    indexing = response.json()["indexing"]
    assert indexing["state"] == "FAILED", indexing
    assert indexing["attempts"] == 2, indexing
    assert "unreachable" in (indexing["error"] or ""), indexing
    # The decision itself is unaffected: the row is the record of truth.
    assert row(db, aid)["marketplace_status"] == "WITHDRAWN"
    assert row(db, aid)["is_global"] is False


# ---------------------------------------------------------------------------
# §2.7 -- moderation is denied server-side  ⚑ BARRED FROM PLAYWRIGHT
#
# The requirement is explicitly "assert via a direct API call, not just that
# the button is hidden". §2.7 keeps exactly one Playwright test -- the route is
# not visible to a non-admin -- and it is labelled UX-only there so nobody
# mistakes it for this.
#
# `authenticate` and `admin_access_only` are declared once, at the router
# (`dependencies=[...]`), so these are expected to pass. They are worth having
# for exactly that reason: a future route registered outside this router, or a
# refactor that moves an endpoint, loses the gate with no local signal.
# ---------------------------------------------------------------------------

MODERATION_ROUTES = ("approve", "reject", "force-publish", "withdraw")
MODERATION_BODIES = {
    "approve": {},
    "reject": {"reason": GOOD_REASON},
    "force-publish": {"override_notice": GOOD_REASON},
    "withdraw": {},
}


@pytest.mark.parametrize("route", MODERATION_ROUTES)
def test_a_non_admin_is_403_on_every_moderation_route(client, db, admin, owner, index_double, route: str) -> None:
    """403 on all four routes -- and, critically, no side effect.

    A 403 that had already mutated the row would be a worse bug than a 200,
    because it would be invisible: the caller is told no and the entity moved
    anyway.
    """
    aid = seed(db, f"authz-{route}", creator=admin)
    submit(client, aid)
    before = row(db, aid)
    before_decisions = len(decisions(db, aid))

    with as_user(owner):
        response = client.post(f"{MM}/pending/{aid}/{route}", json=MODERATION_BODIES[route])

    assert response.status_code == 403, response.text
    assert row(db, aid) == before
    assert len(decisions(db, aid)) == before_decisions


@pytest.mark.parametrize("route", MODERATION_ROUTES)
def test_an_unauthenticated_caller_is_401_on_every_moderation_route(
    client, app, db, admin, index_double, route: str
) -> None:
    """No token at all -> 401, with the same no-side-effect requirement.

    No dependency override is installed for this client: the real
    authentication stack answers, which is the only way this assertion means
    anything.
    """
    aid = seed(db, f"anon-{route}", creator=admin)
    submit(client, aid)
    before = row(db, aid)
    before_decisions = len(decisions(db, aid))

    with as_anonymous(app):
        response = client.post(f"{MM}/pending/{aid}/{route}", json=MODERATION_BODIES[route])

    assert response.status_code == 401, response.text
    assert row(db, aid) == before
    assert len(decisions(db, aid)) == before_decisions


@pytest.mark.parametrize("route", MODERATION_ROUTES)
def test_the_recorded_actor_is_the_principal_not_a_body_field(
    client, db, admin, index_double, activity_events_enabled, route: str
) -> None:
    """§2.7c -- the audit trail's author cannot be chosen by the caller (F-14).

    `actor` used to be a plain request-body field passed straight through to
    the audit row, so any admin -- or anyone who reached an admin session --
    could sign a takedown as somebody else. An audit trail whose author is the
    person being audited is worse than no audit trail.

    6.5 removed the field. This test is what stops it coming back: it sends one
    anyway, on **every** moderation route, and requires the spoofed string to
    appear in no persisted trace of the decision. Because the models ignore
    unknown fields rather than rejecting them, a re-introduced `actor` would be
    silently honoured again -- so the assertion is on the stored row, not on
    the status code.
    """
    import json as _json

    aid = seed(db, f"spoof-{route}", creator=admin)
    submit(client, aid)
    body = {**MODERATION_BODIES[route], "actor": SPOOFED_ACTOR, "decided_by_id": SPOOFED_ACTOR}

    response = client.post(f"{MM}/pending/{aid}/{route}", json=body)

    assert response.status_code == 200, response.text

    ledger = decisions(db, aid)
    assert ledger[-1]["decided_by_id"] == admin.id, ledger[-1]
    assert ledger[-1]["decided_by_email"] == admin.email, ledger[-1]
    assert SPOOFED_ACTOR not in _json.dumps(
        ledger, default=str
    ), f"the request body's actor leaked into the decision ledger: {ledger}"

    assert row(db, aid)["marketplace_decided_by"] == admin.id

    feed = events(db, aid)
    assert feed, (
        "the decision recorded no marketplace activity event. NOTE: "
        "activity_event_repository.insert no-ops when ACTIVITY_EVENTS_ENABLED is "
        "False (its default), which is why this test takes activity_events_enabled."
    )
    assert feed[-1]["actor_id"] == admin.id, feed[-1]
    assert SPOOFED_ACTOR not in _json.dumps(
        feed, default=str
    ), f"the request body's actor leaked into the activity feed: {feed}"


def test_an_owner_may_submit_for_review_but_cannot_reach_approved(client, db, owner, index_double) -> None:
    """§2.7b -- the asymmetry between publish and moderation.

    `POST /marketplace/publish` lives on a different router and is gated only
    by write access on the assistant, while the docstring long claimed
    administrators only (F-02). With the gate armed that is no longer a hole:
    the owner may put their own work *in front of* a reviewer, and that is all
    -- reaching APPROVED still requires the admin-only moderation router.
    """
    aid = seed(db, "ownersubmit", creator=owner)

    with as_user(owner):
        response = publish(client, aid)

    assert response.status_code == 200, response.text
    submitted = row(db, aid)
    assert submitted["marketplace_status"] == "PENDING_REVIEW"
    assert submitted["is_global"] is False, "a non-admin publish reached the marketplace directly"

    # And the owner cannot then approve their own submission.
    with as_user(owner):
        denied = client.post(f"{MM}/pending/{aid}/approve", json={})
    assert denied.status_code == 403, denied.text
    assert row(db, aid)["marketplace_status"] == "PENDING_REVIEW"


# ---------------------------------------------------------------------------
# §2.8b -- `/overview` emits `cleanup_reasons` (F-06)
#
# This is the user's originally reported bug, and it is here rather than in the
# browser because the fix's acceptance criterion is an **API key**: the
# producer never emitted `cleanup_reasons`, the TypeScript type marked it
# optional, the consumer defaulted to `{}`, and the Cleanup reason dropdown
# rendered exactly one option forever. No error, no warning, no failing
# type-check. The vitest half asserts `REASON_OPTIONS` derives from the prop;
# the Playwright half asserts the dropdown has more than one option. Neither
# can see a missing key on the wire.
# ---------------------------------------------------------------------------


@pytest.fixture
def audit_cache(monkeypatch: pytest.MonkeyPatch):
    """A fresh `AuditCache` in place of the module singleton.

    The singleton carries per-kind in-process state -- results, run ids, the
    running flag -- that would otherwise leak between tests in either
    direction: a stale cached result would let an Overview assertion pass
    without an audit, and a `_running` flag left set would wedge every later
    audit test.
    """
    from codemie.rest_api.routers import marketplace_management as mm
    from codemie.service.marketplace_management.audit_service import AuditCache

    cache = AuditCache()
    monkeypatch.setattr(mm, "AUDIT_CACHE", cache)
    return cache


def _run_audit(cache) -> Any:
    """One synchronous, hermetic audit pass over the seeded corpus.

    `include_es_similarity=False` keeps it off Elasticsearch: the gate and
    verdict half of the engine is pure, and it is the half every assertion here
    is about.
    """
    from codemie.service.marketplace_management.models import EntityKind

    return cache.run_audit(EntityKind.ASSISTANT, include_es_similarity=False)


def test_overview_emits_cleanup_reasons_from_the_persisted_verdicts(client, db, admin, audit_cache) -> None:
    """After a run that flagged something, `/overview` names *why* it flagged it.

    The assertion is deliberately not "the map is non-empty": it is that the
    keys the API publishes **equal** the distinct reasons in
    `mm_audit_verdict` for the run being served. A superset assertion is how
    the original bug survived -- a missing key can never fail one.
    """
    flagged = seed(
        db,
        "reasoninvalid",
        creator=admin,
        name="Bot",  # name_length: CRITICAL
        description="A little helper for the team, sort of.",  # description_length: CRITICAL
        marketplace_status="APPROVED",
        is_global=True,
    )
    healthy = seed(db, "reasonok", creator=admin, marketplace_status="APPROVED", is_global=True)

    _run_audit(audit_cache)

    response = client.get(OVERVIEW)
    assert response.status_code == 200, response.text
    body = response.json()

    assert "cleanup_reasons" in body, sorted(body)
    with db.connect() as conn:
        run_id = conn.execute(
            text(
                "select id from codemie.mm_audit_run where entity_kind = 'ASSISTANT' "
                " order by started_ts desc limit 1"
            )
        ).scalar_one()
        persisted = {
            r[0]: r[1]
            for r in conn.execute(
                text(
                    "select cleanup_reason, count(*) from codemie.mm_audit_verdict "
                    " where audit_run_id = :r and cleanup_reason is not null group by 1"
                ),
                {"r": run_id},
            )
        }
        flagged_reason = conn.execute(
            text(
                "select cleanup_reason from codemie.mm_audit_verdict " " where audit_run_id = :r and assistant_id = :a"
            ),
            {"r": run_id, "a": flagged},
        ).scalar_one()

    assert persisted, "the audit persisted no reason at all; the fixture proves nothing"
    assert (
        body["cleanup_reasons"] == persisted
    ), f"/overview published {body['cleanup_reasons']} while the run holds {persisted}"
    # ...and the reason belongs to the assistant this test broke on purpose.
    assert flagged_reason == "invalid", flagged_reason
    assert body["cleanup_reasons"]["invalid"] >= 1, body["cleanup_reasons"]
    assert healthy != flagged  # the healthy row exists so "everything is flagged" cannot pass


# ---------------------------------------------------------------------------
# §2.11 -- audit re-run, idempotency and the lock  ⚑ BARRED FROM PLAYWRIGHT
#
# A run is minutes long and the claim is deployment-wide. A browser test would
# either time out or hang CI, and would be asserting on a spinner rather than
# on the coordination that actually matters.
# ---------------------------------------------------------------------------


def test_a_second_audit_run_is_409_and_does_not_launch_a_competing_run(client, db, admin, audit_cache) -> None:
    """Two clicks, one run — enforced in Postgres, not in a process variable.

    The guard this replaces was a dict on a module singleton: it could only see
    runs started by *this* process, and a rolling deploy guarantees pod
    overlap, so two admins on two pods each started a full audit over the same
    catalogue and both wrote snapshots (F-17). Worse, the collision was only
    detected at the very end, when the second run's persist hit the unique
    index — after it had already spent the minutes.

    The claim is now the `INSERT` and `uq_mm_audit_run_active` is the arbiter,
    so the second caller is refused **synchronously**, with the in-flight run's
    id so it can poll that one instead of believing it started something.
    """
    seed(db, "auditlock", creator=admin, marketplace_status="APPROVED", is_global=True)
    release = threading.Event()
    entered = threading.Event()

    def _blocking_run(kind, **kwargs):
        entered.set()
        release.wait(timeout=30)
        return None

    audit_cache.run_audit = _blocking_run  # type: ignore[method-assign]

    try:
        first = client.post(AUDIT_RUN)
        assert first.status_code == 202, first.text
        assert entered.wait(timeout=10), "the audit worker never started"

        second = client.post(AUDIT_RUN)
    finally:
        release.set()

    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["error"] == "audit_already_running", detail
    assert detail["run_id"] == first.json()["run_id"], detail

    with db.connect() as conn:
        runs = conn.execute(
            text("select count(*) from codemie.mm_audit_run where entity_kind = 'ASSISTANT'")
        ).scalar_one()
    assert runs == 1, f"{runs} run rows were opened for one audit; the second call claimed one too"


def test_overview_stays_under_a_second_while_an_audit_is_running(client, db, admin, audit_cache) -> None:
    """F-08: a running audit must not take the admin UI down with it.

    Measured on the live stack before the fix: `GET /overview` blocked for the
    full 250 s of a run, because the audit held `AuditCache._lock` across the
    whole thing and every read reached for the same lock. The SLO is encoded
    here as the assertion, so the fix has an acceptance criterion rather than a
    recollection.

    The stub deliberately **holds the audit lock** for the duration -- that is
    the F-08 condition. A read that took the lock would block for the full 30 s
    and this test would fail; a read that does not, answers immediately.
    """
    seed(db, "auditslo", creator=admin, marketplace_status="APPROVED", is_global=True)
    release = threading.Event()
    holding = threading.Event()

    def _lock_holding_run(kind, **kwargs):
        with audit_cache._lock:
            holding.set()
            release.wait(timeout=30)
        return None

    audit_cache.run_audit = _lock_holding_run  # type: ignore[method-assign]

    try:
        started = client.post(AUDIT_RUN)
        assert started.status_code == 202, started.text
        assert holding.wait(timeout=10), "the audit worker never took the lock"

        began = time.monotonic()
        overview = client.get(OVERVIEW)
        elapsed = time.monotonic() - began
    finally:
        release.set()

    assert overview.status_code == 200, overview.text
    assert elapsed < 1.0, (
        f"GET /overview took {elapsed:.2f}s while an audit held the audit lock; " "reads must not queue behind a run"
    )


def test_a_rerun_is_idempotent_and_leaves_the_status_clean(client, db, admin, audit_cache) -> None:
    """Two runs over an unchanged corpus agree, and neither wedges the button.

    Three claims:

    * the bucket distribution and the per-rule hits are identical between runs
      -- an audit that disagrees with itself cannot be used to decide anything;
    * each run gets its **own** `mm_audit_run` row, so a re-run is traceable
      rather than overwriting its predecessor;
    * `(run_id, entity_id)` is unique in `mm_audit_verdict`, so a re-run cannot
      double-count an assistant into its own totals.

    Plus the operational half: `/audit/status` afterwards reports
    `is_running: false` and `error: null`, so a crashed or finished run cannot
    leave the Run audit button spinning forever.
    """
    seed(db, "rerun-a", creator=admin, marketplace_status="APPROVED", is_global=True)
    seed(db, "rerun-b", creator=admin, name="Bot", description="tiny", marketplace_status="APPROVED", is_global=True)

    first = _run_audit(audit_cache)
    second = _run_audit(audit_cache)

    assert first.bucket_distribution == second.bucket_distribution, (
        f"the same corpus bucketed differently on a re-run: "
        f"{first.bucket_distribution} then {second.bucket_distribution}"
    )
    assert {k: sorted(v) for k, v in first.per_rule_hits.items()} == {
        k: sorted(v) for k, v in second.per_rule_hits.items()
    }

    with db.connect() as conn:
        runs = [
            dict(r)
            for r in conn.execute(
                text(
                    "select id, status from codemie.mm_audit_run "
                    " where entity_kind = 'ASSISTANT' order by started_ts"
                )
            ).mappings()
        ]
        duplicates = conn.execute(
            text(
                "select audit_run_id, assistant_id, count(*) from codemie.mm_audit_verdict "
                " group by 1, 2 having count(*) > 1"
            )
        ).all()

    assert len(runs) == 2, runs
    assert {r["id"] for r in runs} == {runs[0]["id"], runs[1]["id"]}
    assert [r["status"] for r in runs] == ["SUCCEEDED", "SUCCEEDED"], runs
    assert duplicates == [], duplicates

    status_response = client.get(AUDIT_STATUS)
    assert status_response.status_code == 200, status_response.text
    status_body = status_response.json()
    assert status_body["is_running"] is False, status_body
    assert status_body["error"] is None, status_body


def test_an_audit_run_does_not_mutate_marketplace_status(client, db, admin, audit_cache) -> None:
    """§2.11d -- the audit is advisory. It flags; it never decides.

    A published assistant that later fails a rule must appear in the cleanup
    view and stay exactly as published, so that unpublishing remains a human
    decision with a decision row behind it. Cheap to assert, and it protects
    the whole review queue from a future audit-engine change that decides to be
    helpful.

    Snapshotted over this test's own ids, per spec §4.2 -- never over a
    `group by marketplace_status` of the whole table.
    """
    published = seed(
        db,
        "auditkeep",
        creator=admin,
        name="Bot",
        description="tiny",  # will fail two CRITICAL gates
        marketplace_status="APPROVED",
        is_global=True,
    )
    queued = seed(db, "auditqueued", creator=admin)
    submit(client, queued)

    before = {aid: row(db, aid) for aid in (published, queued)}

    _run_audit(audit_cache)

    after = {aid: row(db, aid) for aid in (published, queued)}
    assert after == before, "the audit changed a marketplace decision; it is advisory and must only flag"

    # ...and it did flag the broken one, so "nothing changed" is not because
    # nothing happened.
    cleanup = client.get(f"{MM}/cleanup", params={"page_size": 100})
    assert cleanup.status_code == 200, cleanup.text
    flagged = {item["id"]: item for item in cleanup.json()["items"]}
    assert published in flagged, sorted(flagged)
    # `cleanup_reason`, flat on the row -- F-68: the nested `verdict.reason` is
    # deprecated and the flat key is the one the table reads.
    assert flagged[published]["cleanup_reason"] == "invalid", flagged[published]
