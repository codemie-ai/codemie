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

"""Chargeback candidates and their grouping by common condition (People tab), built on request.

A candidate does production work from a personal budget on enough days (``candidate_days`` in handler.py, an instant
display setting), so candidates and groups are built from the cached production work (people_work.py) for every
response. A condition counts only when two or more candidates share it:

- ``project``: the CodeMie project the work runs inside, or the project its repositories are linked to;
- ``repositories``: the repository group (a single repository is a group of one);
- ``organization``: the Git organization, the owner of ``owner/repo``.

``common`` tries them in that order on the work not grouped yet: the first condition another candidate shares takes the
work. What no other candidate shares is "none".
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from codemie.service.usage_attribution.constants import PEOPLE_GROUP_BY
from codemie.service.usage_attribution.grouping import short_hash

COMMON, PROJECT, REPOSITORIES, ORGANIZATION = PEOPLE_GROUP_BY
CONDITIONS = (PROJECT, REPOSITORIES, ORGANIZATION)
_FIELD = {PROJECT: "project", REPOSITORIES: "group", ORGANIZATION: "organization"}


def candidate_ids(rows: list[dict[str, Any]], days: int) -> set[str]:
    """People with production paid personally (over $0.50) on at least ``days`` production days."""
    return {r["uid"] for r in rows if r["prod_ncb"] > 0.5 and r["production_days"] >= days}


def _new_group(gid: str, condition: str | None, name: str | None) -> dict[str, Any]:
    return {
        "id": gid,
        "condition": condition,
        "name": name,
        "label": None,
        "how": None,
        "amount": 0.0,
        "estimated": 0.0,
        "people": {},
        "people_estimated": {},
        "repositories": {},
        "reason": None,
        "_how": Counter(),
        "_reason": Counter(),
    }


def _add(g: dict[str, Any], w: dict[str, Any]) -> None:
    g["amount"] += w["amount"]
    g["estimated"] += w["estimated"]
    g["people"][w["uid"]] = g["people"].get(w["uid"], 0.0) + w["amount"]
    g["people_estimated"][w["uid"]] = g["people_estimated"].get(w["uid"], 0.0) + w["estimated"]
    if w.get("repository"):
        repo = g["repositories"].setdefault(w["repository"], {"amount": 0.0, "people": set()})
        repo["amount"] += w["amount"]
        repo["people"].add(w["uid"])
    if g["label"] is None and g["condition"] == REPOSITORIES:
        g["label"] = w.get("group_label") or g["name"]
    if g["condition"] == PROJECT and w.get("project_how"):
        g["_how"][w["project_how"]] += w["amount"]
        g["_reason"][w.get("project_reason") or w["project_how"]] += w["amount"]


def _finish(g: dict[str, Any]) -> dict[str, Any]:
    how, reason = g.pop("_how"), g.pop("_reason")
    if g["condition"] in (PROJECT, ORGANIZATION):
        g["label"] = g["name"]
    g["how"] = how.most_common(1)[0][0] if how else None
    g["reason"] = reason.most_common(1)[0][0] if reason else None
    return g


def _sharers_by_value(remaining: list[dict[str, Any]], field: str) -> dict[str, set]:
    sharers: dict[str, set] = defaultdict(set)
    for w in remaining:
        if w.get(field):
            sharers[w[field]].add(w["uid"])
    return sharers


def _take_shared_work(
    remaining: list[dict[str, Any]], field: str, condition: str, groups: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Moves the work whose ``field`` value is shared by two or more candidates into ``groups``; the rest is left."""
    sharers = _sharers_by_value(remaining, field)
    left = []
    for w in remaining:
        value = w.get(field)
        if not value or len(sharers[value]) < 2:
            left.append(w)
            continue
        gid = f"{condition}-{short_hash(value)}"
        if gid not in groups:
            groups[gid] = _new_group(gid, condition, value)
        _add(groups[gid], w)
    return left


def group_candidates(
    work: list[dict[str, Any]], candidates: set[str], group_by: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Groups of candidates by ``group_by`` (a condition or ``common``), and the work no other candidate shares.

    Conditions are tried in order on the work not grouped yet: a project shared by two or more candidates takes their
    work first; the repositories and then the organization are shared among what is left.
    """
    remaining = [w for w in work if w["uid"] in candidates]
    groups: dict[str, dict[str, Any]] = {}
    for condition in CONDITIONS if group_by == COMMON else (group_by,):
        remaining = _take_shared_work(remaining, _FIELD[condition], condition, groups)
    none = _new_group("none", None, None)
    for w in remaining:
        _add(none, w)
    return [_finish(g) for g in groups.values()], _finish(none)


def median_days(people: dict[str, float], rows_by_uid: dict[str, dict[str, Any]]) -> int:
    """The median production days of a group's people (the upper middle of an even count)."""
    days = sorted(int(rows_by_uid[u]["production_days"]) for u in people if u in rows_by_uid)
    return days[len(days) // 2] if days else 0
