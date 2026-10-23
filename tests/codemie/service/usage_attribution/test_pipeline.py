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

"""The analysis on synthetic data: classification (classification-model.md §5), allocation, Not Charged Back,
Web Platform who pays, grouping, related projects and windows."""

from __future__ import annotations

import pytest

from codemie.service.usage_attribution.classifier import decide
from codemie.service.usage_attribution.grouping import related_project
from codemie.service.usage_attribution.app_db import datasource_rows
from codemie.service.usage_attribution.sections import build_sections
from tests.codemie.service.usage_attribution.builders import (
    app_db,
    inputs,
    person,
    run,
    settings_with,
    tool,
    value,
)

TEAM = ["u1", "u2", "u3"]


def team_repo(name: str, uids: list[str] = TEAM, spend: float = 100.0, cb: float = 0.0, sh: float = 0.0) -> tuple:
    """A repository three people work on with code changes on six days: Production (who + how)."""
    pairs = {uid: {name: (spend, cb, sh, 0)} for uid in uids}
    return pairs, [tool(uids[0], name, sessions=6, edit=5, days=6)]


def merge_people(*pair_maps: dict, names: dict[str, str] | None = None, extra: dict | None = None) -> dict:
    out: dict[str, dict] = {}
    for pm in pair_maps:
        for uid, pairs in pm.items():
            out.setdefault(uid, {}).update(pairs)
    return {
        uid: person((names or {}).get(uid, f"{uid}@x.com"), pairs, extra=(extra or {}).get(uid, ()))
        for uid, pairs in out.items()
    }


# ---------------------------------------------------------------------------------------------------------------------
# Classification: the worked examples of classification-model.md §5.2
# ---------------------------------------------------------------------------------------------------------------------
def sig(sid: str, cat: str, pts: float) -> dict:
    return {"id": sid, "family": sid.split(".")[0], "cat": cat, "pts": float(pts), "args": {}}


class TestClassification:
    def test_production_against_the_name_needs_the_stricter_confidence(self):
        signals = [sig("how.code_changes", "production", 8), sig("where.shared_project", "production", 6)]
        alone = decide(settings_with(), signals, how_available=True, learning=False)
        assert alone["cat"] == "production"  # 14 / (14 + 6) = 70
        named = decide(settings_with(), [*signals, sig("names.experimental", "experimental", 8)], True, False)
        # 14 / (14 + 8 + 6) = 50: enough by the minimum, not against a name that says experiment (60).
        assert round(named["conf"]) == 50
        assert (named["cat"], named["reason"]) == ("unclassified", "low_confidence")  # never "likely production"
        relaxed = decide(
            settings_with(contradictedProductionMinConfidence=50),
            [*signals, sig("names.experimental", "experimental", 8)],
            True,
            False,
        )
        assert relaxed["cat"] == "production"

    def test_likely_production_is_reported_from_its_confidence(self):
        signals = [sig("how.code_changes", "production", 8), sig("how.active_days", "production", 8)]
        d = decide(settings_with(), signals, how_available=True, learning=False)
        assert (d["cat"], d["reason"]) == ("unclassified", "likely_production")  # one family: How
        off = decide(settings_with(likelyProductionMinConfidence=None), signals, True, False)
        assert off["reason"] == "production_one_family"

    def test_person_history_is_a_second_family(self):
        pairs, tools = team_repo("acme/core")
        pairs["u1"]["solo/tool"] = (30, 0, 0, 0)
        data = inputs(merge_people(pairs), tools=[*tools, tool("u1", "solo/tool", sessions=5, edit=4, days=6)])
        k = run(data).K["solo/tool"]
        assert k["cat"] == "production"
        assert "who.person_history" in {x["id"] for x in k["signals"]}
        off = run(data, settings_with(personHistory__enabled=False)).K["solo/tool"]
        assert (off["cat"], off["reason"]) == ("unclassified", "likely_production")
        # A person whose other work is not mostly production gets no such signal.
        mixed = run(data, settings_with(personHistory__minProductionShare=1.0, personHistory__minClassifiedSpend=1000))
        assert mixed.K["solo/tool"]["cat"] == "unclassified"

    def test_team_repository_with_code_changes_active_days_and_tickets_is_production(self):
        pairs = {uid: {"acme/core": (50, 0, 0, 0)} for uid in ["u1", "u2", "u3", "u4"]}
        data = inputs(
            merge_people(pairs),
            values={"acme/core": value(200, people=4, branches={"feature/PAY-431-refunds": 200})},
            tools=[tool("u1", "acme/core", sessions=6, edit=4, days=6)],
        )
        k = run(data).K["acme/core"]
        assert k["cat"] == "production"
        assert k["top"] == 30  # team 10 + code changes 8 + active days 8 + ticket keys 4
        assert round(k["conf"]) == 83
        assert {x["id"] for x in k["signals"]} == {
            "who.team",
            "how.code_changes",
            "how.active_days",
            "names.ticket_keys",
        }

    def test_local_folder_used_once_is_experimental(self):
        data = inputs(
            {"u5": person("u5@x.com", {"projects/billing-ui": (5,)})},
            tools=[tool("u5", "projects/billing-ui", 1, days=1)],
        )
        k = run(data).K["folder:u5:projects/billing-ui"]
        assert k["cat"] == "experimental"
        assert round(k["conf"]) == 73  # local folder 4 + one-off 12

    def test_own_name_dotfiles_is_a_pet_project(self):
        data = inputs(
            {"u6": person("john_smith@x.com", {"john-smith/dotfiles": (10,)})},
            tools=[tool("u6", "john-smith/dotfiles", sessions=2, days=2)],
        )
        k = run(data).K["personal:u6:john-smith/dotfiles"]
        assert k["cat"] == "pet_project"
        assert round(k["conf"]) == 75  # own name 10 + personal word 8

    def test_own_name_with_code_changes_on_many_days_is_unclassified(self):
        data = inputs(
            {"u7": person("jane_brown@x.com", {"jane-brown/budget-tool": (20,)})},
            tools=[tool("u7", "jane-brown/budget-tool", sessions=8, edit=6, days=10)],
        )
        k = run(data).K["personal:u7:jane-brown/budget-tool"]
        # Production leads (16 vs pet 10, confidence 50) on one family only: likely production, still Unclassified.
        assert (k["cat"], k["reason"]) == ("unclassified", "likely_production")
        assert round(k["conf"]) == 50
        strict = run(data, settings_with(likelyProductionMinConfidence=60)).K["personal:u7:jane-brown/budget-tool"]
        assert strict["reason"] == "production_one_family"

    def test_production_from_one_family_is_unclassified(self):
        data = inputs(
            {"u8": person("u8@x.com", {"solo1/service": (30,)})},
            tools=[tool("u8", "solo1/service", sessions=5, edit=4, days=6)],
        )
        k = run(data).K["solo1/service"]
        assert (k["cat"], k["reason"]) == ("unclassified", "likely_production")
        assert k["prod_families"] == ["how"]

    def test_sustained_work_counts_as_two_families(self):
        data = inputs(
            {"u9": person("u9@x.com", {"solo2/api": (30,)})},
            values={"solo2/api": value(30, branches={"PAY-12-fix": 30})},
            tools=[tool("u9", "solo2/api", sessions=5, edit=4, days=6)],
        )
        k = run(data).K["solo2/api"]
        assert k["cat"] == "production"
        # Switched off, the same evidence is one family only.
        off = run(data, settings_with(sustainedWork__enabled=False)).K["solo2/api"]
        assert (off["cat"], off["reason"]) == ("unclassified", "likely_production")

    def test_names_alone_never_make_production(self):
        data = inputs(
            {"u1": person("u1@x.com", {"acme/x": (10,)})}, values={"acme/x": value(10, branches={"PAY-1": 10})}
        )
        k = run(data).K["acme/x"]
        assert k["cat"] == "unclassified"

    def test_names_cap(self):
        data = inputs(
            {"u1": person("u1@x.com", {"acme/test-demo-sandbox": (10,)})},
            tools=[tool("u1", "acme/test-demo-sandbox", sessions=2, days=2)],
        )
        k = run(data, settings_with(namesCap=0)).K["acme/test-demo-sandbox"]
        assert all(x["eff"] == 0 for x in k["signals"] if x["family"] == "names")
        assert (k["cat"], k["reason"]) == ("unclassified", "no_evidence")

    def test_learning_source_is_decisive(self):
        data = inputs({"u1": person("u1@x.com", {"codemie-katas/intro": (10,)})})
        k = run(data).K["codemie-katas/intro"]
        assert (k["cat"], k["conf"]) == ("learning", 100.0)

    def test_tie_is_unclassified(self):
        # Own-name repository (pet 10) used by... nothing else; plus "how.work_tools" (production 10) only.
        data = inputs(
            {"u1": person("john_smith@x.com", {"john-smith/x": (10,)})},
            tools=[tool("u1", "john-smith/x", sessions=2, days=2, work=1)],
        )
        k = run(data).K["personal:u1:john-smith/x"]
        assert (k["cat"], k["reason"]) == ("unclassified", "tie")

    def test_without_cli_metrics_coverage_drops_to_three_quarters(self):
        pairs = {uid: {"acme/core": (10,)} for uid in TEAM}
        k = run(inputs(merge_people(pairs))).K["acme/core"]
        assert k["coverage"] == 0.75
        assert k["how_available"] is False

    def test_minimum_confidence_override(self):
        data = inputs({"u5": person("u5@x.com", {"projects/a": (5,)})}, tools=[tool("u5", "projects/a", 1, days=1)])
        assert run(data, settings_with(minimumConfidence=75)).K["folder:u5:projects/a"]["cat"] == "unclassified"

    def test_shared_project_signal(self):
        data = inputs(
            {"u1": person("u1@x.com", {"acme/core": (100, 0, 100, 0)})},
            values={"acme/core": value(100, personal=0, projects={"proj": 100})},
            tools=[tool("u1", "acme/core", sessions=5, edit=4, days=6)],
            app_db=app_db(shared=["proj"]),
        )
        k = run(data).K["acme/core"]
        assert "where.shared_project" in {x["id"] for x in k["signals"]}
        assert k["cat"] == "production"  # where + how


# ---------------------------------------------------------------------------------------------------------------------
# Money, allocation and Not Charged Back
# ---------------------------------------------------------------------------------------------------------------------
class TestMoney:
    def test_no_repository_and_outside_limits(self):
        data = inputs(
            {
                "u1": person("u1@x.com", {"": (12,), "cowork": (8,), "acme/x": (10,)}, extra=(3, 0, 0, 0)),
            }
        )
        an = run(data, settings_with(allocateByPerson__enabled=False))
        assert an.unclassified["no_repository"] == pytest.approx(20)
        assert an.unclassified["outside_limits"] == pytest.approx(3)
        assert an.cli["billed"] == pytest.approx(33)

    def test_spend_without_a_customer_is_left_out(self):
        an = run(inputs({"u1": person("u1@x.com", {"acme/x": (10, 0, 0, 4)})}))
        assert an.cli["billed"] == pytest.approx(6)
        assert an.cli["nc"] == pytest.approx(4)

    def test_allocation_by_person(self):
        data = inputs(
            {
                "a": person("a@x.com", {"codemie-katas/a": (100,), "": (50,)}),  # 67% classified: allocated
                "b": person("b@x.com", {"codemie-katas/b": (4,), "": (90,)}),  # $4 classified: below $5
                "c": person("c@x.com", {"codemie-katas/c": (5,), "": (95,)}),  # 5% classified: below 10%
            }
        )
        an = run(data)
        assert an.allocated["learning"]["spend"] == pytest.approx(50)
        assert dict(an.person_alloc["a"]) == {"learning": pytest.approx(50)}
        assert "b" not in an.person_alloc and "c" not in an.person_alloc
        assert an.allocated_reason["no_repository"] == pytest.approx(50)
        off = run(data, settings_with(allocateByPerson__enabled=False))
        assert sum(a["spend"] for a in off.allocated.values()) == 0

    def test_allocation_by_shared_project(self):
        """No classified work of their own: spend inside a shared project follows the project's classified work."""
        shared_cli = {
            "proj": {
                "spend": 140.0,
                "cb": 0.0,
                "sh": 140.0,
                "fb": 0.0,
                "people_n": 2,
                "repos": {"codemie-katas/w": [100.0, 100.0], "": [40.0, 40.0]},
                "people": {"w": [100.0, "w@x.com"], "z": [40.0, "z@x.com"]},
                "clients": {},
                "models": {},
            }
        }
        data = inputs(
            {
                "w": person("w@x.com", {"codemie-katas/w": (100, 0, 100)}),
                "z": person("z@x.com", {"": (40, 0, 40)}, extra=(10, 0, 0, 0)),  # $10 in a personal project
            },
            shared_cli=shared_cli,
            app_db=app_db(shared=["proj"]),
        )
        an = run(data)
        assert dict(an.person_alloc["z"]) == {"learning": pytest.approx(40)}  # only the shared-project part
        assert an.allocated["learning"]["sh"] == pytest.approx(40)
        off = run(data, settings_with(allocateByProject__enabled=False))
        assert "z" not in off.person_alloc
        # Too little of the project's CLI spend is classified.
        strict = run(data, settings_with(allocateByProject__minClassifiedShare=0.9))
        assert "z" not in strict.person_alloc

    def test_allocation_thresholds_are_tunable(self):
        data = inputs({"c": person("c@x.com", {"codemie-katas/c": (25,), "": (75,)})})
        an = run(data, settings_with(allocateByPerson__minClassifiedShare=0.2))
        assert an.allocated["learning"]["spend"] == pytest.approx(75)

    def test_not_charged_back_parts(self):
        prod_pairs, prod_tools = team_repo("acme/core", spend=100)  # paid personally in personal projects
        data = inputs(
            merge_people(
                prod_pairs,
                {"u4": {"codemie-katas/k": (40, 0, 40, 0)}},  # learning inside a shared project: excluded
                {"u5": {"other/x": (30, 0, 30, 0)}},  # unclassified inside a shared project: counts
                {"u6": {"lib/y": (20, 20, 0, 0)}},  # charged back: never counts
            ),
            tools=prod_tools,
            app_db=app_db(shared=["proj"]),
        )
        an = run(data, settings_with(allocateByPerson__enabled=False))
        assert an.ncb_parts["cli_personal"] == pytest.approx(300)
        assert an.ncb_parts["cli_shared"] == pytest.approx(30)
        an2 = run(data, settings_with(allocateByPerson__enabled=False, notChargedBack__excludeSharedExperiments=False))
        assert an2.ncb_parts["cli_shared"] == pytest.approx(70)

    def test_allocated_experiments_leave_and_allocated_production_joins(self):
        prod_pairs, prod_tools = team_repo("acme/core", spend=100)
        prod_pairs["u1"][""] = (50, 0, 0, 0)  # u1: no repository, paid personally -> allocated to production
        data = inputs(merge_people(prod_pairs), tools=prod_tools)
        an = run(data)
        assert an.ncb_parts["cli_personal_allocated"] == pytest.approx(50)
        assert an.person_ncb_cli["u1"] == pytest.approx(150)

    def test_categories_add_up_to_cli_spend(self):
        prod_pairs, prod_tools = team_repo("acme/core")
        data = inputs(
            merge_people(prod_pairs, {"u9": {"codemie-katas/k": (40,), "": (10,)}}, extra={"u1": (5, 0, 0, 0)}),
            tools=prod_tools,
        )
        an = run(data)
        total = sum(m["spend"] for m in an.cat_money.values())
        assert total == pytest.approx(an.cli["billed"])


class TestWebWhoPays:
    def data(self, web_funding: dict | None, budgets: list | None = None) -> dict:
        return inputs(
            {},
            web_proj={
                "budgeted": {"spend": 100.0, "people": 2.0, "types": {}},
                "proj": {"spend": 50.0, "people": 1.0, "types": {}},
                "alice@x.com": {"spend": 30.0, "people": 1.0, "types": {}},
            },
            web_fb={"budgeted": {"spend": 20.0, "people": 1.0, "types": {}}},
            web_people={
                "uA": {"name": "a@x.com", "spend": 60.0, "proj": {"budgeted": 60.0}, "fbp": {}},
                "uB": {
                    "name": "b@x.com",
                    "spend": 110.0,
                    "proj": {"budgeted": 40.0, "proj": 50.0},
                    "fbp": {"budgeted": 20.0},
                },
                "uC": {"name": "c@x.com", "spend": 30.0, "proj": {"alice@x.com": 30.0}, "fbp": {}},
            },
            app_db=app_db(shared=["budgeted", "proj"], budgets=budgets or [("budgeted", "platform")]),
            web_funding=web_funding,
        )

    def test_the_funded_part_is_charged_back_and_the_rest_paid_personally(self):
        # uA was funded all period; uB only for part of it (10 of 40), and had fallback records too.
        an = run(self.data({"budgeted": {"uA": [60.0, 60.0], "uB": [40.0, 10.0]}}))
        assert an.web["charged"] == pytest.approx(70)
        assert an.web["shared"] == pytest.approx(100)  # 30 unfunded + 50 in a project without a budget + 20 fallback
        assert an.web["personal"] == pytest.approx(30)
        assert an.web_project["budgeted"] == {"sh": pytest.approx(50), "cb": pytest.approx(70)}
        assert an.person_web["uA"]["charged"] == pytest.approx(60)
        assert an.person_web["uB"]["charged"] == pytest.approx(10)
        assert an.person_web["uB"]["shared"] == pytest.approx(100)
        assert an.person_web["uC"]["personal"] == pytest.approx(30)

    def test_a_platform_budget_without_funding_charges_nothing_back(self):
        # Today's budget table no longer decides: stopped, or no member had an allocation in the period.
        an = run(self.data({}))
        assert an.web["charged"] == 0
        assert an.web["shared"] == pytest.approx(170)

    def test_a_cli_budget_alone_does_not_charge_back_web_usage(self):
        data = inputs(
            {},
            web_proj={"p": {"spend": 10.0, "people": 1.0, "types": {}}},
            web_people={"u": {"name": "u", "spend": 10.0, "proj": {"p": 10.0}, "fbp": {}}},
            app_db=app_db(shared=["p"], budgets=[("p", "cli")]),
        )
        assert run(data).web["shared"] == pytest.approx(10)

    def test_shared_projects_show_how_the_web_platform_was_paid(self):
        cases = {
            "funded": ({"budgeted": {"uA": [60.0, 60.0], "uB": [40.0, 40.0]}}, "partial"),  # the fallback records
            "part": ({"budgeted": {"uA": [60.0, 60.0], "uB": [40.0, 10.0]}}, "partial"),
            "none": ({}, "shared"),
        }
        for name, (web_funding, state) in cases.items():
            rows = {r["name"]: r for r in build_sections(run(self.data(web_funding)))["shared"]}
            assert rows["budgeted"]["web_state"] == state, name


# ---------------------------------------------------------------------------------------------------------------------
# Groups and related projects
# ---------------------------------------------------------------------------------------------------------------------
class TestGroups:
    def test_two_signals_link_repositories(self):
        persons = merge_people({"u1": {"acme/a": (10,), "acme/b": (10,)}, "u2": {"acme/a": (10,), "acme/b": (10,)}})
        an = run(inputs(persons))
        assert an.K["acme/a"]["group"] == an.K["acme/b"]["group"]
        g = an.groups[an.K["acme/a"]["group"]]
        assert g["signals"]["owner"]["acme"] == 1 and g["people_common"] == 2

    def test_one_signal_does_not(self):
        persons = merge_people({"u3": {"beta/a": (10,)}, "u4": {"beta/b": (10,)}})
        an = run(inputs(persons))
        assert an.K["beta/a"]["group"] != an.K["beta/b"]["group"]

    def test_signals_needed_are_tunable(self):
        persons = merge_people({"u1": {"acme/a": (10,), "acme/b": (10,)}, "u2": {"acme/a": (10,), "acme/b": (10,)}})
        an = run(inputs(persons), settings_with(groupMinSignals=3))
        assert an.K["acme/a"]["group"] != an.K["acme/b"]["group"]

    def test_ticket_keys_and_owner(self):
        persons = merge_people({"u5": {"gamma/x": (10,)}, "u6": {"gamma/y": (10,)}})
        values = {"gamma/x": value(10, branches={"GAM-1-a": 10}), "gamma/y": value(10, branches={"feature/GAM-2": 10})}
        an = run(inputs(persons, values=values))
        assert an.K["gamma/x"]["group"] == an.K["gamma/y"]["group"]

    def test_generic_ticket_prefixes_are_ignored(self):
        persons = merge_people({"u5": {"gamma/x": (10,)}, "u6": {"gamma/y": (10,)}})
        values = {"gamma/x": value(10, branches={"FEATURE-1": 10}), "gamma/y": value(10, branches={"FEATURE-2": 10})}
        an = run(inputs(persons, values=values))
        assert an.K["gamma/x"]["group"] != an.K["gamma/y"]["group"]

    def test_size_guard_splits_by_owner(self):
        repos = [f"o1/r{i}" for i in range(4)] + [f"o2/r{i}" for i in range(3)]
        pairs = {uid: {r: (10,) for r in repos} for uid in ("u1", "u2")}
        values = {r: value(20, people=2, branches={f"TKT-{i}": 20}) for i, r in enumerate(repos)}
        an = run(inputs(merge_people(pairs), values=values), settings_with(maxGroupSize=5))
        g1 = {an.K[r]["group"] for r in repos[:4]}
        g2 = {an.K[r]["group"] for r in repos[4:]}
        assert len(g1) == 1 and len(g2) == 1 and g1 != g2
        assert an.stats["components_split_by_owner"] == 1

    def test_datasources_of_a_project_that_registers_many_repositories_do_not_link(self):
        # u1 and u2 work on acme/a and beta/b (people in common); only a broad indexer registers both.
        persons = merge_people({"u1": {"acme/a": (10,), "beta/b": (10,)}, "u2": {"acme/a": (10,), "beta/b": (10,)}})
        links = ["https://github.com/acme/a", "https://github.com/beta/b"]
        narrow = app_db(shared=["team"], datasources=[("team", x) for x in links])
        an = run(inputs(persons, app_db=narrow))
        assert an.K["acme/a"]["group"] == an.K["beta/b"]["group"]
        broad = app_db(
            shared=["indexer"],
            datasources=[("indexer", x) for x in links]
            + [("indexer", f"https://github.com/o{i}/r") for i in range(60)],
        )
        an = run(inputs(persons, app_db=broad))
        assert an.K["acme/a"]["group"] != an.K["beta/b"]["group"]

    def test_excluded_repositories_are_not_grouped(self):
        persons = merge_people({"u1": {"epam/a": (10,), "epam/b": (10,)}, "u2": {"epam/a": (10,), "epam/b": (10,)}})
        an = run(inputs(persons))
        assert an.K["epam/a"]["excluded"] is True
        assert an.K["epam/a"]["group"] != an.K["epam/b"]["group"]


# People who work on their own repositories, so a project's members are a small share of all CLI people.
OTHERS = {f"o{i}": {f"solo{i}/r": (10,)} for i in range(30)}


class TestRelatedProject:
    def _keys(self, an, *names):
        return [an.K[n] for n in names]

    def test_order_budgeted_reuse_datasource_membership_owner_rule(self):
        # u1 and u2 work on acme/a and acme/b; 30 other people work elsewhere, so team-proj is specific to them.
        persons = merge_people(
            {"u1": {"acme/a": (10,), "acme/b": (10,)}, "u2": {"acme/a": (10,), "acme/b": (10,)}, **OTHERS}
        )
        db = app_db(
            shared=["budget-proj", "ds-proj", "team-proj", "rule-proj"],
            datasources=[("ds-proj", "https://github.com/Acme/A.git")],
            members=[("u1", "team-proj"), ("u2", "team-proj")],
        )
        settings = settings_with(ownerProjectRules=[{"owner": "acme", "project": "rule-proj"}])
        with_budget = inputs(
            persons, values={"acme/a": value(20, people=2, projects_cb={"budget-proj": 15})}, app_db=db
        )
        an = run(with_budget, settings)
        g = an.groups[an.K["acme/a"]["group"]]
        assert related_project(an, self._keys(an, "acme/a", "acme/b"), g)[:2] == ("budget-proj", "budgeted_reuse")
        an = run(inputs(persons, app_db=db), settings)
        g = an.groups[an.K["acme/a"]["group"]]
        assert related_project(an, self._keys(an, "acme/a", "acme/b"), g)[:2] == ("ds-proj", "datasource")
        db_no_ds = dict(db, datasources=[])
        an = run(inputs(persons, app_db=db_no_ds), settings)
        g = an.groups[an.K["acme/a"]["group"]]
        assert related_project(an, self._keys(an, "acme/a", "acme/b"), g)[:2] == ("team-proj", "membership")
        an = run(inputs(persons, app_db=dict(db_no_ds, members=[])), settings)
        g = an.groups[an.K["acme/a"]["group"]]
        assert related_project(an, self._keys(an, "acme/a", "acme/b"), g)[:2] == ("rule-proj", "owner_rule")
        an = run(inputs(persons, app_db=dict(db_no_ds, members=[])))
        g = an.groups[an.K["acme/a"]["group"]]
        assert related_project(an, self._keys(an, "acme/a", "acme/b"), g)[:2] == (None, "none")

    def _group(self, an):
        return an.groups[an.K["acme/a"]["group"]]

    def test_budgeted_reuse_needs_a_tenth_of_the_groups_money(self):
        # $1 of the group's $40 is charged back to budget-proj: a stray session, not the project's work.
        persons = merge_people({"u1": {"acme/a": (10,), "acme/b": (10,)}, "u2": {"acme/a": (10,), "acme/b": (10,)}})
        values = {"acme/a": value(20, people=2, projects_cb={"budget-proj": 1})}
        an = run(inputs(persons, values=values, app_db=app_db(shared=["budget-proj"])))
        assert related_project(an, self._keys(an, "acme/a", "acme/b"), self._group(an))[:2] == (None, "none")
        values = {"acme/a": value(20, people=2, projects_cb={"budget-proj": 4})}
        an = run(inputs(persons, values=values, app_db=app_db(shared=["budget-proj"])))
        assert related_project(an, self._keys(an, "acme/a", "acme/b"), self._group(an))[:2] == (
            "budget-proj",
            "budgeted_reuse",
        )

    def test_a_project_that_registers_many_repositories_is_not_their_datasource_project(self):
        persons = merge_people({"u1": {"acme/a": (10,), "acme/b": (10,)}, "u2": {"acme/a": (10,), "acme/b": (10,)}})
        indexer = [("indexer", "https://github.com/acme/a")] + [
            ("indexer", f"https://github.com/other{i}/r") for i in range(60)
        ]
        an = run(inputs(persons, app_db=app_db(shared=["indexer"], datasources=indexer)))
        assert related_project(an, self._keys(an, "acme/a", "acme/b"), self._group(an))[:2] == (None, "none")

    def test_membership_of_a_project_many_people_belong_to_is_not_evidence(self):
        persons = merge_people(
            {"u1": {"acme/a": (10,), "acme/b": (10,)}, "u2": {"acme/a": (10,), "acme/b": (10,)}, **OTHERS}
        )
        # big-proj: u1, u2 and 10 of the 30 others (12 of 32 CLI people); small-proj: u1 and u2 only.
        big = [("u1", "big-proj"), ("u2", "big-proj")] + [(f"o{i}", "big-proj") for i in range(10)]
        db = app_db(shared=["big-proj", "small-proj"], members=big)
        an = run(inputs(persons, app_db=db))
        assert related_project(an, self._keys(an, "acme/a", "acme/b"), self._group(an))[:2] == (None, "none")
        db = app_db(shared=["big-proj", "small-proj"], members=big + [("u1", "small-proj"), ("u2", "small-proj")])
        an = run(inputs(persons, app_db=db))
        assert related_project(an, self._keys(an, "acme/a", "acme/b"), self._group(an))[:2] == (
            "small-proj",
            "membership",
        )

    def test_datasources_survive_the_loader_to_pipeline_boundary(self):
        """The loader caches only the normalized owner/repo (links may carry credentials); the pipeline must use it as
        is. Normalizing it again turned team/repo into None and lost every datasource (F-09)."""
        links = [
            ("p", "https://user:secret@example.test/Team/Repo.git"),
            ("p", "git@example.test:team/other.git"),
            ("p", "https://dev.azure.com/org/proj/_git/AzRepo"),
        ]
        db = app_db(shared=["p"])
        db["datasources"] = datasource_rows(links)  # what the loader caches
        an = run(inputs(merge_people({"u1": {"team/repo": (10,)}}), app_db=db))
        assert dict(an.datasource_projects) == {"team/repo": {"p"}, "team/other": {"p"}, "_git/azrepo": {"p"}}
        assert an.datasource_count["p"] == 3

    def test_datasource_is_where_evidence(self):
        persons = merge_people({"u1": {"acme/a": (10,)}})
        db = app_db(shared=["ds-proj"], datasources=[("ds-proj", "git@github.com:acme/a.git")])
        k = run(inputs(persons, app_db=db)).K["acme/a"]
        assert "where.datasource" in {x["id"] for x in k["signals"]}

    def test_a_broad_indexer_is_not_where_evidence(self):
        # A project that registers 61 repositories indexes them: it says nothing about acme/a being production work.
        persons = merge_people({"u1": {"acme/a": (10,)}})
        links = [("indexer", "git@github.com:acme/a.git")] + [
            ("indexer", f"https://github.com/o{i}/r") for i in range(60)
        ]
        k = run(inputs(persons, app_db=app_db(shared=["indexer"], datasources=links))).K["acme/a"]
        assert "where.datasource" not in {x["id"] for x in k["signals"]}


# ---------------------------------------------------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------------------------------------------------
class TestWindows:
    def test_money_comes_from_the_range_and_categories_from_the_evidence(self):
        ev_pairs, ev_tools = team_repo("acme/core", spend=100)
        evidence = merge_people(ev_pairs)
        money = {"u1": person("u1@x.com", {"acme/core": (10,)})}
        an = run(inputs(money, ev_persons=evidence, tools=ev_tools, days=7))
        assert an.same_windows is False
        assert an.K["acme/core"]["cat"] == "production"
        assert an.cat_money["production"]["spend"] == pytest.approx(10)
        assert an.KM["acme/core"]["billed"] == pytest.approx(10)
        assert an.K["acme/core"]["billed"] == pytest.approx(300)

    def test_repository_beyond_the_top_repositories_of_the_evidence_is_outside_the_limits(self):
        # acme/other was used in the evidence window (it is among its repository values), but in nobody's top list.
        evidence = {"u1": person("u1@x.com", {"acme/core": (100,)})}
        money = {"u1": person("u1@x.com", {"acme/other": (10,)})}
        values = {"acme/core": value(100), "acme/other": value(2)}
        an = run(
            inputs(money, ev_persons=evidence, values=values, days=7), settings_with(allocateByPerson__enabled=False)
        )
        assert an.category_of("acme/other") == ("unclassified", "outside_limits")
        assert an.unclassified["outside_limits"] == pytest.approx(10)

    def test_repository_first_used_after_the_evidence_was_read_has_no_evidence(self):
        """Evidence reused from earlier in the hour ends before the range: a repository first used since is not in it
        at all. It has no evidence yet; it is not beyond anyone's top repositories."""
        ev_pairs, ev_tools = team_repo("acme/core")
        money = {"u1": person("u1@x.com", {"acme/core": (5,), "acme/new": (10,)})}
        data = inputs(
            money,
            ev_persons=merge_people(ev_pairs),
            values={"acme/core": value(300, people=3)},
            tools=ev_tools,
            days=1,
        )
        an = run(data, settings_with(allocateByPerson__enabled=False))
        assert an.category_of("acme/core") == ("production", None)
        assert an.category_of("acme/new") == ("unclassified", "no_evidence")
        assert an.unclassified["no_evidence"] == pytest.approx(10)
        assert an.unclassified["outside_limits"] == 0
