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

"""Router tests for the Marketplace Management admin API.

These drive the endpoints through ``TestClient`` rather than importing the
handler functions and calling them directly (F-41). Direct calls skip the whole
FastAPI layer -- routing and route ordering, path/query coercion, the
``resolve_kind`` dependency, the router-level auth dependencies, the declared
``status_code``, and response-model serialisation -- so a test could pass while
the deployed endpoint 404s, 422s or 500s. Everything below goes over HTTP.

Only the two collaborators the handlers delegate to are mocked
(``AUDIT_CACHE`` and ``get_adapter``); the router itself runs for real.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from codemie.rest_api.routers import marketplace_management as mm
from codemie.rest_api.security.user import User
from codemie.service.marketplace_management.models import (
    AssessmentDimension,
    AssessmentFields,
    AssessmentResponse,
    Bucket,
)
from codemie.service.marketplace_management.validator.models import (
    Bucket as ValidatorBucket,
    Finding,
    Severity,
    Verdict,
    VerdictKind,
)

# ---------------------------------------------------------------------------
# Module path shorthand used by every patch
# ---------------------------------------------------------------------------

_MOD = "codemie.rest_api.routers.marketplace_management"

# Kinds appear in the URL as their plural noun -- `resolve_kind` rejects
# anything else with a 400, which is part of what these tests now exercise.
_BASE = "/v1/marketplace-management/entities/assistants"


# ---------------------------------------------------------------------------
# App under test
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app() -> FastAPI:
    """A bare app carrying only the router under test.

    Deliberately not `codemie.rest_api.main.app`: this keeps the surface to the
    routes being asserted, so a failure here is unambiguously this router's.
    """
    from codemie.core.exceptions import ExtendedHTTPException
    from codemie.rest_api.main import extended_http_exception_handler

    test_app = FastAPI()
    # The real app's handler: the approve gate now raises ExtendedHTTPException,
    # the same structured refusal publish returns.
    test_app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
    test_app.include_router(mm.router)
    return test_app


@pytest.fixture(scope="module")
def admin_user() -> User:
    return User(id="admin-1", username="admin", name="Admin User")


@pytest.fixture(autouse=True)
def override_auth(app: FastAPI, admin_user: User) -> Generator[None, None, None]:
    """Satisfy the two router-level dependencies.

    `authenticate` and `admin_access_only` are declared on the APIRouter, so
    every route below is admin-only. The 401/403 paths are the contract suite's
    job; here we assume an authenticated admin and assert behaviour.
    """
    app.dependency_overrides[mm.authenticate] = lambda: admin_user
    app.dependency_overrides[mm.admin_access_only] = lambda: None
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client(app: FastAPI) -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Shared factory helpers
# ---------------------------------------------------------------------------


def _make_audit_result(bucket_distribution: dict | None = None) -> MagicMock:
    """Minimal MagicMock that looks like an AuditResult."""
    result = MagicMock()
    result.total = 3
    result.took_seconds = 0.05
    result.bucket_distribution = bucket_distribution or {"VERIFIED": 1, "COMMUNITY": 1, "DELETE": 1}
    result.bucket_usage_retention = {}
    result.per_rule_hits = {}
    result.purpose_cluster_id = {}
    result.cluster_size = {}
    result.cluster_canonical = {}
    result.verdicts = {}
    result.usage_tiers = {}
    result.gate_results = {}
    result.similarity_results = {}
    return result


def _make_record(
    record_id: str,
    *,
    mcp_servers: list[str] | None = None,
    categories: list[str] | None = None,
    unique_users_count: int = 0,
) -> MagicMock:
    rec = MagicMock()
    rec.id = record_id
    rec.name = f"assistant-{record_id}"
    rec.description = "A test assistant."
    rec.creator = "test-user"
    rec.mcp_servers = mcp_servers or []
    rec.categories = categories or []
    rec.unique_users_count = unique_users_count
    # Pinned, not left to MagicMock: an auto-attribute answers `__int__` with
    # 1, so a row that silently lost this value would still look plausible.
    rec.unique_likes_count = 0
    return rec


def _make_verdict(bucket_value: str) -> Verdict:
    """A real `Verdict`, not a MagicMock.

    `GET /cleanup` declares `response_model=CleanupResponse`, and a MagicMock
    answers `.kind.value` with another MagicMock — which is both a 500 and,
    worse, a fixture that could never tell a correct row from a wrong one.
    """
    return Verdict(
        kind=VerdictKind.CANDIDATE_FOR_CLEANUP,
        bucket=ValidatorBucket(bucket_value),
        bucket_reason="test-reason",
    )


# ---------------------------------------------------------------------------
# 1. GET /entities/{kind}/overview
# ---------------------------------------------------------------------------


class TestGetOverview:
    # `_pending_kpi` counts the review queue in Postgres (EXECUTION-PLAN 7.3).
    # It is stubbed for the same reason `get_adapter` is: this suite asserts the
    # bucket rollup, and `pytest.ini` points `PG_URL` at a dead port. The KPI's
    # own behaviour is held by
    # `test_marketplace_management_contract.py::test_overview_emits_pending_block`,
    # which runs it against a real database.
    # `buckets.COMMUNITY` and `buckets.DELETE` no longer come from the cached
    # snapshot: both are read live via `audit_reads.cleanup_total` (the same
    # per-bucket query `GET /cleanup` runs) so the Cleanup queue tab badge
    # (`buckets.COMMUNITY + buckets.DELETE`) cannot disagree with what the
    # queue itself lists once an assistant has been unpublished since the
    # audit ran. `audit_reads.cleanup_total` is therefore stubbed here too,
    # rather than left to fall through to the mocked-engine session, which
    # would silently answer with a MagicMock coerced to `1`.
    @patch(f"{_MOD}.audit_reads.cleanup_total")
    @patch(
        f"{_MOD}._pending_kpi",
        return_value={"count": 0, "waiting_over_7d": 0, "avg_wait_days": 0.0},
    )
    @patch(f"{_MOD}._last_run_meta", return_value=(None, False))
    @patch(f"{_MOD}.get_adapter")
    @patch(f"{_MOD}.AUDIT_CACHE")
    def test_overview_returns_the_three_buckets(
        self,
        mock_cache,
        mock_get_adapter,
        _mock_last_run,
        _mock_pending,
        mock_cleanup_total,
        client: TestClient,
    ):
        """GET /overview returns 'buckets' containing all three bucket keys with correct counts."""
        mock_cache.get_or_run.return_value = _make_audit_result({"VERIFIED": 2, "COMMUNITY": 5, "DELETE": 3})
        mock_cache.latest_run_id.return_value = "run-abc"
        mock_get_adapter.return_value.list_records.return_value = []
        # All three buckets are refreshed live now (VERIFIED since EPMCDME-14234).
        mock_cleanup_total.side_effect = lambda session, run_id, **filters: {  # noqa: ARG005
            "VERIFIED": 2,
            "COMMUNITY": 5,
            "DELETE": 3,
        }[filters["bucket"]]

        response = client.get(f"{_BASE}/overview")

        assert response.status_code == 200
        body = response.json()
        assert "buckets" in body
        assert set(body["buckets"].keys()) == {"VERIFIED", "COMMUNITY", "DELETE"}
        assert body["buckets"]["VERIFIED"] == 2
        assert body["buckets"]["COMMUNITY"] == 5
        assert body["buckets"]["DELETE"] == 3
        assert body["latest_audit_run_id"] == "run-abc"

    @patch(f"{_MOD}._last_run_meta", return_value=(None, False))
    @patch(f"{_MOD}.get_adapter")
    @patch(f"{_MOD}.AUDIT_CACHE")
    def test_an_unknown_kind_is_a_400_not_a_default(
        self, mock_cache, mock_get_adapter, _mock_last_run, client: TestClient
    ):
        """`resolve_kind` is a route dependency, so a misspelt segment must 400.

        A direct handler call cannot see this at all -- it takes an already-built
        EntityKind. Over HTTP a regression here would silently serve assistant
        data on a `/entities/assistnats/` URL.
        """
        mock_cache.get_or_run.return_value = _make_audit_result()
        mock_get_adapter.return_value.list_records.return_value = []

        response = client.get("/v1/marketplace-management/entities/assistnats/overview")

        assert response.status_code == 400
        assert "assistnats" in response.json()["detail"]


# ---------------------------------------------------------------------------
# 2. GET /entities/{kind}/facets  and  3. GET /entities/{kind}/cleanup
# ---------------------------------------------------------------------------
#
# Both endpoints are served from SQL now (EXECUTION-PLAN 5.4), so the seam these
# unit tests drive is `audit_read_repository`, not `AUDIT_CACHE`. The fake below
# is a real (tiny) implementation of that repository rather than a `MagicMock`:
# the property worth asserting at this level is that the handler *translates the
# query string into the right filter arguments* -- that `?bucket=` clears the
# DELETE default, that `?rule=` narrows -- and a MagicMock returns the same rows
# whatever it is asked for, so it could not tell a correct translation from a
# wrong one. The real-database contract lives in
# `test_marketplace_management_contract.py`.


def _verdict_row(rid: str, **overrides) -> dict:
    """One `mm_audit_verdict` row joined to its assistant, as the SQL returns it."""
    row = {
        "id": rid,
        "name": f"assistant-{rid}",
        "creator": "test-user",
        "categories": [],
        "unique_users_count": 0,
        "unique_likes_count": 0,
        "usage_tier": None,
        "bucket": None,
        "bucket_reason": "test-reason",
        "verdict_kind": "CANDIDATE_FOR_CLEANUP",
        "cleanup_reason": None,
        "canonical_id": None,
        "canonical_name": None,
        "canonical_users": None,
        "match_score": None,
        "detail": None,
        "critical_count": 0,
        "optional_count": 0,
    }
    row.update(overrides)
    return row


class _FakeReads:
    """In-memory stand-in for `audit_read_repository`, filters and all."""

    def __init__(self, rows: list[dict], *, rule_hits: dict | None = None, run_id: str = "run-1"):
        self._rows = rows
        self._rule_hits = rule_hits or {}
        self._run_id = run_id
        self.mcps: list[dict] = []
        self.categories: list[dict] = []
        self.clusters: list[dict] = []
        self.facets: dict = {}
        self.severities: dict = {}

    # -- run pinning --
    def latest_succeeded_run_id(self, session, kind):  # noqa: ARG002
        return self._run_id

    # -- cleanup --
    def _matching(self, **filters) -> list[dict]:
        out = []
        for row in self._rows:
            if filters.get("bucket") and row["bucket"] != filters["bucket"]:
                continue
            if filters.get("reason") and row["cleanup_reason"] != filters["reason"]:
                continue
            if filters.get("tier") and row["usage_tier"] != filters["tier"]:
                continue
            if filters.get("cluster_id") is not None:
                continue
            rule = filters.get("rule")
            if rule and row["id"] not in self._rule_hits.get(rule, []):
                continue
            if filters.get("q") and filters["q"].lower() not in row["name"].lower():
                continue
            if filters.get("mcp") or filters.get("category"):
                continue
            out.append(row)
        return out

    def cleanup_total(self, session, run_id, **filters):  # noqa: ARG002
        return len(self._matching(**filters))

    def cleanup_page(self, session, run_id, *, page, page_size, **filters):  # noqa: ARG002
        rows = self._matching(**filters)
        start = max(0, (page - 1) * page_size)
        return rows[start : start + page_size]

    def top_similarity_for(self, session, run_id, ids):  # noqa: ARG002
        return {}

    # -- facets --
    def catalogue_facet(self, session, field, *, limit=None):  # noqa: ARG002
        values = self.mcps if field == "mcps" else self.categories
        return values[:limit] if limit else values

    def run_facets(self, session, run_id):  # noqa: ARG002
        return self.facets

    def cluster_facets(self, session, run_id):  # noqa: ARG002
        return self.clusters

    def rule_severities(self, session, run_id):  # noqa: ARG002
        return self.severities


@contextmanager
def _null_session():
    """`get_session()` stand-in — `_FakeReads` ignores the session entirely."""
    yield None


@pytest.fixture
def fake_reads(monkeypatch: pytest.MonkeyPatch):
    """Install a `_FakeReads` in place of the SQL repository."""

    def _install(reads: "_FakeReads") -> "_FakeReads":
        monkeypatch.setattr(mm, "audit_reads", reads)
        monkeypatch.setattr(mm, "get_session", _null_session)
        return reads

    return _install


class TestGetFacets:
    # The documented facet vocabulary the Cleanup filter bar needs. Anything
    # missing here means a dropdown the UI cannot populate.
    DOCUMENTED_FACET_KEYS = {
        "mcps",
        "categories",
        "clusters",
        "reasons",
        "tiers",
        "buckets",
        "rules",
    }

    @staticmethod
    def _reads() -> "_FakeReads":
        reads = _FakeReads([])
        reads.mcps = [
            {"value": "git", "count": 2, "total_users": 0},
            {"value": "slack", "count": 1, "total_users": 0},
        ]
        reads.categories = [
            {"value": "dev", "count": 1, "total_users": 0},
            {"value": "productivity", "count": 1, "total_users": 0},
        ]
        # cluster 0 has 3 members (included); a 1-member cluster never reaches
        # the handler because the SQL filters it with `HAVING count(*) >= 2`.
        reads.clusters = [{"cluster_id": 0, "size": 3, "canonical_name": "rec-a"}]
        return reads

    def test_facets_returns_mcps_categories_clusters(self, fake_reads, client: TestClient):
        """GET /facets returns EXACTLY the documented facet key set.

        Exact equality, not a `>=` subset check: a subset assertion passes while
        four of the seven documented facets are absent, which is precisely how
        F-33 survived (the old assertion was
        `set(resp.keys()) >= {"mcps", "categories", "clusters"}`).
        """
        fake_reads(self._reads())

        response = client.get(f"{_BASE}/facets")

        assert response.status_code == 200
        assert set(response.json().keys()) == self.DOCUMENTED_FACET_KEYS

    def test_facets_mcp_category_and_cluster_values(self, fake_reads, client: TestClient):
        """The three original facets are still aggregated correctly.

        Split out of the key-set assertion above so a regression names whether
        the facet vanished or its contents drifted.
        """
        fake_reads(self._reads())

        response = client.get(f"{_BASE}/facets")

        assert response.status_code == 200
        body = response.json()
        mcp_values = [item["value"] for item in body["mcps"]]
        assert "git" in mcp_values
        assert "slack" in mcp_values
        cat_values = [item["value"] for item in body["categories"]]
        assert "dev" in cat_values
        assert "productivity" in cat_values
        assert len(body["clusters"]) == 1
        assert body["clusters"][0]["cluster_id"] == 0


def _two_verdict_rows() -> list[dict]:
    """One DELETE-bucket row and one COMMUNITY-bucket row."""
    return [
        _verdict_row("rec-del", bucket="DELETE"),
        _verdict_row("rec-com", bucket="COMMUNITY"),
    ]


class TestGetCleanup:
    def test_cleanup_default_bucket_is_delete(self, fake_reads, client: TestClient):
        """The bucket default is applied by FastAPI's Query(default="DELETE").

        Sending no query string at all is the assertion that matters: a direct
        handler call had to pass every parameter explicitly and so could never
        catch a changed default.
        """
        fake_reads(_FakeReads(_two_verdict_rows()))

        response = client.get(f"{_BASE}/cleanup")

        assert response.status_code == 200
        item_ids = [item["id"] for item in response.json()["items"]]
        assert "rec-del" in item_ids
        assert "rec-com" not in item_ids

    def test_cleanup_empty_bucket_returns_all(self, fake_reads, client: TestClient):
        """`?bucket=` disables bucket filtering so all records are returned."""
        fake_reads(_FakeReads(_two_verdict_rows()))

        response = client.get(f"{_BASE}/cleanup", params={"bucket": ""})

        assert response.status_code == 200
        item_ids = [item["id"] for item in response.json()["items"]]
        assert "rec-del" in item_ids
        assert "rec-com" in item_ids

    def test_cleanup_rule_filter_returns_only_entities_failing_that_check(self, fake_reads, client: TestClient):
        """`rule` narrows to the entities counted by the Overview's
        "Failed quality checks" table, so a row there can drill through."""
        fake_reads(_FakeReads(_two_verdict_rows(), rule_hits={"description_length": ["rec-com"]}))

        response = client.get(f"{_BASE}/cleanup", params={"bucket": "", "rule": "description_length"})

        assert response.status_code == 200
        assert [item["id"] for item in response.json()["items"]] == ["rec-com"]

    def test_cleanup_unknown_rule_is_422(self, fake_reads, client: TestClient):
        """An unknown quality-check id is rejected, not silently empty (F-48).

        This assertion is inverted from what it used to be. `rule` was
        `Optional[str]`, so `?rule=no_such_check` matched nothing and answered
        `200 {"items": []}` -- indistinguishable from "every assistant passes
        that check". Typing it `GateRule` makes the mistake nameable at the
        edge, which is the whole point of the step; the old 200 was the bug,
        not the contract.
        """
        fake_reads(_FakeReads(_two_verdict_rows()))

        response = client.get(f"{_BASE}/cleanup", params={"bucket": "", "rule": "no_such_check"})

        assert response.status_code == 422, response.text

    def test_cleanup_rejects_an_out_of_range_page_size(self, fake_reads, client: TestClient):
        """`page_size` is declared `ge=1, le=200`. Only the HTTP layer enforces
        that -- a direct call would happily accept 10_000."""
        fake_reads(_FakeReads(_two_verdict_rows()))

        response = client.get(f"{_BASE}/cleanup", params={"page_size": 10_000})

        assert response.status_code == 422

    def test_get_cleanup_order_by_valid(self, fake_reads, client: TestClient):
        """A recognised `order_by` value returns HTTP 200 (EPMCDME-15333)."""
        fake_reads(_FakeReads(_two_verdict_rows()))

        response = client.get(f"{_BASE}/cleanup", params={"bucket": "", "order_by": "users_asc"})

        assert response.status_code == 200

    def test_get_cleanup_order_by_unknown_returns_200(self, fake_reads, client: TestClient):
        """An unrecognised `order_by` string MUST return 200, not 422 (EPMCDME-15333).

        The `order_by` param uses `Optional[str]` + try/except coercion rather
        than a direct enum type, so an unknown value silently falls back to the
        default ordering instead of rejecting the request.
        """
        fake_reads(_FakeReads(_two_verdict_rows()))

        response = client.get(f"{_BASE}/cleanup", params={"bucket": "", "order_by": "invalid_value"})

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 4. POST /entities/{kind}/pending/{eid}/approve
# ---------------------------------------------------------------------------


def _outcome(entity_id: str, status: str = "APPROVED", *, replayed: bool = False):
    """What `queue_service` returns since EXECUTION-PLAN 6.1.

    The decision no longer returns `None` and lets the handler invent a body:
    the body is built inside the transaction and stored on the
    `mm_review_decision` row, so a replay can return it byte for byte
    (design-persistence.md 4.1).
    """
    from codemie.service.marketplace_management.queue_service import DecisionOutcome

    return DecisionOutcome(
        result={
            "ok": True,
            "entity_kind": "ASSISTANT",
            "entity_id": entity_id,
            "status": status,
        },
        replayed=replayed,
    )


class TestApprovePending:
    @patch(f"{_MOD}._queue_service")
    def test_pending_approve(self, mock_qs, client: TestClient):
        """POST /pending/{eid}/approve calls queue_service.approve with the eid from the path."""
        mock_qs.approve.return_value = _outcome("asst-42")

        response = client.post(f"{_BASE}/pending/asst-42/approve", json={"actor": "someone.else@example.com"})

        assert response.status_code == 200
        # The decider is taken from the authenticated user, never from the body,
        # and the replay token is threaded through from the header. The body's
        # `actor` is an ignored leftover (EXECUTION-PLAN 6.5) -- sending one that
        # names a different person changes nothing.
        kwargs = mock_qs.approve.call_args.kwargs
        assert kwargs["assistant_id"] == "asst-42"
        assert kwargs["actor"] == "admin", (
            "the audit actor must be the authenticated user (username 'admin' here), "
            "not the string in the request body"
        )
        assert kwargs["idempotency_key"] is None
        assert (
            kwargs["decided_by_id"] == "admin-1"
        ), "the decider must come from the authenticated user, not the request body"
        body = response.json()
        assert body["ok"] is True
        # The handler returns the kind-neutral `entity_id`, not `assistant_id`.
        assert body["entity_id"] == "asst-42"
        assert body["status"] == "APPROVED"
        assert "Idempotency-Replayed" not in response.headers

    @patch(f"{_MOD}._queue_service")
    def test_pending_approve_derives_the_actor_from_the_token(self, mock_qs, client: TestClient):
        """An empty body still names the caller: the actor comes from `authenticate`.

        `ApproveIn` no longer has an `actor` field at all, so there is no default
        to fall back to -- `_actor_identity` reads the authenticated `User`
        (email, else username, else id; this fixture's user has no email, so
        "admin" here is its *username*, not the old hardcoded placeholder).
        """
        mock_qs.approve.return_value = _outcome("asst-43")

        response = client.post(f"{_BASE}/pending/asst-43/approve", json={})

        assert response.status_code == 200
        assert mock_qs.approve.call_args.kwargs["actor"] == "admin"
        assert mock_qs.approve.call_args.kwargs["decided_by_id"] == "admin-1"

    @patch(f"{_MOD}._queue_service")
    def test_pending_approve_forwards_the_idempotency_key_and_flags_a_replay(self, mock_qs, client: TestClient):
        """EXECUTION-PLAN 6.4: the header is the replay token, both ways.

        Only an HTTP-level test can see this: the key arrives as a header and
        the replay marker leaves as one, and neither exists on the service call
        the handler makes.
        """
        mock_qs.approve.return_value = _outcome("asst-44", replayed=True)

        response = client.post(f"{_BASE}/pending/asst-44/approve", json={}, headers={"Idempotency-Key": "abc-123"})

        assert response.status_code == 200
        assert mock_qs.approve.call_args.kwargs["idempotency_key"] == "abc-123"
        assert response.headers["Idempotency-Replayed"] == "true"

    @patch(f"{_MOD}._queue_service")
    def test_pending_approve_of_a_moved_entity_is_409(self, mock_qs, client: TestClient):
        """The loser of a race is told who won (F-03).

        `MarketplaceConflictError` -> 409 carrying the row's current status,
        version and decider. Before 6.1 both admins received 200 and one
        decision was silently discarded.
        """
        from codemie.service.marketplace_management.queue_service import MarketplaceConflictError

        mock_qs.approve.side_effect = MarketplaceConflictError(
            entity_kind="ASSISTANT",
            entity_id="asst-45",
            decision="APPROVE",
            expected_version=0,
            allowed_from=("PENDING_REVIEW",),
            current_status="REJECTED",
            current_version=1,
            current_decided_by="admin-b",
        )

        response = client.post(f"{_BASE}/pending/asst-45/approve", json={})

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["marketplace_status"] == "REJECTED"
        assert detail["marketplace_version"] == 1
        assert detail["marketplace_decided_by"] == "admin-b"

    @patch(f"{_MOD}._queue_service")
    def test_pending_approve_of_an_unknown_entity_is_404(self, mock_qs, client: TestClient):
        """The handler maps ValueError to 404; only the HTTP layer renders that."""
        mock_qs.approve.side_effect = ValueError("Assistant 'nope' not found")

        response = client.post(f"{_BASE}/pending/nope/approve", json={"actor": "admin"})

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]


# ---------------------------------------------------------------------------
# 5. POST /entities/{kind}/{eid}/assess
# ---------------------------------------------------------------------------


class TestAssessAssistant:
    @patch(f"{_MOD}.LLMAssessmentService")
    def test_assess_with_ai_delegates_to_service(self, mock_llm_cls, client: TestClient, admin_user: User):
        """POST /{eid}/assess delegates to LLMAssessmentService.assess_assistant."""

        def _dim(score: int) -> AssessmentDimension:
            return AssessmentDimension(score=score, justification="ok", suggested_fix="none")

        canned = AssessmentResponse(
            fields=AssessmentFields(
                metadata_polish=_dim(8),
                description_quality=_dim(7),
                prompt_substance=_dim(9),
                category_fit=_dim(6),
                uniqueness=_dim(8),
            ),
            suggested_fixes=["Improve description length"],
            overall_score=78,
            verdict_recommendation=Bucket.VERIFIED,
        )
        mock_llm_cls.assess_assistant.return_value = canned

        response = client.post(f"{_BASE}/asst-99/assess", json={})

        assert response.status_code == 200
        mock_llm_cls.assess_assistant.assert_called_once_with(
            assistant_id="asst-99",
            user=admin_user,
            llm_model=None,
        )
        # Asserted on the serialised body: `response_model=AssessmentResponse`
        # means a field the model drops never reaches the client, which a
        # direct call (returning the object itself) cannot detect.
        body = response.json()
        assert body["overall_score"] == 78
        assert body["verdict_recommendation"] == Bucket.VERIFIED.value
        assert "Improve description length" in body["suggested_fixes"]

    @patch(f"{_MOD}.LLMAssessmentService")
    def test_assess_is_501_for_workflows(self, mock_llm_cls, client: TestClient):
        """`_reject_non_assistant` guards this route; the workflow adapter is not wired."""
        response = client.post("/v1/marketplace-management/entities/workflows/wf-1/assess", json={})

        assert response.status_code == 501
        mock_llm_cls.assess_assistant.assert_not_called()


# ---------------------------------------------------------------------------
# 6. POST /entities/{kind}/audit/run
# ---------------------------------------------------------------------------


class TestRunAudit:
    @patch(f"{_MOD}.AUDIT_CACHE")
    def test_audit_run_starts_in_background_and_returns_202(self, mock_cache, client: TestClient):
        """POST /audit/run hands off to start_audit and returns 202 immediately.

        The endpoint must NOT block on the audit itself -- a full run takes
        minutes, which held the request open and starved the worker. The 202 is
        declared on the decorator, so only an HTTP-level test can assert it.
        """
        mock_cache.start_audit.return_value = {
            "is_running": True,
            "run_id": "run-xyz-999",
            "started_at": 1234.0,
            "finished_at": None,
            "error": None,
        }

        response = client.post(f"{_BASE}/audit/run")

        assert response.status_code == 202
        body = response.json()
        assert body["is_running"] is True
        assert body["run_id"] == "run-xyz-999"
        mock_cache.start_audit.assert_called_once()
        # The synchronous path must not be used by the endpoint any more.
        mock_cache.run_audit.assert_not_called()


# ---------------------------------------------------------------------------
# 7. GET /entities/{kind}/audit/status
# ---------------------------------------------------------------------------


class TestAuditStatus:
    @patch(f"{_MOD}.AUDIT_CACHE")
    def test_status_reports_running_audit(self, mock_cache, client: TestClient):
        mock_cache.audit_status.return_value = {
            "is_running": True,
            "run_id": "run-1",
            "started_at": 10.0,
            "finished_at": None,
            "error": None,
        }

        response = client.get(f"{_BASE}/audit/status")

        assert response.status_code == 200
        assert response.json()["is_running"] is True
        mock_cache.audit_status.assert_called_once()

    @patch(f"{_MOD}.AUDIT_CACHE")
    def test_status_surfaces_a_failed_run(self, mock_cache, client: TestClient):
        """A crash on the worker thread has no caller to raise into, so the
        failure has to reach the client through the status payload."""
        mock_cache.audit_status.return_value = {
            "is_running": False,
            "run_id": "run-1",
            "started_at": 10.0,
            "finished_at": 20.0,
            "error": "embedding backend unavailable",
        }

        response = client.get(f"{_BASE}/audit/status")

        assert response.status_code == 200
        body = response.json()
        assert body["is_running"] is False
        assert body["error"] == "embedding backend unavailable"


# ---------------------------------------------------------------------------
# 8. GET /entities/{kind}/marketplace-assistants
# ---------------------------------------------------------------------------


def _make_mock_marketplace_assistant(
    *,
    asst_id: str = "asst-1",
    name: str = "Test Assistant",
    marketplace_status: str = "APPROVED",
    is_verified: bool = True,
    marketplace_decision_reason: str | None = None,
    categories: list[str] | None = None,
) -> MagicMock:
    """Build a MagicMock that looks like an Assistant as returned by list_marketplace_assistants."""
    a = MagicMock()
    a.id = asst_id
    a.name = name
    a.project = "demo-project"
    a.marketplace_status = marketplace_status
    a.marketplace_submitted_at = None
    a.update_date = None
    a.date = None
    a.is_verified = is_verified
    a.marketplace_decision_reason = marketplace_decision_reason
    a.categories = categories or []
    # created_by: None so _submitted_by returns None
    a.created_by = None
    # model_dump is used to extract categories
    a.model_dump = MagicMock(return_value={"categories": categories or []})
    return a


class TestListMarketplaceAssistants:
    """GET /entities/{kind}/marketplace-assistants"""

    # (a) Absent status param and ?status=ALL both call service with all three statuses
    @patch(f"{_MOD}._compute_gate_findings", return_value=[])
    @patch(f"{_MOD}._queue_service")
    def test_absent_status_calls_service_with_all_statuses(self, mock_qs, _mock_findings, client: TestClient):
        """No ?status param defaults to ALL — service receives all three status values."""
        mock_qs.list_marketplace_assistants.return_value = ([], 0)

        response = client.get(f"{_BASE}/marketplace-assistants")

        assert response.status_code == 200, response.text
        mock_qs.list_marketplace_assistants.assert_called_once()
        call_kwargs = mock_qs.list_marketplace_assistants.call_args
        statuses_arg = call_kwargs.kwargs.get("statuses") or call_kwargs.args[0]
        assert set(statuses_arg) == {"APPROVED", "PENDING_REVIEW", "REJECTED"}

    @patch(f"{_MOD}._compute_gate_findings", return_value=[])
    @patch(f"{_MOD}._queue_service")
    def test_status_all_calls_service_with_all_statuses(self, mock_qs, _mock_findings, client: TestClient):
        """?status=ALL is the explicit equivalent of absent status — same three values."""
        mock_qs.list_marketplace_assistants.return_value = ([], 0)

        response = client.get(f"{_BASE}/marketplace-assistants", params={"status": "ALL"})

        assert response.status_code == 200, response.text
        call_kwargs = mock_qs.list_marketplace_assistants.call_args
        statuses_arg = call_kwargs.kwargs.get("statuses") or call_kwargs.args[0]
        assert set(statuses_arg) == {"APPROVED", "PENDING_REVIEW", "REJECTED"}

    # (b) Filter values map to correct status lists
    @patch(f"{_MOD}._compute_gate_findings", return_value=[])
    @patch(f"{_MOD}._queue_service")
    def test_status_verified_calls_service_with_approved(self, mock_qs, _mock_findings, client: TestClient):
        """?status=VERIFIED maps to ["APPROVED"]."""
        mock_qs.list_marketplace_assistants.return_value = ([], 0)

        response = client.get(f"{_BASE}/marketplace-assistants", params={"status": "VERIFIED"})

        assert response.status_code == 200, response.text
        call_kwargs = mock_qs.list_marketplace_assistants.call_args
        statuses_arg = call_kwargs.kwargs.get("statuses") or call_kwargs.args[0]
        assert statuses_arg == ["APPROVED"]

    @patch(f"{_MOD}._compute_gate_findings", return_value=[])
    @patch(f"{_MOD}._queue_service")
    def test_status_pending_calls_service_with_pending_review(self, mock_qs, _mock_findings, client: TestClient):
        """?status=PENDING maps to ["PENDING_REVIEW"]."""
        mock_qs.list_marketplace_assistants.return_value = ([], 0)

        response = client.get(f"{_BASE}/marketplace-assistants", params={"status": "PENDING"})

        assert response.status_code == 200, response.text
        call_kwargs = mock_qs.list_marketplace_assistants.call_args
        statuses_arg = call_kwargs.kwargs.get("statuses") or call_kwargs.args[0]
        assert statuses_arg == ["PENDING_REVIEW"]

    @patch(f"{_MOD}._compute_gate_findings", return_value=[])
    @patch(f"{_MOD}._queue_service")
    def test_status_rejected_calls_service_with_rejected(self, mock_qs, _mock_findings, client: TestClient):
        """?status=REJECTED maps to ["REJECTED"]."""
        mock_qs.list_marketplace_assistants.return_value = ([], 0)

        response = client.get(f"{_BASE}/marketplace-assistants", params={"status": "REJECTED"})

        assert response.status_code == 200, response.text
        call_kwargs = mock_qs.list_marketplace_assistants.call_args
        statuses_arg = call_kwargs.kwargs.get("statuses") or call_kwargs.args[0]
        assert statuses_arg == ["REJECTED"]

    # (c) Response shape: total, page, page_size, items with is_verified, marketplace_decision_reason, gate_findings
    @patch(f"{_MOD}._compute_gate_findings", return_value=[])
    @patch(f"{_MOD}._queue_service")
    def test_response_shape_and_item_fields(self, mock_qs, _mock_findings, client: TestClient):
        """Response includes total/page/page_size/items; items include is_verified, marketplace_decision_reason, gate_findings."""
        assistant = _make_mock_marketplace_assistant(
            asst_id="asst-10",
            marketplace_status="APPROVED",
            is_verified=True,
            marketplace_decision_reason="Meets all criteria",
        )
        mock_qs.list_marketplace_assistants.return_value = ([assistant], 1)

        response = client.get(f"{_BASE}/marketplace-assistants")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 1
        assert body["page"] == 1
        assert body["page_size"] == 1  # no page_size requested: reports the rows returned
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["assistant_id"] == "asst-10"
        assert item["is_verified"] is True
        assert item["marketplace_decision_reason"] == "Meets all criteria"
        assert isinstance(item["gate_findings"], list)

    @patch(f"{_MOD}._compute_gate_findings", return_value=[])
    @patch(f"{_MOD}._queue_service")
    def test_is_verified_false_for_non_approved(self, mock_qs, _mock_findings, client: TestClient):
        """is_verified=False is surfaced correctly for rejected assistants."""
        assistant = _make_mock_marketplace_assistant(
            asst_id="asst-11",
            marketplace_status="REJECTED",
            is_verified=False,
            marketplace_decision_reason="Duplicate",
        )
        mock_qs.list_marketplace_assistants.return_value = ([assistant], 1)

        response = client.get(f"{_BASE}/marketplace-assistants", params={"status": "REJECTED"})

        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        assert item["is_verified"] is False
        assert item["marketplace_decision_reason"] == "Duplicate"

    # (d) page_size > 100 returns 422
    def test_page_size_over_100_returns_422(self, client: TestClient):
        """FastAPI enforces le=100 on page_size — values above it must be rejected."""
        response = client.get(f"{_BASE}/marketplace-assistants", params={"page_size": 101})

        assert response.status_code == 422, response.text

    @patch(f"{_MOD}._compute_gate_findings", return_value=[])
    @patch(f"{_MOD}._queue_service")
    def test_pagination_params_forwarded_to_service(self, mock_qs, _mock_findings, client: TestClient):
        """Custom page/page_size are forwarded to list_marketplace_assistants."""
        mock_qs.list_marketplace_assistants.return_value = ([], 0)

        response = client.get(f"{_BASE}/marketplace-assistants", params={"page": 3, "page_size": 10})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["page"] == 3
        assert body["page_size"] == 10
        call_kwargs = mock_qs.list_marketplace_assistants.call_args
        page_arg = call_kwargs.kwargs.get("page") or call_kwargs.args[1]
        page_size_arg = call_kwargs.kwargs.get("page_size") or call_kwargs.args[2]
        assert page_arg == 3
        assert page_size_arg == 10

    @patch(f"{_MOD}._compute_gate_findings", return_value=[])
    @patch(f"{_MOD}._queue_service")
    def test_omitted_page_size_returns_all_rows(self, mock_qs, _mock_findings, client: TestClient):
        """No page_size means no pagination: the service gets page_size=None."""
        mock_qs.list_marketplace_assistants.return_value = ([], 0)

        response = client.get(f"{_BASE}/marketplace-assistants", params={"page": 3})

        assert response.status_code == 200, response.text
        call_kwargs = mock_qs.list_marketplace_assistants.call_args.kwargs
        assert call_kwargs["page_size"] is None

    @patch(f"{_MOD}._compute_gate_findings", return_value=[])
    @patch(f"{_MOD}._queue_service")
    def test_workflow_kind_returns_empty_page(self, mock_qs, _mock_findings, client: TestClient):
        """Non-assistant kinds return an empty page without calling the service."""
        response = client.get("/v1/marketplace-management/entities/workflows/marketplace-assistants")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 0
        assert body["items"] == []


# ---------------------------------------------------------------------------
# 9. CRITICAL-findings gate on approve (EPMCDME-14734, spec §5)
# ---------------------------------------------------------------------------


def _make_critical_finding() -> Finding:
    return Finding(rule="name_length", severity=Severity.CRITICAL, message="Name too short")


def _make_optional_finding() -> Finding:
    return Finding(rule="description_length", severity=Severity.OPTIONAL, message="Description short")


_CFG_MOD = "codemie.configs.marketplace_management"
_GATE_CFG_MOD = "codemie.service.marketplace_management.validator.config"


class TestApproveCriticalGate:
    """Gate: POST .../approve is blocked when CRITICAL gate findings exist (EPMCDME-14734).

    reject and force-publish are not blocked — only approve.

    GateConfig and get_marketplace_management_config are imported inline inside
    the handler, so they are patched at their source modules rather than at _MOD.
    _compute_gate_findings and _load_assistant_or_404 are defined in _MOD, so
    they are patched there directly.
    """

    @patch(f"{_CFG_MOD}.get_marketplace_management_config")
    @patch(f"{_GATE_CFG_MOD}.Config")
    @patch(f"{_MOD}._compute_gate_findings")
    @patch(f"{_MOD}._load_assistant_or_404")
    @patch(f"{_MOD}._queue_service")
    def test_approve_blocked_when_critical_finding_present(
        self,
        mock_qs,
        mock_load,
        mock_gate,
        mock_gate_config,
        mock_get_cfg,
        client: TestClient,
    ):
        """(a) POST .../approve with a CRITICAL finding returns 422.

        The gate_check is passed as a callback into queue_service.approve, which calls
        it immediately before the CAS update.  The mock must simulate this so the
        gate check actually fires and blocks the request.
        """
        mock_load.return_value = MagicMock(id="asst-gate-1")
        mock_gate.return_value = [_make_critical_finding()]

        # Simulate the real queue_service.approve calling gate_check before the CAS.
        def _approve_calls_gate(**kwargs):
            gate_check = kwargs.get("gate_check")
            if gate_check:
                gate_check(kwargs.get("assistant_id", ""))

        mock_qs.approve.side_effect = _approve_calls_gate

        response = client.post(f"{_BASE}/pending/asst-gate-1/approve", json={})

        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert "name_length" in error["message"]
        assert error["details"]["failed_rules"] == ["name_length"]
        assert [f["rule"] for f in error["details"]["gate_findings"]] == ["name_length"]
        assert error["details"]["requires_confirmation"] is False
        assert "detail" not in error["details"]["gate_findings"][0]
        assert "force-publish" in error["help"]
        # approve was called (gate_check raised inside it before the CAS — not "not called")
        mock_qs.approve.assert_called_once()
        mock_qs.list_marketplace_assistants.assert_not_called()

    @patch(f"{_CFG_MOD}.get_marketplace_management_config")
    @patch(f"{_GATE_CFG_MOD}.Config")
    @patch(f"{_MOD}._compute_gate_findings")
    @patch(f"{_MOD}._load_assistant_or_404")
    @patch(f"{_MOD}._queue_service")
    def test_approve_allowed_with_optional_only_findings(
        self,
        mock_qs,
        mock_load,
        mock_gate,
        mock_gate_config,
        mock_get_cfg,
        client: TestClient,
    ):
        """(b) POST .../approve with OPTIONAL-only findings returns 200 and transitions to APPROVED."""
        mock_load.return_value = MagicMock()
        mock_gate.return_value = [_make_optional_finding()]
        mock_qs.approve.return_value = _outcome("asst-gate-2")

        response = client.post(f"{_BASE}/pending/asst-gate-2/approve", json={})

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "APPROVED"

    @patch(f"{_CFG_MOD}.get_marketplace_management_config")
    @patch(f"{_GATE_CFG_MOD}.Config")
    @patch(f"{_MOD}._compute_gate_findings")
    @patch(f"{_MOD}._load_assistant_or_404")
    @patch(f"{_MOD}._queue_service")
    def test_approve_allowed_with_no_findings(
        self,
        mock_qs,
        mock_load,
        mock_gate,
        mock_gate_config,
        mock_get_cfg,
        client: TestClient,
    ):
        """(b) POST .../approve with no findings returns 200 and transitions to APPROVED."""
        mock_load.return_value = MagicMock()
        mock_gate.return_value = []
        mock_qs.approve.return_value = _outcome("asst-gate-3")

        response = client.post(f"{_BASE}/pending/asst-gate-3/approve", json={})

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "APPROVED"

    @patch(f"{_MOD}._queue_service")
    def test_reject_not_blocked_by_critical_finding(
        self,
        mock_qs,
        client: TestClient,
    ):
        """(c) POST .../reject with a CRITICAL finding returns 200 (rejection not blocked).

        The gate is only on approve; reject goes straight to _decide without any
        gate check — no patching of gate helpers is needed or expected here.
        """
        mock_qs.reject.return_value = _outcome("asst-gate-4", status="REJECTED")

        response = client.post(
            f"{_BASE}/pending/asst-gate-4/reject",
            json={"reason": "x" * 25},
        )

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "REJECTED"

    @patch(f"{_MOD}._queue_service")
    def test_force_publish_not_blocked_by_critical_finding(
        self,
        mock_qs,
        client: TestClient,
    ):
        """(d) POST .../force-publish with a CRITICAL finding returns 200 (override path unaffected).

        The gate is only on approve; force-publish goes straight to _decide without
        any gate check — no patching of gate helpers is needed or expected here.
        """
        from codemie.service.marketplace_management.queue_service import DecisionOutcome

        mock_qs.force_publish.return_value = DecisionOutcome(
            result={
                "ok": True,
                "entity_kind": "ASSISTANT",
                "entity_id": "asst-gate-5",
                "status": "APPROVED",
                "override_notice": "x" * 25,
            },
            replayed=False,
        )

        response = client.post(
            f"{_BASE}/pending/asst-gate-5/force-publish",
            json={"override_notice": "x" * 25},
        )

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "APPROVED"


# ---------------------------------------------------------------------------
# 10. EntityReviewDetail new fields: bucket_reason, usage_tier (EPMCDME-15352)
# ---------------------------------------------------------------------------


class TestEntityReviewDetailNewFields:
    """bucket_reason and usage_tier promoted from _review_verdict() into EntityReviewDetail."""

    def test_entity_review_detail_serializes_bucket_reason_and_usage_tier(self):
        """EntityReviewDetail accepts and serializes bucket_reason and usage_tier."""
        detail = mm.EntityReviewDetail(
            id="x",
            name="T",
            description="",
            bucket_reason="similarity_too_high",
            usage_tier="low",
        )
        data = detail.model_dump()
        assert data["bucket_reason"] == "similarity_too_high"
        assert data["usage_tier"] == "low"

    @patch(f"{_MOD}.audit_reads")
    @patch(f"{_MOD}.get_session")
    def test_review_verdict_returns_bucket_reason_and_usage_tier(self, mock_get_session, mock_audit_reads):
        """_review_verdict() includes bucket_reason and usage_tier from the ORM row."""
        from contextlib import contextmanager

        from codemie.service.marketplace_management.models import Bucket

        mock_row = MagicMock()
        mock_row.verdict_kind = "CANDIDATE_FOR_CLEANUP"
        mock_row.cleanup_reason = "sim"
        mock_row.bucket = Bucket.DELETE
        mock_row.detail = "detail text"
        mock_row.bucket_reason = "similarity_too_high"
        mock_row.usage_tier = "low"

        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = mock_row

        @contextmanager
        def _fake_get_session():
            yield mock_session

        mock_get_session.side_effect = _fake_get_session
        mock_audit_reads.latest_succeeded_run_id.return_value = "run-1"

        result = mm._review_verdict("asst-1")

        assert result["bucket_reason"] == "similarity_too_high"
        assert result["usage_tier"] == "low"


# ---------------------------------------------------------------------------
# 11. PolishCheckResult model and _review_polish_checks() (EPMCDME-15352)
# ---------------------------------------------------------------------------

_CFG_MOD_FULL = "codemie.service.marketplace_management.validator.config"
_MM_CFG_MOD = "codemie.configs.marketplace_management"


class TestReviewPolishChecks:
    """_review_polish_checks() returns all 4 checks and handles config failure."""

    @patch(f"{_MOD}.Assistant")
    @patch(f"{_MM_CFG_MOD}.get_marketplace_management_config")
    @patch(f"{_CFG_MOD_FULL}.Config")
    def test_review_polish_checks_returns_all_four_entries(
        self, mock_gate_config_cls, mock_get_mm_config, mock_assistant_cls
    ):
        """Returns a list of 4 PolishCheckResult entries with correct pass/fail state."""
        # Build a mock assistant with description >= 250, icon_url set, no starters
        mock_assistant = MagicMock()
        mock_assistant.description = "x" * 300
        mock_assistant.categories = ["cat1"]
        mock_assistant.icon_url = "http://img"
        mock_assistant.conversation_starters = []
        mock_assistant_cls.get_by_id.return_value = mock_assistant

        # Build a mock polish gates config
        mock_pg = MagicMock()
        mock_pg.description_min = 250
        mock_pg.categories_min = 1
        mock_pg.require_icon = True
        mock_pg.conversation_starters_min = 1

        mock_cfg = MagicMock()
        mock_cfg.verification.polish_gates = mock_pg
        # `for_assistant_gates()` is the single loader every gate call site now
        # shares (MR !4070 review comment #12); it wraps
        # `from_marketplace_management` plus the config read.
        mock_gate_config_cls.for_assistant_gates.return_value = mock_cfg

        result = mm._review_polish_checks("asst-1")

        assert result is not None
        assert len(result) == 4
        by_check = {r.check: r for r in result}
        assert by_check["description"].passed is True
        assert by_check["categories"].passed is True
        assert by_check["icon"].passed is True
        assert by_check["starters"].passed is False

    @patch(f"{_MM_CFG_MOD}.get_marketplace_management_config")
    def test_review_polish_checks_returns_none_on_config_failure(self, mock_get_mm_config):
        """Returns None (no exception raised) when config load fails."""
        mock_get_mm_config.side_effect = RuntimeError("config failed")
        result = mm._review_polish_checks("asst-1")
        assert result is None


# ---------------------------------------------------------------------------
# 11. expected_version: the decision is pinned to the version the reviewer saw
# ---------------------------------------------------------------------------


class TestDecisionsCarryExpectedVersion:
    """Approve, reject and force-publish forward the reviewed `marketplace_version` to the CAS."""

    @pytest.mark.parametrize(
        "action, method, body",
        [
            ("approve", "approve", {}),
            ("reject", "reject", {"reason": "x" * 25}),
            ("force-publish", "force_publish", {"override_notice": "x" * 25}),
        ],
    )
    @pytest.mark.parametrize("sent, expected", [(7, 7), (None, None)])
    @patch(f"{_GATE_CFG_MOD}.Config")
    @patch(f"{_MOD}._queue_service")
    def test_expected_version_reaches_the_queue_service(
        self, mock_qs, mock_gate_config, action, method, body, sent, expected, client: TestClient
    ):
        from codemie.service.marketplace_management.queue_service import DecisionOutcome

        result = {"ok": True, "entity_kind": "ASSISTANT", "entity_id": "asst-v-1", "status": "APPROVED"}
        if action == "force-publish":
            result["override_notice"] = "x" * 25
        getattr(mock_qs, method).return_value = DecisionOutcome(result=result, replayed=False)
        payload = dict(body)
        if sent is not None:
            payload["expected_version"] = sent

        response = client.post(f"{_BASE}/pending/asst-v-1/{action}", json=payload)

        assert response.status_code == 200, response.text
        assert getattr(mock_qs, method).call_args.kwargs["expected_version"] == expected

    @pytest.mark.parametrize("action, body", [("approve", {}), ("reject", {"reason": "x" * 25})])
    @patch(f"{_GATE_CFG_MOD}.Config")
    @patch(f"{_MOD}._queue_service")
    def test_negative_expected_version_is_rejected(self, mock_qs, mock_gate_config, action, body, client: TestClient):
        response = client.post(f"{_BASE}/pending/asst-v-2/{action}", json={**body, "expected_version": -1})

        assert response.status_code == 422
