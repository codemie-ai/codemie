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

"""Production work paid from personal budgets, per person and repository, with the conditions people can share.

The People tab groups chargeback candidates by these conditions on request (people_groups.py): the CodeMie project the
work belongs to, the repository group, and the Git organization. A person's rows add up to their production paid
personally (``prod_ncb``); the part allocated from their unclassified work is marked estimated.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from codemie.service.usage_attribution.constants import PRODUCTION
from codemie.service.usage_attribution.grouping import related_project
from codemie.service.usage_attribution.identity import REMOTE

if TYPE_CHECKING:
    from codemie.service.usage_attribution.pipeline import Analysis
    from codemie.service.usage_attribution.sections import Labels

_EPS = 0.005
INSIDE = "inside"
LINKED = "linked"


def _r2(x: float) -> float:
    return round(float(x), 2)


def _shared_projects(an: Analysis, p: dict[str, Any]) -> dict[str, float]:
    """The shared projects a person paid for personally: projects without a budget for them (records before release
    2.57.0) and the projects of fallback records (since 2.57.0), by amount."""
    out: Counter = Counter()
    for project, money in (p.get("projects") or {}).items():
        if not an.is_personal_project(project) and money[2] > _EPS:
            out[project] += money[2]
    for project, amount in (p.get("fallback") or {}).items():
        if not an.is_personal_project(project) and amount > _EPS:
            out[project] += amount
    return dict(out)


def _no_condition(uid: str, amount: float) -> dict[str, Any]:
    """Allocated production of a person without production work of their own paid personally: no repository."""
    return {
        "uid": uid,
        "amount": amount,
        "estimated": amount,
        "repository": None,
        "project": None,
        "project_how": None,
        "project_reason": None,
        "group": None,
        "group_label": None,
        "organization": None,
    }


class _Linked:
    """The shared project a repository group is linked to (grouping.related_project), once per group."""

    def __init__(self, an: Analysis):
        self.an = an
        self.cache: dict[str, tuple[str | None, str | None]] = {}

    def __call__(self, group_id: str | None) -> tuple[str | None, str | None]:
        if not group_id:
            return None, None
        if group_id not in self.cache:
            g = self.an.groups.get(group_id)
            found, why = (None, "none")
            if g:
                found, why, _text = related_project(self.an, [self.an.K[m] for m in g["members"]], g)
            self.cache[group_id] = (found, why) if found and why != "none" else (None, None)
        return self.cache[group_id]


def _base_row(uid: str, labels: Labels, group_names: dict[str, str], k: dict[str, Any]) -> dict[str, Any]:
    """The fields shared by every row produced for a single repository key."""
    return {
        "uid": uid,
        "estimated": 0.0,
        "repository": labels.key_display(k),
        "group": k["group"],
        "group_label": group_names.get(k["group"]) or labels.key_display(k),
        "organization": k["owner"].lower() if k["kind"] == REMOTE and k.get("owner") else None,
    }


def _inside_rows(
    base: dict[str, Any], inside: float, shared: dict[str, float], shared_total: float
) -> list[dict[str, Any]]:
    """Rows for the part of a repository's work paid inside shared projects, split across them by amount."""
    split = shared.items() if shared_total > 0 else [(None, 1.0)]
    total = shared_total if shared_total > 0 else 1.0
    rows: list[dict[str, Any]] = []
    for project, amount in split:
        how = INSIDE if project else None
        rows.append(
            {
                **base,
                "amount": inside * amount / total,
                "project": project,
                "project_how": how,
                "project_reason": how,
            }
        )
    return rows


def _own_row(base: dict[str, Any], own: float, k: dict[str, Any], linked: _Linked) -> dict[str, Any]:
    """The row for the part of a repository's work paid in personal projects."""
    project, reason = linked(k["group"])
    return {
        **base,
        "amount": own,
        "project": project,
        "project_how": LINKED if project else None,
        "project_reason": reason,
    }


def _repository_rows(
    an: Analysis, labels: Labels, group_names: dict[str, str], uid: str, linked: _Linked
) -> list[dict[str, Any]]:
    """A person's production work paid personally, per repository: the part paid inside shared projects carries
    those projects; the part paid in personal projects carries the project the repository group is linked to."""
    shared = _shared_projects(an, an.persons.get(uid) or {})
    shared_total = sum(shared.values())
    rows: list[dict[str, Any]] = []
    for _v, key, cat, billed, pair in an.person_pairs.get(uid, []):
        k = an.K.get(key) if key else None
        if cat != PRODUCTION or k is None:
            continue
        inside, own = pair[2], max(0.0, billed - pair[1] - pair[2])
        base = _base_row(uid, labels, group_names, k)
        if inside > _EPS:
            rows.extend(_inside_rows(base, inside, shared, shared_total))
        if own > _EPS:
            rows.append(_own_row(base, own, k, linked))
    return rows


def build_people_work(an: Analysis, labels: Labels, group_names: dict[str, str]) -> list[dict[str, Any]]:
    linked = _Linked(an)
    rows: list[dict[str, Any]] = []
    for uid in an.person_pairs:
        person_rows = _repository_rows(an, labels, group_names, uid, linked)
        allocated = (an.person_alloc_paid.get(uid) or Counter()).get(PRODUCTION, 0.0)
        total = sum(r["amount"] for r in person_rows)
        if allocated > _EPS and total > 0:
            for r in person_rows:
                part = allocated * r["amount"] / total
                r["amount"] += part
                r["estimated"] += part
        elif allocated > _EPS:
            person_rows.append(_no_condition(uid, allocated))
        for r in person_rows:
            r["amount"], r["estimated"] = _r2(r["amount"]), _r2(r["estimated"])
        rows.extend(person_rows)
    return rows
