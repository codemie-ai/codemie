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

"""Production work paid personally, per person and repository, with the conditions people can share (People tab)."""

from __future__ import annotations

from collections import Counter

import pytest

from codemie.service.usage_attribution.sections import build_sections
from tests.codemie.service.usage_attribution.builders import app_db, inputs, person, run, tool, value
from tests.codemie.service.usage_attribution.test_sections_and_queries import sample, sample_with_charged_back_person


def team_repo(u1: dict, shared: list[str] | None = None, **value_kwargs) -> dict:
    """acme/core worked on by three people with code changes on six days and a ticket key: Production. ``u1`` replaces
    the first person; u2 and u3 pay for their work from personal projects."""
    persons = {
        "u1": u1,
        "u2": person("u2@x.com", {"acme/core": (100, 0, 0, 0)}),
        "u3": person("u3@x.com", {"acme/core": (100, 0, 0, 0)}),
    }
    return inputs(
        persons,
        values={"acme/core": value(300, people=3, branches={"ACME-1": 300}, **value_kwargs)},
        tools=[tool("u1", "acme/core", sessions=6, edit=5, days=6)],
        app_db=app_db(shared=shared or []),
    )


def work_of(data: dict, uid: str) -> list[dict]:
    return [w for w in build_sections(run(data))["pwork"] if w["uid"] == uid]


def test_contributions_add_up_to_production_paid_personally():
    for data in (sample(), sample_with_charged_back_person()):
        secs = build_sections(run(data))
        by_uid: Counter = Counter()
        for w in secs["pwork"]:
            by_uid[w["uid"]] += w["amount"]
        for r in secs["people"]:
            assert by_uid[r["uid"]] == pytest.approx(r["prod_ncb"], abs=0.02), r["uid"]


def test_work_inside_a_shared_project_without_a_budget_carries_that_project():
    u1 = person("u1@x.com", {"acme/core": (100, 0, 100, 0)}, projects={"nwnd-pay": (100, 0, 100)})
    work = work_of(team_repo(u1, shared=["nwnd-pay"]), "u1")
    assert {(w["project"], w["project_how"], w["project_reason"]) for w in work} == {("nwnd-pay", "inside", "inside")}
    assert sum(w["amount"] for w in work) == pytest.approx(100.0)


def test_fallback_records_carry_the_fallback_project():
    # Since release 2.57.0 the record carries the person's own project; the shared project is in budget_fallback_from.
    u1 = person(
        "u1@x.com", {"acme/core": (100, 0, 100, 0)}, projects={"u1@x.com": (100, 0, 100)}, fallback={"nwnd-pay": 100}
    )
    work = work_of(team_repo(u1, shared=["nwnd-pay"]), "u1")
    assert {(w["project"], w["project_how"]) for w in work} == {("nwnd-pay", "inside")}


def test_personal_project_work_carries_the_linked_project():
    # acme/core is already charged back to the budgeted shared project "acme-delivery" for someone else.
    data = team_repo(
        person("u1@x.com", {"acme/core": (100, 0, 0, 0)}),
        shared=["acme-delivery"],
        projects={"acme-delivery": 60.0},
        projects_cb={"acme-delivery": 60.0},
    )
    work = work_of(data, "u2")
    assert {(w["project"], w["project_how"], w["project_reason"]) for w in work} == {
        ("acme-delivery", "linked", "budgeted_reuse")
    }


def test_shared_money_without_a_known_project_has_no_project_condition():
    # Paid inside a shared project, but neither a shared project row nor a fallback project names it.
    u1 = person("u1@x.com", {"acme/core": (100, 0, 100, 0)}, projects={"u1@x.com": (100, 0, 100)})
    work = work_of(team_repo(u1), "u1")
    assert work and all(w["project"] is None and w["project_how"] is None for w in work)


def test_organization_is_the_remote_owner_and_repository_groups_are_kept():
    work = build_sections(run(sample()))["pwork"]
    acme = [w for w in work if w["repository"] == "acme/core"]
    assert acme and {w["organization"] for w in acme} == {"acme"}
    assert len({w["group"] for w in acme}) == 1 and all(w["group_label"] for w in acme)


def test_allocated_production_follows_the_persons_repositories():
    # u1's desktop work ("" value, $20) is allocated to Production by their own classified work on acme/core.
    work = work_of(sample(), "u1")
    assert all(w["repository"] == "acme/core" for w in work)
    assert sum(w["estimated"] for w in work) == pytest.approx(20.0, abs=0.02)


def test_allocated_production_without_personal_repository_work_stands_alone():
    # u9's own acme/core work is charged back; only the allocated desktop work is paid personally.
    work = work_of(sample_with_charged_back_person(), "u9")
    assert work == [
        {
            "uid": "u9",
            "amount": 40.0,
            "estimated": 40.0,
            "repository": None,
            "project": None,
            "project_how": None,
            "project_reason": None,
            "group": None,
            "group_label": None,
            "organization": None,
        }
    ]
