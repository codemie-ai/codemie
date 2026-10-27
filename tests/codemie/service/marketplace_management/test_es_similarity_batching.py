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

"""The similarity phase must embed the corpus in batches, not one call per record.

MEASURED, live stack, 2026-08-24 (219 published assistants, local Elasticsearch,
Azure OpenAI `codemie-text-embedding-ada-002`):

    one embed_query round trip .............. 0.73 s
    one similarity_search_with_score ........ 1.17 s cold / 0.37 s warm
    embed_documents([219 texts]) ............ 3.34 s        <- ONE round trip
    one search_by_vector (vector in hand) ... 0.003 s

    57 SUCCEEDED audit runs in mm_audit_run: mean wall clock 196 s.

219 x (embed + query) is the 196 s. The embedding is the whole cost and it is
paid one text at a time; the Elasticsearch half is three milliseconds. That is
F-95 -- 282 sequential embedding round trips, no batching, no cache -- and it is
also why the 10 s circuit breaker fires at all:

    8 runs in mm_audit_run are PARTIAL / UNAVAILABLE with
    "TimeoutError: Elasticsearch KNN exceeded its 10.0s budget",
    against 57 that succeeded with similarity_backend = ES.

The failure is intermittent, not universal. One embedding call in a couple of
hundred takes longer than ten seconds -- a provider hiccup, or the openai SDK's
`max_retries=10` backoff on a 429 -- and the breaker trips *for the rest of the
run*, discarding the neighbours already gathered for the records before it. Two
hundred and nineteen sequential chances to draw a slow call is the actual
mechanism; making it one collapses the exposure by the same factor it collapses
the runtime.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from codemie.service.marketplace_management import es_similarity
from codemie.service.marketplace_management.es_similarity import (
    MarketplaceAssistantsIndex,
    marketplace_assistants_index,
)


class _CountingEmbeddings:
    """Records how many round trips the caller makes, and how wide each one is."""

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def embed_documents(self, texts):
        self.batch_sizes.append(len(texts))
        return [[float(len(t)), 0.5, 0.25] for t in texts]

    def embed_query(self, text):
        self.batch_sizes.append(1)
        return [float(len(text)), 0.5, 0.25]

    @property
    def calls(self) -> int:
        return len(self.batch_sizes)


class _FakeDoc:
    def __init__(self, aid: str, name: str) -> None:
        self.metadata = {"id": aid, "name": name}


class _CountingStore:
    """An ElasticsearchStore stand-in that embeds through `_CountingEmbeddings`."""

    def __init__(self, embeddings: _CountingEmbeddings) -> None:
        self.embeddings = embeddings
        self.vector_queries = 0

    def similarity_search_with_score(self, query_text, k):
        self.embeddings.embed_query(query_text)
        return self._hits(k)

    def similarity_search_by_vector_with_relevance_scores(self, embedding, k, **kwargs):
        self.vector_queries += 1
        return self._hits(k)

    @staticmethod
    def _hits(k):
        return [(_FakeDoc(f"neighbour-{i}", f"Neighbour {i}"), 0.9 - i / 100) for i in range(k)]


@pytest.fixture(autouse=True)
def _reset_breaker():
    marketplace_assistants_index.reset_similarity_circuit()
    yield
    marketplace_assistants_index.reset_similarity_circuit()


@pytest.fixture
def counting_store(monkeypatch):
    embeddings = _CountingEmbeddings()
    store = _CountingStore(embeddings)
    monkeypatch.setattr(MarketplaceAssistantsIndex, "get_store", classmethod(lambda cls: store))
    return store


CORPUS = [(f"assistant-{i}", f"assistant {i} that reviews python code") for i in range(219)]


def test_the_corpus_is_embedded_in_one_round_trip(counting_store):
    """219 records must not cost 219 embedding round trips (F-95)."""
    results = marketplace_assistants_index.knn_batch(CORPUS, k=5)

    embed_calls = counting_store.embeddings.calls
    assert embed_calls == 1, (
        f"embedding the corpus took {embed_calls} round trips for {len(CORPUS)} "
        f"records (batch widths: {counting_store.embeddings.batch_sizes[:5]}...). "
        "Measured live: one round trip embeds all 219 texts in 3.3s, while 219 "
        "separate ones take 196s and give the 10s breaker 219 chances to trip."
    )
    assert counting_store.embeddings.batch_sizes == [len(CORPUS)]
    assert len(results) == len(CORPUS)


def test_batch_excludes_each_record_from_its_own_neighbours(counting_store):
    """`knn_for_assistant_text` never returns the record itself; batch must match."""
    corpus = [("neighbour-0", "a self-similar assistant"), ("other", "another assistant")]

    results = marketplace_assistants_index.knn_batch(corpus, k=5)

    assert "neighbour-0" not in [
        n.assistant_id for n in results["neighbour-0"]
    ], "a record was returned as its own nearest neighbour"
    assert "neighbour-0" in [n.assistant_id for n in results["other"]], "the exclusion must be per-record, not global"


def test_batch_returns_at_most_k_per_record(counting_store):
    results = marketplace_assistants_index.knn_batch(CORPUS[:3], k=4)
    for aid, neighbours in results.items():
        assert len(neighbours) <= 4, f"{aid} got {len(neighbours)} neighbours for k=4"


# ---------------------------------------------------------------------------
# The breaker must survive a blip
# ---------------------------------------------------------------------------


class _OneSlowCallEmbeddings(_CountingEmbeddings):
    """Fast, except for the Nth call, which blows the budget. The live pattern."""

    def __init__(self, slow_on: int, release: threading.Event) -> None:
        super().__init__()
        self.slow_on = slow_on
        self.release = release
        # Counted on entry, not on return: the slow call never returns within
        # the budget, so counting on return would make every later call "first"
        # as well -- and the test would be asserting on its own stub, not on
        # the breaker.
        self.entered = 0

    def embed_documents(self, texts):
        self.entered += 1
        if self.entered == self.slow_on:
            self.release.wait(timeout=30)
        return super().embed_documents(texts)


def test_one_slow_batch_does_not_discard_the_rest_of_the_run(monkeypatch):
    """A single provider hiccup must not cost the whole corpus its similarity.

    The live breaker latches on the first timeout and returns [] for every
    later lookup, so a blip 80% of the way through a run threw away the
    neighbours for the other 80% too and reported the run UNAVAILABLE.
    """
    release = threading.Event()
    embeddings = _OneSlowCallEmbeddings(slow_on=1, release=release)
    store = _CountingStore(embeddings)
    monkeypatch.setattr(MarketplaceAssistantsIndex, "get_store", classmethod(lambda cls: store))
    monkeypatch.setattr(es_similarity, "ES_SIMILARITY_TIMEOUT_SECONDS", 0.3)
    try:
        started = time.monotonic()
        # Two chunks: the first blows its budget, the second must still run.
        results = marketplace_assistants_index.knn_batch(CORPUS[:4], k=3, chunk_size=2)
        elapsed = time.monotonic() - started

        assert elapsed < 5.0, f"the slow chunk was not bounded ({elapsed:.1f}s)"
        recovered = [aid for aid, n in results.items() if n]
        assert recovered, (
            "one slow chunk discarded every later chunk as well. A transient "
            "hiccup must cost only the records in the chunk it hit."
        )
    finally:
        release.set()


def test_giving_up_names_the_thing_an_operator_must_check(monkeypatch, codemie_logs):
    """ "exceeded its 10.0s budget" says what tripped, not what is wrong.

    The live run row carried only the budget message, so the admin reading it
    learned the audit had a timer and nothing about where to look. The
    abandonment log must name the embeddings deployment.
    """
    release = threading.Event()
    embeddings = _OneSlowCallEmbeddings(slow_on=1, release=release)
    # Every chunk is slow: this is the systemic-outage case the breaker is for.
    embeddings.slow_on = -1
    original = embeddings.embed_documents

    def _always_slow(texts):
        release.wait(timeout=30)
        return original(texts)

    embeddings.embed_documents = _always_slow
    store = _CountingStore(embeddings)
    monkeypatch.setattr(MarketplaceAssistantsIndex, "get_store", classmethod(lambda cls: store))
    monkeypatch.setattr(
        MarketplaceAssistantsIndex,
        "_embedding_deployment_name",
        classmethod(lambda cls: "codemie-text-embedding-ada-002"),
    )
    monkeypatch.setattr(es_similarity, "ES_SIMILARITY_TIMEOUT_SECONDS", 0.05)
    try:
        marketplace_assistants_index.knn_batch(CORPUS[:6], k=3, chunk_size=2)
    finally:
        release.set()

    giving_up = [r.getMessage() for r in codemie_logs.records if r.levelno >= logging.ERROR]
    assert giving_up, "abandoning similarity for the whole run was not logged at ERROR"
    assert "codemie-text-embedding-ada-002" in giving_up[-1], (
        "the log names a budget but not the embeddings deployment behind it: " + giving_up[-1]
    )
