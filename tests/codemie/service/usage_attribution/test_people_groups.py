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

"""Chargeback candidates and their grouping by common condition (People tab)."""

from __future__ import annotations

import re

import pytest

from codemie.service.usage_attribution.people_groups import candidate_ids, group_candidates, median_days


def w(uid, amount, project=None, group=None, org=None, how=None, estimated=0.0, repository="acme/core"):
    return {
        "uid": uid,
        "amount": float(amount),
        "estimated": float(estimated),
        "repository": repository,
        "project": project,
        "project_how": how or ("inside" if project else None),
        "project_reason": how or ("inside" if project else None),
        "group": group,
        "group_label": f"label-{group}" if group else None,
        "organization": org,
    }


def by_key(groups):
    return {(g["condition"], g["name"]): g for g in groups}


def test_candidates_need_production_paid_personally_and_the_days():
    rows = [
        {"uid": "a", "prod_ncb": 200.0, "production_days": 5},
        {"uid": "b", "prod_ncb": 200.0, "production_days": 4},
        {"uid": "c", "prod_ncb": 0.4, "production_days": 20},
    ]
    assert candidate_ids(rows, 5) == {"a"}


def test_a_condition_needs_two_candidates():
    groups, none = group_candidates([w("a", 100, project="p1"), w("b", 50, project="p2")], {"a", "b"}, "project")
    assert groups == []
    assert none["amount"] == 150 and none["people"] == {"a": 100, "b": 50}


def test_common_condition_prefers_project_then_repositories_then_organization():
    work = [
        w("a", 100, project="p1", group="g1", org="acme"),
        w("b", 60, project="p1", group="g2", org="acme"),
        w("c", 40, group="g2", org="acme"),
        w("d", 30, org="acme"),
    ]
    groups, none = group_candidates(work, {"a", "b", "c", "d"}, "common")
    groups = by_key(groups)
    assert groups[("project", "p1")]["amount"] == 160
    assert ("repositories", "g2") not in groups  # only c is left on g2 once b's work went to p1
    assert groups[("organization", "acme")]["people"] == {"c": 40, "d": 30}
    assert none["amount"] == 0


def test_single_conditions_group_by_that_condition_only():
    work = [w("a", 100, project="p1", group="g1", org="acme"), w("b", 60, project="p2", group="g1", org="acme")]
    assert set(by_key(group_candidates(work, {"a", "b"}, "repositories")[0])) == {("repositories", "g1")}
    assert set(by_key(group_candidates(work, {"a", "b"}, "organization")[0])) == {("organization", "acme")}
    assert group_candidates(work, {"a", "b"}, "project")[0] == []


def test_groups_and_none_add_up_to_the_candidates_work():
    work = [
        w("a", 100, project="p1", group="g1", org="acme", estimated=20),
        w("b", 60, project="p1"),
        w("c", 40, group="g2", org="beta"),
        w("d", 30, group="g2"),
        w("e", 25, org="gamma"),
        w("z", 999, project="p1"),
    ]
    groups, none = group_candidates(work, {"a", "b", "c", "d", "e"}, "common")
    assert sum(g["amount"] for g in groups) + none["amount"] == pytest.approx(255)
    assert by_key(groups)[("project", "p1")]["estimated"] == 20


def test_a_person_in_two_groups_keeps_the_amount_of_each():
    work = [w("a", 100, project="p1"), w("b", 10, project="p1"), w("a", 60, project="p2"), w("c", 5, project="p2")]
    groups, _ = group_candidates(work, {"a", "b", "c"}, "project")
    assert {g["name"]: g["people"]["a"] for g in groups} == {"p1": 100, "p2": 60}


def test_non_candidates_are_left_out():
    groups, none = group_candidates([w("a", 100, project="p1"), w("z", 100, project="p1")], {"a"}, "project")
    assert groups == [] and none["people"] == {"a": 100}


def test_labels_how_and_repositories():
    work = [
        w("a", 100, project="p1", how="linked", repository="acme/core"),
        w("b", 30, project="p1", how="inside", repository="acme/web"),
        w("a", 20, group="g1", repository="acme/api"),
        w("c", 10, group="g1", repository="acme/api"),
    ]
    groups = by_key(group_candidates(work, {"a", "b", "c"}, "common")[0])
    p1, g1 = groups[("project", "p1")], groups[("repositories", "g1")]
    assert (p1["label"], p1["how"]) == ("p1", "linked")  # the larger part of its money
    assert set(p1["repositories"]) == {"acme/core", "acme/web"}
    assert (g1["label"], g1["how"]) == ("label-g1", None)
    assert g1["repositories"]["acme/api"]["amount"] == 30 and g1["repositories"]["acme/api"]["people"] == {"a", "c"}


def test_group_ids_are_safe_in_urls():
    groups, _ = group_candidates(
        [w("a", 1, project="me@x.com / p"), w("b", 1, project="me@x.com / p")], {"a", "b"}, "project"
    )
    assert re.fullmatch(r"project-[0-9a-f]{10}", groups[0]["id"])


def test_median_days():
    rows = {"a": {"production_days": 5}, "b": {"production_days": 9}, "c": {"production_days": 20}}
    assert median_days({"a": 1, "b": 1, "c": 1}, rows) == 9
    assert median_days({}, rows) == 0
