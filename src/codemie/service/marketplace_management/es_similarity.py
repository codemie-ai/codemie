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

"""Thin Elasticsearch wrapper for marketplace_assistants KNN similarity.

Mirrors the pattern from toolkit_lookup_service._setup_elasticsearch_index:
  index_name  = config.PLATFORM_MARKETPLACE_DATASOURCE_NAME
  embeddings  = llm_service.get_embedding_deployment_name(llm_service.default_embedding_model)
  store       = get_elasticsearch(index_name, embedding_deployment_name)
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.dependecies import get_elasticsearch
from codemie.service.llm_service.llm_service import llm_service


@dataclass
class ESNeighbor:
    """A single KNN result from the marketplace_assistants index."""

    assistant_id: str  # from Document.metadata['id']
    name: str  # from metadata['name'] if present, else empty
    # The COSINE, in [-1, 1] -- not the Elasticsearch `_score`, which for a
    # `similarity: cosine` dense_vector is `(1 + cosine) / 2`. Converted once, at
    # the boundary, by `MarketplaceAssistantsIndex.to_cosine`; every threshold
    # downstream is written as a cosine and comparing them against the half scale
    # made a 0.85 CRITICAL bar select all 219 published assistants.
    score: float


ES_SIMILARITY_TIMEOUT_SECONDS = 10.0
"""Wall-clock budget for a single KNN lookup, embeddings round-trip included."""

ES_SIMILARITY_BATCH_SIZE = 256
"""Records embedded per round trip by :meth:`MarketplaceAssistantsIndex.knn_batch`.

Measured on the live stack (219 published assistants, Azure OpenAI
`codemie-text-embedding-ada-002`, local Elasticsearch):

    embed_query(one text) ............................. 0.73 s
    similarity_search_with_score(one text, k=11) ...... 1.17 s cold / 0.37 s warm
    embed_documents([219 texts]) ...................... 3.34 s   <- ONE round trip
    search_by_vector(vector already in hand, k=11) .... 0.003 s

The embedding is the entire cost of the similarity phase and the Elasticsearch
half is noise. 256 keeps the whole present corpus in a single round trip while
staying inside the provider's per-request input cap.
"""

ES_SIMILARITY_BATCH_BUDGET_MULTIPLIER = 6.0
"""A batch gets this many times the single-lookup budget: 60 s by default.

Fifteen times the 4 s a full 219-record batch actually takes. The breaker is
meant to catch an endpoint that is not answering at all, not to arbitrate
ordinary provider latency -- which is precisely the distinction the per-record
budget could not make.
"""

ES_SIMILARITY_MAX_CONSECUTIVE_TIMEOUTS = 2
"""Consecutive batch timeouts before similarity is abandoned for the run.

One timeout is a hiccup; two in a row is an outage. The per-record breaker
latched on the first, so a single slow call at 80% through a run discarded the
neighbours already gathered for the other 80% and reported the whole run
UNAVAILABLE -- 8 of 65 runs in `mm_audit_run` ended that way while 57 succeeded.
"""


class MarketplaceAssistantsIndex:
    """Thin wrapper around the existing marketplace_assistants ES index.

    State that belongs to a single audit run — ``_similarity_degraded`` and
    ``_last_error`` — is **instance-level**.  The module-level singleton
    ``marketplace_assistants_index`` is the object all production callers use.
    Tests that need isolation can construct their own instance.

    The remaining methods (``get_store``, ``index_name``, ``to_cosine``,
    ``_neighbors_from_hits``, ``_run_within_deadline``,
    ``_embedding_deployment_name``) carry no per-run state and are kept as
    class/static methods so they can be called without an instance in contexts
    that only need the backing store.
    """

    def __init__(self) -> None:
        # Per-run circuit-breaker state.  Kept on the instance so that
        # reset_similarity_circuit() called at the start of one audit cannot
        # race with threads still live from a previous audit on the same class.
        self._similarity_degraded: bool = False
        self._last_error: str | None = None

    def reset_similarity_circuit(self) -> None:
        """Re-arm the lookup after a timeout. Called at the start of an audit."""
        self._similarity_degraded = False
        self._last_error = None

    def _note_failure(self, exc: BaseException) -> None:
        """Remember *why* similarity stopped answering.

        Every lookup path in this class returns `[]` on failure, which is the
        right thing for the caller -- an audit must not die because a search
        index blinked -- but it also made a dead Elasticsearch and a clean
        marketplace produce byte-identical output (F-27, R14). The audit reads
        this back through `similarity_health()` and labels the run PARTIAL
        rather than silently downgrading it.
        """
        self._last_error = f"{type(exc).__name__}: {exc}"[:1000]

    def similarity_health(self) -> tuple[bool, str | None]:
        """`(degraded, error)` for the lookups made since the last re-arm."""
        return (self._similarity_degraded or self._last_error is not None), self._last_error

    @classmethod
    def index_name(cls) -> str:
        """The index similarity is actually searching."""
        return config.PLATFORM_MARKETPLACE_DATASOURCE_NAME

    def doc_count(self) -> int | None:
        """How many documents similarity is comparing against, or None.

        This is the coverage number: with 19 documents indexed against 219
        published assistants, "no duplicates found" means "no duplicates among
        the 8.7% of the catalogue Elasticsearch has heard of". Persisting it on
        the run (`mm_audit_run.es_doc_count`) is what turns that from a silent
        lie into a number the UI can show.

        Returns None when the index cannot be reached or counted -- recorded as
        a failure so the run is labelled UNAVAILABLE.

        Deliberately does **not** trip the KNN circuit breaker: a count is one
        cheap call, while the breaker exists to stop the per-record embedding
        round-trip from being paid once per assistant.
        """
        try:
            store = self.get_store()
            client = getattr(store, "client", None)
            if client is None:
                raise RuntimeError("Elasticsearch store exposes no client to count with")
            response = client.count(index=self.index_name())
            return int(response["count"])
        except Exception as exc:  # noqa: BLE001 - reported through similarity_health()
            self._note_failure(exc)
            logger.warning("MarketplaceAssistantsIndex: cannot count %s: %s", self.index_name(), exc)
            return None

    @classmethod
    def get_store(cls):
        """Return an ElasticsearchStore for the marketplace_assistants index."""
        index_name = config.PLATFORM_MARKETPLACE_DATASOURCE_NAME
        embedding_deployment = llm_service.get_embedding_deployment_name(llm_service.default_embedding_model)
        return get_elasticsearch(index_name, embedding_deployment)

    def _search_within_deadline(self, store, query_text: str, fetch_k: int):
        """Run the KNN query under a wall-clock budget.

        Returns the raw hits, or None if the lookup failed or ran out of time.

        The query reaches Elasticsearch through langchain, which first embeds
        *query_text* over HTTP. That request carries no timeout — it inherits
        the shared embeddings client from get_embeddings_model, which has no
        timeout parameter to pass — so an unreachable embeddings endpoint
        blocks in socket.connect indefinitely. Because knn runs once per record
        inside the audit loop, that single blocked connect strands the audit
        worker thread, whose `finally` is what clears AuditCache._running.
        The result is an audit that reports is_running=True forever and refuses
        every subsequent run in that process.

        A future cannot interrupt a thread blocked in a syscall, so the worker
        is abandoned rather than cancelled and the executor is shut down
        without waiting. The circuit breaker is what keeps that to at most one
        stranded thread per degraded period instead of one per record.
        """
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mm-es-knn")
        future = executor.submit(store.similarity_search_with_score, query_text, k=fetch_k)
        try:
            return future.result(timeout=ES_SIMILARITY_TIMEOUT_SECONDS)
        except FuturesTimeoutError:
            self._similarity_degraded = True
            # A bare FuturesTimeoutError stringifies to nothing, and "" on the
            # run row is indistinguishable from "no error".
            self._last_error = (
                f"TimeoutError: Elasticsearch KNN exceeded its {ES_SIMILARITY_TIMEOUT_SECONDS:.1f}s budget"
            )
            logger.warning(
                "MarketplaceAssistantsIndex: KNN exceeded %.1fs budget; disabling ES "
                "similarity for the rest of this audit. The embeddings endpoint is "
                "most likely unreachable. Verdicts stay valid — the structural gates "
                "still run — but similarity neighbours and clusters will be absent.",
                ES_SIMILARITY_TIMEOUT_SECONDS,
            )
            return None
        except Exception as exc:
            self._note_failure(exc)
            logger.warning("MarketplaceAssistantsIndex: KNN query failed: %s", exc)
            return None
        finally:
            # wait=False: never block on the abandoned worker.
            executor.shutdown(wait=False)

    def knn(
        self,
        query_text: str,
        k: int = 10,
        exclude_ids: set[str] | None = None,
    ) -> list[ESNeighbor]:
        """Return up to *k* nearest neighbors for *query_text*.

        Fetches k + len(exclude_ids) candidates from ES, then filters out any
        document whose metadata['id'] is in *exclude_ids*.
        """
        if self._similarity_degraded:
            # A previous lookup exhausted its budget. The audit calls this once
            # per record, so retrying would cost the timeout 282 times over and
            # strand a blocked thread on each attempt.
            return []

        try:
            store = self.get_store()
        except Exception as exc:
            self._note_failure(exc)
            logger.warning("MarketplaceAssistantsIndex: ES store unavailable: %s", exc)
            return []

        fetch_k = k + (len(exclude_ids) if exclude_ids else 0)
        raw = self._search_within_deadline(store, query_text, fetch_k)
        if raw is None:
            return []

        neighbors: list[ESNeighbor] = []
        for doc, score in raw:
            meta = doc.metadata or {}
            aid = meta.get("id", "") or meta.get("assistant_id", "")
            if not aid:
                continue
            if exclude_ids and aid in exclude_ids:
                continue
            neighbors.append(
                ESNeighbor(
                    assistant_id=aid,
                    name=meta.get("name", ""),
                    # The same conversion the batch path applies -- see
                    # `to_cosine`. Two entry points on two scales would make an
                    # audit's neighbours depend on which one served it.
                    score=self.to_cosine(score),
                )
            )
            if len(neighbors) >= k:
                break

        return neighbors

    def knn_for_assistant_text(
        self,
        assistant_id: str,
        text: str,
        k: int = 10,
    ) -> list[ESNeighbor]:
        """KNN for this assistant, excluding itself from the results."""
        return self.knn(text, k, exclude_ids={assistant_id})

    # ------------------------------------------------------------------
    # Batched lookup -- the path the audit uses
    # ------------------------------------------------------------------

    @staticmethod
    def _run_within_deadline(fn, budget: float, what: str):
        """`(value, error)` for *fn*, abandoning the worker if it overruns.

        A future cannot interrupt a thread blocked in a socket syscall, so the
        worker is abandoned rather than cancelled and the executor is shut down
        without waiting. Batching is what keeps that to one abandoned thread per
        chunk instead of one per record.
        """
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mm-es-knn")
        future = executor.submit(fn)
        try:
            return future.result(timeout=budget), None
        except FuturesTimeoutError:
            # A bare FuturesTimeoutError stringifies to nothing, and "" on the
            # run row is indistinguishable from "no error".
            return None, f"TimeoutError: {what} exceeded its {budget:.1f}s budget"
        except Exception as exc:  # noqa: BLE001 - reported through the tuple
            return None, f"{type(exc).__name__}: {exc}"
        finally:
            executor.shutdown(wait=False)  # never block on the abandoned worker

    @staticmethod
    def to_cosine(es_score: float) -> float:
        """An Elasticsearch relevance score, as the cosine it encodes.

        `marketplace_assistants` maps its vector field as
        `dense_vector {"similarity": "cosine"}`, and for that similarity
        Elasticsearch returns `_score = (1 + cosine) / 2` — a half scale, chosen
        so the score stays positive. Every threshold downstream
        (`Config.similarity.thresholds`, `critical_threshold`, the cluster
        builder's bar, `aggregate_verdict`'s `>= 0.85`) is written as a cosine,
        as is the "cosine 0.87" the Cleanup drawer prints, so the two scales
        have to meet exactly once and this is where.

        Measured on the live index rather than assumed: two real documents whose
        stored vectors give a raw cosine of 0.8146 came back with `_score`
        0.9018, against 0.9073 predicted by `(1 + cos)/2` and 0.8146 by "the
        score is already a cosine" — and the same query's self-match scored
        0.9972 rather than a perfect 1.0, which is the `int8_hnsw` quantisation
        error accounting for the whole of the 0.0055 gap.

        Left uncorrected the bar stops discriminating: across the 219 published
        assistants the `_score`s span 0.8621..0.9972, so *every* pair in the
        marketplace clears a 0.85 CRITICAL threshold. That is not a corpus full
        of duplicates, it is a comparison between two different units.

        Clamped to `[-1, 1]`: a similarity outside the cosine range is one
        nothing downstream can reason about, and a store that has already
        normalised differently should be pinned to the scale rather than
        propagate a number that is not a cosine at all.
        """
        cosine = 2.0 * float(es_score) - 1.0
        return max(-1.0, min(1.0, cosine))

    @staticmethod
    def _neighbors_from_hits(raw, k: int, exclude_id: str | None) -> list[ESNeighbor]:
        """Turn `(Document, score)` pairs into `ESNeighbor`s, dropping *exclude_id*."""
        neighbors: list[ESNeighbor] = []
        for doc, score in raw or []:
            meta = doc.metadata or {}
            aid = meta.get("id", "") or meta.get("assistant_id", "")
            if not aid or aid == exclude_id:
                continue
            neighbors.append(
                ESNeighbor(
                    assistant_id=aid,
                    name=meta.get("name", ""),
                    score=MarketplaceAssistantsIndex.to_cosine(score),
                )
            )
            if len(neighbors) >= k:
                break
        return neighbors

    def knn_batch(
        self,
        items: "list[tuple[str, str]]",
        k: int = 10,
        chunk_size: int | None = None,
    ) -> "dict[str, list[ESNeighbor]]":
        """Nearest neighbours for many records, embedding them in batches.

        *items* is `[(record_id, query_text), ...]`; each record is excluded from
        its own neighbours. Records whose chunk failed map to `[]` -- the same
        contract as :meth:`knn`, so a caller cannot tell a failed lookup from an
        empty one without consulting :meth:`similarity_health`.

        This replaces one `similarity_search_with_score` per record (F-95: 282
        sequential round trips, no batching, no cache). The embedding is the
        entire cost -- 0.73 s per text against 3.34 s for all 219 at once -- so
        collapsing it also collapses the number of chances the run has to draw a
        call slow enough to trip the breaker, from one per record to one per
        chunk.
        """
        results: dict[str, list[ESNeighbor]] = {aid: [] for aid, _ in items}
        if not items:
            return results

        try:
            store = self.get_store()
        except Exception as exc:  # noqa: BLE001 - reported through similarity_health()
            self._note_failure(exc)
            logger.warning("MarketplaceAssistantsIndex: ES store unavailable: %s", exc)
            return results

        embeddings = getattr(store, "embeddings", None)
        by_vector = getattr(store, "similarity_search_by_vector_with_relevance_scores", None)
        if embeddings is None or not hasattr(embeddings, "embed_documents") or by_vector is None:
            # An older or stubbed store that cannot separate embedding from query.
            # Correctness first: fall back to the per-record path.
            logger.info(
                "MarketplaceAssistantsIndex: store does not support batched "
                "embedding; falling back to one lookup per record"
            )
            for aid, text in items:
                results[aid] = self.knn_for_assistant_text(aid, text, k=k)
            return results

        chunk_size = chunk_size or ES_SIMILARITY_BATCH_SIZE
        consecutive_timeouts = 0

        for start in range(0, len(items), chunk_size):
            if self._similarity_degraded:
                break
            chunk = items[start : start + chunk_size]
            budget = ES_SIMILARITY_TIMEOUT_SECONDS * ES_SIMILARITY_BATCH_BUDGET_MULTIPLIER
            what = f"embedding + KNN for {len(chunk)} marketplace records"
            value, error = self._run_within_deadline(self._chunk_lookup(embeddings, by_vector, chunk, k), budget, what)

            if error is not None:
                consecutive_timeouts += 1
                self._note_chunk_failure(what, error, len(chunk), consecutive_timeouts)
                continue

            consecutive_timeouts = 0
            results.update(value or {})

        return results

    @staticmethod
    def _chunk_lookup(embeddings, by_vector, chunk: "list[tuple[str, str]]", k: int):
        """Closure that embeds one chunk and runs a KNN per vector."""

        def _lookup():
            vectors = embeddings.embed_documents([text for _, text in chunk])
            out: dict[str, list[ESNeighbor]] = {}
            for (aid, _), vector in zip(chunk, vectors, strict=False):
                # +1 candidate so excluding the record itself cannot make
                # the result short.
                raw = by_vector(vector, k=k + 1)
                out[aid] = MarketplaceAssistantsIndex._neighbors_from_hits(raw, k, exclude_id=aid)
            return out

        return _lookup

    def _note_chunk_failure(self, what: str, error: str, chunk_len: int, consecutive_timeouts: int) -> None:
        """Record a failed chunk and trip the breaker after too many in a row."""
        self._last_error = error[:1000]
        logger.warning(
            "MarketplaceAssistantsIndex: %s failed (%s). %s records in this "
            "chunk have no similarity neighbours; the run continues.",
            what,
            error,
            chunk_len,
        )
        if consecutive_timeouts >= ES_SIMILARITY_MAX_CONSECUTIVE_TIMEOUTS:
            self._similarity_degraded = True
            logger.error(
                "MarketplaceAssistantsIndex: %s consecutive similarity batches "
                "failed (last: %s). Abandoning ES similarity for the rest of "
                "this audit. Verdicts stay valid -- the structural gates still "
                "run -- but neighbours and clusters will be absent. Check that "
                "the embeddings deployment (%s) is reachable.",
                consecutive_timeouts,
                error,
                self._embedding_deployment_name(),
            )

    def _embedding_deployment_name(self) -> str:
        """Name the thing an operator has to go and check. Never raises."""
        try:
            return llm_service.get_embedding_deployment_name(llm_service.default_embedding_model)
        except Exception:  # noqa: BLE001
            return "<unknown embedding deployment>"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
#
# All production callers use this singleton so that per-run circuit-breaker
# state (_similarity_degraded, _last_error) is scoped to the instance and
# reset_similarity_circuit() at the start of one audit cannot race with
# threads that are still live from a concurrent audit (CR-019).
#
# Tests that need isolation can construct their own MarketplaceAssistantsIndex().
marketplace_assistants_index = MarketplaceAssistantsIndex()
