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

"""Read the analysis inputs from Elasticsearch: complete, partitioned aggregations, a few at a time.

``terms`` aggregations over people, repository values and projects use ``include.partition`` so that each response
stays under ``search.max_buckets``; the number of partitions comes from the cardinalities of A0. When a partition
overflows (``sum_other_doc_count`` > 0) or Elasticsearch answers ``too_many_buckets_exception`` or
``circuit_breaking_exception``, that query is retried with twice as many partitions. Each response is parsed into
the compact inputs as soon as it arrives and dropped. Responses leave out document counts: the analysis never reads
them, and every bucket and filter carries one.

A request that fails for a transient reason (connection reset, 502/503/504, failed shards) is retried a few times
with a growing pause. A request that timed out is not retried as transient, nor a generic 429: sending the same
request again would only add load. Two specific overload signals are the exception, because the retry is not the
same request sent again, but a smaller one that actually fits: ``too_many_buckets_exception`` and
``circuit_breaking_exception`` are both recognised in the error body and in the reasons of failed shards, wherever
Elasticsearch reports them, and handled as a partition overflow (``_retry_with_more_partitions``). A
``too_many_buckets_exception`` is a deterministic, cheap rejection (the request has more buckets than
``search.max_buckets`` allows) and costs the cluster nothing, so the smaller retry goes out immediately; a
``circuit_breaking_exception`` can also mean the node is generally low on memory from unrelated load, so that round
waits a growing pause first, the same way a transient error does.
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from elastic_transport import ConnectionError as TransportConnectionError

logger = logging.getLogger(__name__)

_MAX_ROUNDS = 4
# Exclusions apply first, then the inclusions. ``sum_other_doc_count`` stays: it tells that a partition overflowed.
_FILTER_PATH = [
    "took",
    "timed_out",
    "_shards.failed",
    "_shards.failures.reason",
    "aggregations",
    "-**.doc_count",
    "-**.doc_count_error_upper_bound",
]
_TRANSIENT_RETRIES = 3
_RETRY_PAUSE_S = 1.0
_TRANSIENT_STATUS = frozenset({502, 503, 504})
_TOO_MANY_BUCKETS = "too_many_buckets_exception"
_CIRCUIT_BREAKING = "circuit_breaking_exception"
_OVERLOAD_TYPES = (_TOO_MANY_BUCKETS, _CIRCUIT_BREAKING)


class FetchError(RuntimeError):
    """An Elasticsearch request of the analysis failed."""


class _ShardsFailedError(RuntimeError):
    """Some shards failed to answer: the response is incomplete."""


def error_types(node: Any) -> set[str]:
    """The ``type`` of an Elasticsearch error and of everything nested in it (root causes, causes, shard failures)."""
    found: set[str] = set()
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if isinstance(item.get("type"), str):
                found.add(item["type"])
            stack.extend(item.get(key) for key in ("error", "root_cause", "caused_by", "reason", "failed_shards"))
        elif isinstance(item, list):
            stack.extend(item)
    return found


def _overload_type(node: Any) -> str | None:
    """Which of ``_OVERLOAD_TYPES`` (if any) is present among ``node``'s error types, in a fixed preference order."""
    found = error_types(node)
    return next((t for t in _OVERLOAD_TYPES if t in found), None)


def is_transient(exc: Exception) -> bool:
    """Worth retrying: the connection broke, Elasticsearch was restarting or unavailable, or shards failed."""
    if isinstance(exc, (TransportConnectionError, _ShardsFailedError)):
        return True
    meta = getattr(exc, "meta", None)
    status = getattr(meta, "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status in _TRANSIENT_STATUS


@dataclass
class Spec:
    """One logical query. Partitioned specs merge the buckets of the terms aggregation at ``path`` (under
    ``aggregations``) from every partition; the others parse the whole ``aggregations`` object."""

    key: str
    label: str
    builder: Callable[[tuple[int, int] | None, int], dict | None]
    parse: Callable[[Any, Any], Any]  # (buckets or aggregations, accumulator) -> accumulator
    new_acc: Callable[[], Any]
    est: float = 1.0
    per_item: float = 1.0
    path: tuple[str, ...] | None = None
    parts: int = 1
    result: Any = None
    stats: dict[str, Any] = field(default_factory=lambda: {"requests": 0, "took_ms": 0, "wall_ms": 0.0, "kb": 0.0})

    @property
    def partitioned(self) -> bool:
        return self.path is not None


def _dig(node: Any, path: tuple[str, ...]) -> Any:
    for part in path:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


class Fetcher:
    """Runs specs against Elasticsearch with bounded concurrency (in the worker process)."""

    def __init__(
        self, client: Any, index: str, concurrency: int, bucket_share: float, max_buckets: int, timeout_s: int
    ):
        self.client = client
        self.index = index
        self.concurrency = max(1, int(concurrency))
        self.target_buckets = max(1000, int(max_buckets * bucket_share))
        self.timeout_s = timeout_s
        self.requests = 0
        self.lock = threading.Lock()

    def plan(self, specs: list[Spec]) -> None:
        for sp in specs:
            if sp.partitioned:
                sp.parts = max(1, int(math.ceil(sp.est * sp.per_item / self.target_buckets)))

    def _search_once(self, body: dict) -> tuple[dict, str | None]:
        with self.lock:
            self.requests += 1
        resp = self.client.search(
            index=self.index,
            body={"track_total_hits": False, **body},
            pre_filter_shard_size=1,
            request_timeout=self.timeout_s,
            filter_path=_FILTER_PATH,
        )
        data = resp.body if hasattr(resp, "body") else dict(resp)
        if data.get("timed_out"):
            return data, "timed out"
        shards = data.get("_shards") or {}
        if shards.get("failed"):
            overload = _overload_type(shards.get("failures"))
            if overload:
                return data, overload
            raise _ShardsFailedError(f"{shards['failed']} shard(s) failed")
        return data, None

    def _search(self, body: dict) -> tuple[dict | None, str | None, float, float]:
        t0 = time.perf_counter()
        attempt = 0
        while True:
            try:
                data, error = self._search_once(body)
                break
            except Exception as exc:  # noqa: BLE001 - every transport/API error is reported per request
                overload = _overload_type(getattr(exc, "body", None))
                if overload:
                    data, error = None, overload  # answered by more partitions, not by a plain retry
                    break
                if attempt < _TRANSIENT_RETRIES and is_transient(exc):
                    attempt += 1
                    pause = _RETRY_PAUSE_S * 2 ** (attempt - 1) * (1 + random.random() / 2)  # noqa: S311 - jitter
                    logger.warning(
                        "usage attribution: transient Elasticsearch error (%s), retry %d of %d in %.1f s",
                        type(exc).__name__,
                        attempt,
                        _TRANSIENT_RETRIES,
                        pause,
                    )
                    time.sleep(pause)
                    continue
                data, error = None, f"{type(exc).__name__}: {str(exc)[:500]}"
                break
        wall = (time.perf_counter() - t0) * 1000.0
        return data, error, wall, 0.0

    def execute(self, specs: list[Spec]) -> None:
        pending = [sp for sp in specs if sp.builder(None, 10) is not None]
        for round_idx in range(_MAX_ROUNDS):
            batch, locks = self._build_round_batch(pending)
            overflow, errors = self._run_batch(batch, locks)
            pending, circuit_broken = self._settle_round(pending, overflow, errors, round_idx)
            if not pending:
                break
            if circuit_broken:
                time.sleep(_RETRY_PAUSE_S * 2**round_idx)

    def _build_round_batch(self, pending: list[Spec]) -> tuple[list[tuple[Spec, dict]], dict[int, threading.Lock]]:
        """One round's requests: every spec starts a fresh accumulator and contributes one request per partition."""
        batch: list[tuple[Spec, dict]] = []
        for sp in pending:
            sp.result = sp.new_acc()
            batch.extend(self._spec_requests(sp))
        locks = {id(sp): threading.Lock() for sp in pending}
        return batch, locks

    def _spec_requests(self, sp: Spec) -> list[tuple[Spec, dict]]:
        if not sp.partitioned:
            return [(sp, sp.builder(None, 0))]
        size = int(math.ceil(sp.est / float(sp.parts) * 1.3)) + 100
        requests = []
        for i in range(sp.parts):
            part = (i, sp.parts) if sp.parts > 1 else None
            requests.append((sp, sp.builder(part, size)))
        return requests

    def _run_batch(
        self, batch: list[tuple[Spec, dict]], locks: dict[int, threading.Lock]
    ) -> tuple[dict[int, float], dict[int, list[str]]]:
        """Executes one round's requests concurrently, collecting per-spec overflow amounts and errors."""
        overflow: dict[int, float] = {}
        errors: dict[int, list[str]] = {}

        def run(item: tuple[Spec, dict]) -> None:
            self._run_one(item, overflow, errors, locks)

        with ThreadPoolExecutor(max_workers=min(self.concurrency, max(1, len(batch)))) as pool:
            list(pool.map(run, batch))
        return overflow, errors

    def _run_one(
        self,
        item: tuple[Spec, dict],
        overflow: dict[int, float],
        errors: dict[int, list[str]],
        locks: dict[int, threading.Lock],
    ) -> None:
        sp, body = item
        data, error, wall, _ = self._search(body)
        with locks[id(sp)]:
            self._record_stats(sp, data, wall)
            if error or data is None:
                errors.setdefault(id(sp), []).append(error or "no response")
                return
            self._merge_response(sp, data, overflow)

    @staticmethod
    def _record_stats(sp: Spec, data: dict | None, wall: float) -> None:
        st = sp.stats
        st["requests"] += 1
        st["took_ms"] += (data or {}).get("took") or 0
        st["wall_ms"] = round(max(st["wall_ms"], wall), 1)

    @staticmethod
    def _merge_response(sp: Spec, data: dict, overflow: dict[int, float]) -> None:
        """Parses one response's buckets or aggregations into ``sp.result``, noting any partition overflow."""
        aggs = data.get("aggregations") or {}
        if not sp.partitioned:
            sp.result = sp.parse(aggs, sp.result)
            return
        terms_node = _dig(aggs, sp.path) or {}
        other = terms_node.get("sum_other_doc_count") or 0
        if other:
            overflow[id(sp)] = overflow.get(id(sp), 0) + other
        sp.result = sp.parse(terms_node.get("buckets") or [], sp.result)

    def _settle_round(
        self, pending: list[Spec], overflow: dict[int, float], errors: dict[int, list[str]], round_idx: int
    ) -> tuple[list[Spec], bool]:
        """Decides, for each spec in this round, whether to retry with more partitions, fail, or accept the result.
        Also reports whether a circuit breaker triggered any of this round's retries (see ``execute``)."""
        retry = []
        circuit_broken = False
        for sp in pending:
            if self._retry_with_more_partitions(sp, overflow, errors, round_idx):
                retry.append(sp)
                circuit_broken = circuit_broken or any(_CIRCUIT_BREAKING in e for e in errors.get(id(sp), []))
                continue
            self._raise_if_failed(sp, overflow, errors)
        return retry, circuit_broken

    @staticmethod
    def _retry_with_more_partitions(
        sp: Spec, overflow: dict[int, float], errors: dict[int, list[str]], round_idx: int
    ) -> bool:
        if not sp.partitioned or round_idx >= _MAX_ROUNDS - 1:
            return False
        errs = errors.get(id(sp), [])
        overloaded = next((t for t in _OVERLOAD_TYPES if any(t in e for e in errs)), None)
        if not (overloaded or overflow.get(id(sp))):
            return False
        sp.parts *= 2
        logger.warning(
            "usage attribution %s: %s; retrying with %d partitions",
            sp.label,
            overloaded or f"{int(overflow[id(sp)])} records beyond the terms size",
            sp.parts,
        )
        return True

    @staticmethod
    def _raise_if_failed(sp: Spec, overflow: dict[int, float], errors: dict[int, list[str]]) -> None:
        errs = errors.get(id(sp), [])
        if errs:
            raise FetchError(f"{sp.label} failed: {errs[0]}")
        if overflow.get(id(sp)):
            raise FetchError(f"{sp.label}: {int(overflow[id(sp)])} records left out after {_MAX_ROUNDS} rounds")
