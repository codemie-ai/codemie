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

"""CLI classification (classification-model.md §5).

Each repository collects signals from four families (Where, Who, How, Names); each signal adds points to a category.
Confidence is ``100 × top ÷ (top + runner-up + constant) × coverage``, where coverage is the weighted share of
families with data (How has no data without CLI tool or session metrics). A repository is Unclassified when the
confidence is below the minimum, on a tie, or when Production leads with fewer than ``productionMinFamilies`` families
other than Names, unless the sustained-work rule holds (code changes on many days plus one more work signal).
Names add at most ``namesCap`` points per category and can never make Production alone; when a name points to
another category, Production needs ``contradictedProductionMinConfidence``.

Unclassified repositories where Production leads, with at least ``likelyProductionMinConfidence`` and no name
against it, are reported as likely production: still Unclassified, shown apart.

A second pass adds a weak Who signal (``who.person_history``) to Production-leaning repositories of a person whose
other classified work is mostly Production (``personHistory``).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Any

from codemie.service.usage_attribution.constants import (
    EXPERIMENTS,
    FAMILIES,
    LEARNING,
    LIKELY_PRODUCTION,
    LOW_CONFIDENCE,
    NO_EVIDENCE,
    PRODUCTION,
    PRODUCTION_ONE_FAMILY,
    TIE,
    UNCLASSIFIED,
)
from codemie.service.usage_attribution.grouping import broad_indexer
from codemie.service.usage_attribution.identity import AZURE, FOLDER, PERSONAL_KEY, REMOTE

if TYPE_CHECKING:
    from codemie.service.usage_attribution.pipeline import Analysis


def sustained_work(settings: dict[str, Any], signals: list[dict[str, Any]]) -> bool:
    """Code changes on many days plus another work signal: enough to stand for two evidence families."""
    rule = settings.get("sustainedWork") or {}
    if not rule.get("enabled"):
        return False
    ids = {x["id"] for x in signals if x["cat"] == PRODUCTION}
    return set(rule.get("all", [])) <= ids and (not rule.get("anyOf") or bool(ids & set(rule["anyOf"])))


def _name_text(k: dict[str, Any]) -> str:
    if k["kind"] == REMOTE:
        return k["key"]
    return k["name"] or ""


def _accumulate_scores(settings: dict[str, Any], signals: list[dict[str, Any]]) -> Counter:
    """Apply the names-family cap to each signal's points and sum effective points per category."""
    scores: Counter = Counter()
    names_pts: Counter = Counter()
    for x in signals:
        if x["family"] == "names":
            x["eff"] = min(x["pts"], max(0.0, settings["namesCap"] - names_pts[x["cat"]]))
            names_pts[x["cat"]] += x["eff"]
        else:
            x["eff"] = x["pts"]
        scores[x["cat"]] += x["eff"]
    return scores


def _rank_scores(scores: Counter, settings: dict[str, Any], coverage: float) -> tuple[str | None, float, float, float]:
    """Top and runner-up categories plus the confidence they produce."""
    ranked = [(c, v) for c, v in scores.most_common() if v > 0]
    top_cat, top = ranked[0] if ranked else (None, 0.0)
    runner = ranked[1][1] if len(ranked) > 1 else 0.0
    conf = 100.0 * top / (top + runner + settings["confidenceConstant"]) * coverage if top > 0 else 0.0
    return top_cat, top, runner, conf


def _is_contradicted(top_cat: str | None, signals: list[dict[str, Any]]) -> bool:
    """A name that points to another category (learning, testing, experiment, pet project) raises the bar."""
    return top_cat == PRODUCTION and any(x["family"] == "names" and x["cat"] != PRODUCTION for x in signals)


def _min_confidence(settings: dict[str, Any], contradicted: bool) -> float:
    min_conf = settings["minimumConfidence"]
    if contradicted:
        min_conf = max(min_conf, settings.get("contradictedProductionMinConfidence", min_conf))
    return min_conf


def _classify_category(
    settings: dict[str, Any],
    signals: list[dict[str, Any]],
    top_cat: str | None,
    top: float,
    runner: float,
    conf: float,
    min_conf: float,
    prod_families: list[str],
    learning: bool,
) -> tuple[str | None, str | None, float]:
    """The category/reason/confidence decision, from the strongest override down to the default."""
    if learning:
        return LEARNING, None, 100.0
    if top <= 0:
        return UNCLASSIFIED, NO_EVIDENCE, conf
    if runner == top:
        return UNCLASSIFIED, TIE, conf
    if conf < min_conf:
        return UNCLASSIFIED, LOW_CONFIDENCE, conf
    one_family = (
        top_cat == PRODUCTION
        and len(prod_families) < settings["productionMinFamilies"]
        and not sustained_work(settings, signals)
    )
    if one_family:
        return UNCLASSIFIED, PRODUCTION_ONE_FAMILY, conf
    return top_cat, None, conf


def _apply_likely_production(
    settings: dict[str, Any], top_cat: str | None, reason: str | None, contradicted: bool, conf: float
) -> str | None:
    likely_min = settings.get("likelyProductionMinConfidence")
    is_likely = (
        reason in (LOW_CONFIDENCE, PRODUCTION_ONE_FAMILY)
        and top_cat == PRODUCTION
        and not contradicted
        and likely_min is not None
        and conf >= likely_min
    )
    return LIKELY_PRODUCTION if is_likely else reason


def decide(
    settings: dict[str, Any], signals: list[dict[str, Any]], how_available: bool, learning: bool
) -> dict[str, Any]:
    """Points, confidence and the decision for one repository's signals."""
    fam_w = settings["familyWeights"]
    fam_total = float(sum(fam_w.get(f, 0) for f in FAMILIES)) or 1.0
    scores = _accumulate_scores(settings, signals)
    available = {"where", "who", "names"} | ({"how"} if how_available else set())
    coverage = sum(fam_w.get(f, 0) for f in available) / fam_total
    top_cat, top, runner, conf = _rank_scores(scores, settings, coverage)
    prod_families = sorted({x["family"] for x in signals if x["cat"] == PRODUCTION and x["family"] != "names"})
    contradicted = _is_contradicted(top_cat, signals)
    min_conf = _min_confidence(settings, contradicted)
    cat, reason, conf = _classify_category(
        settings, signals, top_cat, top, runner, conf, min_conf, prod_families, learning
    )
    reason = _apply_likely_production(settings, top_cat, reason, contradicted, conf)
    return {
        "scores": dict(scores),
        "top_cat": top_cat,
        "top": top,
        "runner": runner,
        "coverage": coverage,
        "conf": conf,
        "cat": cat,
        "reason": reason,
        "prod_families": prod_families,
    }


class _Signals:
    """The signals one repository collects, with their category and points from the settings."""

    def __init__(self, weights: dict[str, Any]):
        self.weights = weights
        self.items: list[dict[str, Any]] = []

    def add(self, sid: str, **args: Any) -> None:
        if sid not in self.weights:
            return
        cat, pts = self.weights[sid]
        self.items.append({"id": sid, "family": sid.split(".")[0], "cat": cat, "pts": float(pts), "args": args})


def _where(an: Analysis, k: dict[str, Any], out: _Signals, owner_people: dict[str, set[str]]) -> None:
    thresholds = an.S["thresholds"]
    own_people = {u for u, a in k["persons"].items() if a > 0}
    if k["learning"]:
        out.add("where.learning_source")
    owner = k["owner"]
    if (
        k["kind"] == REMOTE
        and an.identity.groupable_owner(owner)
        and len(owner_people[owner]) >= thresholds["ownerSharedMinPeople"]
        and len(owner_people[owner] - own_people) > 0
    ):
        out.add("where.owner_shared", owner=owner, people=len(owner_people[owner]))
    if k["cb"] >= thresholds["budgetedReuseMinSpend"] and k["cb"] >= thresholds["budgetedReuseMinShare"] * max(
        k["billed"], 1e-9
    ):
        budgeted = [p for p, a in k["projects_cb"].most_common() if a > 0 and not an.is_personal_project(p)]
        out.add("where.budgeted_reuse", cb=k["cb"], project=budgeted[0] if budgeted else None)
    shared_spend = sum(k["projects"].values())
    known = shared_spend + k["personal_spend"]
    if (
        out.weights.get("where.shared_project", [None, 0])[1]
        and known > 0
        and shared_spend / known >= thresholds.get("sharedProjectMinShare", 0.5)
    ):
        out.add("where.shared_project", project=k["projects"].most_common(1)[0][0], share=shared_spend / known)
    # A broad indexer (grouping.broad_indexer) registers repositories without owning them: not evidence.
    owners = sorted(p for p in an.datasource_projects.get(k["key"], ()) if not broad_indexer(an, p))
    if k["kind"] in (REMOTE, AZURE) and owners:
        out.add("where.datasource", projects=owners)
    if k["kind"] == PERSONAL_KEY:
        out.add("where.personal_key")
    if k["kind"] == FOLDER:
        out.add("where.local_folder")


def _who(an: Analysis, k: dict[str, Any], out: _Signals) -> None:
    team = an.S["teamSignalPeople"]
    g = an.groups.get(k["group"])
    group_people = g["people_n"] if g and len(g["members"]) > 1 else 0
    if k["people_n"] >= team:
        out.add("who.team", people=k["people_n"])
    elif group_people >= team:
        out.add("who.team_group", people=group_people)


def _how(an: Analysis, k: dict[str, Any], out: _Signals) -> bool:
    """How the work was done. Returns whether the family has data (CLI tool or session metrics)."""
    thresholds = an.S["thresholds"]
    t, s = k["tool"], k["sess"]
    if t["sessions"] >= thresholds["minToolSessions"]:
        share = t["edit"] / t["sessions"]
        if share >= thresholds["editShareProduction"]:
            out.add("how.code_changes", share=share, sessions=t["sessions"])
        elif 1.0 - share >= thresholds["readOnlyShareLearning"]:
            out.add("how.read_only", share=1.0 - share, sessions=t["sessions"])
    if t["days_max"] >= thresholds["activeDaysProduction"]:
        out.add("how.active_days", days=t["days_max"])
    elif 0 < t["sessions"] <= thresholds["oneOffMaxSessions"] and t["days_max"] <= thresholds["oneOffMaxDays"]:
        out.add("how.one_off", days=t["days_max"], sessions=t["sessions"])
    if t["work"] + t["mr"] >= thresholds["workToolMinSessions"]:
        out.add("how.work_tools", work=t["work"], mr=t["mr"])
    if s["mcp_work"] > 0:
        out.add("how.mcp_project_work", sessions=s["mcp_work"])
    if s["framework"] > 0:
        out.add("how.framework", sessions=s["framework"])
    return t["sessions"] > 0 or s["sessions"] > 0


def _names(an: Analysis, k: dict[str, Any], out: _Signals) -> None:
    if k["tickets"]:
        out.add("names.ticket_keys", prefixes=[x for x, _ in k["tickets"].most_common(3)])
    text = _name_text(k)
    for cat in EXPERIMENTS:
        rx = an.name_res.get(cat)
        m = rx.search(text) if rx else None
        if m:
            out.add("names." + cat, word=m.group(0).strip("-_./ "))


def classify(an: Analysis) -> None:
    owner_people: dict[str, set[str]] = defaultdict(set)
    for k in an.K.values():
        if k["kind"] == REMOTE and an.identity.groupable_owner(k["owner"]):
            owner_people[k["owner"]].update(u for u, a in k["persons"].items() if a > 0)
    weights = an.S["weights"]
    for k in an.K.values():
        signals = _Signals(weights)
        _where(an, k, signals, owner_people)
        _who(an, k, signals)
        how_available = _how(an, k, signals)
        _names(an, k, signals)
        k.update(decide(an.S, signals.items, how_available, k["learning"]))
        k["signals"] = signals.items
        k["how_available"] = how_available
    _person_history(an)


def _classified_totals(an: Analysis) -> dict[str, Counter]:
    """Each person's spend per category, counting only first-pass classified, non-excluded repositories."""
    classified: dict[str, Counter] = defaultdict(Counter)
    for k in an.K.values():
        if k["cat"] == UNCLASSIFIED or k["excluded"]:
            continue
        for uid, amount in k["persons"].items():
            if amount > 0:
                classified[uid][k["cat"]] += amount
    return classified


def _qualified_people(classified: dict[str, Counter], rule: dict[str, Any]) -> set[str]:
    """People whose classified work is mostly Production, above the minimum spend."""
    min_spend = float(rule.get("minClassifiedSpend", 20))
    min_share = float(rule.get("minProductionShare", 0.7))
    qualified = set()
    for uid, c in classified.items():
        total = sum(c.values())
        if total >= min_spend and c[PRODUCTION] / total >= min_share:
            qualified.add(uid)
    return qualified


def _main_person(k: dict[str, Any]) -> str | None:
    people = [(u, a) for u, a in k["persons"].items() if a > 0]
    if not people:
        return None
    return max(people, key=lambda ua: (ua[1], ua[0]))[0]


def _add_person_history_signal(an: Analysis, k: dict[str, Any], cat: str, pts: float, main: str) -> None:
    signals = [
        *k["signals"],
        {"id": "who.person_history", "family": "who", "cat": cat, "pts": float(pts), "args": {"person": main}},
    ]
    k.update(decide(an.S, signals, k["how_available"], k["learning"]))
    k["signals"] = signals


def _person_history(an: Analysis) -> None:
    """Second pass: a weak Who signal for Production-leaning, unclassified repositories of a person whose other
    classified work is mostly Production. Only first-pass decisions count, so the signal never feeds itself."""
    rule = an.S.get("personHistory") or {}
    weight = an.S["weights"].get("who.person_history")
    if not rule.get("enabled") or not weight:
        return
    qualified = _qualified_people(_classified_totals(an), rule)
    cat, pts = weight
    for k in an.K.values():
        if k["cat"] != UNCLASSIFIED or k["top_cat"] != PRODUCTION or k["learning"]:
            continue
        main = _main_person(k)
        if main is None or main not in qualified:
            continue
        _add_person_history_signal(an, k, cat, pts, main)
