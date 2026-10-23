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

"""Responses built from the cached analysis: display thresholds, filters, sorting, totals and settings checks."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.usage_attribution import handler as h
from codemie.service.usage_attribution.analysis import AnalysisHandle, resolve_range
from codemie.service.usage_attribution.sections import shard_of
from codemie.service.usage_attribution.settings import SettingsError, build_defaults, build_effective, encode_overrides

NOW = datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)
ADMIN = SimpleNamespace(id="admin", is_admin=True, is_maintainer=False, is_auditor=False)
AUDITOR = SimpleNamespace(id="auditor", is_admin=False, is_maintainer=False, is_auditor=True)


def shared_row(name, ncb, platform=0.0, cli=0.0):
    return {"id": f"s-{name}", "name": name, "ncb": ncb, "platform": platform, "cli": cli, "cli_sh": cli, "web_sh": platform,
            "people": 1, "prod_pct": None, "via": ["web"]}  # fmt: skip


def group_row(gid, ncb, n=2, excluded=False):
    return {"id": gid, "name": gid, "grouped": "x", "repositories": n, "people": 2, "spend": ncb + 10, "prod": ncb,
            "ncb": ncb, "prod_pct": 50, "excluded": excluded, "repos_preview": [], "related": None, "related_reason": "none",
            "search": f"{gid} repo-{gid}"}  # fmt: skip


def person_row(uid, total, exp=0.0, platform=0.0, prod_ncb=0.0, shared_nb=0.0, days=0, charged_back=0.0, learning=None):
    """``learning`` is the learning and experiments paid personally: all of ``exp`` unless given."""
    learning = exp if learning is None else learning
    return {"uid": uid, "name": uid.upper(), "email": f"{uid}@x.com", "city": "", "country": "", "job": "", "total": total,
            "ncb": 0.0, "platform": platform, "cli": total - platform, "prod": 0.0, "exp": exp, "uncl": 0.0, "alloc": 0.0,
            "prod_ncb": prod_ncb, "shared_no_budget": shared_nb, "projects": "personal", "in_shared_project": False,
            "main_repo": f"{uid}/repo", "main_group": None, "charged_back": charged_back, "personal_learning": learning,
            "personal_other": max(0.0, total - charged_back - prod_ncb - learning), "production_days": days}  # fmt: skip


def pw(uid, amount, project=None, org=None, group=None, estimated=0.0):
    """One row of the production work section (people_work.py)."""
    return {"uid": uid, "amount": float(amount), "estimated": float(estimated), "repository": f"{org or 'x'}/{uid}-repo",
            "project": project, "project_how": "inside" if project else None,
            "project_reason": "inside" if project else None, "group": group, "group_label": group, "organization": org}  # fmt: skip


def two_groups():
    """Four candidates: a and b in the CodeMie project nwnd-pay ($900), c and d in the Git organization acme ($1,200)."""
    amounts = {"a": 500, "b": 400, "c": 700, "d": 500}
    rows = [person_row(u, 1000, prod_ncb=a, days=8, charged_back=100) for u, a in amounts.items()]
    work = [pw("a", 500, project="nwnd-pay"), pw("b", 400, project="nwnd-pay"), pw("c", 700, org="acme"),
            pw("d", 500, org="acme")]  # fmt: skip
    return {"people": rows, "pwork": work}


class FakeService:
    def __init__(self, sections):
        self.sections = sections
        self.cache = MagicMock(backend="in-process")

    async def get_analysis(self, rng, eff, user_id, extra):
        return AnalysisHandle(
            "prefix", {"computed_at": "2026-10-13T15:00:00Z", "duration_ms": 10, "headline": {}}, "hit"
        )

    async def load_sections(self, handle, names):
        return {n: self.sections[n] for n in names}


def setup(sections, overrides=None, period="last_30_days", total_spend=10_000.0):
    eff = build_effective(build_defaults({}), overrides or {})
    rng = resolve_range(period, None, None, 90, 30, now=NOW)
    # The period's Total Spend, which the % of Total Spend column divides by.
    sections = {"summary": {"tiles": {"total": total_spend}}, **sections}
    return h.UsageAttributionHandler(ADMIN, FakeService(sections)), eff, rng


@pytest.mark.asyncio
class TestPeopleChargeback:
    async def test_candidate_needs_production_on_five_days(self):
        rows = [person_row("a", 300, prod_ncb=200, days=5), person_row("b", 300, prod_ncb=200, days=4),
                person_row("c", 300, prod_ncb=0.0, days=20)]  # fmt: skip
        handler, eff, rng = setup({"people": rows, "pwork": []})
        out = await handler.people(rng, eff, "cand", None, 0, 10, None, "desc")
        assert [r["uid"] for r in out["rows"]] == ["a"] and out["counts"]["cand"] == 1
        flags = {
            r["uid"]: r["candidate"] for r in (await handler.people(rng, eff, "all", None, 0, 10, None, "desc"))["rows"]
        }
        assert flags == {"a": True, "b": False, "c": False}

    async def test_candidate_days_override_is_instant(self):
        handler, eff, rng = setup(
            {"people": [person_row("a", 300, prod_ncb=200, days=6)], "pwork": []},
            overrides={"candidateProductionDaysPer30Days": 10},
        )
        assert (await handler.people(rng, eff, "cand", None, 0, 10, None, "desc"))["rows"] == []

    async def test_short_ranges_keep_the_thirty_day_evidence_threshold(self):
        handler, eff, rng = setup(
            {"people": [person_row("a", 300, prod_ncb=200, days=6)], "pwork": []}, period="last_7_days"
        )
        assert (await handler.people(rng, eff, "cand", None, 0, 10, None, "desc"))["counts"]["cand"] == 1

    async def test_people_sort_by_production_on_personal_budgets(self):
        rows = [person_row("a", 900, prod_ncb=10, days=9), person_row("b", 100, prod_ncb=90, days=9)]
        handler, eff, rng = setup({"people": rows, "pwork": []})
        out = await handler.people(rng, eff, "cand", None, 0, 10, "prod_ncb", "desc")
        assert [r["uid"] for r in out["rows"]] == ["b", "a"]

    async def test_learning_filter_and_highlight_follow_the_personal_budget_column(self):
        # a's $800 of learning is charged back to a project: the platform pays none of it.
        rows = [person_row("a", 900, exp=800, learning=0.0, charged_back=800), person_row("b", 700, exp=600)]
        handler, eff, rng = setup({"people": rows, "pwork": []})
        out = await handler.people(rng, eff, "exp", None, 0, 10, None, "desc")
        assert [r["uid"] for r in out["rows"]] == ["b"] and out["counts"]["exp"] == 1
        everyone = (await handler.people(rng, eff, "all", None, 0, 10, None, "desc"))["rows"]
        assert {r["uid"]: r["exp_highlight"] for r in everyone} == {"a": False, "b": True}

    async def test_no_common_condition_comes_with_the_last_page_only(self):
        data = two_groups()
        data["people"].append(person_row("e", 1000, prod_ncb=100, days=8))
        data["pwork"].append(pw("e", 100))
        handler, eff, rng = setup(data)
        first = await handler.people_groups(rng, eff, "common", 0, None, 0, 1, None, "desc")
        last = await handler.people_groups(rng, eff, "common", 0, None, 1, 1, None, "desc")
        assert first["none"] is None
        assert last["none"]["amount"] == 100

    async def test_groups_are_built_once_per_analysis_days_and_grouping(self):
        handler, eff, rng = setup(two_groups())
        with patch.object(h, "group_candidates", wraps=h.group_candidates) as built:
            await handler.people_summary(rng, eff)
            await handler.people_groups(rng, eff, "common", 0, None, 0, 25, None, "desc")
            assert built.call_count == 1
            await handler.people_groups(rng, eff, "project", 0, None, 0, 25, None, "desc")
            assert built.call_count == 2
            other = build_effective(build_defaults({}), {"candidateProductionDaysPer30Days": 9})
            await handler.people_summary(rng, other)
            assert built.call_count == 3

    async def test_over_mark_uses_the_group_threshold(self):
        handler, eff, rng = setup(two_groups())  # last 30 days: the threshold is about $1,000
        rows = (await handler.people_groups(rng, eff, "common", 0, None, 0, 25, None, "desc"))["rows"]
        assert {r["amount"]: r["over_threshold"] for r in rows} == {900: False, 1200: True}
        assert [r["label"] for r in rows] == ["acme", "nwnd-pay"]  # the biggest first
        assert {r["label"]: (r["candidates"], r["median_days"]) for r in rows} == {"acme": (2, 8), "nwnd-pay": (2, 8)}

    async def test_people_groups_min_amount_scales_with_the_range(self):
        handler, eff, rng = setup(two_groups(), period="last_60_days")
        out = await handler.people_groups(rng, eff, "common", 1000, None, 0, 25, None, "desc")
        assert out["rows"] == []  # $1,000 per 30 days is about $2,000 over 60 days
        assert out["none"] is None

    async def test_no_common_condition_row(self):
        data = two_groups()
        data["people"].append(person_row("e", 300, prod_ncb=60, days=7))
        data["pwork"].append(pw("e", 60))
        handler, eff, rng = setup(data)
        out = await handler.people_groups(rng, eff, "common", 0, None, 0, 25, None, "desc")
        assert out["none"]["candidates"] == 1 and out["none"]["amount"] == 60
        assert (await handler.people_groups(rng, eff, "common", 500, None, 0, 25, None, "desc"))["none"] is None

    async def test_people_groups_search_matches_groups_and_people(self):
        handler, eff, rng = setup(two_groups())
        by_label = await handler.people_groups(rng, eff, "common", 0, "nwnd", 0, 25, None, "desc")
        assert [r["label"] for r in by_label["rows"]] == ["nwnd-pay"]
        by_person = await handler.people_groups(rng, eff, "common", 0, "c@x.com", 0, 25, None, "desc")
        assert [r["label"] for r in by_person["rows"]] == ["acme"]

    async def test_people_summary_tiles(self):
        handler, eff, rng = setup(two_groups())
        tiles = (await handler.people_summary(rng, eff))["tiles"]
        assert (tiles["candidates"], tiles["groups"], tiles["groups_over"], tiles["in_groups"]) == (4, 2, 1, 2100)
        assert (tiles["candidates_production"], tiles["production"], tiles["people_with_production"]) == (2100, 2100, 4)

    async def test_people_group_detail_and_unknown_id(self):
        handler, eff, rng = setup(two_groups())
        gid = (await handler.people_groups(rng, eff, "common", 0, None, 0, 25, None, "desc"))["rows"][0]["id"]
        detail = await handler.people_group(rng, eff, "common", gid)
        assert sum(m["in_group"] for m in detail["members"]) == detail["row"]["amount"] == 1200
        assert [m["uid"] for m in detail["members"]] == ["c", "d"]
        assert detail["charged_back_same_people"] == 200
        assert detail["facts"]["condition"] == "organization"
        with pytest.raises(h.EntityNotFoundError):
            await handler.people_group(rng, eff, "common", "project-0000000000")

    async def test_person_popup_has_the_chargeback_block(self):
        data = two_groups()
        data[f"pdetail:{shard_of('a')}"] = {"a": {"row": data["people"][0], "detail": {"repos": []}}}
        handler, eff, rng = setup(data)
        handler._person_live = AsyncMock(return_value={})
        block = (await handler.person(rng, eff, "a"))["detail"]["chargeback"]
        assert (block["candidate"], block["production_days"], block["threshold_days"], block["alone"]) == (
            True,
            8,
            5,
            0,
        )
        assert [(g["condition"], g["label"], g["amount"], g["others"]) for g in block["groups"]] == [
            ("project", "nwnd-pay", 500, 1)
        ]


@pytest.mark.asyncio
class TestTables:
    async def test_shared_projects_threshold_and_scope(self):
        rows = [shared_row("a", 5000), shared_row("b", 1500), shared_row("c", 200)]
        handler, eff, rng = setup({"shared": rows})
        out = await handler.shared_projects(rng, eff, "threshold", None, 0, 10, None, "desc")
        threshold = 1000 * rng.scale
        assert out["threshold"] == pytest.approx(threshold, abs=0.01)
        assert [r["name"] for r in out["rows"]] == ["a", "b"]
        assert out["counts"] == {"threshold": 2, "all": 3}
        assert out["totals"]["ncb"] == 6500
        everything = await handler.shared_projects(rng, eff, "all", None, 0, 10, "ncb", "asc")
        assert [r["name"] for r in everything["rows"]] == ["c", "b", "a"]
        assert [r["candidate"] for r in everything["rows"]] == [False, True, True]

    async def test_display_override_changes_the_view_only(self):
        rows = [shared_row("a", 5000), shared_row("b", 1500), shared_row("c", 200)]
        handler, eff, rng = setup({"shared": rows}, {"sharedProjectThresholdPer30Days": 100})
        out = await handler.shared_projects(rng, eff, "threshold", None, 0, 10, None, "desc")
        assert out["counts"]["threshold"] == 3

    async def test_search_sort_and_pagination(self):
        rows = [shared_row(f"proj-{i}", 2000 + i) for i in range(25)]
        handler, eff, rng = setup({"shared": rows})
        out = await handler.shared_projects(rng, eff, "all", "PROJ-1", 1, 5, "name", "asc")
        assert out["pagination"]["total_count"] == 11  # proj-1, proj-10..19
        assert out["pagination"]["page"] == 1 and out["pagination"]["has_more"] is True
        assert [r["name"] for r in out["rows"]] == ["proj-14", "proj-15", "proj-16", "proj-17", "proj-18"]
        # An unknown sort column falls back to the default.
        default = await handler.shared_projects(rng, eff, "all", None, 0, 3, "__class__", "desc")
        assert [r["name"] for r in default["rows"]] == ["proj-24", "proj-23", "proj-22"]

    async def test_repository_groups(self):
        rows = [
            group_row("g-1", 3000),
            group_row("g-2", 50, n=3),
            group_row("g-3", 5000, excluded=True),
            group_row("g-4", 2000, n=1),
        ]
        handler, eff, rng = setup({"groups": rows})
        out = await handler.repository_groups(rng, eff, "threshold", None, 0, 10, None, "desc")
        assert [r["id"] for r in out["rows"]] == ["g-1", "g-4"]
        assert all("search" not in r for r in out["rows"])
        everything = await handler.repository_groups(rng, eff, "all", "repo-g-2", 0, 10, None, "desc")
        assert [r["id"] for r in everything["rows"]] == ["g-2"]
        assert out["counts"] == {"threshold": 2, "all": 3}

    async def test_people_filters(self):
        rows = [
            person_row("a", 900, exp=600),
            person_row("b", 800, platform=400),
            person_row("c", 700, prod_ncb=50),
            person_row("d", 100, shared_nb=20),
        ]
        handler, eff, rng = setup({"people": rows})
        for flag, expected in (
            ("all", ["a", "b", "c", "d"]),
            ("exp", ["a"]),
            ("platform", ["b"]),
            ("prod", ["c"]),
            ("shared", ["d"]),
        ):
            out = await handler.people(rng, eff, flag, None, 0, 10, None, "desc")
            assert [r["uid"] for r in out["rows"]] == expected, flag
        out = await handler.people(rng, eff, "all", "d@x", 0, 10, None, "desc")
        assert [r["uid"] for r in out["rows"]] == ["d"]
        assert out["counts"]["exp"] == 1
        highlighted = await handler.people(rng, eff, "all", None, 0, 10, None, "desc")
        assert {r["uid"]: r["exp_highlight"] for r in highlighted["rows"]}["a"] is True

    async def test_share_of_total_spend(self):
        # A project's spend is its Web Platform and CLI spend; the total row adds up every matching row, not only the
        # page.
        projects = [shared_row("a", 1500, platform=300, cli=700), shared_row("b", 1200, platform=200, cli=300)]
        handler, eff, rng = setup({"shared": projects})
        out = await handler.shared_projects(rng, eff, "all", None, 0, 1, "share", "asc")
        assert [(r["name"], r["share"]) for r in out["rows"]] == [("b", 5.0)]
        assert out["totals"]["share"] == 15.0
        groups = [group_row("g-1", 3000), group_row("g-2", 2000)]  # spend: ncb + 10
        handler, eff, rng = setup({"groups": groups})
        out = await handler.repository_groups(rng, eff, "all", None, 0, 10, "share", "desc")
        assert [(r["id"], r["share"]) for r in out["rows"]] == [("g-1", 30.1), ("g-2", 20.1)]
        assert out["totals"]["share"] == 50.2
        handler, eff, rng = setup({"people": [person_row("a", 900), person_row("b", 100)]})
        out = await handler.people(rng, eff, "all", None, 0, 10, "share", "desc")
        assert [(r["uid"], r["share"]) for r in out["rows"]] == [("a", 9.0), ("b", 1.0)]

    async def test_share_without_spend_in_the_period(self):
        handler, eff, rng = setup({"shared": [shared_row("a", 1500, cli=10)]}, total_spend=0.0)
        out = await handler.shared_projects(rng, eff, "all", None, 0, 10, None, "desc")
        assert out["rows"][0]["share"] is None
        assert out["totals"]["share"] is None

    async def test_thresholds_scale_with_the_range(self):
        handler, eff, rng = setup({"people": [person_row("a", 900, exp=200)]}, period="last_7_days")
        out = await handler.people(rng, eff, "exp", None, 0, 10, None, "desc")
        assert out["display"]["experiments_highlight"] == pytest.approx(500 * rng.scale, abs=0.01)
        assert [r["uid"] for r in out["rows"]] == ["a"]  # $200 is over the 7-day share of $500

    async def test_candidate_days_scale_with_the_evidence_window(self):
        for period, expected in (("last_30_days", 5), ("last_7_days", 5), ("last_60_days", 10)):
            handler, eff, rng = setup({"people": []}, period=period)
            assert handler.display(eff, rng)["candidate_production_days"] == expected, period
        handler, eff, _ = setup({"people": []})
        rng90 = resolve_range(None, NOW - timedelta(days=90), NOW, 90, 30, now=NOW)
        assert handler.display(eff, rng90)["candidate_production_days"] == 15

    async def test_popup_of_an_unknown_entity(self):
        handler, eff, rng = setup({"pdetail:0": {}, **{f"pdetail:{i}": {} for i in range(16)}})
        with pytest.raises(h.EntityNotFoundError):
            await handler.person(rng, eff, "nobody")


class TestSettingsChecks:
    def test_auditor_with_overrides_is_forbidden(self):
        with patch.object(h, "component_settings", return_value={}):
            with pytest.raises(h.TuningForbiddenError):
                h.effective_settings(AUDITOR, encode_overrides({"minimumConfidence": 50}))
            # Without overrides auditors read the default analysis.
            assert h.effective_settings(AUDITOR, None).tier is None

    def test_invalid_overrides(self):
        with patch.object(h, "component_settings", return_value={}):
            with pytest.raises(SettingsError):
                h.effective_settings(ADMIN, encode_overrides({"minimumConfidence": 5}))

    def test_environment_overrides_for_one_deployment(self):
        """Local development through a tunnel: fewer parallel requests and a longer timeout, without the YAML."""
        cfg = SimpleNamespace(USAGE_ATTRIBUTION_ES_CONCURRENCY=2, USAGE_ATTRIBUTION_ANALYSIS_TIMEOUT_SECONDS=900)
        component = SimpleNamespace(settings=SimpleNamespace(model_dump=lambda: {"minimumConfidence": 70}))
        with (
            patch("codemie.configs.config.config", cfg),
            patch("codemie.configs.customer_config.CustomerConfig.resolve_component", return_value=component),
        ):
            raw = h.component_settings()
        assert raw == {"minimumConfidence": 70, "elasticsearchConcurrency": 2, "analysisTimeoutSeconds": 900}
        values = build_defaults(raw).values
        assert (values["elasticsearchConcurrency"], values["analysisTimeoutSeconds"]) == (2, 900)
        unset = SimpleNamespace(USAGE_ATTRIBUTION_ES_CONCURRENCY=None, USAGE_ATTRIBUTION_ANALYSIS_TIMEOUT_SECONDS=None)
        with (
            patch("codemie.configs.config.config", unset),
            patch("codemie.configs.customer_config.CustomerConfig.resolve_component", return_value=component),
        ):
            assert h.component_settings() == {"minimumConfidence": 70}

    def test_framework_regex_from_cli_analytics_rules(self):
        rules = {
            "rules": [{"regex": "^sdlc-factory:", "label": "A"}, {"regex": "^x\\d+", "label": "B"}, {"regex": "^bmad-"}]
        }
        with patch("codemie.configs.customer_config.CustomerConfig.get_feature_setting", return_value=rules):
            assert h.framework_regex() == "(sdlc-factory:|bmad-).*"
        with patch("codemie.configs.customer_config.CustomerConfig.get_feature_setting", return_value=None):
            assert h.framework_regex().startswith("(sdlc-factory:")


class TestWebWhoPaysInTotals:
    def test_the_funded_part_is_charged_back(self):
        start, end = datetime(2026, 9, 13, tzinfo=timezone.utc), datetime(2026, 10, 13, tzinfo=timezone.utc)
        s_ms, e_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        mid = int(datetime(2026, 9, 28, tzinfo=timezone.utc).timestamp() * 1000)
        fplan = h.funding.plan({"budgeted": {"uA": [[s_ms, e_ms]], "uB": [[mid, e_ms]]}}, start, end)
        aggs = {
            "fb": {"spend": {"value": 20}},
            "personal": {"spend": {"value": 30}},
            "shared": {"spend": {"value": 150}},
            "fp": {"p": {"buckets": [{"key": "budgeted", "u": {"buckets": [
                {"key": "uA", "spend": {"value": 60}}, {"key": "uB", "spend": {"value": 40}}]}}]}},
            "fg": {"g": {"buckets": {"g0": {"u": {"buckets": [{"key": "uB", "spend": {"value": 10}}]}}}}},
        }  # fmt: skip
        # uA all period (60), uB only for part of it (10 of 40); fallback records and the rest paid personally.
        assert h._web_who_pays(aggs, fplan) == {"charged": 70, "shared": 100, "personal": 30}

    def test_without_funding_nothing_is_charged_back(self):
        aggs = {"fb": {"spend": {"value": 0}}, "personal": {"spend": {"value": 5}}, "shared": {"spend": {"value": 9}}}
        fplan = {"projects": [], "full": {}, "groups": []}
        assert h._web_who_pays(aggs, fplan) == {"charged": 0, "shared": 9, "personal": 5}


@pytest.mark.asyncio
async def test_elasticsearch_errors_do_not_reach_the_client():
    """F-07: the repository puts the transport error (host, port, exception names) in the details; the API answers
    with a public text and the original goes to the server log."""
    handler, _eff, rng = setup({})
    handler.service.cache = MagicMock()
    handler.service.cache.get = AsyncMock(return_value=None)
    leak = "Elasticsearch service error: ConnectionError(Cannot connect to host localhost:9200 ssl:default)"
    repo = MagicMock()
    repo.execute_aggregation_query = AsyncMock(
        side_effect=ExtendedHTTPException(code=503, message="Analytics query failed", details=leak)
    )
    with (
        patch.object(h.app_db, "project_facts", AsyncMock(return_value={"shared": ["p"], "funding": {}})),
        patch("codemie.repository.metrics_elastic_repository.MetricsElasticRepository", return_value=repo),
        pytest.raises(ExtendedHTTPException) as raised,
    ):
        await handler.totals(rng)
    assert raised.value.code == 503
    assert "localhost" not in str(raised.value.details) and "ConnectionError" not in str(raised.value.details)


@pytest.mark.asyncio
async def test_totals_are_computed_and_cached():
    handler, eff, rng = setup({})
    handler.service.cache = MagicMock()
    handler.service.cache.get = AsyncMock(return_value=None)
    handler.service.cache.set_many = AsyncMock()
    totals_resp = {
        "aggregations": {
            "source": {
                "buckets": {
                    "cli": {"spend": {"value": 100}, "who_pays": {"buckets": {
                        "charged_back": {"spend": {"value": 30}}, "shared_no_budget": {"spend": {"value": 20}},
                        "personal": {"spend": {"value": 45}}, "no_customer": {"spend": {"value": 5}}}}},
                    "other_proxy": {"spend": {"value": 7}},
                    "web_assistant": {"spend": {"value": 40}},
                    "web_workflow": {"spend": {"value": 10}},
                    "web_datasource": {"spend": {"value": 0}},
                }
            }
        }
    }  # fmt: skip
    web_resp = {
        "aggregations": {
            "fb": {"spend": {"value": 0}},
            "personal": {"spend": {"value": 20}},
            "shared": {"spend": {"value": 10}},
        }
    }
    repo = MagicMock()
    repo.execute_aggregation_query = AsyncMock(side_effect=[totals_resp, web_resp])
    with (
        patch.object(h.app_db, "project_facts", AsyncMock(return_value={"shared": ["p"], "funding": {}})),
        patch("codemie.repository.metrics_elastic_repository.MetricsElasticRepository", return_value=repo),
    ):
        data = await handler.totals(rng)
    assert data["cli"]["total"] == 95  # billed: without the 5 that had no customer
    assert data["web"]["total"] == 50
    assert data["total"] == 145
    assert data["who_pays"]["web"] == {"charged": 0, "shared": 10, "personal": 40}  # 20 more records without a project
    assert data["charged_back"] == 30
    assert data["excluded"] == {"no_customer": 5, "other_proxy": 7}
    handler.service.cache.set_many.assert_awaited_once()


@pytest.mark.asyncio
async def test_totals_charge_back_the_funded_web_platform_spend():
    handler, _eff, rng = setup({})
    handler.service.cache = MagicMock()
    handler.service.cache.get = AsyncMock(return_value=None)
    handler.service.cache.set_many = AsyncMock()
    facts = {
        "shared": ["p"],
        "funding": {
            "assignments": [["p", "b1", "2026-01-01T00:00:00Z", None, True, None]],
            "allocations": [["p", "uA", "2026-01-01T00:00:00Z", None]],
            "stops": [],
        },
    }
    totals_resp = {"aggregations": {"source": {"buckets": {"cli": {"spend": {"value": 0}}}}}}
    web_resp = {
        "aggregations": {
            "fb": {"spend": {"value": 0}},
            "personal": {"spend": {"value": 0}},
            "shared": {"spend": {"value": 100}},
            "fp": {"p": {"buckets": [{"key": "p", "u": {"buckets": [
                {"key": "uA", "spend": {"value": 60}}, {"key": "uB", "spend": {"value": 40}}]}}]}},
        }
    }  # fmt: skip
    repo = MagicMock()
    repo.execute_aggregation_query = AsyncMock(side_effect=[totals_resp, web_resp])
    with (
        patch.object(h.app_db, "project_facts", AsyncMock(return_value=facts)),
        patch("codemie.repository.metrics_elastic_repository.MetricsElasticRepository", return_value=repo),
    ):
        data = await handler.totals(rng)
    web_body = repo.execute_aggregation_query.await_args_list[1].args[0]
    assert web_body["aggs"]["fp"]["filter"]["bool"]["filter"][1] == {"terms": {"attributes.project.keyword": ["p"]}}
    # uA has an allocation of the active budget for the whole range; uB has none and paid personally.
    assert data["who_pays"]["web"] == {"charged": 60, "shared": 40, "personal": 0}
    assert data["charged_back"] == 60
