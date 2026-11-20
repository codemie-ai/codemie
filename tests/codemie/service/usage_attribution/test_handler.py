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

import json
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.spend.coverage import TimeRange
from codemie.service.usage_attribution import handler as h
from codemie.service.usage_attribution.analysis import AnalysisHandle, resolve_range
from codemie.service.usage_attribution.constants import CATEGORIES, DEFAULT_FRAMEWORK_SKILL_REGEX
from codemie.service.usage_attribution.sections import shard_of
from codemie.service.usage_attribution.settings import SettingsError, build_defaults, build_effective, encode_overrides
from codemie.service.usage_attribution.spend_widgets import (
    CliUsageFigures,
    SpendFigures,
    SpendTable,
    WhoPaysFigures,
    WhoPaysSplit,
)
from codemie.service.usage_attribution.spend_widgets.base import CliCategorySpend, CliReasonSpend

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
    handler = h.UsageAttributionHandler(ADMIN, FakeService(sections))
    echo_ledger(sections)
    return handler, eff, rng


def ledger_groups(total=None, platform=None, cli=None, embedding=None):
    return SpendFigures(total=total, platform=platform, cli=cli, embedding=embedding)


def ledger_table(**rows):
    return SpendTable(rows)


class FakeLedger:
    """What the hourly ledger says: a test sets the async ``platform(start, end)``, ``people(start, end, uids)`` and
    ``projects(start, end, names)`` the spend widgets answer through."""

    platform = people = projects = who_pays = None


LEDGER = FakeLedger()


class FakeWhoPaysSpend:
    """The Who Pays widget: answers ``LEDGER.who_pays`` and keeps the classifier the handler gave it."""

    classify = None

    def __init__(self, context, *, classify):
        self.context = context
        FakeWhoPaysSpend.classify = classify

    async def read(self, *args):
        return await LEDGER.who_pays(*args)


def fake_widget(name):
    class Widget:
        def __init__(self, context):
            self.context = context

        async def read(self, *args):
            return await getattr(LEDGER, name)(*args)

    return Widget


@pytest.fixture(autouse=True)
def spend_widgets_say_what_the_test_says():
    with (
        patch.object(h.spend_widgets, "PlatformSpend", fake_widget("platform")),
        patch.object(h.spend_widgets, "PeopleSpend", fake_widget("people")),
        patch.object(h.spend_widgets, "ProjectSpend", fake_widget("projects")),
        patch.object(h.spend_widgets, "WhoPaysSpend", FakeWhoPaysSpend),
    ):
        yield


def echo_ledger(sections):
    """The hourly ledger answering what Elasticsearch answered, so a test about something else reads as before."""
    tiles = sections["summary"]["tiles"]

    async def platform(_start, _end):
        return ledger_groups(
            tiles.get("total"), tiles.get("web"), tiles.get("cli"), (tiles.get("web_types") or {}).get("datasource")
        )

    people = {r["uid"]: r for r in sections.get("people") or []}

    async def person_figures(_start, _end, uids):
        return SpendTable(
            {
                uid: ledger_groups(people[uid]["total"], people[uid]["platform"], people[uid]["cli"])
                if uid in people
                else ledger_groups()
                for uid in uids
            }
        )

    projects = {r["name"]: r for r in sections.get("shared") or []}

    async def project_figures(_start, _end, names):
        return SpendTable(
            {
                name: ledger_groups(None, projects[name]["platform"], projects[name]["cli"])
                if name in projects
                else ledger_groups()
                for name in names
            }
        )

    LEDGER.platform, LEDGER.people, LEDGER.projects = platform, person_figures, project_figures


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

    @pytest.mark.parametrize("name", ["stlc-factory:exploratory", "stlc-feature", "stlc-feature-x", "sdlc-factory:x"])
    def test_default_framework_regex_matches_stlc_and_sdlc(self, name):
        assert re.fullmatch(DEFAULT_FRAMEWORK_SKILL_REGEX, name)

    @pytest.mark.parametrize("name", ["stlc-factory:suite-health", "stlc-feature"])
    def test_framework_regex_from_real_config_matches_stlc(self, name):
        assert re.fullmatch(h.framework_regex(), name)

    def test_framework_regex_drops_rule_with_unbalanced_regex(self):
        rules = {"rules": [{"regex": "^sdlc-factory:"}, {"regex": "^(unterminated"}, {"regex": "^bmad-"}]}
        with patch("codemie.configs.customer_config.CustomerConfig.get_feature_setting", return_value=rules):
            regex = h.framework_regex()
        assert regex == "(sdlc-factory:|bmad-).*"
        assert re.fullmatch(regex, "sdlc-factory:x")

    def test_framework_regex_falls_back_when_every_rule_is_malformed(self):
        rules = {"rules": [{"regex": "^(unterminated"}, {"regex": "^another[unterminated"}]}
        with patch("codemie.configs.customer_config.CustomerConfig.get_feature_setting", return_value=rules):
            assert h.framework_regex() == DEFAULT_FRAMEWORK_SKILL_REGEX


class TestWebWhoPaysClassification:
    """The Web Platform's per-request classification: fallback, funded, shared project, personal project."""

    def fplan(self):
        start, end = datetime(2026, 9, 13, tzinfo=timezone.utc), datetime(2026, 10, 13, tzinfo=timezone.utc)
        s_ms, e_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        mid = int(datetime(2026, 9, 28, tzinfo=timezone.utc).timestamp() * 1000)
        return start, end, h.funding.plan({"budgeted": {"uA": [[s_ms, e_ms]], "uB": [[mid, e_ms]]}}, start, end)

    def test_the_funded_members_are_charged_back_inside_their_intervals(self):
        start, end, fplan = self.fplan()
        body = h.queries.request_classification_body(TimeRange(start, end), ["budgeted"], "web", ["r"], fplan)
        sub = body["aggs"]["requests"]["aggs"]
        assert list(sub) == ["fb", "fd", "sh", "pr"]
        funded = json.dumps(sub["fd"])
        assert '"uA"' in funded and '"uB"' in funded and "epoch_millis" in funded

    def test_without_funding_nothing_is_charged_back(self):
        start, end, _fplan = self.fplan()
        body = h.queries.request_classification_body(
            TimeRange(start, end), [], "web", ["r"], h.funding.plan({}, start, end)
        )
        assert {"match_none": {}} in body["aggs"]["requests"]["aggs"]["fd"]["filter"]["bool"]["filter"]

    def test_a_fallback_record_is_shared_before_it_is_charged_back(self):
        buckets = [
            {"key": "r", "fb": {"doc_count": 1}, "fd": {"doc_count": 1}, "sh": {"doc_count": 0}, "pr": {"doc_count": 0}}
        ]
        assert h.parsing.parse_request_classification(buckets, {}, "web") == {"r": ("web", "shared_no_budget")}


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


TOTALS_RESP = {
    "aggregations": {
        "source": {
            "buckets": {
                "cli": {"spend": {"value": 100}},
                "other_proxy": {"spend": {"value": 7}},
                "web_assistant": {"spend": {"value": 40}},
                "web_workflow": {"spend": {"value": 10}},
                "web_datasource": {"spend": {"value": 3}},
            }
        }
    }
}  # fmt: skip

WHO_PAYS = WhoPaysFigures(
    web=WhoPaysSplit(charged=1.0, shared=2.0, personal=0.5, unclassified=46.5, no_customer=0.0),
    cli=WhoPaysSplit(charged=30.0, shared=20.0, personal=45.0, unclassified=5.0, no_customer=5.0),
)


async def totals_with(handler, rng, ledger, who_pays=WHO_PAYS, facts=None):
    """``totals()`` with the hourly ledger's cards answering ``ledger`` and the Who Pays widget ``who_pays``."""
    handler.service.cache = MagicMock()
    handler.service.cache.get = AsyncMock(return_value=None)
    handler.service.cache.set_many = AsyncMock()
    LEDGER.platform = AsyncMock(return_value=ledger)
    LEDGER.who_pays = AsyncMock(return_value=who_pays)
    repo = MagicMock()
    repo.execute_aggregation_query = AsyncMock(return_value=TOTALS_RESP)
    with (
        patch.object(h.app_db, "project_facts", AsyncMock(return_value=facts or {"shared": ["p"], "funding": {}})),
        patch("codemie.repository.metrics_elastic_repository.MetricsElasticRepository", return_value=repo),
    ):
        return await handler.totals(rng)


@pytest.mark.asyncio
class TestTotals:
    async def test_who_pays_is_the_widgets_and_foots_on_the_cards(self):
        handler, _eff, rng = setup({})
        data = await totals_with(handler, rng, ledger_groups(150.0, 50.0, 100.0, 1.0))
        assert data["who_pays"] == {
            "web": {"charged": 1.0, "shared": 2.0, "personal": 0.5, "unclassified": 46.5},
            "cli": {"charged": 30.0, "shared": 20.0, "personal": 45.0, "unclassified": 5.0},
        }
        assert data["charged_back"] == 31.0
        assert data["excluded"] == {"other_proxy": 7, "no_customer": 5.0}

    async def test_the_platform_figures_come_from_the_ledger(self):
        handler, _eff, rng = setup({})
        data = await totals_with(handler, rng, ledger_groups(150.0, 50.0, 100.0, 1.0))
        assert (data["total"], data["cli"], data["web"]["total"]) == (150.0, {"total": 100.0}, 50.0)
        assert (data["web"]["assistant"], data["web"]["workflow"], data["web"]["datasource"]) == (40, 10, 1.0)

    async def test_without_ledger_figures_charged_back_has_no_value(self):
        handler, _eff, rng = setup({})
        data = await totals_with(handler, rng, ledger_groups(), who_pays=WhoPaysFigures())
        assert data["charged_back"] is None and data["excluded"]["no_customer"] is None
        assert data["who_pays"]["cli"] == {"charged": None, "shared": None, "personal": None, "unclassified": None}

    async def test_the_widget_is_keyed_on_the_shared_projects_and_funding(self):
        handler, _eff, rng = setup({})
        await totals_with(handler, rng, ledger_groups(150.0, 50.0, 100.0, 1.0))
        start, end, key = LEDGER.who_pays.await_args.args
        assert (start, end) == (rng.start, rng.end)
        assert key == h.stable_hash([["p"], h.funding.plan({}, rng.start, rng.end)])

    async def test_the_cached_block_holds_only_what_elasticsearch_has(self):
        handler, _eff, rng = setup({})
        await totals_with(handler, rng, ledger_groups(150.0, 50.0, 100.0, 1.0))
        key, cached = next(iter(handler.service.cache.set_many.await_args.args[0].items()))
        assert key.startswith("codemie:ua:v7:totals:")
        assert set(json.loads(cached)) == {"web", "excluded", "duration_ms", "cache"}

    async def test_a_cache_hit_gets_the_ledger_figures_too(self):
        handler, _eff, rng = setup({})
        cached = {"web": {"assistant": 40, "workflow": 10, "datasource": 3}, "excluded": {"other_proxy": 7},
                  "duration_ms": 1.0, "cache": "miss"}  # fmt: skip
        handler.service.cache = MagicMock()
        handler.service.cache.get = AsyncMock(return_value=json.dumps(cached).encode("utf-8"))
        LEDGER.platform = AsyncMock(return_value=ledger_groups(10.0, 4.0, 6.0, 1.0))
        LEDGER.who_pays = AsyncMock(return_value=WHO_PAYS)
        with patch.object(h.app_db, "project_facts", AsyncMock(return_value={"shared": [], "funding": {}})):
            data = await handler.totals(rng)
        assert (data["total"], data["cli"]["total"], data["web"]["total"], data["web"]["assistant"]) == (10, 6, 4, 40)
        assert data["who_pays"]["cli"]["charged"] == 30.0 and data["cache"] == "hit"


def classified(channel, **ids):
    """A classification response: request id -> its alias (cb/sh/pr/nc for CLI, fb/fd/sh/pr for web)."""
    aliases = [alias for alias, _ in h.queries.CLASSIFICATION_ALIASES[channel]]
    return {"aggregations": {"requests": {"buckets": [
        {"key": rid, **{a: {"doc_count": int(a == alias)} for a in aliases}} for rid, alias in ids.items()]}}}  # fmt: skip


@pytest.mark.asyncio
class TestClassifyRequests:
    """The classifier the handler gives the Who Pays widget: the ledger request IDs for a channel."""

    WINDOW = SimpleNamespace(start=NOW - timedelta(days=30), end=NOW)

    async def classify(self, channel, ids, fplan=None):
        async def answer(body):
            batch = body["aggs"]["requests"]["terms"]["include"]
            return classified(channel, **{rid: ids[rid] for rid in batch})

        repo = MagicMock()
        repo.execute_aggregation_query = AsyncMock(side_effect=answer)
        fplan = fplan or h.funding.plan({}, self.WINDOW.start, self.WINDOW.end)
        with patch("codemie.repository.metrics_elastic_repository.MetricsElasticRepository", return_value=repo):
            out = await h._classify_requests(self.WINDOW, ["p"], channel, fplan, list(ids))
        return out, [c.args[0] for c in repo.execute_aggregation_query.await_args_list]

    async def test_every_request_id_gets_its_category(self):
        out, _bodies = await self.classify("cli", {"a": "cb", "b": "sh", "c": "pr", "d": "nc"})
        assert out == {"a": ("cli", "charged_back"), "b": ("cli", "shared_no_budget"), "c": ("cli", "personal"),
                       "d": ("cli", "no_customer")}  # fmt: skip

    async def test_classification_is_scoped_to_the_ledger_batch(self):
        _, bodies = await self.classify("cli", {"a": "pr", "b": "cb"})
        assert len(bodies) == 1
        assert bodies[0]["aggs"]["requests"]["terms"]["include"] == ["a", "b"]

    async def test_the_web_funding_reaches_the_classification(self):
        start, end = self.WINDOW.start, self.WINDOW.end
        whole = [[int(start.timestamp() * 1000), int(end.timestamp() * 1000)]]
        _out, bodies = await self.classify("web", {"w": "fd"}, h.funding.plan({"p": {"uA": whole}}, start, end))
        assert '"uA"' in json.dumps(bodies[0]["aggs"]["requests"]["aggs"]["fd"])

    async def test_the_handler_gives_the_widget_this_classifier(self):
        handler, _eff, rng = setup({})
        await totals_with(handler, rng, ledger_groups(150.0, 50.0, 100.0, 1.0))
        with patch.object(h, "_classify_requests", AsyncMock(return_value={"r": ("cli", "personal")})) as real:
            assert await FakeWhoPaysSpend.classify(self.WINDOW, "cli", ["r"]) == {"r": ("cli", "personal")}
        real.assert_awaited_once_with(self.WINDOW, ["p"], "cli", h.funding.plan({}, rng.start, rng.end), ["r"])

    async def classify_with_failures(self, channel, ids, fail_codes):
        calls = {"n": -1}

        async def answer(body):
            calls["n"] += 1
            if calls["n"] < len(fail_codes):
                raise ExtendedHTTPException(code=fail_codes[calls["n"]], message="x", details="y")
            batch = body["aggs"]["requests"]["terms"]["include"]
            return classified(channel, **{rid: ids[rid] for rid in batch})

        repo = MagicMock()
        repo.execute_aggregation_query = AsyncMock(side_effect=answer)
        fplan = h.funding.plan({}, self.WINDOW.start, self.WINDOW.end)
        with (
            patch("codemie.repository.metrics_elastic_repository.MetricsElasticRepository", return_value=repo),
            patch.object(h.asyncio, "sleep", AsyncMock()) as sleep,
        ):
            out = await h._classify_requests(self.WINDOW, ["p"], channel, fplan, list(ids))
        return out, sleep

    async def test_an_overloaded_partition_is_retried_with_more_partitions_and_succeeds(self):
        ids = {"a": "cb", "b": "sh", "c": "pr", "d": "nc"}
        out, sleep = await self.classify_with_failures("cli", ids, fail_codes=[400])
        assert out == {"a": ("cli", "charged_back"), "b": ("cli", "shared_no_budget"), "c": ("cli", "personal"),
                        "d": ("cli", "no_customer")}  # fmt: skip
        sleep.assert_awaited_once()

    async def test_a_different_error_code_is_not_retried_and_propagates(self):
        ids = {"a": "cb"}
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await self.classify_with_failures("cli", ids, fail_codes=[503, 503, 503, 503])
        assert exc_info.value.code == 503

    async def test_a_connection_error_is_not_retried_and_propagates(self):
        repo = MagicMock()
        repo.execute_aggregation_query = AsyncMock(side_effect=ConnectionError("boom"))
        fplan = h.funding.plan({}, self.WINDOW.start, self.WINDOW.end)
        with patch("codemie.repository.metrics_elastic_repository.MetricsElasticRepository", return_value=repo):
            with pytest.raises(ConnectionError):
                await h._classify_requests(self.WINDOW, ["p"], "cli", fplan, ["r"])

    async def test_retries_are_bounded_and_eventually_raise(self):
        ids = {str(i): "cb" for i in range(16)}
        with pytest.raises(ExtendedHTTPException) as exc_info:
            await self.classify_with_failures("cli", ids, fail_codes=[400, 400, 400, 400, 400, 400])
        assert exc_info.value.code == 400


SUMMARY = {
    "tiles": {"charged_back": 99.0, "total": 148.0, "web": 53.0, "cli": 95.0, "prod": 20.0, "uncl": 1.0, "uncl_parts": {},
              "cli_people": 1, "prod_cb": 5.0, "prod_alloc": 0.0, "exp": 5.0, "exp_alloc": 0.0,
              "exp_parts": {}, "likely_prod": 0.0, "allocated": 0.0, "excluded": {"no_customer": 5}, "web_types": {"assistant": 40.0, "workflow": 10.0,
              "datasource": 3.0}},
    "pie": [{"id": "platform", "label": "Web Platform", "value": 53.0}, {"id": "production", "value": 20.0}],
    "who_pays": {}, "categories": [], "reasons": [], "coverage": {}, "signals": [], "identity": {},
    "folder_like": [], "confidence": {}, "db": {}, "counts": {},
}  # fmt: skip


@pytest.mark.asyncio
class TestSummaryFromTheLedger:
    async def test_cli_summary_tiles_and_pie(self):
        summary = json.loads(json.dumps(SUMMARY))
        evidence = {"persons": {}}
        handler, eff, rng = setup({"summary": summary, "cli_evidence": evidence})
        totals = await totals_with(
            handler,
            rng,
            ledger_groups(10.0, 4.0, 6.0, 1.0),
            who_pays=WhoPaysFigures(
                web=WhoPaysSplit(charged=1, shared=1, personal=1, unclassified=1),
                cli=WhoPaysSplit(charged=2, shared=1, personal=1, unclassified=2),
            ),
        )
        amounts = {"production": 2.0, "learning": 1.0, "unclassified": 3.0}
        cli = CliUsageFigures(
            total=6.0,
            people=2,
            production=2.0,
            production_charged=0.5,
            production_allocated=0.25,
            experiments=1.0,
            experiments_allocated=0.5,
            experiments_parts={"learning": 1.0},
            allocated=0.75,
            unclassified=3.0,
            likely_production=1.0,
            unclassified_other=2.0,
            categories=tuple(CliCategorySpend(category=c, spend=amounts.get(c, 0.0)) for c in CATEGORIES),
            reasons=(CliReasonSpend(reason="no_repository", spend=3.0, remaining=3.0),),
            coverage_share=100.0,
            analysed=6.0,
            outside_limits=0.0,
        )
        with (
            patch.object(h.spend_widgets.CliUsageSpend, "read", AsyncMock(return_value=cli)) as read,
            patch.object(h.app_db, "project_facts", AsyncMock(return_value={"shared": ["p"], "funding": {}})),
        ):
            out = await handler.cli_summary(rng, eff)
        read.assert_awaited_once_with(rng.start, rng.end, "prefix:2026-10-13T15:00:00Z")
        tiles = out["tiles"]
        assert (tiles["total"], tiles["cli"], tiles["web"], tiles["web_types"]["datasource"]) == (10, 6, 4, 1)
        assert (tiles["prod"], tiles["web_types"]["assistant"], tiles["web_types"]["workflow"]) == (2, 40, 10)
        assert out["pie"] == [
            {"id": "platform", "label": "Web Platform", "value": 4.0},
            {"id": "production", "value": 2.0},
        ]
        assert sum(row["spend"] for row in out["categories"]) == 6.0
        assert out["reasons"][0]["spend"] == 3.0
        assert out["coverage"]["analysed"] == 6.0
        assert out["who_pays"] == totals["who_pays"]
        assert tiles["charged_back"] == totals["charged_back"] == 3
        for channel in ("web", "cli"):
            assert set(out["who_pays"][channel]) == {"charged", "shared", "personal", "unclassified"}
            assert sum(out["who_pays"][channel].values()) == tiles[channel]
        assert LEDGER.who_pays.await_args_list[0] == LEDGER.who_pays.await_args_list[1]
        assert summary == SUMMARY  # the cached section is not changed

    async def test_cli_summary_below_the_floor(self):
        handler, eff, rng = setup({"summary": json.loads(json.dumps(SUMMARY)), "cli_evidence": {"persons": {}}})
        LEDGER.platform = AsyncMock(return_value=ledger_groups())
        LEDGER.who_pays = AsyncMock(return_value=WhoPaysFigures())
        with (
            patch.object(h.spend_widgets.CliUsageSpend, "read", AsyncMock(return_value=CliUsageFigures())),
            patch.object(h.app_db, "project_facts", AsyncMock(return_value={"shared": [], "funding": {}})),
        ):
            out = await handler.cli_summary(rng, eff)
        assert (out["tiles"]["total"], out["tiles"]["cli"], out["tiles"]["web"]) == (None, None, None)
        assert out["tiles"]["web_types"]["datasource"] is None and out["pie"][0]["value"] is None
        assert set(out["tiles"]) == set(SUMMARY["tiles"])
        assert out["tiles"]["charged_back"] is None
        assert out["tiles"]["prod"] is None and out["tiles"]["exp"] is None and out["tiles"]["uncl"] is None
        assert all(row["spend"] is None for row in out["categories"])

    async def test_status_cli_tile(self):
        summary = json.loads(json.dumps(SUMMARY))
        handler, eff, rng = setup({"summary": summary})
        LEDGER.platform = AsyncMock(return_value=ledger_groups(10.0, 4.0, 6.0, 1.0))
        with patch.object(h, "schema_payload", return_value={"settings": []}):
            out = await handler.status(rng, eff)
        assert out["tiles"]["cli"] == 6.0 and out["tiles"]["uncl"] == 1.0
        assert summary == SUMMARY


class TestRowFigureLogic:
    def test_a_threshold_is_never_met_by_no_figure_or_zero(self):
        assert h._at_least(5.0, 5.0)
        assert not h._at_least(None, 0.0)
        assert not h._at_least(0.0, 0.0)

    def test_a_project_without_a_part_has_no_spend(self):
        assert h._project_spend({"platform": 1.0, "cli": 2.0}) == 3.0
        assert h._project_spend({"platform": None, "cli": 2.0}) is None

    def test_a_sum_adds_the_values_that_have_a_figure_in_cents(self):
        assert h._sum_or_none([1.1, None, 2.2]) == 3.3

    def test_a_sum_of_no_figure_at_all_is_none_not_zero(self):
        assert h._sum_or_none([None, None]) is None
        assert h._sum_or_none([]) is None


@pytest.mark.asyncio
class TestTheSpendWidgetsShareOneContext:
    """The cache layer itself is tested in spend_widgets/; here, that the handler builds each widget over the one
    context of its caller and the shared cache, and reads the range."""

    async def test_every_widget_gets_the_one_context_of_the_caller_and_the_shared_cache(self):
        handler, eff, rng = setup({"summary": json.loads(json.dumps(SUMMARY))})
        seen, reads = [], []

        class Recording:
            def __init__(self, context):
                seen.append(context)

            async def read(self, *args):
                reads.append(args)
                return ledger_groups(10.0, 4.0, 6.0, 1.0)

        with (
            patch.object(h.spend_widgets, "PlatformSpend", Recording),
            patch.object(h, "schema_payload", return_value={"settings": []}),
        ):
            await handler.status(rng, eff)
            await handler.status(rng, eff)
        assert len(seen) == 2 and seen[0] is seen[1]
        assert seen[0].user is ADMIN and seen[0].cache is handler.service.cache
        assert reads == [(rng.start, rng.end)] * 2


@pytest.mark.asyncio
class TestPeopleFromTheLedger:
    async def test_a_row_shows_the_ledger_figures_and_keeps_the_elasticsearch_ones(self):
        es = person_row("a", 5.0, platform=2.0, prod_ncb=1.0, charged_back=0.5)
        handler, eff, rng = setup({"people": [es]})
        LEDGER.platform = AsyncMock(return_value=ledger_groups(total=70.0))
        LEDGER.people = AsyncMock(return_value=SpendTable({"a": ledger_groups(7.0, 3.0, 4.0)}))
        (row,) = (await handler.people(rng, eff, "all", None, 0, 10, None, "desc"))["rows"]
        assert (row["total"], row["platform"], row["cli"]) == (7.0, 3.0, 4.0)
        assert (row["ncb"], row["prod"], row["prod_ncb"], row["charged_back"]) == (0.0, 0.0, 1.0, 0.5)
        assert row["share"] == 10.0  # 7 of the ledger's 70
        assert set(row) == {*es, "share", "exp_highlight", "candidate"}
        assert es["total"] == 5.0  # the cached section row is not changed

    async def test_a_person_without_a_ledger_figure_sorts_last(self):
        rows = [person_row("a", 900, platform=400), person_row("b", 100, platform=50), person_row("c", 500)]
        handler, eff, rng = setup({"people": rows})
        LEDGER.people = AsyncMock(
            return_value=SpendTable(
                {"a": ledger_groups(), "b": ledger_groups(1.0, 0.5, 0.5), "c": ledger_groups(2.0, 0.0, 2.0)}
            )
        )
        for order in ("desc", "asc"):
            out = await handler.people(rng, eff, "all", None, 0, 10, "total", order)
            assert [r["uid"] for r in out["rows"]][-1] == "a", order
        out = await handler.people(rng, eff, "all", None, 0, 10, None, "desc")
        assert out["rows"][-1]["share"] is None and out["rows"][-1]["total"] is None

    async def test_a_person_without_a_ledger_figure_fails_the_platform_filter(self):
        rows = [person_row("a", 900, platform=800), person_row("b", 900, platform=800)]
        handler, eff, rng = setup({"people": rows})
        LEDGER.people = AsyncMock(
            return_value=SpendTable({"a": ledger_groups(), "b": ledger_groups(900.0, 800.0, 100.0)})
        )
        out = await handler.people(rng, eff, "platform", None, 0, 10, None, "desc")
        assert [r["uid"] for r in out["rows"]] == ["b"] and out["counts"]["platform"] == 1

    async def test_below_the_floor_there_is_no_share(self):
        handler, eff, rng = setup({"people": [person_row("a", 900)]})
        LEDGER.platform = AsyncMock(return_value=ledger_groups())
        LEDGER.people = AsyncMock(return_value=SpendTable({"a": ledger_groups()}))
        (row,) = (await handler.people(rng, eff, "all", None, 0, 10, None, "desc"))["rows"]
        assert (row["total"], row["platform"], row["cli"], row["share"]) == (None, None, None, None)

    async def test_the_person_popup_row_matches_the_table_row(self):
        es = person_row("a", 5.0, platform=2.0)
        handler, eff, rng = setup({"people": [es], f"pdetail:{shard_of('a')}": {"a": {"row": es, "detail": {}}},
                                   "pwork": []})  # fmt: skip
        LEDGER.people = AsyncMock(return_value=SpendTable({"a": ledger_groups(7.0, 3.0, 4.0)}))
        handler._person_live = AsyncMock(return_value={})
        popup = (await handler.person(rng, eff, "a"))["row"]
        table = (await handler.people(rng, eff, "all", None, 0, 10, None, "desc"))["rows"][0]
        assert (popup["total"], popup["platform"], popup["cli"]) == (table["total"], table["platform"], table["cli"])
        assert popup["total"] == 7.0 and es["total"] == 5.0


@pytest.mark.asyncio
class TestSharedProjectsFromTheLedger:
    async def test_a_row_shows_the_ledger_figures_and_keeps_the_elasticsearch_ones(self):
        es = shared_row("a", 1500, platform=3.0, cli=2.0)
        handler, eff, rng = setup({"shared": [es]})
        LEDGER.platform = AsyncMock(return_value=ledger_groups(total=50.0))
        LEDGER.projects = AsyncMock(return_value=SpendTable({"a": ledger_groups(5.0, 4.0, 1.0)}))
        out = await handler.shared_projects(rng, eff, "all", None, 0, 10, None, "desc")
        (row,) = out["rows"]
        assert (row["platform"], row["cli"]) == (4.0, 1.0)
        assert (row["ncb"], row["cli_sh"], row["web_sh"]) == (1500, 2.0, 3.0)
        assert row["share"] == 10.0  # (4 + 1) of the ledger's 50
        assert set(row) == {*es, "share", "candidate"}
        assert es["platform"] == 3.0  # the cached section row is not changed

    async def test_the_footer_adds_the_ledger_figures(self):
        rows = [shared_row("a", 1500, platform=3.0, cli=2.0), shared_row("b", 1200, platform=1.0, cli=1.0)]
        handler, eff, rng = setup({"shared": rows})
        LEDGER.platform = AsyncMock(return_value=ledger_groups(total=100.0))
        LEDGER.projects = AsyncMock(
            return_value=SpendTable({"a": ledger_groups(5.0, 4.0, 1.0), "b": ledger_groups(None, None, 6.0)})
        )
        out = await handler.shared_projects(rng, eff, "all", None, 0, 10, None, "desc")
        totals = out["totals"]
        assert (totals["platform"], totals["cli"]) == (4.0, 7.0)
        assert (totals["ncb"], totals["cli_sh"], totals["web_sh"]) == (2700, 3.0, 4.0)
        assert {r["name"]: r["share"] for r in out["rows"]} == {"a": 5.0, "b": None}
        assert totals["share"] == 5.0
        assert set(totals) == {"ncb", "platform", "cli", "cli_sh", "web_sh", "share"}

    async def test_below_the_floor_a_project_has_no_figures(self):
        handler, eff, rng = setup({"shared": [shared_row("a", 1500, platform=3.0, cli=2.0)]})
        LEDGER.platform = AsyncMock(return_value=ledger_groups())
        LEDGER.projects = AsyncMock(return_value=SpendTable({"a": ledger_groups()}))
        out = await handler.shared_projects(rng, eff, "all", None, 0, 10, "platform", "desc")
        (row,) = out["rows"]
        assert (row["platform"], row["cli"], row["share"]) == (None, None, None)
        assert (out["totals"]["platform"], out["totals"]["cli"], out["totals"]["share"]) == (None, None, None)

    async def test_repository_group_shares_keep_the_elasticsearch_total(self):
        handler, eff, rng = setup({"groups": [group_row("g-1", 3000)]})  # spend 3010 of the ES 10,000
        LEDGER.platform = AsyncMock(return_value=ledger_groups(total=50.0))
        out = await handler.repository_groups(rng, eff, "all", None, 0, 10, None, "desc")
        assert out["rows"][0]["share"] == 30.1 and out["totals"]["share"] == 30.1

    async def test_the_project_popup_row(self):
        es = shared_row("a", 1500, platform=3.0, cli=2.0)
        handler, eff, rng = setup({f"sdetail:{shard_of('a')}": {"a": {"row": es, "detail": {"models": []}}}})
        LEDGER.projects = AsyncMock(return_value=SpendTable({"a": ledger_groups(5.0, 4.0, 1.0)}))
        handler._project_web = AsyncMock(return_value={})
        out = await handler.shared_project(rng, eff, "a")
        assert (out["row"]["platform"], out["row"]["cli"], out["row"]["ncb"]) == (4.0, 1.0, 1500)
        assert out["detail"]["models"] == [] and es["platform"] == 3.0
        LEDGER.projects.assert_awaited_once_with(rng.start, rng.end, ["a"])
