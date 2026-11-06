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

"""Marketplace indexing must not fail silently (EXECUTION-PLAN 9.6).

Traces F-87, F-27, and §7 F7 of design-persistence.md.

The measured divergence this file exists for, re-measured on 2026-08-23 against
the live stack:

    select count(*) from codemie.assistants where is_global   ->  219
    curl -s localhost:9200/marketplace_assistants/_count      ->   19

Two independent mechanisms produced it, and they need different fixes:

* **A config consequence.** `PLATFORM_DATASOURCES_SYNC_ENABLED` is `False`
  (`configs/config.py:703`), so `sync_all_platform_datasources` never runs at
  startup and the only writer into the index is the per-publish incremental
  call. Every assistant that became `is_global` any other way -- seeded,
  migrated, or backfilled straight into Postgres -- is absent by construction.
  All 19 documents in the index are `QA *` fixtures published through the API,
  which is exactly the signature of an incremental-only writer.
* **A code defect.** Every one of those incremental call sites swallows its
  exception. `queue_service._side_effects` wrapped both calls in a bare
  `except Exception: logger.error`, and `routers/assistant.py` scheduled
  `_index_marketplace_assistant` as a fire-and-forget BackgroundTask whose own
  body catches everything. A failed index and a successful one produced the
  same clean 200, and nothing anywhere published the two counts side by side,
  so the gap could not be noticed even in principle.

These tests pin the code defect. The config consequence is fixed by running the
backfill (`POST /v1/admin/marketplace/reindex`), which is a data operation, not
a code change -- and `test_published_assistants_are_all_indexed` is the
invariant that says whether it has been run.
"""

from __future__ import annotations

import os
from typing import Any, Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from codemie.service.marketplace_management.queue_service import (
    MarketplaceQueueService,
)

_SCHEMA = "codemie"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine(mm_pg_engine: Engine) -> Engine:
    return mm_pg_engine


@pytest.fixture
def db(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """Point the application's `get_session()` at the throwaway database."""
    from codemie.clients import postgres as pg

    monkeypatch.setattr(pg.PostgresClient, "get_engine", classmethod(lambda cls: engine))
    _clean(engine)
    with engine.connect() as conn:
        conn.execute(
            text(
                "insert into codemie.users (id, username, email, name, is_admin, is_active, date) "
                "values ('admin-idx', 'admin-idx', 'idx@codemie.ai', 'Idx Admin', true, true, now()) "
                "on conflict (id) do nothing"
            )
        )
        conn.commit()
    try:
        yield engine
    finally:
        _clean(engine)


def _clean(engine: Engine) -> None:
    with engine.connect() as conn:
        for tbl in ("mm_review_decision", "activity_events", "assistants", "users"):
            conn.execute(text(f"delete from {_SCHEMA}.{tbl}"))
        conn.commit()


def _seed(engine: Engine, assistant_id: str, status: str, *, is_global: bool = False) -> None:
    with engine.connect() as conn:
        conn.execute(
            text(
                "insert into codemie.assistants "
                "(id, name, description, system_prompt, project, shared, is_react, "
                " created_date, creator, is_global, categories, marketplace_status, "
                " marketplace_version, unique_users_count, unique_likes_count, "
                " unique_dislikes_count, version_count) "
                "values (:id, :n, 'a seeded assistant', 'you are seeded', 'demo', true, true, "
                "        now(), 'seed-creator', :g, '[]'::jsonb, :s, 0, 3, 1, 0, 1)"
            ),
            {"id": assistant_id, "n": f"Assistant {assistant_id}", "g": is_global, "s": status},
        )
        conn.commit()


@pytest.fixture
def metric_spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService

    seen: list[tuple[str, dict[str, Any]]] = []

    def _send(name: str, description: str = "", unit: str = "", attributes: dict | None = None, count: int = 1) -> None:
        seen.append((name, dict(attributes or {}, count=count)))

    monkeypatch.setattr(BaseMonitoringService, "send_count_metric", staticmethod(_send))
    return seen


class _Boom(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# The approve path: a failed index must reach the caller
# ---------------------------------------------------------------------------


def test_index_failure_is_surfaced_not_swallowed(db: Engine, monkeypatch, metric_spy) -> None:
    """Approving with a dead index must not answer as if it had worked.

    `queue_service._side_effects` caught every exception and returned None, so
    the router built a 200 body from a decision whose search-index projection
    had not happened. That is the mechanism behind the 219-vs-19 divergence:
    the failure existed only as a log line.

    The decision itself must still stand -- it is the record of truth and the
    index is a projection of it -- so the assertion is that the *response*
    carries the degraded outcome, not that the approval is rolled back.
    """
    from codemie.service.platform.platform_indexing_service import PlatformIndexingService

    calls: list[str] = []

    def _explode(assistant_id: str, user: Any = None, is_update: bool = False) -> None:
        calls.append(assistant_id)
        raise _Boom("elasticsearch bulk index failed: connection refused")

    monkeypatch.setattr(PlatformIndexingService, "index_single_assistant", staticmethod(_explode))

    _seed(db, "idx-fail", "PENDING_REVIEW")
    outcome = MarketplaceQueueService().approve("idx-fail", actor="idx@codemie.ai", decided_by_id="admin-idx")

    indexing = outcome.result.get("indexing")
    assert indexing is not None, (
        "the approve response says nothing about whether the assistant reached "
        "the marketplace index -- this is exactly how 200 published assistants "
        "went missing without a single alert (F-87)"
    )
    assert indexing["state"] == "FAILED", indexing
    assert indexing["action"] == "index", indexing
    assert "connection refused" in (indexing.get("error") or ""), indexing
    assert indexing["attempts"] >= 2, f"a transient index failure was not retried (attempts={indexing['attempts']})"
    assert len(calls) == indexing["attempts"]

    # The moderation decision stands; only its projection failed.
    with db.connect() as conn:
        row = (
            conn.execute(text("select marketplace_status, is_global from codemie.assistants where id='idx-fail'"))
            .mappings()
            .one()
        )
    assert row["marketplace_status"] == "APPROVED"
    assert row["is_global"] is True

    names = [n for n, _ in metric_spy]
    assert "marketplace_index_failed" in names, f"no metric recorded the indexing failure; emitted={names}"


def test_a_successful_index_is_reported_as_such(db: Engine, monkeypatch, metric_spy) -> None:
    """The positive control: OK must be distinguishable from FAILED."""
    from codemie.service.platform.platform_indexing_service import PlatformIndexingService

    monkeypatch.setattr(
        PlatformIndexingService,
        "index_single_assistant",
        staticmethod(lambda assistant_id, user=None, is_update=False: None),
    )

    _seed(db, "idx-ok", "PENDING_REVIEW")
    outcome = MarketplaceQueueService().approve("idx-ok", actor="idx@codemie.ai", decided_by_id="admin-idx")

    assert outcome.result["indexing"] == {
        "action": "index",
        "state": "OK",
        "attempts": 1,
        "error": None,
    }
    assert "marketplace_index_failed" not in [n for n, _ in metric_spy]


def test_a_transient_index_failure_is_retried_and_recovers(db: Engine, monkeypatch) -> None:
    """One flap must not cost an assistant its place in marketplace search."""
    from codemie.service.platform.platform_indexing_service import PlatformIndexingService

    attempts: list[int] = []

    def _flaky(assistant_id: str, user: Any = None, is_update: bool = False) -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise _Boom("transient: connection reset by peer")

    monkeypatch.setattr(PlatformIndexingService, "index_single_assistant", staticmethod(_flaky))

    _seed(db, "idx-flaky", "PENDING_REVIEW")
    outcome = MarketplaceQueueService().approve("idx-flaky", actor="idx@codemie.ai", decided_by_id="admin-idx")

    assert outcome.result["indexing"]["state"] == "OK"
    assert outcome.result["indexing"]["attempts"] == 2
    assert len(attempts) == 2


def test_deindex_failure_is_surfaced_too(db: Engine, monkeypatch, metric_spy) -> None:
    """Withdraw is the take-it-down button; a failed removal leaves it up.

    Reporting a clean 200 for a withdrawal that left the assistant searchable
    is the same defect as F-46 with the two halves reversed.
    """
    from codemie.service.platform.platform_indexing_service import PlatformIndexingService

    def _explode(assistant_id: str, assistant_name: str | None = None, user: Any = None) -> None:
        raise _Boom("elasticsearch delete failed: index_not_found_exception")

    monkeypatch.setattr(PlatformIndexingService, "remove_single_assistant", staticmethod(_explode))

    _seed(db, "idx-rm", "APPROVED", is_global=True)
    outcome = MarketplaceQueueService().withdraw("idx-rm", actor="idx@codemie.ai", decided_by_id="admin-idx")

    indexing = outcome.result.get("indexing")
    assert indexing is not None and indexing["action"] == "remove", indexing
    assert indexing["state"] == "FAILED", indexing
    assert "marketplace_index_failed" in [n for n, _ in metric_spy]


# ---------------------------------------------------------------------------
# The publish path
# ---------------------------------------------------------------------------


def test_publish_background_indexing_reports_its_outcome(monkeypatch, metric_spy) -> None:
    """`routers/assistant.py` scheduled indexing and never looked at the result.

    A BackgroundTask cannot change a response that has already been sent, so
    the requirement here is the other half of "not silently": the task must
    retry, must return a readable outcome, and must emit the failure so the
    divergence is attributable to a moment rather than discovered months later
    by counting two databases.
    """
    from contextlib import contextmanager

    from codemie.rest_api.routers.assistant import _shared as assistant_router
    from codemie.service.llm_service import utils as llm_utils
    from codemie.service.marketplace_management import marketplace_index_sync as mis
    from codemie.service.platform.platform_indexing_service import PlatformIndexingService

    monkeypatch.setattr(
        PlatformIndexingService,
        "index_single_assistant",
        staticmethod(_raise_index_error),
    )
    # The task sets an LLM context for cost attribution before indexing; that
    # needs project credentials this suite has no reason to carry.
    monkeypatch.setattr(llm_utils, "set_llm_context", lambda *a, **k: None)

    # Bypass the DB-read / advisory-lock path so the test does not need a real
    # Postgres connection. Yield True (published) so CR-017 does not short-circuit
    # before the indexing call we actually want to test.
    @contextmanager
    def _committed_state_published(_assistant_id):
        yield True

    monkeypatch.setattr(mis, "_committed_state", _committed_state_published)

    class _User:
        id = "u-1"
        current_project = "demo"

    outcome = assistant_router._index_marketplace_assistant("pub-1", _User(), is_update=False)

    assert outcome is not None, "the publish path still discards the indexing result"
    assert outcome["state"] == "FAILED", outcome
    assert outcome["attempts"] >= 2, outcome
    assert "marketplace_index_failed" in [n for n, _ in metric_spy]


def _raise_index_error(assistant_id: str, user: Any = None, is_update: bool = False) -> None:
    raise _Boom("elasticsearch bulk index failed: connection refused")


# ---------------------------------------------------------------------------
# The divergence itself
# ---------------------------------------------------------------------------


def test_divergence_is_measured_and_emitted(monkeypatch, metric_spy) -> None:
    """Both counts, side by side, as a metric. Nothing published them before."""
    from codemie.service.marketplace_management import marketplace_index_sync as sync

    monkeypatch.setattr(sync, "_count_published_assistants", lambda: 219)
    monkeypatch.setattr(sync, "_count_indexed_documents", lambda: 19)

    divergence = sync.measure_index_divergence(emit=True)

    assert divergence == {
        "published": 219,
        "indexed": 19,
        "missing": 200,
        "surplus": 0,
        "index_name": divergence["index_name"],
    }
    emitted = dict(metric_spy)
    assert "marketplace_index_divergence" in emitted, [n for n, _ in metric_spy]
    assert emitted["marketplace_index_divergence"]["missing_count"] == 200


def test_divergence_counts_surplus_documents_too(monkeypatch, metric_spy) -> None:
    """An index holding too many documents is a divergence, and used to read as zero.

    `missing` is `max(published - indexed, 0)`, which is structurally incapable
    of seeing over-indexing: measured 2026-08-25, the marketplace index held 226
    documents against 219 published assistants -- seven listings for assistants
    that had been deleted -- and the number whose whole purpose is to make this
    impossible to miss reported a clean zero.
    """
    from codemie.service.marketplace_management import marketplace_index_sync as sync

    monkeypatch.setattr(sync, "_count_published_assistants", lambda: 219)
    monkeypatch.setattr(sync, "_count_indexed_documents", lambda: 226)

    divergence = sync.measure_index_divergence(emit=True)

    assert divergence["missing"] == 0
    assert divergence["surplus"] == 7, (
        "7 documents with no published assistant behind them were reported as " f"no divergence at all: {divergence}"
    )
    assert dict(metric_spy)["marketplace_index_divergence"]["surplus_count"] == 7


def test_an_uncountable_store_reports_neither_direction_as_zero(monkeypatch) -> None:
    """Unknown is not zero, in either direction."""
    from codemie.service.marketplace_management import marketplace_index_sync as sync

    monkeypatch.setattr(sync, "_count_published_assistants", lambda: 219)
    monkeypatch.setattr(sync, "_count_indexed_documents", lambda: None)

    divergence = sync.measure_index_divergence(emit=False)
    assert divergence["missing"] is None and divergence["surplus"] is None, divergence


# ---------------------------------------------------------------------------
# The third mechanism: one oversized document sank the whole backfill
# ---------------------------------------------------------------------------
#
# Discovered by *running* the backfill rather than by reading the code. The
# reindex reported "Marketplace reindexing completed" and indexed **0 of 219**
# documents. The container log carries the reason:
#
#   openai.BadRequestError: Error code: 400 - "This model's maximum context
#   length is 8192 tokens, however you requested 11126 tokens"
#
# `AssistantLoader._build_page_content` bounds only the system prompt
# (`ASSISTANT_PROMPT_MAX_LENGTH = 1000`); every toolkit and every tool
# description is appended unbounded after it. Measured across the live
# catalogue: median page_content 1,670 chars, and exactly **one** document at
# 50,636 chars ("AI Readiness Report Generator"). `get_embeddings_model` builds
# the client with `check_embedding_ctx_length=False`, which disables langchain's
# own split-and-average path, so that one document reaches the embeddings API
# raw, 400s, and takes the entire `add_documents` batch — and therefore the
# entire sync — down with it.
#
# The guard belongs at the embedding boundary rather than in one loader: no
# loader's output may exceed what the embedding model accepts, whatever it
# chooses to put in a document.


def test_one_oversized_document_does_not_sink_the_whole_batch() -> None:
    """219 published assistants, one fat one, zero indexed. Never again."""
    from langchain_core.documents import Document

    from codemie.datasource.platform import base_platform_processor as bpp

    documents = [
        Document(page_content="x" * 200, metadata={"id": "small-1", "name": "Small One"}),
        Document(
            page_content="y" * 50_636,
            metadata={"id": "huge", "name": "AI Readiness Report Generator"},
        ),
        Document(page_content="z" * 200, metadata={"id": "small-2", "name": "Small Two"}),
    ]

    bounded = bpp.bound_documents_for_embedding(documents)

    assert len(bounded) == 3, "a document that is too long must be shortened, not dropped"
    assert max(len(d.page_content) for d in bounded) <= bpp.EMBEDDING_MAX_CHARS
    assert bpp.EMBEDDING_MAX_CHARS < 50_636, "the ceiling must actually bind the observed case"
    # Documents already within budget are passed through untouched.
    assert bounded[0].page_content == "x" * 200
    assert bounded[2].page_content == "z" * 200
    assert [d.metadata["id"] for d in bounded] == ["small-1", "huge", "small-2"]


def test_the_indexing_call_applies_the_embedding_ceiling(monkeypatch) -> None:
    """`_add_documents` is the boundary; the ceiling has to be enforced there."""
    from langchain_core.documents import Document

    from codemie.datasource.platform import base_platform_processor as bpp
    from codemie.datasource.platform.assistant_datasource_processor import (
        AssistantDatasourceProcessor,
    )

    class _Store:
        def __init__(self) -> None:
            self.seen: list[Document] = []
            self._store = type("_S", (), {"_create_index_if_not_exists": lambda _self: None})()

        def add_documents(self, documents, ids):  # noqa: ARG002
            for doc in documents:
                if len(doc.page_content) > bpp.EMBEDDING_MAX_CHARS:
                    raise RuntimeError("This model's maximum context length is 8192 tokens")
            self.seen.extend(documents)

    store = _Store()
    monkeypatch.setattr(
        bpp.BasePlatformDatasourceProcessor,
        "_get_store_by_index",
        staticmethod(lambda index_name, embeddings_model: store),
    )
    monkeypatch.setattr(bpp.llm_service, "get_embedding_deployment_name", lambda model: "ada-002")

    processor = object.__new__(AssistantDatasourceProcessor)
    processor.index = type("_Idx", (), {"embeddings_model": "ada-002"})()
    # `_index_name` is a read-only property derived from the datasource name.
    processor.datasource_name = "marketplace_assistants"

    processor._add_documents(
        [Document(page_content="y" * 50_636, metadata={"id": "huge", "name": "Huge"})],
        is_update=True,
    )

    assert len(store.seen) == 1
    assert len(store.seen[0].page_content) <= bpp.EMBEDDING_MAX_CHARS


# ---------------------------------------------------------------------------
# The fourth mechanism: the "full sync" only ever added
# ---------------------------------------------------------------------------
#
# Also discovered by running the backfill. Afterwards the two stores read
# 219 published in Postgres against **220** documents in Elasticsearch, and the
# id-set difference named the extra one:
#
#   000de9d4-6ca5-42de-8787-36ea77b196a7  "QA Test Assistant cqa_pmhzprgzfvrogdq"
#   is_global = f, marketplace_status = DRAFT
#
# An assistant that was published, indexed, and then unpublished, whose document
# was never removed -- the de-index either never ran or failed into the bare
# `except` this step deletes. `_process` upserts the documents the loader
# returns and touches nothing else, so a "full sync" cannot repair
# over-indexing, only under-indexing. An unpublished assistant staying findable
# in marketplace search is the same class of defect as a published one being
# absent, pointing the other way, and it is the half a reconciliation job is
# for (design-persistence.md §7 F7 / V29).


def test_full_sync_removes_documents_for_entities_that_are_no_longer_published(
    monkeypatch,
) -> None:
    """A sync that only adds is not a sync."""
    from langchain_core.documents import Document

    from codemie.datasource.platform import base_platform_processor as bpp
    from codemie.datasource.platform.assistant_datasource_processor import (
        AssistantDatasourceProcessor,
    )

    live = [
        Document(page_content="a", metadata={"id": "still-published", "name": "A"}),
        Document(page_content="b", metadata={"id": "also-published", "name": "B"}),
    ]

    class _Client:
        def __init__(self) -> None:
            self.deleted_bodies: list[dict] = []

        def delete_by_query(self, index, body, refresh=False, **kwargs):  # noqa: ARG002
            self.deleted_bodies.append(body)
            return {"deleted": 1}

    client = _Client()
    processor = object.__new__(AssistantDatasourceProcessor)
    processor.datasource_name = "marketplace_assistants"
    processor.index = type("_Idx", (), {"embeddings_model": "ada-002"})()
    processor.loader = type("_L", (), {"lazy_load": lambda _self: iter(live)})()
    processor.client = client
    monkeypatch.setattr(
        bpp.BasePlatformDatasourceProcessor,
        "_apply_guardrails_for_documents",
        lambda _self, docs: None,
    )
    monkeypatch.setattr(
        bpp.BasePlatformDatasourceProcessor,
        "_split_entity_documents",
        lambda _self, docs: docs,
    )
    monkeypatch.setattr(
        bpp.BasePlatformDatasourceProcessor,
        "_add_documents",
        lambda _self, docs, is_update=False: None,
    )
    # The actual candidate search (`elasticsearch.helpers.scan`) and the
    # Postgres advisory-lock re-check (comment #25's fix) are exercised by
    # their own tests; here only the "delete the still-stale candidates"
    # wiring is under test.
    monkeypatch.setattr(
        bpp.BasePlatformDatasourceProcessor,
        "_find_candidate_stale_ids",
        lambda _self, live_ids: {"stale-1"},
    )
    monkeypatch.setattr(
        AssistantDatasourceProcessor,
        "_delete_stale_ids",
        bpp.BasePlatformDatasourceProcessor._delete_stale_ids,
    )

    indexed = processor._process()

    assert indexed == 2
    assert client.deleted_bodies, (
        "a full sync left every document for an entity that is no longer "
        "published in the index; 219 published assistants produced 220 documents"
    )
    query = client.deleted_bodies[0]
    # Chunks are stored as '<entity id>_<n>', so removal matches the entity id in the metadata.
    assert query["query"]["terms"]["metadata.id.keyword"] == ["stale-1"]


class _RecordingClient:
    """Records each `delete_by_query` together with whether the id's lock was held at that moment."""

    def __init__(self, held: set) -> None:
        self._held = held
        self.deletes: list[tuple[list, bool]] = []

    def delete_by_query(self, index, body, refresh=False, **kwargs):  # noqa: ARG002
        ids = body["query"]["terms"]["metadata.id.keyword"]
        self.deletes.append((ids, all(i in self._held for i in ids)))
        return {"deleted": len(ids)}


def _prune_processor(client, monkeypatch, states: dict):
    """An AssistantDatasourceProcessor whose per-id committed state is `states` (id -> True/False/None)."""
    from contextlib import contextmanager

    from codemie.datasource.platform.assistant_datasource_processor import (
        AssistantDatasourceProcessor,
    )
    from codemie.service.marketplace_management import marketplace_index_sync as mis

    @contextmanager
    def _committed_state(assistant_id):
        client._held.add(assistant_id)
        try:
            yield states[assistant_id]
        finally:
            client._held.discard(assistant_id)

    monkeypatch.setattr(mis, "_committed_state", _committed_state)
    processor = object.__new__(AssistantDatasourceProcessor)
    processor.datasource_name = "marketplace_assistants"
    processor.client = client
    return processor


def test_prune_does_not_delete_an_assistant_published_during_the_race_window(monkeypatch) -> None:
    """Comment #25: the full-reindex prune must not undo a concurrent publish.

    `_find_candidate_stale_ids` is a snapshot taken before this point; an
    assistant published in the window between that snapshot and the prune
    actually running is a false positive, not real staleness. The per-id
    re-check (under the same advisory lock `marketplace_index_sync` uses for
    its own writes) must keep it out of the deletion set.
    """
    client = _RecordingClient(held=set())
    processor = _prune_processor(client, monkeypatch, {"republished-mid-sync": True, "genuinely-stale": False})

    deleted = processor._delete_stale_ids({"republished-mid-sync", "genuinely-stale"})

    assert deleted == 1
    assert [ids for ids, _ in client.deletes] == [["genuinely-stale"]]


def test_prune_deletes_while_still_holding_the_per_assistant_lock(monkeypatch) -> None:
    """The re-check and the delete are one critical section, so a publish cannot fall between them."""
    client = _RecordingClient(held=set())
    processor = _prune_processor(client, monkeypatch, {"a": False, "b": False})

    processor._delete_stale_ids({"a", "b"})

    assert client.deletes, "nothing was deleted"
    assert all(lock_held for _, lock_held in client.deletes)
    assert sorted(ids[0] for ids, _ in client.deletes) == ["a", "b"]


def test_prune_skips_an_assistant_whose_state_cannot_be_read(monkeypatch) -> None:
    """`None` means Postgres could not be consulted -- not "unpublished"; never delete on it."""
    client = _RecordingClient(held=set())
    processor = _prune_processor(client, monkeypatch, {"unreadable": None, "genuinely-stale": False})

    deleted = processor._delete_stale_ids({"unreadable", "genuinely-stale"})

    assert deleted == 1
    assert [ids for ids, _ in client.deletes] == [["genuinely-stale"]]


def test_an_empty_load_never_wipes_the_index(monkeypatch) -> None:
    """A failed read of Postgres must not be mistaken for "nothing is published".

    Pruning on an empty document set would turn one bad database read into a
    total loss of marketplace search.
    """
    from codemie.datasource.platform import base_platform_processor as bpp
    from codemie.datasource.platform.assistant_datasource_processor import (
        AssistantDatasourceProcessor,
    )

    class _Client:
        def delete_by_query(self, *a, **k):  # noqa: ARG002
            raise AssertionError("an empty load must never delete anything")

    processor = object.__new__(AssistantDatasourceProcessor)
    processor.datasource_name = "marketplace_assistants"
    processor.index = type("_Idx", (), {"embeddings_model": "ada-002"})()
    processor.loader = type("_L", (), {"lazy_load": lambda _self: iter([])})()
    processor.client = _Client()
    monkeypatch.setattr(
        bpp.BasePlatformDatasourceProcessor,
        "_apply_guardrails_for_documents",
        lambda _self, docs: None,
    )

    assert processor._process() == 0


# ---------------------------------------------------------------------------
# The live invariant
# ---------------------------------------------------------------------------


_LIVE_PG = os.getenv("MM_TEST_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")
_LIVE_ES = os.getenv("MM_TEST_ES_URL", "http://localhost:9200")


def _live_stack_reachable() -> bool:
    import socket
    from sqlalchemy.engine import make_url as _make_url

    try:
        u = _make_url(_LIVE_PG)
        socket.create_connection((u.host or "localhost", u.port or 5432), timeout=2).close()
    except OSError:
        return False
    try:
        import urllib.request

        urllib.request.urlopen(_LIVE_ES, timeout=2).close()
    except Exception:  # noqa: BLE001
        return False
    return True


@pytest.mark.live_index
def test_published_assistants_are_all_indexed() -> None:
    """`count(is_global) == _count(marketplace_assistants)` on the real stack.

    Deliberately not a mocked assertion: the whole finding is that the two
    stores disagreed while every in-process check said they agreed. Measured
    2026-08-23 before the backfill: 219 vs 19.
    """
    if not _live_stack_reachable():
        pytest.skip(f"Live stack not reachable at {_LIVE_PG} / {_LIVE_ES}")
    published = _live_published_count()
    indexed = _live_indexed_count()

    assert published == indexed, (
        f"{published - indexed} of {published} published assistants are missing from "
        f"the marketplace index ({indexed} documents). Run the backfill: "
        f"POST /v1/admin/marketplace/reindex."
    )


def _live_published_count() -> int:
    from sqlalchemy import create_engine

    eng = create_engine(_LIVE_PG, pool_pre_ping=True)
    try:
        with eng.connect() as conn:
            return int(conn.execute(text("select count(*) from codemie.assistants where is_global")).scalar_one())
    finally:
        eng.dispose()


def _live_indexed_count() -> int:
    import json
    import urllib.error
    import urllib.request

    url = f"{_LIVE_ES.rstrip('/')}/marketplace_assistants/_count"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return int(json.loads(resp.read())["count"])
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return 0  # index not created yet: zero documents, honestly reported
        raise


# ---------------------------------------------------------------------------
# The fifth mechanism: writes that are *ordered by chance* (13.1)
# ---------------------------------------------------------------------------
#
# Measured 2026-08-25 on the live stack, after the 9.6 backfill had run:
#
#     select count(*) from codemie.assistants where is_global   ->  219
#     curl -s localhost:9200/marketplace_assistants/_count      ->  226
#
# Seven documents too *many*, and none of the seven has a row in Postgres at
# all -- they are assistants that were published, indexed, and then deleted:
#
#     271cb471-...  "E2E E2E-mt7mn7xo-0 pending queue self test"
#     2fa02cff-...  "FIXTURE mt7pzfqh pending"
#     30b29cfc-...  "E2E E2E-mt7m9lln-1 submitted-at"
#     5609397f-...  "E2E E2E-mt7mn9wd-1 es teardown self test"
#     5d35e3b8-...  "E2E E2E-mt7m9xzp-0 contract"
#     a3f5e163-...  "E2E E2E-mt7m9kl2-0 contract"
#     f69a0845-...  "FIXTURE mt7pyxho pending"
#
# Two distinct defects put them there.
#
# **DELETE never de-indexes.** `routers/assistant.py::delete_assistant` removes
# the row and returns. There is no `_remove_marketplace_assistant` on that path
# at all, so deleting a *published* assistant orphans its document forever, and
# the marketplace keeps serving a listing whose assistant no longer exists.
#
# **publish -> unpublish is a lost delete.** Both paths hand their index write
# to `BackgroundTasks`, which runs sync callables in a threadpool, so the
# publish-time index and the unpublish-time remove are two unordered threads.
# When the remove wins, it 404s -- and since 12.3 (D4) a 404 is correctly
# reported as success -- and the losing index write lands afterwards and
# re-creates the document. D4 did not create this race; it removed the only
# signal that it had happened. The same interleaving used to surface as a
# FAILED outcome and two tracebacks. Making a non-error quiet is right; the
# cost was that a real bug lost its last symptom, which is why the fix here is
# an ordering guarantee and not a louder log line.


class _FakeIndex:
    """The marketplace index, reduced to the only property under test."""

    def __init__(self) -> None:
        self.ids: set[str] = set()
        self.calls: list[str] = []
        self.on_index = None  # optional barrier, called before the write lands

    def index_single_assistant(self, assistant_id: str, user: Any = None, is_update: bool = False) -> None:
        self.calls.append(f"index:{assistant_id}")
        if self.on_index is not None:
            self.on_index()
        self.ids.add(assistant_id)

    def remove_single_assistant(self, assistant_id: str, assistant_name: str | None = None, user: Any = None) -> None:
        self.calls.append(f"remove:{assistant_id}")
        if assistant_id not in self.ids:
            raise _NotFound404(f"no such document: {assistant_id}")
        self.ids.discard(assistant_id)


class _NotFound404(Exception):
    """What Elasticsearch raises for a delete of an absent document."""

    status_code = 404


@pytest.fixture
def fake_index(monkeypatch: pytest.MonkeyPatch) -> _FakeIndex:
    from codemie.service.platform.platform_indexing_service import PlatformIndexingService

    index = _FakeIndex()
    monkeypatch.setattr(
        PlatformIndexingService,
        "index_single_assistant",
        staticmethod(index.index_single_assistant),
    )
    monkeypatch.setattr(
        PlatformIndexingService,
        "remove_single_assistant",
        staticmethod(index.remove_single_assistant),
    )
    return index


def _fake_user():
    from unittest.mock import MagicMock

    user = MagicMock()
    user.id = "admin-idx"
    user.name = "Idx Admin"
    user.current_project = "demo"
    return user


def test_deleting_a_published_assistant_removes_its_document(db: Engine, fake_index: _FakeIndex, monkeypatch) -> None:
    """Deleting a published assistant must take its listing down with it.

    Seven of the seven surplus documents measured on 2026-08-25 are exactly
    this: an id present in Elasticsearch with no row in Postgres. The
    marketplace goes on serving a card for an assistant that cannot be opened,
    cloned or run, and no reconcile pass existed to notice.
    """
    from codemie.rest_api.routers.assistant import management as assistant_router
    from codemie.rest_api.models.assistant import Assistant

    _seed(db, "del-published", "APPROVED", is_global=True)
    fake_index.ids.add("del-published")

    row = _AssistantRow(id="del-published", name="Assistant del-published", is_global=True)
    monkeypatch.setattr(assistant_router, "get_assistant_by_id_or_raise", lambda _id: row)
    monkeypatch.setattr(assistant_router, "check_user_can_access_assistant", lambda *a, **k: None)
    monkeypatch.setattr(assistant_router, "_track_mcp_usage_on_delete", lambda *a, **k: None)
    monkeypatch.setattr(assistant_router, "_track_assistant_management_metric", lambda *a, **k: None)

    def _delete(assistant_id: str, user=None) -> None:
        with db.connect() as conn:
            conn.execute(text("delete from codemie.assistants where id = :i"), {"i": assistant_id})
            conn.commit()

    monkeypatch.setattr(Assistant, "delete_assistant", staticmethod(_delete))

    assistant_router.delete_assistant("del-published", user=_fake_user())

    assert "del-published" not in fake_index.ids, (
        "deleting a published assistant left its document in the marketplace "
        "index; the listing outlives the assistant and nothing removes it. "
        f"index calls={fake_index.calls}"
    )


def test_deleting_an_unpublished_assistant_does_not_error(db: Engine, fake_index: _FakeIndex, monkeypatch) -> None:
    """The negative control: a draft has no document, and that is not a failure."""
    from codemie.rest_api.routers.assistant import management as assistant_router
    from codemie.rest_api.models.assistant import Assistant

    _seed(db, "del-draft", "DRAFT", is_global=False)

    row = _AssistantRow(id="del-draft", name="Assistant del-draft", is_global=False)
    monkeypatch.setattr(assistant_router, "get_assistant_by_id_or_raise", lambda _id: row)
    monkeypatch.setattr(assistant_router, "check_user_can_access_assistant", lambda *a, **k: None)
    monkeypatch.setattr(assistant_router, "_track_mcp_usage_on_delete", lambda *a, **k: None)
    monkeypatch.setattr(assistant_router, "_track_assistant_management_metric", lambda *a, **k: None)
    monkeypatch.setattr(
        Assistant,
        "delete_assistant",
        staticmethod(lambda assistant_id, user=None: None),
    )

    assistant_router.delete_assistant("del-draft", user=_fake_user())

    assert fake_index.ids == set()


def test_unpublish_immediately_after_publish_leaves_no_document(db: Engine, fake_index: _FakeIndex) -> None:
    """The lost delete: the losing index write must not resurrect the document.

    Faithful to the real sequence rather than a hopeful one. Both HTTP handlers
    commit their intent to Postgres *before* scheduling their background task,
    so by the time either task runs the row already reads `is_global = false`.
    The interleaving is then forced, not raced: the index write is held at a
    barrier until the remove has completed and reported OK, which is the
    ordering the production log showed (remove 404s, state OK, index lands
    ~1s later). Forcing it makes this a proof and not a flaky test.
    """
    import threading

    from codemie.rest_api.routers.assistant._shared import (
        _index_marketplace_assistant,
        _remove_marketplace_assistant,
    )

    # Publish committed, then unpublish committed. Both before either task runs.
    _seed(db, "race-1", "WITHDRAWN", is_global=False)
    fake_index.ids.add("race-1")  # the publish-time document, already there

    removed = threading.Event()
    fake_index.on_index = lambda: removed.wait(timeout=10)

    errors: list[BaseException] = []

    def _run(fn, *args):
        try:
            fn(*args)
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            errors.append(exc)

    user = _fake_user()
    indexer = threading.Thread(target=_run, args=(_index_marketplace_assistant, "race-1", user), daemon=True)
    remover = threading.Thread(
        target=_run,
        args=(_remove_marketplace_assistant, "race-1", "Assistant race-1", user),
        daemon=True,
    )

    indexer.start()
    remover.start()
    remover.join(timeout=20)
    removed.set()
    indexer.join(timeout=20)

    assert not errors, errors
    assert not indexer.is_alive() and not remover.is_alive(), "index/remove deadlocked"
    assert "race-1" not in fake_index.ids, (
        "the losing index write re-created the document after the remove had "
        "already reported OK: an unpublished assistant is still findable in "
        f"marketplace search. index calls={fake_index.calls}"
    )


def test_a_stale_remove_does_not_delete_a_republished_assistant(db: Engine, fake_index: _FakeIndex) -> None:
    """The same race pointing the other way, which a delete-wins rule would break.

    unpublish -> publish, with the remove landing last. Ordering must converge
    on what Postgres says, not on 'whichever write is a delete'.
    """
    import threading

    from codemie.rest_api.routers.assistant._shared import (
        _index_marketplace_assistant,
        _remove_marketplace_assistant,
    )

    _seed(db, "race-2", "APPROVED", is_global=True)  # republished; committed

    indexed = threading.Event()
    original_index = fake_index.index_single_assistant

    def _index(assistant_id, user=None, is_update=False):
        original_index(assistant_id, user=user, is_update=is_update)
        indexed.set()

    from codemie.service.platform.platform_indexing_service import PlatformIndexingService

    PlatformIndexingService.index_single_assistant = staticmethod(_index)

    user = _fake_user()
    indexer = threading.Thread(target=_index_marketplace_assistant, args=("race-2", user), daemon=True)
    indexer.start()
    indexer.join(timeout=20)
    assert indexed.wait(timeout=5)

    _remove_marketplace_assistant("race-2", "Assistant race-2", user)

    assert "race-2" in fake_index.ids, (
        "a stale removal deleted the document of an assistant that is currently "
        f"published (is_global = true). index calls={fake_index.calls}"
    )


class _AssistantRow:
    """Just enough of `Assistant` for the delete handler."""

    def __init__(self, *, id: str, name: str, is_global: bool) -> None:
        self.id = id
        self.name = name
        self.is_global = is_global
        self.mcp_servers = []
        self.project = "demo"


def test_a_write_in_flight_blocks_the_opposite_write_for_the_same_assistant(db: Engine, fake_index: _FakeIndex) -> None:
    """Re-reading the committed row is not enough on its own; it must be locked.

    The interleaving that the truth read alone cannot survive: the index write
    reads `is_global = true`, the assistant is unpublished, the removal runs and
    finds nothing to delete, and only then does the index write land. Every
    individual step is correct and the end state is still an orphan. Holding
    the lock across the read *and* the write is what closes it, so this test
    exists to fail if the lock is ever removed as redundant.
    """
    import threading

    from codemie.service.marketplace_management import marketplace_index_sync

    _seed(db, "lock-1", "APPROVED", is_global=True)

    entered = threading.Event()
    proceed = threading.Event()

    def _barrier() -> None:
        entered.set()
        proceed.wait(timeout=10)

    fake_index.on_index = _barrier

    indexer = threading.Thread(target=marketplace_index_sync.index_assistant, args=("lock-1",), daemon=True)
    indexer.start()
    assert entered.wait(timeout=10), "the index write never started"

    # The unpublish commits while the index write is in flight.
    with db.connect() as conn:
        conn.execute(text("update codemie.assistants set is_global = false where id='lock-1'"))
        conn.commit()

    remover = threading.Thread(
        target=marketplace_index_sync.remove_assistant,
        args=("lock-1", "Assistant lock-1"),
        daemon=True,
    )
    remover.start()
    remover.join(timeout=1.5)
    assert remover.is_alive(), (
        "the removal ran while an index write for the same assistant was still "
        "in flight; nothing serialises the two and the loser decides the "
        f"outcome. calls={fake_index.calls}"
    )

    proceed.set()
    indexer.join(timeout=20)
    remover.join(timeout=20)
    assert not indexer.is_alive() and not remover.is_alive()

    assert fake_index.calls == ["index:lock-1", "remove:lock-1"], fake_index.calls
    assert "lock-1" not in fake_index.ids, (
        "an unpublished assistant is still in the marketplace index after both "
        f"writes settled. calls={fake_index.calls}"
    )


def test_the_write_lock_is_held_in_postgres_not_only_in_this_process(db: Engine, fake_index: _FakeIndex) -> None:
    """The lock has to survive more than one worker.

    A `threading.Lock` orders two BackgroundTasks inside one uvicorn worker and
    nothing else; publish and unpublish arriving at two pods would interleave
    exactly as before. The serialisation is therefore a Postgres advisory lock
    -- the same primitive `postgres.py` and `utils/leader_lock.py` already use
    -- and this test proves it by taking that lock from an unrelated connection
    and watching the index write wait for it.
    """
    import threading

    from codemie.service.marketplace_management import marketplace_index_sync

    _seed(db, "lock-2", "APPROVED", is_global=True)

    conn = db.connect()
    try:
        conn.execute(
            text("select pg_advisory_xact_lock(:ns, :key)"),
            {
                "ns": marketplace_index_sync._ADVISORY_LOCK_NAMESPACE,
                "key": marketplace_index_sync._advisory_key("lock-2"),
            },
        )

        done = threading.Event()

        def _index() -> None:
            marketplace_index_sync.index_assistant("lock-2")
            done.set()

        worker = threading.Thread(target=_index, daemon=True)
        worker.start()

        assert not done.wait(timeout=2.0), (
            "an index write proceeded while another connection held the "
            "advisory lock for that assistant: the ordering guarantee is "
            "process-local and does not hold across workers or pods"
        )
    finally:
        conn.rollback()
        conn.close()

    assert done.wait(timeout=15), "the index write never resumed after the lock was released"
    assert "lock-2" in fake_index.ids


def test_an_unreadable_committed_state_does_not_invent_a_removal(monkeypatch) -> None:
    """Unreadable DB state must never cause a removal of a live listing (CR-017).

    For a *remove* request: no-opinion means "do as asked" — safe, since
    removing an unknown assistant cannot surface bad data.
    For an *index* request: CR-017 requires the write to be SKIPPED rather than
    applied blindly, so stale data is never written into the index when the
    committed row cannot be verified.  The `bool(x)` trap still applies — a
    non-boolean published value must be treated as unknown.
    """
    from codemie.service.marketplace_management import marketplace_index_sync as mis

    class _Row:
        def __getitem__(self, _i):
            return object()  # truthy, and not an answer

    class _Result:
        def first(self):
            return _Row()

    class _Session:
        def execute(self, *a, **k):  # noqa: ARG002
            return _Result()

    assert mis._read_committed_state(_Session(), "whatever") is None

    calls: list[str] = []

    class _Stub:
        @staticmethod
        def remove_single_assistant(assistant_id, assistant_name, user=None):  # noqa: ARG004
            calls.append(f"remove:{assistant_id}")

        @staticmethod
        def index_single_assistant(assistant_id, user=None, is_update=False):  # noqa: ARG004
            calls.append(f"index:{assistant_id}")

    monkeypatch.setattr(mis, "_import_platform_indexing_service", lambda: _Stub)

    outcome = mis.remove_assistant("unknown-state")
    assert outcome.ok and outcome.action == "remove" and not outcome.superseded, outcome
    assert calls == ["remove:unknown-state"], calls

    calls.clear()
    # CR-017: an index request with unknown committed state is SKIPPED to avoid
    # writing stale data; the indexing service is never called in this path.
    outcome = mis.index_assistant("unknown-state")
    assert not outcome.ok and outcome.action == "index" and outcome.state == "SKIPPED", outcome
    assert calls == [], calls
