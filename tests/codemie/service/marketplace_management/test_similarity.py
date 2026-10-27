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

"""Similarity scoring after the D3b cutover: structural + exact-name + clone.

v1 similarity has exactly three mechanisms (CHANGE-BRAINSTORM D3b):

  1. a weighted sum over **structural** signals only — name, tools, MCP
     servers, skills, datasources, categories;
  2. an **exact normalized-name** override;
  3. a **recorded clone edge** override, read from `assistant_clone_event`.

The three dense-vector text signals (`description`, `system_prompt`,
`purpose`) are gone. They were scored by a backend whose `fit_transform`
returns `None`, so they contributed 0.0 to every pair while holding 0.45 of
the weight budget — capping every combined score at 0.65, below the 0.70
OPTIONAL bar (F-13). Nothing could ever be flagged on score alone.

These tests pin the replacement: the clone edge — the one piece of duplicate
provenance the platform already records and the detector ignored (F-70) —
pins a pair to CRITICAL while its current content still backs the edge up,
and stops doing so once the clone has been reworked past the critical bar
(MR !4070 review comment #23).
"""

from __future__ import annotations

import pytest

from codemie.service.marketplace_management.validator import similarity as sim
from codemie.service.marketplace_management.validator.models import Severity

from .conftest import make_assistant


def _unrelated_pair() -> tuple:
    """Two assistants that share nothing a structural signal can see.

    Disjoint names, tools, MCP servers, skills, datasources and categories, so
    the weighted structural score between them is 0.0. Any similarity they are
    credited with therefore came from somewhere other than the weights.
    """
    a = make_assistant(
        id="source-1",
        name="Release Notes Writer",
        tools=["jira_get_issue"],
        mcp_servers=["mcp-atlassian"],
        skills=["skill-release"],
        datasources=["ds-changelog"],
        categories=["documentation"],
    )
    b = make_assistant(
        id="clone-1",
        name="Quarterly Budget Reviewer",
        tools=["excel_read_sheet"],
        mcp_servers=["mcp-finance"],
        skills=["skill-budget"],
        datasources=["ds-ledger"],
        categories=["finance"],
    )
    return a, b


def test_structurally_unrelated_assistants_do_not_pair(cfg) -> None:
    """Control: without a clone edge these two score 0 and are not flagged."""
    a, b = _unrelated_pair()

    results, _ = sim.run_all_similarity([a, b], cfg)

    assert results["clone-1"].top_pair() is None, (
        "two assistants with disjoint structural signals must not be reported "
        "as similar; if they are, the control for the clone test is invalid"
    )


def _still_similar_clone_pair() -> tuple:
    """A clone whose content still matches its source closely.

    Same name, tools, MCP servers, skills, datasources and categories, so the
    structural score alone already clears the critical bar — the clone edge
    is corroborating evidence here, not the only evidence.
    """
    a = make_assistant(
        id="source-2",
        name="Release Notes Writer",
        tools=["jira_get_issue"],
        mcp_servers=["mcp-atlassian"],
        skills=["skill-release"],
        datasources=["ds-changelog"],
        categories=["documentation"],
    )
    b = make_assistant(
        id="clone-2",
        name="Release Notes Writer",
        tools=["jira_get_issue"],
        mcp_servers=["mcp-atlassian"],
        skills=["skill-release"],
        datasources=["ds-changelog"],
        categories=["documentation"],
    )
    return a, b


def test_clone_edge_raises_similarity(cfg) -> None:
    """A recorded clone edge pins the score to 1.0 while the content still matches.

    `assistant_clone_event` records that someone created assistant B by
    cloning assistant A. While the clone's current content is still at or
    above the critical bar on its own structural signals, the edge is treated
    as a receipt and the pair is pinned to the maximum score.
    """
    a, b = _still_similar_clone_pair()

    results, _ = sim.run_all_similarity([a, b], cfg, clone_edges={("source-2", "clone-2")})

    pair = results["clone-2"].top_pair()
    assert pair is not None, "a recorded clone edge produced no similarity pair"
    assert pair.signals["clone"] == 1.0
    assert pair.combined == 1.0
    assert pair.severity is Severity.CRITICAL
    assert pair.reason == "clone_edge"


def test_diverged_clone_edge_does_not_force_critical(cfg) -> None:
    """A clone edge alone cannot pin CRITICAL once the content has diverged.

    MR !4070 review comment #23: a clone that has been substantially reworked
    since it was created must be judged on its current content, not on the
    fact that it was once cloned. The two assistants here share nothing a
    structural signal can see, so the clone edge must not resurrect a
    duplicate verdict on its own.
    """
    a, b = _unrelated_pair()

    results, _ = sim.run_all_similarity([a, b], cfg, clone_edges={("source-1", "clone-1")})

    pair = results["clone-1"].top_pair()
    assert pair is None or pair.severity is not Severity.CRITICAL, (
        "a diverged clone edge still forced a CRITICAL verdict — the override "
        "must be gated on current content similarity"
    )


def test_clone_edge_is_direction_agnostic(cfg) -> None:
    """The edge is stored source→clone; either lookup order must find it."""
    a, b = _still_similar_clone_pair()

    results, _ = sim.run_all_similarity([a, b], cfg, clone_edges={("source-2", "clone-2")})

    forward = results["source-2"].top_pair()
    backward = results["clone-2"].top_pair()
    assert forward is not None and backward is not None
    assert forward.signals["clone"] == backward.signals["clone"] == 1.0


def test_clone_signal_is_reported_on_every_pair(cfg) -> None:
    """`clone` is a first-class signal, present (as 0.0) even when absent.

    A signal that only appears in the payload when it fires is a signal the UI
    cannot render a column for, and one nobody can tell apart from a signal
    that was never computed.
    """
    a = make_assistant(id="p-1", name="Duplicate Detector", tools=["t1"])
    b = make_assistant(id="p-2", name="Duplicate Detector", tools=["t1"])

    results, _ = sim.run_all_similarity([a, b], cfg)

    pair = results["p-1"].top_pair()
    assert pair is not None
    assert "clone" in pair.signals
    assert pair.signals["clone"] == 0.0


def test_dead_text_signals_are_gone(cfg) -> None:
    """`description` / `system_prompt` / `purpose` are no longer scored.

    They are not merely zero — they are absent. A signal that is reported as
    0.0000 on every pair for every run reads as "these two share no wording",
    which is a measurement. It was never a measurement; it was a backend
    returning None (F-13).
    """
    a = make_assistant(id="t-1", name="Text One", description="identical text")
    b = make_assistant(id="t-2", name="Text One", description="identical text")

    results, _ = sim.run_all_similarity([a, b], cfg)

    pair = results["t-1"].top_pair()
    assert pair is not None
    for dead in ("description", "system_prompt", "purpose"):
        assert dead not in pair.signals, (
            f"{dead!r} is still reported as a similarity signal, but no "
            "dense-vector text backend is wired — it can only ever be 0.0"
        )


def test_exact_name_still_flags_critical(cfg) -> None:
    """The exact-name override survives the cutover (D3b keeps it)."""
    a = make_assistant(id="n-1", name="Code Review Buddy", tools=["x"], datasources=[])
    b = make_assistant(id="n-2", name="code review  buddy", tools=["y"], datasources=[])

    results, _ = sim.run_all_similarity([a, b], cfg)

    pair = results["n-1"].top_pair()
    assert pair is not None
    assert pair.severity is Severity.CRITICAL
    assert pair.reason == "exact_name_match"


def test_no_numpy_vector_machinery_remains() -> None:
    """The unreachable KNN / cosine / clustering code is deleted, not disabled.

    F-71: ~300 lines of numpy KNN, cosine helpers and sklearn agglomerative
    clustering sat behind a backend that returns None. Leaving them in tree
    behind a flag is how the next reader concludes semantic similarity works.
    """
    with pytest.raises(ImportError):
        from codemie.service.marketplace_management.validator import (  # noqa: F401
            text_backends,
        )

    from codemie.service.marketplace_management.validator import engine

    assert not hasattr(engine, "_purpose_analysis_static"), (
        "the sklearn AgglomerativeClustering purpose path is still in tree; it "
        "cannot run because run_all_similarity never returns a vector index"
    )
