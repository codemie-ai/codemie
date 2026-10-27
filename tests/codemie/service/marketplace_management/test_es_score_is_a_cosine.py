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

"""`ESNeighbor.score` must be the cosine the thresholds are written against.

MEASURED, ON THE LIVE INDEX (13.2 item 4)
-----------------------------------------
`marketplace_assistants` maps its vector field as
`dense_vector { "similarity": "cosine", "index_options": {"type": "int8_hnsw"} }`,
and for that similarity Elasticsearch returns

    _score = (1 + cosine) / 2

so a `_score` is a **half-scaled** cosine, never a cosine. Measured against two
real documents in the running index:

    raw cosine(a, b), computed from the stored vectors ....... 0.8146
    (1 + cosine) / 2 .......................................... 0.9073
    the `_score` Elasticsearch returned for b ................. 0.9018
    the same query's self-match `_score` ...................... 0.9972

`_score` tracks `(1 + cosine)/2` to within 0.0055 — the same direction and
magnitude as the 0.0028 by which the self-match falls short of a perfect 1.0,
which is the `int8_hnsw` quantisation error. Raw cosine would have predicted
0.8146 and is out by 0.087, sixteen times further.

WHY IT MATTERS: EVERY PAIR IN THE CORPUS CLEARS A 0.85 THRESHOLD
-----------------------------------------------------------------
`Config.similarity.critical_threshold` defaults to 0.85 and is documented as a
cosine — it is the same number the structural signals are scored against. Over
all 219 published assistants the `_score`s span

    0.8621 .. 0.9972      (implied cosine 0.7242 .. 0.9943)

so on the half scale *nothing* is below the CRITICAL bar. The bar stopped
discriminating: it selected the entire corpus. That is what turned the
now-reachable `purpose_duplicate` rule into 88 of 219 assistants recommended for
deletion on the first live run after 13.2 item 4 — not because the marketplace
holds 88 duplicates, but because a threshold on one scale was being compared
with a number on another.

The conversion therefore belongs at the boundary, where the ES answer becomes an
`ESNeighbor`: one scale in the system, cosine, the one every threshold, every
persisted `mm_audit_similarity.combined` and every "cosine 0.87" in the UI
already claims to be.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from codemie.service.marketplace_management.es_similarity import (
    ESNeighbor,
    MarketplaceAssistantsIndex,
)


class _Doc:
    def __init__(self, aid: str, name: str) -> None:
        self.metadata = {"id": aid, "name": name}


# The measurement above, as a table: `(elasticsearch _score, the cosine it encodes)`.
_MEASURED = (
    (0.9073, 0.8146),  # the two live documents
    (1.0000, 1.0000),  # an exact match
    (0.8621, 0.7242),  # the least similar pair anywhere in the live corpus
    (0.5000, 0.0000),  # orthogonal
)


@pytest.mark.parametrize("es_score,cosine", _MEASURED)
def test_a_hit_is_reported_as_the_cosine_it_encodes(es_score: float, cosine: float) -> None:
    """`_neighbors_from_hits` is the one place the two scales meet."""
    neighbours = MarketplaceAssistantsIndex._neighbors_from_hits(
        [(_Doc("other-1", "Other"), es_score)], k=5, exclude_id=None
    )

    assert len(neighbours) == 1
    assert neighbours[0].score == pytest.approx(cosine, abs=1e-6), (
        f"an Elasticsearch _score of {es_score} encodes a cosine of {cosine} "
        f"((1 + cos) / 2 for a `similarity: cosine` dense_vector), but the neighbour "
        f"reports {neighbours[0].score}. Every similarity threshold in "
        "`validator/config.py` is written as a cosine, so handing them the half scale "
        "makes the CRITICAL bar (0.85) select the entire corpus"
    )


def test_the_bar_that_matters_stops_selecting_the_whole_corpus() -> None:
    """The consequence, asserted as the consequence rather than as arithmetic.

    Two hits an admin would judge completely differently — the pair measured on
    the live index at cosine 0.81, and a genuine near-copy at 0.96 — must fall on
    opposite sides of the 0.85 CRITICAL bar. On the half scale they are 0.91 and
    0.98 and both clear it, which is precisely how 88 of 219 assistants came to
    be recommended for deletion in one run.
    """
    critical = 0.85
    unrelated, near_copy = MarketplaceAssistantsIndex._neighbors_from_hits(
        [(_Doc("unrelated", "Unrelated"), 0.9073), (_Doc("near-copy", "Near copy"), 0.9800)],
        k=5,
        exclude_id=None,
    )

    assert unrelated.score < critical, (
        f"a pair at cosine 0.81 is reported as {unrelated.score}, which clears the "
        "CRITICAL near-duplicate bar. On the raw Elasticsearch scale nothing in the "
        "corpus is below 0.862, so the bar selects everything"
    )
    assert near_copy.score >= critical, (
        f"a genuine near-copy is reported as {near_copy.score} and no longer clears the "
        "bar — the conversion must not throw the real duplicates out with the noise"
    )


def test_a_score_outside_the_scale_is_clamped_not_propagated() -> None:
    """A store that already returns a cosine must not be pushed below -1.

    Elasticsearch's own scores cannot leave `[0, 1]`, but `_neighbors_from_hits`
    also serves the fallback path through whatever store `get_store()` returns,
    and a value that leaves `[-1, 1]` would be a similarity nothing downstream
    can reason about. Clamped rather than trusted, and never silently dropped.
    """
    low, high = MarketplaceAssistantsIndex._neighbors_from_hits(
        [(_Doc("low", "Low"), -0.5), (_Doc("high", "High"), 4.0)], k=5, exclude_id=None
    )

    assert low.score == pytest.approx(-1.0)
    assert high.score == pytest.approx(1.0)


def test_the_knn_path_reports_the_same_scale_as_the_batch_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One conversion, both entry points.

    `knn()` and `knn_batch()` reach Elasticsearch by different routes —
    `similarity_search_with_score` and
    `similarity_search_by_vector_with_relevance_scores` — and a conversion
    applied to only one of them would make an audit's neighbours depend on which
    code path served it.
    """
    hits = [(_Doc("other-1", "Other"), 0.9073)]

    idx = MarketplaceAssistantsIndex()
    idx._similarity_degraded = False
    monkeypatch.setattr(
        idx,
        "get_store",
        lambda: SimpleNamespace(similarity_search_with_score=lambda *a, **kw: hits),
    )

    neighbours = idx.knn("anything", k=5)

    assert [n.score for n in neighbours] == [pytest.approx(0.8146, abs=1e-6)], (
        f"knn() reported {[n.score for n in neighbours]}; the batch path reports the "
        "cosine, and two paths on two scales is worse than either scale alone"
    )
    assert isinstance(neighbours[0], ESNeighbor)
