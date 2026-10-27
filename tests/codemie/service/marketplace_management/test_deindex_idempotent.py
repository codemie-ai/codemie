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

"""Removing a marketplace document that is already absent is success, not failure.

Observed on the live stack while withdrawing an assistant that had never been
indexed (or had already been removed):

    marketplace remove attempt 1/2 failed for <id>: NotFoundError(404, ...
        {'result': 'not_found'})
    marketplace remove attempt 2/2 failed for <id>: NotFoundError(404, ...
    marketplace remove failed for <id> after 2 attempts: NotFoundError: ...

Two full tracebacks, one 0.25s backoff sleep inside a moderation request, and an
`IndexingOutcome(state="FAILED")` handed back to the caller -- all to report that
the document the caller wanted gone is gone.

De-indexing is a *desired end state*, not a transaction: "this id must not be in
the index" is already true when Elasticsearch answers 404. Retrying cannot
improve on it, and reporting FAILED makes an admin chase an outage that does not
exist -- and, worse, trains them to ignore the one that does.
"""

from __future__ import annotations

import logging


from codemie.service.marketplace_management import marketplace_index_sync as mis


class _FakeNotFoundError(Exception):
    """Shaped like `elasticsearch.NotFoundError`, without importing the client.

    The real exception carries `status_code`/`meta.status` = 404; the string
    form is what appeared in the live log.
    """

    status_code = 404

    def __init__(self) -> None:
        super().__init__("NotFoundError(404, \"{'_index': 'marketplace_assistants', " "'result': 'not_found'}\")")


def test_remove_of_absent_document_succeeds_without_retry(monkeypatch, codemie_logs):
    """A 404 from the index means the document is already gone: that is OK."""
    calls: list[str] = []

    def _raise_404(assistant_id, assistant_name, user=None):
        calls.append(assistant_id)
        raise _FakeNotFoundError

    class _StubIndexingService:
        remove_single_assistant = staticmethod(_raise_404)

    monkeypatch.setattr(
        mis,
        "_import_platform_indexing_service",
        lambda: _StubIndexingService,
        raising=False,
    )

    slept: list[float] = []
    monkeypatch.setattr(mis.time, "sleep", lambda s: slept.append(s))

    outcome = mis.remove_assistant("assistant-that-is-not-indexed")

    assert outcome.ok, (
        f"removing an absent document reported {outcome.state}: {outcome.error}. "
        "The requested end state -- no such document in the index -- already holds."
    )
    assert outcome.attempts == 1, (
        f"a 404 was retried {outcome.attempts} times. Retrying cannot make an "
        "absent document more absent; it only adds latency to a moderation request."
    )
    assert calls == ["assistant-that-is-not-indexed"], calls
    assert slept == [], f"backoff slept {slept} for a non-failure"

    tracebacks = [r for r in codemie_logs.records if "Traceback (most recent call last)" in r.getMessage()]
    assert not tracebacks, (
        f"{len(tracebacks)} traceback(s) logged for a document that was already "
        "absent: " + "; ".join(r.getMessage()[:80] for r in tracebacks)
    )
    assert not [
        r for r in codemie_logs.records if r.levelno >= logging.WARNING
    ], "an already-absent document must not be reported at WARNING or above"


def test_remove_still_reports_a_real_failure(monkeypatch):
    """The 404 shortcut must not swallow genuine index failures."""

    class _StubIndexingService:
        @staticmethod
        def remove_single_assistant(assistant_id, assistant_name, user=None):
            raise ConnectionError("connection refused")

    monkeypatch.setattr(
        mis,
        "_import_platform_indexing_service",
        lambda: _StubIndexingService,
        raising=False,
    )
    monkeypatch.setattr(mis.time, "sleep", lambda s: None)

    outcome = mis.remove_assistant("assistant-1")

    assert not outcome.ok
    assert outcome.attempts == mis.INDEX_ATTEMPTS
    assert "connection refused" in (outcome.error or "")
