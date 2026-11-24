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

"""`/projects` on the SQL ranking against the Python assembly it replaced (EPMCDME-17301).

`data/projects_parity_old.json` is what `LocalAnalyticsHandler.get_projects` returned over the live seed of
`test_live_dashboard_aggregates` before the assembly was deleted (per-session reads priced in Python, the page's
funding buckets from `get_session_funding_costs`): the response of ten requests (page, search, empty page). The new
path must give the same response, and must read the facts, the buckets and the funding costs of the page's groups,
not of the whole window. Live: skipped without `TEST_LOCAL_PG_URL`.

The file was regenerated after EPMCDME-17260, which counts the Personal-paid cost in every aggregate: the project and
the repository that hold the seed's Personal-paid session cost its 0.00192 more, and that moves `repo-c` above `repo-a`
(ranked by cost). Their figures, branch by branch, are those of the earlier file.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import patch

import pytest

from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.postgres import test_live_dashboard_aggregates as live

pytestmark = pytest.mark.skipif(
    not live._LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against"
)

_OLD = json.loads((Path(__file__).parent / "data" / "projects_parity_old.json").read_text())
_TOLERANCE = 1e-9  # absolute: the money of the seed is a few thousandths of a dollar


def _same(actual, expected, path: str = "") -> list[str]:
    """The differences between two responses, as `path: actual != expected`; money within `_TOLERANCE`."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or actual.keys() != expected.keys():
            return [f"{path}: keys {sorted(actual or {})} != {sorted(expected)}"]
        return [d for k in expected for d in _same(actual[k], expected[k], f"{path}.{k}")]
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return [f"{path}: {actual!r} != {expected!r}"]
        return [d for i, (a, e) in enumerate(zip(actual, expected)) for d in _same(a, e, f"{path}[{i}]")]
    if isinstance(expected, float) or isinstance(actual, float):
        ok = (
            actual is not None
            and expected is not None
            and math.isclose(actual, expected, rel_tol=0, abs_tol=_TOLERANCE)
        )
        return [] if ok else [f"{path}: {actual!r} != {expected!r}"]
    return [] if actual == expected else [f"{path}: {actual!r} != {expected!r}"]


async def _run(handler, f, search, page, per_page):
    with patch(live._PRICING, live._lookup):
        return await handler.get_projects(f, page, per_page, search)


@pytest.mark.asyncio
async def test_projects_equal_the_python_assembly_they_replaced():
    engine, seed = await live._engine(), live._Seed()
    try:
        await seed.insert(engine)
        handler, f = LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine)), seed.filter()
        for case in _OLD:
            data, pricer = await _run(handler, f, case["search"], case["page"], case["per_page"])
            label = (case["search"], case["page"], case["per_page"])
            assert _same(json.loads(json.dumps(data, default=str)), case["data"]) == [], label
            assert list(pricer.unpriced) == case["unpriced"], label
    finally:
        await seed.cleanup(engine)


@pytest.mark.asyncio
async def test_drilldown_projects_equal_the_project_rows_of_the_default_page():
    """The totals-only page (`include_repositories=false`, one statement over the page's sessions) gives every figure of
    the project rows the nested default gives, and `repository_count` is the number of nested repositories."""
    engine, seed = await live._engine(), live._Seed()
    try:
        await seed.insert(engine)
        handler, f = LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine)), seed.filter()
        with patch(live._PRICING, live._lookup):
            nested, _ = await handler.get_projects(f, 0, 10, None)
            totals, _ = await handler.get_projects(f, 0, 10, None, include_repositories=False)
        nested_by_name = {p["project_name"]: p for p in nested["rows"]}
        assert {p["project_name"] for p in totals["rows"]} == set(nested_by_name)
        for row in totals["rows"]:
            full = nested_by_name[row["project_name"]]
            assert row["repository_count"] == len(full["repositories"])
            figures = {k: v for k, v in row.items() if k != "repository_count"}
            expected = {k: full[k] for k in figures}
            assert (
                _same(json.loads(json.dumps(figures, default=str)), json.loads(json.dumps(expected, default=str))) == []
            ), row["project_name"]
    finally:
        await seed.cleanup(engine)


class _Recorder:
    """What the new path reads: the reader methods with the ids their filter is restricted to, and for each statement
    whether its parameters carry that restriction."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str] | None]] = []
        self.statements: list[bool] = []


def _record(recorder: _Recorder):
    patches = []
    for name in (
        "get_cost_models",
        "get_project_page",
        "get_repository_ranking",
        "get_repository_facts",
        "get_project_buckets",
    ):
        original = getattr(PostgresCliAnalyticsReader, name, None)
        if original is None:
            continue

        def wrap(original=original, name=name):
            async def call(self, f, *args, **kwargs):
                recorder.calls.append((name, f.session_ids))
                return await original(self, f, *args, **kwargs)

            return call

        patches.append(patch.object(PostgresCliAnalyticsReader, name, wrap()))
    for name in ("_q", "_priced_q"):
        original = getattr(PostgresCliAnalyticsReader, name)

        def wrap_statement(original=original, name=name):
            async def call(self, *args, **kwargs):
                params = args[1] if name == "_q" else args[2]
                recorder.statements.append("session_ids" in params)
                return await original(self, *args, **kwargs)

            return call

        patches.append(patch.object(PostgresCliAnalyticsReader, name, wrap_statement()))
    return patches


@pytest.mark.asyncio
@pytest.mark.parametrize("search", [None, "repo-a"])
async def test_projects_read_the_facts_buckets_and_funding_of_the_page_only(search):
    engine, seed = await live._engine(), live._Seed()
    recorder = _Recorder()
    try:
        await seed.insert(engine)
        handler, f = LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine)), seed.filter()
        patches = _record(recorder)
        for p in patches:
            p.start()
        try:
            data, _ = await _run(handler, f, search, 0, 1)
        finally:
            for p in patches:
                p.stop()
        assert [r["project_name"] for r in data["rows"]] == ["P1"]
        page_ids = next(ids for name, ids in recorder.calls if name == "get_repository_ranking")
        assert page_ids, "the page's groups are read from the page's sessions"
        by_name = {}
        for name, ids in recorder.calls:
            by_name.setdefault(name, []).append(ids)
        assert by_name["get_repository_facts"] == [page_ids]
        assert by_name["get_cost_models"] == [None]  # the window's models, to price the page order
        assert by_name["get_project_page"] == [None]
        # The page's buckets: restricted to the page; the window's totals: the whole window, or what the search kept.
        project_buckets = by_name["get_project_buckets"]
        assert page_ids in project_buckets and len(project_buckets) == 2
        window = next(ids for ids in project_buckets if ids != page_ids)
        assert (window is None) == (search is None)
        # No statement of the page's reads is over the whole window: the unrestricted ones are the model list, the
        # page, and (without a search) the window's totals.
        assert recorder.statements.count(False) == (3 if search is None else 2)
    finally:
        await seed.cleanup(engine)
