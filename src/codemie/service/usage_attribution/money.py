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

"""Money: CLI spend by category and who pays, allocation by person, Web Platform who pays, Not Charged Back.

Not Charged Back is the potential chargeback (architecture.md §7.3), not Total minus Charged Back. It is the sum of:
1. CLI paid personally inside shared projects, except work classified or allocated as an experiment;
2. CLI in personal projects classified or allocated as Production;
3. Web Platform paid personally inside shared projects.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Any

from codemie.service.usage_attribution.constants import (
    CATEGORIES,
    CHARGED,
    EXPERIMENTS,
    NO_REPOSITORY,
    OUTSIDE_LIMITS,
    PERSONAL,
    PRODUCTION,
    SHARED,
    UNCLASSIFIED,
)

if TYPE_CHECKING:
    from codemie.service.usage_attribution.pipeline import Analysis

_MIN = 0.005


def exclude_shared_experiments(settings: dict[str, Any]) -> bool:
    return bool((settings.get("notChargedBack") or {}).get("excludeSharedExperiments", True))


def ncb_cli(cat: str, shared: float, personal: float, exclude_experiments: bool) -> tuple[float, float]:
    """Potential chargeback of CLI spend paid from personal budgets.

    In a shared project the project itself is the context, so everything counts except work classified as an
    experiment (when that rule is on). In a personal project only work classified as Production counts.
    """
    in_shared = 0.0 if (exclude_experiments and cat in EXPERIMENTS) else shared
    return in_shared, (personal if cat == PRODUCTION else 0.0)


def _apply_pair(
    an: Analysis,
    uid: str,
    v: str,
    pair: tuple[float, float, float, float],
    cat_money: dict[str, Counter],
    cat_users: dict[str, set],
    exclude_exp: bool,
) -> None:
    """Classify one repository-value pair of one person and tally its billed money by category."""
    spend, cb, sh, nc = pair
    key = an.resolution(uid, v)[0]
    billed = spend - nc
    pe = max(0.0, billed - cb - sh)
    cat, reason = an.category_of(key)
    if key is None:
        reason = NO_REPOSITORY
    m = cat_money[cat]
    m["spend"] += billed
    m["cb"] += cb
    m["sh"] += sh
    m["pe"] += pe
    if billed > _MIN:
        cat_users[cat].add(uid)
    if cat == UNCLASSIFIED:
        an.unclassified[reason] += billed
        an.person_uncl[uid].update({"spend": billed, "cb": cb, "sh": sh, "pe": pe})
        an.person_uncl_reason[uid][reason] += billed
    an.person_cat[uid][cat] += billed
    ncb_shared, ncb_personal = ncb_cli(cat, sh, pe, exclude_exp)
    an.person_ncb_cli[uid] += ncb_shared + ncb_personal
    an.ncb_parts["cli_shared"] += ncb_shared
    an.ncb_parts["cli_personal"] += ncb_personal
    an.value_mix[v][cat] += billed
    an.person_pairs[uid].append((v, key, cat, billed, pair))


def _apply_person_pairs(
    an: Analysis,
    uid: str,
    p: dict[str, Any],
    cat_money: dict[str, Counter],
    cat_users: dict[str, set],
    exclude_exp: bool,
) -> Counter:
    """Classify and tally each of a person's repository-value pairs; returns what they cover of the person's
    totals (spend, cb, sh, nc)."""
    covered: Counter = Counter()
    for v, pair in p["pairs"].items():
        spend, cb, sh, nc = pair
        covered["spend"] += spend
        covered["cb"] += cb
        covered["sh"] += sh
        covered["nc"] += nc
        _apply_pair(an, uid, v, pair, cat_money, cat_users, exclude_exp)
    return covered


def _apply_remainder(
    an: Analysis,
    uid: str,
    p: dict[str, Any],
    covered: Counter,
    cat_money: dict[str, Counter],
    cat_users: dict[str, set],
) -> None:
    """The part of a person's totals not covered by any repository-value pair (beyond the analysis limits) joins
    Unclassified, outside limits."""
    rem = {fld: max(0.0, p[fld] - covered[fld]) for fld in ("spend", "cb", "sh", "nc")}
    rem_billed = max(0.0, rem["spend"] - rem["nc"])
    if rem_billed <= _MIN:
        return
    rem_pe = max(0.0, rem_billed - rem["cb"] - rem["sh"])
    m = cat_money[UNCLASSIFIED]
    m["spend"] += rem_billed
    m["cb"] += rem["cb"]
    m["sh"] += rem["sh"]
    m["pe"] += rem_pe
    an.unclassified[OUTSIDE_LIMITS] += rem_billed
    an.person_cat[uid][UNCLASSIFIED] += rem_billed
    cat_users[UNCLASSIFIED].add(uid)
    an.person_ncb_cli[uid] += rem["sh"]  # unclassified spend in shared projects still counts
    an.ncb_parts["cli_shared"] += rem["sh"]
    an.person_uncl[uid].update({"spend": rem_billed, "cb": rem["cb"], "sh": rem["sh"], "pe": rem_pe})
    an.person_uncl_reason[uid][OUTSIDE_LIMITS] += rem_billed


def compute_money(an: Analysis) -> None:
    exclude_exp = exclude_shared_experiments(an.S)
    cat_money: dict[str, Counter] = {c: Counter() for c in CATEGORIES}
    cat_users: dict[str, set] = defaultdict(set)
    an.unclassified = Counter()
    an.person_cat = defaultdict(Counter)
    an.person_ncb_cli = Counter()
    an.ncb_parts = Counter()
    an.person_pairs = defaultdict(list)
    an.value_mix = defaultdict(Counter)
    an.person_uncl = defaultdict(Counter)  # a person's unclassified CLI money: spend, cb, sh, pe
    an.person_uncl_reason = defaultdict(Counter)
    for uid, p in an.persons.items():
        covered = _apply_person_pairs(an, uid, p, cat_money, cat_users, exclude_exp)
        _apply_remainder(an, uid, p, covered, cat_money, cat_users)
    an.cat_money, an.cat_users = cat_money, cat_users
    allocate_by_person(an, exclude_exp)
    allocate_by_project(an, exclude_exp)
    an.cli = Counter()
    for p in an.persons.values():
        for fld in ("spend", "cb", "sh", "nc"):
            an.cli[fld] += p[fld]
    an.cli["billed"] = an.cli["spend"] - an.cli["nc"]
    an.cli["pe"] = max(0.0, an.cli["billed"] - an.cli["cb"] - an.cli["sh"])
    web_money(an)
    an.project_cli_prod = shared_cli_production(an, exclude_exp)


def _classified_for_person(
    an: Analysis, uid: str, u: Counter, min_spend: float, min_share: float
) -> tuple[dict[str, float] | None, float]:
    """A person's classified spend by category, and its total, when it qualifies to receive an allocation of that
    person's unclassified spend; (None, 0.0) otherwise."""
    if u["spend"] <= _MIN:
        return None, 0.0
    classified = {c: v for c, v in an.person_cat[uid].items() if c != UNCLASSIFIED and v > 0}
    total = sum(classified.values())
    if total <= 0 or total < min_spend or total / (total + u["spend"]) < min_share:
        return None, 0.0
    return classified, total


def _allocate_person_category(an: Analysis, uid: str, u: Counter, cat: str, share: float, exclude_exp: bool) -> None:
    """Allocate one category's share of a person's unclassified spend, and adjust Not Charged Back for it."""
    for fld in ("spend", "cb", "sh", "pe"):
        an.allocated[cat][fld] += u[fld] * share
    an.person_alloc[uid][cat] += u["spend"] * share
    an.person_alloc_paid[uid][cat] += (u["sh"] + u["pe"]) * share
    an.allocated_users[cat].add(uid)
    if cat in EXPERIMENTS and exclude_exp:  # experiments are not a chargeback candidate, even in a shared project
        an.person_ncb_cli[uid] -= u["sh"] * share
        an.ncb_parts["cli_shared"] -= u["sh"] * share
    elif cat == PRODUCTION:
        an.person_ncb_cli[uid] += u["pe"] * share
        an.ncb_parts["cli_personal_allocated"] += u["pe"] * share


def allocate_by_person(an: Analysis, exclude_exp: bool) -> None:
    """Split each person's unclassified CLI spend by that person's classified spend (``allocateByPerson``).

    Allocated amounts are kept apart from classified ones. Not Charged Back follows them: allocated experiments
    leave the shared-project part, and allocated production in personal projects joins it.
    """
    rule = an.S.get("allocateByPerson") or {}
    an.allocated = {c: Counter() for c in CATEGORIES if c != UNCLASSIFIED}
    an.allocated_users = defaultdict(set)
    an.person_alloc = defaultdict(Counter)
    an.person_alloc_paid = defaultdict(Counter)  # the part paid from personal budgets
    an.allocated_reason = Counter()
    if not rule.get("enabled"):
        return
    min_spend = float(rule.get("minClassifiedSpend", 20))
    min_share = float(rule.get("minClassifiedShare", 0.3))
    for uid, u in an.person_uncl.items():
        classified, total = _classified_for_person(an, uid, u, min_spend, min_share)
        if classified is None:
            continue
        for cat, amount in classified.items():
            _allocate_person_category(an, uid, u, cat, amount / total, exclude_exp)
        for reason, amount in an.person_uncl_reason[uid].items():
            an.allocated_reason[reason] += amount


def _project_mix(an: Analysis, project: str, min_spend: float, min_share: float) -> dict[str, float] | None:
    """A shared project's classified CLI work by category (from its repository values), or None when too little of
    its CLI spend is classified."""
    d = an.shared_cli.get(project) or {}
    mix: Counter = Counter()
    for value, (spend, _sh) in (d.get("repos") or {}).items():
        value_mix = an.value_mix.get(value) or Counter()
        total = sum(value_mix.values())
        if total <= 0:
            continue
        for cat, amount in value_mix.items():
            if cat != UNCLASSIFIED and amount > 0:
                mix[cat] += spend * amount / total
    classified = sum(mix.values())
    cli = float(d.get("spend") or 0.0)
    if classified < min_spend or cli <= 0 or classified / cli < min_share:
        return None
    return {cat: amount / classified for cat, amount in mix.items()}


def _apply_project_category(
    an: Analysis, uid: str, u: Counter, cat: str, alloc_spend: float, cb_sh_ratio: float, exclude_exp: bool
) -> None:
    """Allocate one category's share of a person's remaining unclassified project spend, and adjust Not Charged
    Back for it."""
    cb, sh = u["cb"] * cb_sh_ratio, u["sh"] * cb_sh_ratio
    an.allocated[cat]["spend"] += alloc_spend
    an.allocated[cat]["cb"] += cb
    an.allocated[cat]["sh"] += sh
    an.person_alloc[uid][cat] += alloc_spend
    an.person_alloc_paid[uid][cat] += sh
    an.allocated_users[cat].add(uid)
    if cat in EXPERIMENTS and exclude_exp:
        an.person_ncb_cli[uid] -= sh
        an.ncb_parts["cli_shared"] -= sh


def _allocate_project_person(
    an: Analysis,
    project: str,
    uid: str,
    row: tuple,
    done: set,
    mixes: dict[str, dict[str, float] | None],
    left: dict[str, float],
    exclude_exp: bool,
    min_spend: float,
    min_share: float,
) -> None:
    """Allocate one person's unclassified spend inside one shared project by that project's classified mix, unless
    allocation by person already covered them."""
    u = an.person_uncl.get(uid)
    if uid in done or not u:
        return
    shared_part = u["cb"] + u["sh"]
    if shared_part <= _MIN:
        return
    if project not in mixes:
        mixes[project] = _project_mix(an, project, min_spend, min_share)
    mix = mixes[project]
    remaining = left.setdefault(uid, shared_part)
    amount = min(float(row[0]), remaining)
    if not mix or amount <= _MIN:
        return
    left[uid] = remaining - amount
    ratio = amount / shared_part
    for cat, share in mix.items():
        _apply_project_category(an, uid, u, cat, amount * share, ratio * share, exclude_exp)
    for reason, amount_r in an.person_uncl_reason[uid].items():
        an.allocated_reason[reason] += amount_r * amount / u["spend"]


def allocate_by_project(an: Analysis, exclude_exp: bool) -> None:
    """Split the unclassified CLI spend that a person spent inside a shared project, and that allocation by person did
    not cover (too little classified work of their own), by that project's classified CLI work (``allocateByProject``).

    Only the shared-project part of the person's unclassified spend (charged back or shared without a budget) is split;
    allocated amounts are kept apart, like allocation by person.
    """
    rule = an.S.get("allocateByProject") or {}
    if not rule.get("enabled"):
        return
    min_spend = float(rule.get("minClassifiedSpend", 50))
    min_share = float(rule.get("minClassifiedShare", 0.3))
    done = set().union(*an.allocated_users.values()) if an.allocated_users else set()
    mixes: dict[str, dict[str, float] | None] = {}
    left: dict[str, float] = {}
    for project in sorted(an.shared_cli):
        if an.is_personal_project(project):
            continue
        for uid, row in sorted((an.shared_cli[project].get("people") or {}).items()):
            _allocate_project_person(an, project, uid, row, done, mixes, left, exclude_exp, min_spend, min_share)


def web_funded(an: Analysis, project: str, uid: str) -> float:
    """The part of a member's Web Platform spend in a shared project (records without fallback) that the project's
    platform budget paid: while the member had an allocation of an active budget (funding.py)."""
    row = (an.web_funding.get(project) or {}).get(uid)
    return row[1] if row else 0.0


def web_money(an: Analysis) -> None:
    """Web Platform who pays (funding.py).

    - Records with budget_fallback_from were paid personally inside that shared project.
    - Other records in a shared project were charged back for the part the project's platform budget paid: while the
      member had an allocation of it and the budget was active. The rest was paid personally inside the project.
    - Records in personal projects were paid personally.
    """
    an.web = Counter()
    an.web_project = {}
    for project, d in an.web_proj.items():
        if an.is_personal_project(project):
            an.web[PERSONAL] += d["spend"]
            continue
        cb = min(d["spend"], sum(row[1] for row in (an.web_funding.get(project) or {}).values()))
        an.web[CHARGED] += cb
        an.web[SHARED] += d["spend"] - cb
        an.web_project[project] = {"sh": d["spend"] - cb, "cb": cb}
    for project, d in an.web_fb.items():
        an.web[SHARED] += d["spend"]
        an.web["fallback"] += d["spend"]
        wp = an.web_project.setdefault(project, {"sh": 0.0, "cb": 0.0})
        wp["sh"] += d["spend"]
    an.web["total"] = an.web[CHARGED] + an.web[SHARED] + an.web[PERSONAL]
    an.person_web = {}
    for uid, d in an.web_people.items():
        w: Counter = Counter()
        for project, amount in d["proj"].items():
            if an.is_personal_project(project):
                w[PERSONAL] += amount
                continue
            cb = min(amount, web_funded(an, project, uid))
            w[CHARGED] += cb
            w[SHARED] += amount - cb
        w[SHARED] += sum(d["fbp"].values())
        w["total"] = d["spend"]
        an.person_web[uid] = w


def value_fractions(an: Analysis, value: str) -> tuple[float, float] | None:
    """Shares of a repository value's spend that are Production and experiments, or None if not analysed."""
    mix = an.value_mix.get(value)
    total = sum(mix.values()) if mix else 0.0
    if total <= 0:
        return None
    return mix[PRODUCTION] / total, sum(mix[c] for c in EXPERIMENTS) / total


def shared_cli_production(an: Analysis, exclude_exp: bool) -> dict[str, dict[str, float]]:
    """Production and experiment parts of each shared project's CLI spend (overall and paid personally).

    Only the project's top repositories are known; spend outside them, and spend without a repository, stays in the
    potential chargeback because it was made inside the shared project.
    """
    out = {}
    for project, d in an.shared_cli.items():
        prod = prod_sh = exp_sh = known = 0.0
        for value, (spend, sh) in d["repos"].items():
            fractions = value_fractions(an, value)
            if fractions is None:
                continue
            known += spend
            prod += spend * fractions[0]
            prod_sh += sh * fractions[0]
            exp_sh += sh * fractions[1]
        potential = max(0.0, d["sh"] - exp_sh) if exclude_exp else d["sh"]
        out[project] = {"prod": prod, "prod_sh": prod_sh, "exp_sh": exp_sh, "known": known, "potential_sh": potential}
    return out
