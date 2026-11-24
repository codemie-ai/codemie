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

"""Same-scope pricing of each amount: users, the chart, repositories, branches, projects (EPMCDME-17152).

Every row reports `cost_incomplete` / `unpriced_models` / `cost_unknown` over exactly the sessions behind its own
`cost_usd`: a fully priced row stays unmarked, a partly priced row keeps its priced part, an entirely unpriced row is
`cost_unknown`, a true $0 is not, and nothing is taken from the window's metadata or from a funding bucket.
"""

from unittest.mock import AsyncMock

import pytest

from codemie.repository.cli_analytics.ports import CostGroup
from codemie.repository.cli_analytics.postgres.user_rollup import Cohort, OverviewAgg, assemble_overview
from codemie.service.analytics.handlers.cli_analytics_handler import (
    _build_repository_rows,
    _cost_kpis,
    _group_repositories_into_projects,
)
from codemie.service.analytics.handlers.funding_buckets import (
    group_pricing,
    merge_pricing,
    pricing_of_rows,
    row_pricing,
    rows_of,
    session_index,
)
from tests.codemie.service.analytics.handlers.test_cli_analytics_handler import (
    BASE_COST_FACT,
    _drilldown_handler,
    _drilldown_page,
    _project_groups,
    _project_handler,
    _project_page,
)
from tests.codemie.service.analytics.handlers.test_cli_analytics_spend_scope import (
    MODEL,
    catalog,
    handler_over,
    make_filter,
)

UNPRICED = "mystery-model"
PRICED = {"cost_incomplete": False, "unpriced_models": [], "cost_unknown": False}


def _partial(*names):
    return {"cost_incomplete": True, "unpriced_models": list(names), "cost_unknown": False}


def _unknown(*names):
    return {"cost_incomplete": True, "unpriced_models": list(names), "cost_unknown": True}


def _pricing(row):
    return {k: row[k] for k in ("cost_incomplete", "unpriced_models", "cost_unknown")}


# ann: priced. bob: priced + unpriced (the priced part is kept). cid: only unpriced. dee: a priced model, no tokens.
SESSIONS = {
    "a1": {"dev": "ann", "repo": "repo-a", "paid": "corporate", "tokens": 1_000_000, "route": "api_based"},
    "b1": {"dev": "bob", "repo": "repo-b", "paid": "corporate", "tokens": 1_000_000, "route": "api_based"},
    "b2": {"dev": "bob", "repo": "repo-b", "paid": "corporate", "tokens": 1_000_000, "model": UNPRICED},
    "c1": {"dev": "cid", "repo": "repo-c", "paid": "corporate", "tokens": 1_000_000, "model": UNPRICED},
    "d1": {"dev": "dee", "repo": "repo-d", "paid": "corporate", "tokens": 0},
}
EXPECTED = {
    "ann": (1.0, PRICED),
    "bob": (1.0, _partial(UNPRICED)),
    "cid": (0.0, _unknown(UNPRICED)),
    "dee": (0.0, PRICED),
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "sessions", "free", "expected"),
    [
        ("complete", {"a": SESSIONS["a1"]}, False, PRICED),
        ("unknown", {"c": SESSIONS["c1"]}, False, _unknown(UNPRICED)),
        ("mixed", SESSIONS, False, _partial(UNPRICED)),
        ("free", {"a": SESSIONS["a1"], "c": SESSIONS["c1"]}, True, _partial(UNPRICED)),
    ],
)
@pytest.mark.parametrize("endpoint", ["overview", "cost"])
async def test_headline_kpis_carry_their_own_pricing(endpoint, scenario, sessions, free, expected):
    handler, _ = handler_over(sessions)
    with catalog() as models:
        if free:
            models.return_value[0].cost.input = 0
        data, *_ = await getattr(handler, f"get_{endpoint}")(make_filter())
    assert _pricing(data["kpis"]) == expected, scenario
    if free or scenario == "unknown":
        assert data["kpis"]["total_cost_usd"] == 0


def test_headline_cost_kpi_keeps_uncertain_rollup_evidence():
    total = CostGroup({}, 0.0, 0.0, 1, (UNPRICED,), True, has_priced=None)
    assert _pricing(_cost_kpis(total, [{"spend_sessions": 1}], [])) == {
        "cost_incomplete": True,
        "unpriced_models": [UNPRICED],
        "cost_unknown": None,
    }


@pytest.mark.asyncio
async def test_headline_overview_keeps_uncertain_rollup_evidence():
    handler, _ = handler_over({})
    handler._rollup_overview = AsyncMock(
        return_value=assemble_overview(Cohort(overview=OverviewAgg(unpriced={UNPRICED})))
    )
    data, *_ = await handler.get_overview(make_filter(), include_class_lists=False, include_buckets=False)
    assert data["kpis"]["total_cost_usd"] == 0
    assert _pricing(data["kpis"]) == {
        "cost_incomplete": True,
        "unpriced_models": [UNPRICED],
        "cost_unknown": None,
    }


@pytest.mark.asyncio
async def test_users_rows_and_the_chart_mark_each_users_own_amount():
    handler, _ = handler_over(SESSIONS)
    with catalog():
        data, pricer = await handler.get_users(make_filter(), 0, 2, include_chart=True)

    page = {r["developer_name"]: r for r in data["rows"]}
    assert len(page) == 2  # a page, not everyone
    for name, row in page.items():
        assert (row["cost_usd"], _pricing(row)) == (pytest.approx(EXPECTED[name][0]), EXPECTED[name][1])
    # the chart covers everyone, including the users outside the page, with the same marks
    assert {c["developer_name"]: (c["cost_usd"], _pricing(c)) for c in data["chart"]} == {
        name: (pytest.approx(cost), pricing) for name, (cost, pricing) in EXPECTED.items()
    }
    assert pricer.unpriced == [UNPRICED] or set(pricer.unpriced) == {UNPRICED}  # the window still reports it


@pytest.mark.asyncio
async def test_a_page_of_fully_priced_users_stays_unmarked_although_the_window_is_incomplete():
    handler, _ = handler_over(SESSIONS)
    with catalog():
        data, _ = await handler.get_users(make_filter(), 0, 10, include_chart=True)

    by_name = {r["developer_name"]: r for r in data["rows"]}
    assert _pricing(by_name["ann"]) == PRICED and _pricing(by_name["dee"]) == PRICED
    # the funding buckets describe other amounts and are not what marks a row
    assert by_name["ann"]["funding"]["api_based"]["cost_incomplete"] is False


@pytest.mark.asyncio
async def test_cost_by_user_and_by_model_rows_keep_their_own_marks():
    handler, _ = handler_over(SESSIONS)
    with catalog():
        data, _ = await handler.get_cost(make_filter())

    users = {r["developer_name"]: _pricing(r) for r in data["cost_by_user"]}
    assert users == {name: pricing for name, (_, pricing) in EXPECTED.items()}
    models = {r["model_name"]: (r["cost_usd"], _pricing(r)) for r in data["cost_by_model"]}
    assert models[MODEL] == (pytest.approx(2.0), PRICED)
    assert models[UNPRICED] == (0.0, _unknown(UNPRICED))  # the model's own row: nothing of it was priced


@pytest.mark.asyncio
@pytest.mark.parametrize("include_branches", [False, True])
async def test_repository_rows_mark_their_own_sessions(include_branches):
    handler, _ = handler_over(SESSIONS)
    with catalog():
        data, _ = await handler.get_repositories(make_filter(), 0, 50, include_branches=include_branches)

    rows = {r["repository"]: r for r in data["rows"]}
    assert _pricing(rows["repo-a"]) == PRICED
    assert _pricing(rows["repo-b"]) == _partial(UNPRICED)  # repo-b's priced part is kept
    assert rows["repo-b"]["cost_usd"] == pytest.approx(1.0)
    assert _pricing(rows["repo-c"]) == _unknown(UNPRICED)
    assert _pricing(rows["repo-d"]) == PRICED  # a true $0
    if include_branches:
        assert all(_pricing(b) == _pricing(r) for r in data["rows"] for b in r["branches"])  # one branch each


def _group(project, repository, branch, cost, *, names=(), priced=True, incomplete=None):
    return {
        **BASE_COST_FACT,
        "project_name": project,
        "repository": repository,
        "branch": branch,
        "n": 1,
        "cost_usd": cost,
        "unpriced_models": list(names),
        "cost_incomplete": bool(names) if incomplete is None else incomplete,
        "cost_priced": priced,
    }


def test_a_repository_is_incomplete_when_any_branch_is_and_unknown_only_when_no_branch_was_priced():
    rows = _build_repository_rows(
        [
            _group("p", "mixed", "ok", 3.0),
            _group("p", "mixed", "bad", 0.0, names=["b"], priced=False),
            _group("p", "lost", "x", 0.0, names=["a"], priced=False),
            _group("p", "lost", "y", 0.0, names=["b"], priced=False),
            _group("p", "free", "z", 0.0),
        ],
        include_branches=True,
    )

    by_repo = {r["repository"]: r for r in rows}
    assert _pricing(by_repo["mixed"]) == _partial("b")
    assert [(b["branch"], _pricing(b)) for b in by_repo["mixed"]["branches"]] == [
        ("bad", _unknown("b")),
        ("ok", PRICED),  # the clean sibling stays unmarked
    ]
    assert _pricing(by_repo["lost"]) == _unknown("a", "b")
    assert _pricing(by_repo["free"]) == PRICED


def test_an_empty_complete_child_is_no_evidence_but_a_used_zero_rate_one_is():
    rows = _build_repository_rows(
        [
            _group("p", "empty-sibling", "e", 0.0, priced=False),  # complete, no used token: nothing priced
            _group("p", "empty-sibling", "u", 0.0, names=["a"], priced=False),
            _group("p", "free-sibling", "f", 0.0, priced=True),  # complete, a used kind with a (zero) rate
            _group("p", "free-sibling", "u", 0.0, names=["a"], priced=False),
        ],
        include_branches=True,
    )

    by_repo = {r["repository"]: r for r in rows}
    assert _pricing(by_repo["empty-sibling"]) == _unknown("a")
    assert _pricing(by_repo["free-sibling"]) == _partial("a")  # $0 total, yet the free usage was priced


def test_merging_children_without_evidence_does_not_guess_from_their_completeness():
    unpriced = {**_unknown("a"), "_priced": False}
    empty = {**PRICED, "_priced": False}
    priced = {**PRICED, "_priced": True}
    no_evidence = {**PRICED}  # a child that cannot say (stored rollup): no `_priced` at all

    assert merge_pricing([unpriced, empty])["cost_unknown"] is True
    assert merge_pricing([unpriced, priced])["cost_unknown"] is False
    assert merge_pricing([unpriced, no_evidence])["cost_unknown"] is None
    assert merge_pricing([empty, priced])["cost_incomplete"] is False


def test_a_project_carries_the_marks_of_its_repositories_up():
    repos = _build_repository_rows(
        [_group("p", "clean", "m", 1.0), _group("p", "dirty", "m", 0.0, names=["a"], priced=False)],
        include_branches=True,
    )
    project = _group_repositories_into_projects(repos, ["p"])[0]

    assert _pricing(project) == _partial("a")  # the clean repository's priced usage rules out "unknown"
    assert {r["repository"]: _pricing(r) for r in project["repositories"]} == {
        "clean": PRICED,
        "dirty": _unknown("a"),
    }


def test_the_source_that_cannot_prove_usage_reports_none_instead_of_guessing_from_zero():
    unknown_cost = CostGroup({}, 0.0, 0.0, 1, ("m",), True)  # a stored rollup: names, no usage evidence
    assert group_pricing(unknown_cost)["cost_unknown"] is None
    assert group_pricing(CostGroup({}, 2.0, 0.0, 1, ("m",), True))["cost_unknown"] is False  # money proves usage
    assert group_pricing(CostGroup({}, 0.0, 0.0, 1, (), False))["cost_unknown"] is False  # a true $0
    assert group_pricing(CostGroup({}, 0.0, 0.0, 1, ("m",), True, has_priced=False))["cost_unknown"] is True
    assert group_pricing(CostGroup({}, 0.0, 0.0, 1, ("m",), True, has_priced=True))["cost_unknown"] is False
    assert group_pricing(None) == {"cost_incomplete": False, "unpriced_models": [], "cost_unknown": False}
    assert merge_pricing([_unknown("a"), _unknown("b")])["cost_unknown"] is None  # no `_priced`: cannot say
    row = row_pricing({"cost_usd": 0, "cost_incomplete": True, "unpriced_models": ["m"], "cost_priced": False})
    assert _pricing(row) == _unknown("m") and row["_priced"] is False


def test_python_priced_rows_distinguish_unknown_partial_and_a_free_priced_model():
    rows = [
        {"session_id": "s1", "model": "free", "missing": False, "priced": True, "cost_usd": 0.0},  # a zero rate
        {"session_id": "s2", "model": UNPRICED, "missing": True, "priced": False, "cost_usd": 0.0},
        {"session_id": "s3", "model": UNPRICED, "missing": True, "priced": False, "cost_usd": 0.0},
        {"session_id": "s4", "model": "empty", "missing": False, "priced": False, "cost_usd": 0.0},  # no used token
        {"session_id": "s5", "model": "mixed", "missing": True, "priced": True, "cost_usd": 0.0},  # zero-rate + no-rate
    ]
    index = session_index(rows)
    assert pricing_of_rows(rows_of(rows, index, ["s1"])) == PRICED  # a zero rate still prices the usage
    assert pricing_of_rows(rows_of(rows, index, ["s2", "s3"])) == _unknown(UNPRICED)
    assert pricing_of_rows(rows_of(rows, index, ["s1", "s2"])) == _partial(UNPRICED)
    assert pricing_of_rows(rows_of(rows, index, ["s2", "s4"])) == _unknown(UNPRICED)
    assert pricing_of_rows(rows_of(rows, index, ["s5"])) == _partial("mixed")
    assert pricing_of_rows(rows_of(rows, index, [])) == PRICED


def _priced_group(group: dict, *, names=(), priced=True) -> dict:
    """A ranking group of the reader with its pricing columns (`unpriced_models`, `cost_incomplete`, `cost_priced`)."""
    return {**group, "unpriced_models": list(names), "cost_incomplete": bool(names), "cost_priced": priced}


@pytest.mark.asyncio
async def test_projects_mark_the_project_each_repository_and_each_branch_from_their_own_groups():
    """The groups of the page carry their own pricing from the statement that prices them (a1 priced, a2 and n1 not);
    the repositories take their branches' marks and the project those of its repositories."""
    handler = _project_handler(_project_page())
    groups = [
        {**_priced_group(g, names=names, priced=priced), "cost_usd": cost}
        for g, (names, priced, cost) in zip(
            _project_groups()[0], [((), True, 1.0), ((UNPRICED,), False, 0.0), ((UNPRICED,), False, 0.0)], strict=True
        )
    ]
    handler._repo.get_repository_ranking = AsyncMock(return_value=groups)
    with catalog():
        data, _ = await handler.get_projects(make_filter(), page=0, per_page=10)

    alpha, none = data["rows"]
    assert alpha["project_name"] == "alpha"
    assert _pricing(alpha) == _partial(UNPRICED)  # repo-a was priced, repo-b was not
    repos = {r["repository"]: r for r in alpha["repositories"]}
    assert _pricing(repos["repo-a"]) == PRICED
    assert _pricing(repos["repo-b"]) == _unknown(UNPRICED)
    assert _pricing(repos["repo-a"]["branches"][0]) == PRICED
    assert _pricing(repos["repo-b"]["branches"][0]) == _unknown(UNPRICED)
    assert _pricing(none) == _unknown(UNPRICED)
    assert "_sids" not in repos["repo-b"]["branches"][0]
    assert not any("_priced" in row for row in _walk(data["rows"]))  # the merge evidence never reaches the answer


def _row(sid, model):
    return {
        "session_id": sid,
        "model_name": model,
        "normalized_model_name": model,
        "input_tokens": 1_000_000,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }


def test_the_python_evidence_is_a_used_kind_with_a_rate_by_the_pricers_own_resolution():
    from unittest.mock import MagicMock, patch

    from codemie.configs.llm_config import CostConfig
    from codemie.service.analytics.handlers.funding_buckets import price_bucket_rows
    from tests.codemie.service.analytics.handlers.test_cli_analytics_spend_scope import CATALOG

    free_input_model = MagicMock()
    free_input_model.base_name = free_input_model.deployment_name = "zero-rate-model"
    free_input_model.cost = CostConfig(input=1e-6, output=5e-6, cache_read_input_token_cost=0.0)

    def run(model, **tokens):
        row = {"session_id": "s", "model_name": model, "normalized_model_name": model, **tokens}
        with patch(CATALOG, return_value=[free_input_model]):
            (priced,) = price_bucket_rows([row])
        return priced["missing"], priced["priced"], priced["cost_usd"]

    # a used zero-rate kind next to a used kind with no rate: incomplete, and priced (at $0)
    assert run("zero-rate-model", cache_read_tokens=3, cache_creation_tokens=7) == (True, True, 0.0)
    # only the kind with no rate was used: nothing priced
    assert run("zero-rate-model", cache_creation_tokens=7) == (True, False, 0.0)
    # no used token: neither incomplete nor priced
    assert run("zero-rate-model", input_tokens=0) == (False, False, 0.0)
    # a model outside the catalog: unpriced, nothing priced
    assert run("mystery-model", input_tokens=5) == (True, False, 0.0)


# ── The per-entity reads of /projects go through one session index (EPMCDME-17152 follow-up) ───────────────────────


def test_rows_of_keeps_the_order_of_the_priced_rows_and_ignores_unknown_sessions():
    from codemie.service.analytics.handlers.funding_buckets import rows_of, session_index

    priced = [{"session_id": s, "n": n} for n, s in enumerate(["b", "a", "b", "c", "a"])]
    index = session_index(priced)

    assert [r["n"] for r in rows_of(priced, index, {"a", "b", "zzz"})] == [0, 1, 2, 4]  # the linear scan's order
    assert rows_of(priced, index, []) == []
    assert rows_of(priced, index, {"zzz"}) == []


@pytest.mark.asyncio
async def test_drilldown_projects_equal_the_linear_scan_on_a_paginated_fixture_with_filtered_out_rows(monkeypatch):
    """Alpha and beta are the page; gamma is in the window (its rows are in `priced`) but not on the page. The index
    reads give the response the whole-window scan gave, and gamma's models reach no project of the page."""
    import codemie.service.analytics.handlers.cli_analytics_handler as handler_module

    sessions = [  # (session, project, window rows as (model, tokens))
        ("a1", "alpha", [(MODEL, 1_000_000)]),
        ("a2", "alpha", [(UNPRICED, 1_000_000)]),
        ("a3", "alpha", [(MODEL, 700_000), (UNPRICED, 300_000)]),
        ("b1", "beta", [(MODEL, 0)]),  # a zero-token row beside nothing else: no evidence, complete
        ("b2", "beta", [("beta-unpriced", 5)]),
        ("n1", None, [(MODEL, 123_456)]),
        ("g1", "gamma", [("gamma-only-model", 9)]),
        ("g2", "gamma", [(MODEL, 2_000_000)]),
    ]
    on_page = {"alpha": ["a1", "a2", "a3"], "beta": ["b1", "b2"], None: ["n1"]}
    totals = [
        {
            "project_name": project,
            "session_count": len(ids),
            "turns": 0,
            "cost_usd": 0.0,
            "files_changed": 0,
            "lines_added": None,
            "lines_removed": None,
            "tool_calls": 0,
            "tool_calls_success": 0,
            "subscription_types": ["api"],
            "project_origins": ["none"],
            "repository_count": 1,
            "session_ids": ids,
        }
        for project, ids in on_page.items()
    ]
    window = [
        {
            **_row(s, model),
            "input_tokens": tokens,
            "funding_route": ("api_based", "subscription", None)[number % 3],
            "personal_billed": number % 4 == 0,
            "subscription_type": "corporate",
        }
        for number, (s, _project, rows) in enumerate(sessions)
        for model, tokens in rows
    ]

    async def respond(index_reads: bool):
        handler = _drilldown_handler(
            _drilldown_page(
                project_names=["alpha", "beta"],
                session_ids=["a1", "a2", "a3", "b1", "b2"],
                total=2,
                unattributed_session_ids=["n1"],
            )  # fmt: skip
        )
        handler._repo.get_project_totals = AsyncMock(return_value=totals)
        handler._repo.get_session_funding_costs = AsyncMock(return_value=window)
        with monkeypatch.context() as patched, catalog():
            if not index_reads:  # the reviewed implementation: every project scans the whole window
                patched.setattr(
                    handler_module,
                    "rows_of",
                    lambda priced, _index, sids: [r for r in priced if r["session_id"] in set(sids)],
                )
            data, _ = await handler.get_projects(make_filter(), include_repositories=False, page=0, per_page=3)
        return data

    indexed, scanned = await respond(True), await respond(False)

    assert indexed == scanned  # every value, float sums included
    names = {name for row in indexed["rows"] for name in row["unpriced_models"]}
    assert "gamma-only-model" not in names and {UNPRICED, "beta-unpriced"} <= names
    by_project = {p["project_name"]: p for p in indexed["rows"]}
    assert _pricing(by_project["beta"]) == _unknown("beta-unpriced")  # b1's zero-token row is no evidence
    assert _pricing(by_project["alpha"]) == _partial(UNPRICED)
    assert all("_sids" not in row and "_priced" not in row for row in indexed["rows"])
    # the whole-window buckets still read the whole window: gamma's model is in the window's funding buckets
    assert "gamma-only-model" in {name for bucket in indexed["funding"].values() for name in bucket["unpriced_models"]}


def _walk(rows):
    for row in rows:
        yield row
        yield from _walk(row.get("repositories") or [])
        yield from _walk(row.get("branches") or [])
