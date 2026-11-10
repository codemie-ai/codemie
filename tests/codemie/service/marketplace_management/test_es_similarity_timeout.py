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

"""The ES similarity lookup must be bounded in wall-clock time.

Root cause this guards against: `knn` reaches Elasticsearch through
langchain's ElasticsearchStore, which first calls the embeddings provider over
HTTP. That request carries no timeout, so when the embeddings endpoint is
unreachable the socket connect blocks forever. `knn` is called once per record
inside the audit loop, so a single blocked connect wedges the whole audit
thread: `start_audit`'s `finally` never runs and `_running` stays True for the
lifetime of the process.

Observed on the live stack with py-spy:

    Thread "marketplace-audit-assistants" (idle):
        create_connection (socket.py:850)
        ...
        knn (codemie/service/marketplace_management/es_similarity.py:73)

`knn` already documents that it returns [] when the lookup fails. A hang is a
failure, so it must honour that contract rather than block indefinitely.
"""

import threading
import time

import pytest

from codemie.service.marketplace_management import es_similarity
from codemie.service.marketplace_management.es_similarity import (
    MarketplaceAssistantsIndex,
    marketplace_assistants_index,
)

_SCHEMA = "codemie"


class _BlockingStore:
    """Stands in for an ElasticsearchStore whose embeddings call never returns."""

    def __init__(self, release: threading.Event) -> None:
        self.release = release
        self.calls = 0

    def similarity_search_with_score(self, query_text, k):  # noqa: ARG002
        self.calls += 1
        # Blocks until the test releases it, emulating a socket connect with
        # no timeout. Never sleeps unbounded so the suite cannot hang.
        self.release.wait(timeout=30)
        return []


@pytest.fixture(autouse=True)
def _reset_breaker():
    marketplace_assistants_index.reset_similarity_circuit()
    yield
    marketplace_assistants_index.reset_similarity_circuit()


@pytest.fixture
def blocking_store(monkeypatch):
    release = threading.Event()
    store = _BlockingStore(release)
    monkeypatch.setattr(MarketplaceAssistantsIndex, "get_store", classmethod(lambda cls: store))
    monkeypatch.setattr(es_similarity, "ES_SIMILARITY_TIMEOUT_SECONDS", 0.5)
    yield store
    release.set()


def test_knn_returns_empty_instead_of_blocking_forever(blocking_store):
    """A store that never responds must not hold the audit thread."""
    started = time.monotonic()

    result = marketplace_assistants_index.knn("some assistant purpose", k=5)

    elapsed = time.monotonic() - started
    assert result == []
    assert elapsed < 5.0, (
        f"knn blocked for {elapsed:.1f}s. It must give up after "
        f"ES_SIMILARITY_TIMEOUT_SECONDS and return [] so the audit worker "
        f"thread can finish and clear its is_running flag."
    )


def test_repeated_calls_short_circuit_after_the_first_timeout(blocking_store):
    """The audit calls knn once per record; only the first may pay the timeout.

    Without a circuit breaker a dead embeddings endpoint costs
    ES_SIMILARITY_TIMEOUT_SECONDS per assistant and leaks one blocked thread
    per call.
    """
    marketplace_assistants_index.knn("first", k=5)
    calls_after_first = blocking_store.calls

    started = time.monotonic()
    for _ in range(5):
        assert marketplace_assistants_index.knn("subsequent", k=5) == []
    elapsed = time.monotonic() - started

    assert (
        blocking_store.calls == calls_after_first
    ), "after one timeout the lookup must be skipped, not retried per record"
    assert elapsed < 0.5, f"five short-circuited calls took {elapsed:.2f}s"
