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

"""Build the cached sections of one analysis.

Display settings (thresholds, highlights) are not applied here: sections keep the raw numbers, and responses apply the
display values when they are built, so changing a display setting never recomputes anything. Lists that a display
setting can widen (repository groups, shared projects, people) therefore hold every row with money in them.

Per-entity details are stored in shards (by a hash of the entity id), so a popup reads a small part of the cache.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from codemie.service.usage_attribution.constants import (
    CATEGORIES,
    CHARGED,
    DETAIL_SHARDS,
    EXPERIMENTS,
    LIKELY_PRODUCTION,
    NO_REPOSITORY,
    OUTSIDE_LIMITS,
    PERSONAL,
    PRODUCTION,
    REASONS,
    RULES,
    SECTION_BREAKDOWN,
    SECTION_GROUP_DETAIL,
    SECTION_GROUPS,
    SECTION_PEOPLE,
    SECTION_PEOPLE_WORK,
    SECTION_PERSON_DETAIL,
    SECTION_SHARED,
    SECTION_SHARED_DETAIL,
    SECTION_SUMMARY,
    SHARED,
    SIGNAL_LABELS,
    TOO_LITTLE_EVIDENCE_REASONS,
    UNCLASSIFIED,
)
from codemie.service.usage_attribution.grouping import related_project, short_hash
from codemie.service.usage_attribution.identity import AZURE, FOLDER, PERSONAL_KEY, REMOTE, canonical_value
from codemie.service.usage_attribution.money import web_funded
from codemie.service.usage_attribution.people_work import build_people_work

if TYPE_CHECKING:
    from codemie.service.usage_attribution.pipeline import Analysis

_MIN = 0.5
_GROUP_REPOS_IN_DETAIL = 60
_PROJECT_REPOS_IN_DETAIL = 25
_PERSON_REPOS_IN_DETAIL = 25


def r2(x: Any) -> float:
    return round(float(x or 0.0), 2)


def shard_of(entity_id: str) -> int:
    return int(hashlib.sha1(entity_id.encode("utf-8"), usedforsecurity=False).hexdigest()[:8], 16) % DETAIL_SHARDS


def project_row_id(project: str) -> str:
    return "s-" + short_hash(project, 10)


def _signal_text_datasource(a: dict[str, Any]) -> str:
    names = a["projects"]
    more = f" and {len(names) - 2} more" if len(names) > 2 else ""
    return f"Git datasource of {', '.join(names[:2])}{more}"


def _signal_text_budgeted_reuse(a: dict[str, Any]) -> str:
    return f"${a['cb']:,.0f} charged back" + (f" in {a['project']}" if a.get("project") else "")


def _signal_text_shared_project(a: dict[str, Any]) -> str:
    return f"{a['share']:.0%} of its CLI spend is inside shared projects, mostly {a['project']}"


def _signal_text_work_tools(a: dict[str, Any]) -> str:
    return f"Work-tracking tools in {a['work']:.0f} sessions, merge-request tools in {a['mr']:.0f}"


def _signal_text_mcp_project_work(a: dict[str, Any]) -> str:
    return f"{a['sessions']:.0f} sessions with project-scoped Jira/GitLab/GitHub/Confluence MCP servers"


# One text template per signal id (or pair of ids sharing a template). Keeping this as a lookup table, rather than a
# long if/elif chain, is what keeps signal_text's cognitive complexity low: each entry is a plain assignment, not a
# branch.
_SIGNAL_TEXT: dict[str, Callable[[dict[str, Any]], str]] = {
    "where.owner_shared": lambda a: f"Owner {a['owner']} is used by {a['people']} people across its repositories",
    "where.budgeted_reuse": _signal_text_budgeted_reuse,
    "where.shared_project": _signal_text_shared_project,
    "where.datasource": _signal_text_datasource,
    "where.personal_key": lambda _a: "The owner is the person's own name",
    "where.local_folder": lambda _a: "No Git remote: a local folder",
    "where.learning_source": lambda _a: "Matches a learning source in the settings",
    "who.team": lambda a: f"{a['people']} people",
    "who.team_group": lambda a: f"{a['people']} people",
    "how.code_changes": lambda a: f"Code changes in {a['share']:.0%} of {a['sessions']:.0f} sessions",
    "how.read_only": lambda a: f"No code changes in {a['share']:.0%} of {a['sessions']:.0f} sessions",
    "how.active_days": lambda a: f"Active on {a['days']:.0f} days",
    "how.one_off": lambda a: f"{a['sessions']:.0f} session(s) on {max(1, a['days']):.0f} day(s)",
    "how.work_tools": _signal_text_work_tools,
    "how.mcp_project_work": _signal_text_mcp_project_work,
    "how.framework": lambda a: f"{a['sessions']:.0f} sessions with delivery-framework skills",
    "names.ticket_keys": lambda a: "Ticket keys " + ", ".join(t + "-" for t in a["prefixes"]),
}


# ---------------------------------------------------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------------------------------------------------
class Labels:
    def __init__(self, an: Analysis):
        self.an = an

    def person_name(self, uid: str) -> str:
        info = self.an.people_info.get(uid)
        if info and info["name"]:
            return info["name"]
        p = self.an.persons.get(uid) or self.an.ev_persons.get(uid)
        if p:
            return p["name"]
        return (self.an.web_people.get(uid) or {}).get("name", uid)

    def person_email(self, uid: str) -> str:
        info = self.an.people_info.get(uid)
        if info and info.get("email"):
            return info["email"]
        p = self.an.persons.get(uid) or self.an.ev_persons.get(uid)
        name = p["name"] if p else (self.an.web_people.get(uid) or {}).get("name", "")
        return name if "@" in (name or "") else ""

    def person_place(self, uid: str) -> tuple[str, str, str]:
        info = self.an.people_info.get(uid) or {}
        return info.get("city", ""), info.get("country", ""), info.get("job", "")

    def person_location(self, uid: str) -> str:
        city, country, _job = self.person_place(uid)
        return ", ".join(x for x in (city, country) if x)

    def key_display(self, k: dict[str, Any]) -> str:
        if k["kind"] == REMOTE:
            best = None
            for (v, _rule), var in k["variants"].items():
                if canonical_value(v) == k["key"] and (best is None or var["spend"] > best[1]):
                    best = (v, var["spend"])
            return best[0] if best else k["key"]
        if k["kind"] == AZURE:
            return "_git/" + k["name"]
        return k["name"] or k["key"]

    def key_owner(self, k: dict[str, Any]) -> str | None:
        """Whose repository it is, for per-person keys (folders, own-name, generic Azure names)."""
        if k["kind"] in (FOLDER, PERSONAL_KEY) or k["key"].startswith("azure:"):
            return self.person_name(k["uid"])
        return None

    def rule_text(self, rule: str, args: dict[str, Any]) -> str:
        if rule == "subfolder":
            return f"Subfolder: '{args['part']}' is the name of {args['target']}, which the same person uses"
        if rule == "folder_like_remote":
            return f"Local folder named like {args['target']}, which the same person uses"
        if rule == "personal_like_remote":
            return f"Own-name repository named like {args['target']}, which the same person uses"
        if rule == "local_folder":
            if re.fullmatch(r"[a-z]:", args.get("owner", "")):
                return f"Local folder on Windows drive {args['owner'].upper()}"
            return f"Local folder: '{args.get('owner')}' is a common folder name"
        if rule == "folder_like_owner":
            return f"Local folder: '{args.get('owner')}' is used as a folder by many people (detected)"
        if rule == "own_name":
            return f"Personal: owner '{args.get('owner')}' is the person's own name"
        return RULES.get(rule, (0, rule))[1]

    @staticmethod
    def signal_text(x: dict[str, Any]) -> str:
        a, sid = x["args"], x["id"]
        func = _SIGNAL_TEXT.get(sid)
        if func:
            return func(a)
        if sid.startswith("names."):
            return f"'{a['word']}' in the name"
        return ""


def pay_label(an: Analysis, project: str, charged: float, shared: float) -> str:
    if an.is_personal_project(project):
        return PERSONAL
    if charged > 0 and charged >= shared:
        return CHARGED
    return SHARED


def pair_payer(cb: float, sh: float, pe: float) -> str:
    """Who mostly paid for a person's work on one repository: on a tie a project budget first, then a shared
    project, then the personal budget; personal when nothing was billed."""
    amounts = {CHARGED: cb, SHARED: sh, PERSONAL: pe}
    top = max(amounts.values())
    if top <= 0:
        return PERSONAL
    return next(payer for payer in (CHARGED, SHARED, PERSONAL) if amounts[payer] == top)


def group_name(an: Analysis, labels: Labels, g: dict[str, Any]) -> str:
    if g.get("owner"):
        return g["owner"]
    anchor = an.K[g["anchor"]]
    display = labels.key_display(anchor)
    return display.split("/")[-1] if anchor["kind"] == REMOTE else display


# ---------------------------------------------------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------------------------------------------------
def build_summary(an: Analysis) -> dict[str, Any]:
    cm = an.cat_money
    cli = an.cli["billed"]
    web_types = (an.totals.get("web") or {}) if an.totals else {}
    web = sum(web_types.get(k, 0.0) for k in ("assistant", "workflow", "datasource")) if web_types else 0.0
    if web <= 0:
        web = an.web["total"]
    alloc = {c: an.allocated[c]["spend"] for c in an.allocated}
    allocated = sum(alloc.values())
    prod = cm[PRODUCTION]["spend"] + alloc.get(PRODUCTION, 0.0)
    exp_parts = {c: cm[c]["spend"] + alloc.get(c, 0.0) for c in EXPERIMENTS}
    exp = sum(exp_parts.values())
    uncl = max(0.0, cm[UNCLASSIFIED]["spend"] - allocated)
    uncl_parts = {k: max(0.0, v - an.allocated_reason.get(k, 0.0)) for k, v in an.unclassified.items()}
    likely = min(uncl, uncl_parts.get(LIKELY_PRODUCTION, 0.0))
    ncb_cli_shared = an.ncb_parts["cli_shared"]
    ncb_cli_personal = an.ncb_parts["cli_personal"] + an.ncb_parts["cli_personal_allocated"]
    ncb_web = an.web[SHARED]
    web_pay = {CHARGED: an.web[CHARGED], SHARED: an.web[SHARED], PERSONAL: an.web[PERSONAL]}
    # Web who-pays shares applied to the Q0 Web Platform total (records without a project are personal).
    web_pay[PERSONAL] += max(0.0, web - an.web["total"])
    tiles = {
        "total": r2(cli + web),
        "web": r2(web),
        "web_types": {k: r2(web_types.get(k, 0.0)) for k in ("assistant", "workflow", "datasource")},
        "cli": r2(cli),
        "cli_people": len(an.persons),
        "charged_back": r2(an.cli["cb"] + web_pay[CHARGED]),
        "prod": r2(prod),
        "prod_cb": r2(cm[PRODUCTION]["cb"] + an.allocated[PRODUCTION]["cb"]),
        "prod_alloc": r2(alloc.get(PRODUCTION, 0.0)),
        "exp": r2(exp),
        "exp_alloc": r2(sum(alloc.get(c, 0.0) for c in EXPERIMENTS)),
        "exp_parts": {c: r2(v) for c, v in exp_parts.items()},
        "allocated": r2(allocated),
        "ncb": r2(ncb_cli_shared + ncb_cli_personal + ncb_web),
        "ncb_cli_shared": r2(ncb_cli_shared),
        "ncb_cli_personal": r2(ncb_cli_personal),
        "ncb_allocated": r2(an.ncb_parts["cli_personal_allocated"]),
        "ncb_web": r2(ncb_web),
        "uncl": r2(uncl),
        # Part of Unclassified, shown apart: Production leads, on one kind of evidence or below the confidence.
        "likely_prod": r2(likely),
        "uncl_parts": {
            LIKELY_PRODUCTION: r2(likely),
            NO_REPOSITORY: r2(uncl_parts.get(NO_REPOSITORY, 0.0)),
            "too_little_evidence": r2(sum(uncl_parts.get(k, 0.0) for k in TOO_LITTLE_EVIDENCE_REASONS)),
            OUTSIDE_LIMITS: r2(uncl_parts.get(OUTSIDE_LIMITS, 0.0)),
        },
        "excluded": {
            "no_customer": r2(an.cli["nc"]),
            "other_proxy": r2((an.totals or {}).get("other_proxy", 0.0)),
        },
    }
    pie = [
        {"id": "platform", "label": "Web Platform", "value": r2(web)},
        {"id": PRODUCTION, "label": "CLI Production", "value": r2(prod)},
        {"id": LIKELY_PRODUCTION, "label": "CLI Likely production", "value": r2(likely)},
        {"id": "experiments", "label": "CLI Experiments", "value": r2(exp)},
        # The rest of Unclassified: likely production has its own slice, so this is less than the tile.
        {"id": UNCLASSIFIED, "label": "CLI Unclassified, other", "value": r2(max(0.0, uncl - likely))},
    ]
    who_pays = {
        "web": {k: r2(v) for k, v in web_pay.items()},
        "cli": {CHARGED: r2(an.cli["cb"]), SHARED: r2(an.cli["sh"]), PERSONAL: r2(an.cli["pe"])},
    }
    outside = an.unclassified.get(OUTSIDE_LIMITS, 0.0)
    analysed = max(0.0, cli - outside)
    return {
        "tiles": tiles,
        "pie": pie,
        "who_pays": who_pays,
        "categories": build_categories(an),
        "reasons": build_reasons(an),
        "signals": build_signals(an),
        "identity": build_identity_stats(an),
        "folder_like": [
            {"owner": o, "values": d["values"], "people": d["people"], "shared": d["shared"]}
            for o, d in sorted(an.identity.folder_like.items(), key=lambda kv: -kv[1]["people"])[:50]
        ],
        "ignored": [
            {"value": v or "(empty)", "spend": r2(d["spend"]), "people": len(d["people"])}
            for v, d in sorted(an.ignored.items(), key=lambda kv: -kv[1]["spend"])[:20]
        ],
        "confidence": build_confidence(an),
        "db": build_db_stats(an),
        "coverage": {
            "share": round(analysed / cli * 100.0, 2) if cli > 0 else 100.0,
            "analysed": r2(analysed),
            "outside_limits": r2(outside),
            "repositories_per_person": an.S["repositoriesPerPerson"],
        },
        "counts": {
            "people": len(an.persons),
            "web_people": len(an.web_people),
            "repository_values": len(an.values),
            "repositories": len(an.K),
            "groups": sum(1 for g in an.groups.values() if len(g["members"]) > 1),
            "group_edges": len(an.edges),
            "folder_like_owners": len(an.identity.folder_like),
            "shared_projects_with_cli": len(an.shared_cli),
        },
        "analysis_steps_ms": dict(an.timing),
    }


def build_categories(an: Analysis) -> list[dict[str, Any]]:
    moved: Counter = Counter()
    for a in an.allocated.values():
        moved.update(a)
    rows = []
    for c in CATEGORIES:
        m = an.cat_money[c]
        a = an.allocated.get(c) if c != UNCLASSIFIED else Counter({k: -v for k, v in moved.items()})
        a = a or Counter()
        users = an.cat_users.get(c, set()) | an.allocated_users.get(c, set())
        rows.append(
            {
                "category": c,
                "spend": r2(m["spend"] + a["spend"]),
                "classified": r2(m["spend"]),
                "allocated": r2(a["spend"]),
                "cb": r2(m["cb"] + a["cb"]),
                "sh": r2(m["sh"] + a["sh"]),
                "pe": r2(m["pe"] + a["pe"]),
                "users": len(users),
            }
        )
    return rows


def build_reasons(an: Analysis) -> list[dict[str, Any]]:
    keys = Counter(k["reason"] for k in an.K.values() if k["cat"] == UNCLASSIFIED)
    return [
        {
            "reason": reason,
            "label": REASONS.get(reason, reason),
            "repositories": keys.get(reason, 0),
            "spend": r2(spend),
            "allocated": r2(an.allocated_reason.get(reason, 0.0)),
            "remaining": r2(max(0.0, spend - an.allocated_reason.get(reason, 0.0))),
        }
        for reason, spend in an.unclassified.most_common()
    ]


def build_signals(an: Analysis) -> list[dict[str, Any]]:
    fired: dict[str, list] = defaultdict(lambda: [0, 0.0, 0])
    total = sum(k["billed"] for k in an.K.values()) or 1.0
    for k in an.K.values():
        for x in k["signals"]:
            f = fired[x["id"]]
            f[0] += 1
            f[1] += k["billed"]
            f[2] += 1 if k["cat"] == x["cat"] else 0
    rows = []
    for sid, (cat, pts) in an.S["weights"].items():
        f = fired.get(sid, [0, 0.0, 0])
        rows.append(
            {
                "id": sid,
                "label": SIGNAL_LABELS.get(sid, sid),
                "family": sid.split(".")[0],
                "category": cat,
                "points": pts,
                "repositories": f[0],
                "spend_share": round(f[1] / total * 100, 1),
                "won": f[2],
            }
        )
    return rows


def build_identity_stats(an: Analysis) -> list[dict[str, Any]]:
    rows = []
    for rule, rs in sorted(an.rule_stats.items(), key=lambda kv: (RULES.get(kv[0], (9,))[0], -kv[1]["spend"])):
        rows.append(
            {
                "rule": rule,
                "number": RULES.get(rule, (0,))[0],
                "label": RULES.get(rule, (0, rule))[1],
                "values": len(rs["values"]),
                "pairs": rs["pairs"],
                "repositories": len(rs["keys"]),
                "spend": r2(rs["spend"]),
            }
        )
    return rows


def build_confidence(an: Analysis) -> list[dict[str, Any]]:
    hist: dict[int, list] = defaultdict(lambda: [0, 0.0])
    for k in an.K.values():
        b = min(9, int(k["conf"] // 10))
        hist[b][0] += 1
        hist[b][1] += k["billed"]
    return [
        {"bucket": f"{b * 10}–{b * 10 + 9 if b < 9 else 100}", "repositories": hist[b][0], "spend": r2(hist[b][1])}
        for b in range(10)
    ]


def build_db_stats(an: Analysis) -> dict[str, Any]:
    return {
        "shared_projects": len(an.shared_projects),
        "budgeted_projects": len(an.budgets),
        "platform_budgets": sum(1 for b in an.budgets.values() if "platform" in b),
        "cli_budgets": sum(1 for b in an.budgets.values() if "cli" in b),
        "premium_budgets": sum(1 for b in an.budgets.values() if "premium_models" in b),
        "datasource_repositories": len(an.datasource_projects),
        "memberships": sum(len(v) for v in an.members.values()),
        "profiles": len(an.people_info),
        "datasource_matches": sum(
            1 for k in an.K.values() if any(x["id"] == "where.datasource" for x in k.get("signals", ()))
        ),
    }


# ---------------------------------------------------------------------------------------------------------------------
# Shared projects
# ---------------------------------------------------------------------------------------------------------------------
def _web_by_project(an: Analysis) -> dict[str, Counter]:
    """Per-project web spend by person, used for the "web people" list of a shared project's detail."""
    web_by_project: dict[str, Counter] = defaultdict(Counter)
    for uid, d in an.web_people.items():
        for project, amount in list((d.get("proj") or {}).items()) + list((d.get("fbp") or {}).items()):
            if not an.is_personal_project(project):
                web_by_project[project][uid] += amount
    return web_by_project


def _shared_project_web_state(web_cb: float, web_sh: float) -> str:
    if web_cb <= _MIN:
        return SHARED
    # Partial: the budget paid for some members, or for part of the period, and the rest was paid personally.
    return "partial" if web_sh > _MIN else CHARGED


def _shared_project_row(an: Analysis, project: str, cli: dict[str, Any]) -> dict[str, Any] | None:
    """The shared-projects table row for one project, or None when it has no not-charged-back money to show."""
    web_nofb = (an.web_proj.get(project) or {}).get("spend", 0.0)
    web_fb = (an.web_fb.get(project) or {}).get("spend", 0.0)
    web_sh = (an.web_project.get(project) or {}).get("sh", 0.0)
    web_cb = (an.web_project.get(project) or {}).get("cb", 0.0)
    web_total = web_nofb + web_fb
    prod = an.project_cli_prod.get(project) or {}
    ncb = web_sh + prod.get("potential_sh", 0.0)
    if ncb <= _MIN:
        return None
    cli_spend = cli.get("spend", 0.0)
    people = max(
        (an.web_proj.get(project) or {}).get("people", 0.0) + (an.web_fb.get(project) or {}).get("people", 0.0),
        cli.get("people_n", 0.0),
    )
    budgets = an.budgets.get(project) or {}
    info = an.project_info.get(project) or {}
    return {
        "id": project_row_id(project),
        "name": project,
        "platform": r2(web_total),
        "web_sh": r2(web_sh),
        "cli": r2(cli_spend),
        "cli_sh": r2(cli.get("sh", 0.0)),
        "cli_exp": r2(prod.get("exp_sh", 0.0)),
        "cli_potential": r2(prod.get("potential_sh", 0.0)),
        "ncb": r2(ncb),
        "people": int(people),
        "prod_pct": round(prod["prod"] / cli_spend * 100) if cli_spend and prod.get("known") else None,
        "web_state": _shared_project_web_state(web_cb, web_sh),
        # A platform budget also covers CLI usage when the project has no CLI budget.
        "cli_budget": bool({"cli", "platform"} & set(budgets)),
        "budgets": sorted(budgets),
        "cost_center": info.get("cc") or None,
        "chargeback": bool(info.get("chargeback")),
        "deleted": bool(info.get("deleted")),
        "members": len(an.members.get(project, ())),
        "datasources": an.datasource_count.get(project, 0),
        "via": [x for x, on in (("web", web_total > 0), ("cli", cli_spend > 0)) if on],
    }


def _shared_project_entry(
    an: Analysis, labels: Labels, project: str, web_by_project: dict[str, Counter]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The table row and popup detail for one shared project, or None when it has no money to show."""
    cli = an.shared_cli.get(project) or {}
    row = _shared_project_row(an, project, cli)
    if row is None:
        return None
    detail = _project_detail(an, labels, cli)
    web_people = web_by_project.get(project, Counter())
    detail["web_people"] = [
        {"uid": uid, "name": labels.person_name(uid), "location": labels.person_location(uid), "web": r2(amount)}
        for uid, amount in web_people.most_common(12)
    ]
    detail["web_people_total"] = len(web_people)
    return row, detail


def build_shared(an: Analysis, labels: Labels) -> tuple[list[dict], dict[int, dict]]:
    projects = set(an.shared_cli) | {p for p in an.web_proj if not an.is_personal_project(p)} | set(an.web_fb)
    rows: list[dict] = []
    details: dict[int, dict] = defaultdict(dict)
    web_by_project = _web_by_project(an)
    for project in projects:
        if an.is_personal_project(project):
            continue
        entry = _shared_project_entry(an, labels, project, web_by_project)
        if entry is None:
            continue
        row, detail = entry
        rows.append(row)
        details[shard_of(project)][project] = {"row": row, "detail": detail}
    rows.sort(key=lambda r: (-r["ncb"], r["name"]))
    return rows, details


def _project_detail(an: Analysis, labels: Labels, cli: dict[str, Any]) -> dict[str, Any]:
    sessionless = set(an.S.get("sessionlessClients") or [])
    repos = []
    for value, (spend, sh) in sorted((cli.get("repos") or {}).items(), key=lambda kv: -kv[1][0])[
        :_PROJECT_REPOS_IN_DETAIL
    ]:
        mix = an.value_mix.get(value) or Counter()
        cat = mix.most_common(1)[0][0] if mix else UNCLASSIFIED
        lv = canonical_value(value or "")
        repos.append(
            {
                "repository": value or "",
                # Desktop and IDE clients send no repository; ignored values (temporary folders, Cowork) neither.
                "no_repository": not lv or an.identity.is_ignored(lv),
                "category": cat,
                "spend": r2(spend),
                "paid_personally": r2(sh),
            }
        )
    people = [
        {"uid": uid, "name": labels.person_name(uid), "location": labels.person_location(uid), "cli": r2(d[0])}
        for uid, d in sorted((cli.get("people") or {}).items(), key=lambda kv: -kv[1][0])
    ]
    agents = [
        {"client": c, "sessions": None if c in sessionless else int(v[1]), "spend": r2(v[0])}
        for c, v in sorted((cli.get("clients") or {}).items(), key=lambda kv: -kv[1][0])[:6]
    ]
    models = [{"model": m, "spend": r2(a)} for m, a in Counter(cli.get("models") or {}).most_common(6)]
    return {
        "repos": repos,
        "cli_people": people,
        "agents": agents,
        "models": models,
        "cli_people_total": int(cli.get("people_n", 0)),
        "cli_fallback": r2(cli.get("fb", 0.0)),
        "cli_charged_back": r2(cli.get("cb", 0.0)),
    }


# ---------------------------------------------------------------------------------------------------------------------
# Repository groups
# ---------------------------------------------------------------------------------------------------------------------
def _group_reasons(an: Analysis, g: dict[str, Any], n_keys: int) -> tuple[list[str], str]:
    """Why the repositories are one group: the full list, and the short text of the table."""
    sig = g["signals"]
    why = [f"Same owner: {owner}" for owner, _n in sig["owner"].most_common(2)]
    why += [
        f"Ticket key {prefix}- in branches of {n} pairs of repositories" for prefix, n in sig["ticket"].most_common(3)
    ]
    if g["people_common"]:
        why.append(f"Up to {g['people_common']} people work on two of these repositories")
    why += [f"Same shared CodeMie project: {project}" for project, _n in sig["project"].most_common(2)]
    why += [f"Owner rule → {project}" for project, _n in sig["rule"].most_common(1)]
    why += [
        f"Git datasources of the same shared project: {project}" for project, _n in sig["datasource"].most_common(2)
    ]
    if g["oversized"]:
        why.append(f"Larger than {an.S['maxGroupSize']} repositories after the split by owner")
    grouped = " · ".join(
        ["owner " + o for o, _ in sig["owner"].most_common(1)]
        + ["ticket key " + x + "-" for x, _ in sig["ticket"].most_common(1)]
        + ([f"{g['people_common']} people in common"] if g["people_common"] else [])
        + ["project " + p for p, _ in sig["project"].most_common(1)]
        + ["datasources of " + p for p, _ in sig["datasource"].most_common(1)]
    ) or ("single repository" if n_keys == 1 else "linked repositories")
    return why or ["One repository"], grouped


def _group_repo(labels: Labels, k: dict[str, Any], m: dict[str, Any], display: str) -> dict[str, Any]:
    """One repository of a group popup: its money, category, the values it was identified from, and its signals."""
    return {
        "repository": display,
        "kind": k["kind"],
        "owner_person": labels.key_owner(k),
        "category": k["cat"],
        "confidence": round(k["conf"]),
        "reason": k["reason"],
        "people": m["people_n"],
        "sessions": int(k["tool"]["sessions"]),
        "spend": r2(m["billed"]),
        "ncb": r2(m["pe"] if k["cat"] == PRODUCTION else 0.0),
        "projects": _projects_label(k),
        "variants": [
            {
                "value": v,
                "rule": rule,
                "number": RULES.get(rule, (0, rule))[0],
                "text": labels.rule_text(rule, var["args"]),
                "people": len(var["people"]),
                "spend": r2(var["spend"]),
            }
            for (v, rule), var in sorted(k["variants"].items(), key=lambda kv: -kv[1]["spend"])[:8]
        ],
        "signals": [
            {
                "family": x["family"],
                "id": x["id"],
                "category": x["cat"],
                "points": round(x["eff"], 1),
                "text": labels.signal_text(x),
            }
            for x in k["signals"]
        ],
    }


def _group_people(money: list[dict[str, Any]]) -> tuple[Counter, Counter]:
    persons: Counter = Counter()
    person_repos: Counter = Counter()
    for m in money:
        for uid, amount in m["persons"].items():
            if amount > 0:
                persons[uid] += amount
                person_repos[uid] += 1
    return persons, person_repos


def _group_detail(
    an: Analysis,
    labels: Labels,
    keys: list[dict[str, Any]],
    money: list[dict[str, Any]],
    displays: list[str],
    persons: Counter,
    person_repos: Counter,
) -> dict[str, Any]:
    sessionless = set(an.S.get("sessionlessClients") or [])
    projects: Counter = Counter()
    projects_cb: Counter = Counter()
    models: Counter = Counter()
    agents: Counter = Counter()
    personal_total = 0.0
    cats: Counter = Counter()
    for k, m in zip(keys, money, strict=True):
        projects.update(k["projects"])
        projects_cb.update(k["projects_cb"])
        models.update(k["models"])
        agents.update(k["clients"])
        personal_total += k["personal_spend"]
        cats[k["cat"]] += m["billed"]
    personal_row = [{"project": None, "pay": PERSONAL, "spend": r2(personal_total)}] if personal_total > _MIN else []
    return {
        "categories": {c: r2(v) for c, v in cats.items()},
        "repos": [
            _group_repo(labels, k, m, display)
            for k, m, display in list(zip(keys, money, displays, strict=True))[:_GROUP_REPOS_IN_DETAIL]
        ],
        "models": [{"model": x, "spend": r2(a)} for x, a in models.most_common(6)],
        "agents": [{"client": c, "spend": r2(a), "sessionless": c in sessionless} for c, a in agents.most_common(6)],
        "projects": personal_row
        + [
            {"project": p, "pay": CHARGED if projects_cb[p] > 0 else SHARED, "spend": r2(a)}
            for p, a in projects.most_common(8)
        ],
        "people": [
            {
                "uid": uid,
                "name": labels.person_name(uid),
                "location": labels.person_location(uid),
                "repositories": person_repos[uid],
                "spend": r2(a),
            }
            for uid, a in persons.most_common(25)
        ],
        "sessions": int(sum(k["tool"]["sessions"] for k in keys)),
    }


def build_groups(an: Analysis, labels: Labels) -> tuple[list[dict], dict[int, dict]]:
    """Repository groups with money in the range: every group of two or more, and single repositories with
    production CLI work in personal projects (a display threshold may make them candidates)."""
    rows: list[dict] = []
    details: dict[int, dict] = defaultdict(dict)
    for g in an.groups.values():
        keys = [an.K[m] for m in g["members"]]
        money = [an.key_money(k["key"]) for k in keys]
        spend = sum(m["billed"] for m in money)
        if spend <= 0:
            continue
        prod = sum(m["billed"] for k, m in zip(keys, money, strict=True) if k["cat"] == PRODUCTION)
        ncb = sum(m["pe"] for k, m in zip(keys, money, strict=True) if k["cat"] == PRODUCTION)
        excluded = any(k["excluded"] for k in keys)
        if len(keys) == 1 and (ncb <= _MIN or excluded):
            continue
        why, grouped = _group_reasons(an, g, len(keys))
        related, related_reason, related_text = related_project(an, keys, g)
        persons, person_repos = _group_people(money)
        name = group_name(an, labels, g)
        displays = [labels.key_display(k) for k in keys]
        row = {
            "id": g["id"],
            "name": name,
            "grouped": grouped,
            "repositories": len(keys),
            "people": len(persons),
            "spend": r2(spend),
            "prod": r2(prod),
            "ncb": r2(ncb),
            "prod_pct": round(prod / spend * 100) if spend else 0,
            "excluded": excluded,
            "repos_preview": displays[:6],
            "related": related,
            "related_reason": related_reason,
        }
        rows.append(dict(row, search=" ".join([name] + displays[:_GROUP_REPOS_IN_DETAIL]).lower()))
        detail = _group_detail(an, labels, keys, money, displays, persons, person_repos)
        detail.update(
            why=why,
            related={"project": related, "reason": related_reason, "text": related_text},
            oversized=g["oversized"],
        )
        details[shard_of(g["id"])][g["id"]] = {"row": row, "detail": detail}
    rows.sort(key=lambda r: (-r["ncb"], -r["spend"], r["id"]))
    return rows, details


def _projects_label(k: dict[str, Any]) -> str:
    personal_people = len([u for u, a in k["persons"].items() if a > 0]) if k["personal_spend"] > _MIN else 0
    parts = [f"{personal_people} personal"] if personal_people else []
    parts += [p for p, _ in k["projects"].most_common(2)]
    return " · ".join(parts)


# ---------------------------------------------------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------------------------------------------------
def _person_work_projects(an: Analysis, uid: str, p: dict[str, Any]) -> Counter:
    """Every project the person has money in, CLI and Web Platform, personal-budget keys included."""
    projects: Counter = Counter()
    for project, d in (p.get("projects") or {}).items():
        projects[project] += d[0]
    wp = an.web_people.get(uid) or {}
    for project, amount in (wp.get("proj") or {}).items():
        projects[project] += amount
    for project, amount in (wp.get("fbp") or {}).items():
        projects[project] += amount
    return projects


def _person_production_days(an: Analysis, uid: str, pairs: list[tuple]) -> int:
    """The most active production repository paid personally (tool evidence, per person and repository value). A
    repository's active days count by the share of its money paid personally, rounded half up: 20 days on a
    repository a project pays 75% of are 5 production days. Work without a repository and work charged back to a
    project add none."""
    production_days = 0
    for v, _key, cat, billed, pair in pairs:
        if cat != PRODUCTION or billed <= 0:
            continue
        personal = pair[2] + max(0.0, billed - pair[1] - pair[2])
        if personal <= _MIN:
            continue
        days = int(an.tool.get((uid, v), {}).get("days", 0) * personal / billed + 0.5)
        production_days = max(production_days, days)
    return production_days


def _person_who_pays(
    an: Analysis, uid: str, p: dict[str, Any], web: Counter, total: float, pairs: list[tuple]
) -> tuple[float, float, float, float]:
    """Who pays, in positive terms: Total Spend = charged back + production, learning and experiments, and other
    spend paid from personal budgets (other: unclassified CLI and Web Platform)."""
    paid_alloc = an.person_alloc_paid.get(uid) or Counter()
    # Production CLI paid personally: in shared projects without a budget, or in personal projects.
    prod_ncb = sum(
        pair[2] + max(0.0, billed - pair[1] - pair[2]) for _v, _key, cat, billed, pair in pairs if cat == PRODUCTION
    )
    prod_ncb += paid_alloc.get(PRODUCTION, 0.0)
    charged_back = (p.get("cb") or 0.0) + web.get(CHARGED, 0.0)
    learning = sum(
        pair[2] + max(0.0, billed - pair[1] - pair[2]) for _v, _key, cat, billed, pair in pairs if cat in EXPERIMENTS
    )
    learning += sum(paid_alloc.get(c, 0.0) for c in EXPERIMENTS)
    other = max(0.0, total - charged_back - prod_ncb - learning)
    return prod_ncb, charged_back, learning, other


def _person_row(
    an: Analysis, labels: Labels, uid: str, group_names: dict[str, str]
) -> tuple[dict[str, Any], dict[str, Any], Counter, list[tuple]] | None:
    """The people-table row for one person, plus the inputs its popup detail needs, or None when they have no
    money to show."""
    cats = an.person_cat.get(uid) or Counter()
    web = an.person_web.get(uid) or Counter()
    p = an.persons.get(uid) or {}
    cli = sum(cats.values())
    total = cli + web.get("total", 0.0)
    if total <= 0.005:
        return None
    ncb = an.person_ncb_cli.get(uid, 0.0) + web.get(SHARED, 0.0)
    projects = _person_work_projects(an, uid, p)
    shared_projects = [x for x, _ in projects.most_common(4) if not an.is_personal_project(x)]
    works_in = " · ".join((["personal"] if any(an.is_personal_project(x) for x in projects) else []) + shared_projects)
    pairs = sorted(an.person_pairs.get(uid, []), key=lambda r: -r[3])
    main = next((r for r in pairs if r[1]), None)
    al = an.person_alloc.get(uid) or Counter()
    prod_ncb, charged_back, learning, other = _person_who_pays(an, uid, p, web, total, pairs)
    production_days = _person_production_days(an, uid, pairs)
    city, country, job = labels.person_place(uid)
    main_key = an.K.get(main[1]) if main else None
    row = {
        "uid": uid,
        "name": labels.person_name(uid),
        "email": labels.person_email(uid),
        "city": city,
        "country": country,
        "job": job,
        "total": r2(total),
        "ncb": r2(ncb),
        "platform": r2(web.get("total", 0.0)),
        "cli": r2(cli),
        "prod": r2(cats.get(PRODUCTION, 0.0) + al[PRODUCTION]),
        "exp": r2(sum(cats.get(c, 0.0) + al[c] for c in EXPERIMENTS)),
        "uncl": r2(max(0.0, cats.get(UNCLASSIFIED, 0.0) - sum(al.values()))),
        "alloc": r2(sum(al.values())),
        "prod_ncb": r2(prod_ncb),
        "charged_back": r2(charged_back),
        "personal_learning": r2(learning),
        "personal_other": r2(other),
        "production_days": production_days,
        "shared_no_budget": r2((p.get("sh") or 0.0) + web.get(SHARED, 0.0)),
        "projects": works_in or "—",
        "in_shared_project": bool(shared_projects),
        "main_repo": labels.key_display(main_key) if main_key else None,
        "main_group": group_names.get(main_key["group"]) if main_key else None,
    }
    return row, p, projects, pairs


def build_people(an: Analysis, labels: Labels, group_names: dict[str, str]) -> tuple[list[dict], dict[int, dict]]:
    uids = set(an.persons) | set(an.person_web)
    rows: list[dict] = []
    details: dict[int, dict] = defaultdict(dict)
    for uid in uids:
        result = _person_row(an, labels, uid, group_names)
        if result is None:
            continue
        row, p, projects, pairs = result
        rows.append(row)
        details[shard_of(uid)][uid] = {
            "row": row,
            "detail": _person_detail(an, labels, uid, p, pairs, projects, group_names),
        }
    rows.sort(key=lambda r: (-r["total"], r["uid"]))
    return rows, details


def _person_detail_projects(an: Analysis, uid: str, p: dict[str, Any], projects: Counter) -> list[dict[str, Any]]:
    """Per-project row of a person popup: how much web and CLI money, and who mostly paid for it."""
    wp = an.web_people.get(uid) or {}
    proj_rows = []
    for project in projects:
        cli_d = (p.get("projects") or {}).get(project) or [0.0, 0.0, 0.0]
        web_amount = (wp.get("proj") or {}).get(project, 0.0)
        web_fb = (wp.get("fbp") or {}).get(project, 0.0)
        if an.is_personal_project(project):
            pay = PERSONAL
        elif cli_d[1] > 0 or web_funded(an, project, uid) > _MIN:
            pay = CHARGED
        else:
            pay = SHARED
        proj_rows.append({"project": project, "pay": pay, "web": r2(web_amount + web_fb), "cli": r2(cli_d[0])})
    proj_rows.sort(key=lambda r: -(r["web"] + r["cli"]))
    return proj_rows


def _person_repo_row(
    an: Analysis, labels: Labels, group_names: dict[str, str], uid: str, pair_row: tuple
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """One repository row of a person popup, plus its tool evidence and resolved key (for callers that also build
    the by-category totals and the "why" reasons from the same pair)."""
    v, key, cat, billed, pair = pair_row
    ev = an.tool.get((uid, v)) or {}
    k = an.K.get(key) if key else None
    rule = an.resolution(uid, v)[2]
    cb, sh = pair[1], pair[2]
    pe = max(0.0, billed - cb - sh)
    row = {
        "value": v,
        "repository": labels.key_display(k) if k else None,
        "group_id": k["group"] if k and len(an.groups.get(k["group"], {}).get("members", ())) > 1 else None,
        "group": group_names.get(k["group"]) if k else None,
        "category": cat,
        "sessions": int(ev.get("sessions", 0)),
        "spend": r2(billed),
        "net_lines": int(ev.get("la", 0) - ev.get("lr", 0)),
        "rule": rule,
        "rule_text": RULES.get(rule, (0, rule))[1],
        "pay": pair_payer(cb, sh, pe),
    }
    return row, ev, k


def _person_repo_reason(
    labels: Labels, k: dict[str, Any] | None, key: str | None, billed: float, reasons: list, seen_keys: set
) -> None:
    """Append the "why" reason this repository/key explains, up to 5, or the no-repository reason once."""
    if k and key not in seen_keys and len(reasons) < 5:
        seen_keys.add(key)
        if k["cat"] == UNCLASSIFIED:
            text = f"{labels.key_display(k)}: {REASONS.get(k['reason'], '').lower()}"
        else:
            top = sorted(k["signals"], key=lambda x: -x["eff"])[:3]
            text = f"{labels.key_display(k)}: " + ", ".join(SIGNAL_LABELS.get(x["id"], x["id"]).lower() for x in top)
        reasons.append(
            {"kind": "negative" if k["cat"] == UNCLASSIFIED else "positive", "text": text, "category": k["cat"]}
        )
    elif key is None and billed > _MIN and not any(r.get("reason") == NO_REPOSITORY for r in reasons):
        reasons.append(
            {
                "kind": "neutral",
                "text": "Work without a repository (desktop and IDE clients, ignored folders)",
                "category": UNCLASSIFIED,
                "reason": NO_REPOSITORY,
            }
        )


def _person_repos_and_categories(
    an: Analysis, labels: Labels, uid: str, pairs: list[tuple], group_names: dict[str, str]
) -> tuple[list[dict[str, Any]], dict[str, list], list[dict[str, Any]]]:
    """Every repository row of a person popup, the by-category totals they roll up into, and the "why" reasons
    collected along the way."""
    repos = []
    bycat: dict[str, list] = defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    reasons: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for pair_row in pairs:
        _v, key, cat, billed, _pair = pair_row
        row, ev, k = _person_repo_row(an, labels, group_names, uid, pair_row)
        repos.append(row)
        b = bycat[cat]
        b[0] += int(ev.get("sessions", 0))
        b[1] += billed
        b[2] += ev.get("prompts", 0)
        b[3] += ev.get("la", 0) - ev.get("lr", 0)
        _person_repo_reason(labels, k, key, billed, reasons, seen_keys)
    return repos, bycat, reasons


def _person_extra_reasons(an: Analysis, uid: str, pairs: list[tuple], al: Counter, reasons: list) -> None:
    """Reasons that are not about one repository: allocation from classified work, spend outside the analysis
    limits, and spend on repositories used after the evidence was read."""
    if sum(al.values()) > _MIN:
        parts = ", ".join(f"{c.replace('_', ' ')} ${v:,.0f}" for c, v in al.most_common() if v >= _MIN)
        top_cat = al.most_common(1)[0][0]
        reasons.append(
            {
                "kind": "positive",
                "text": f"${sum(al.values()):,.0f} of unclassified spend allocated by this person's classified work: "
                f"{parts}",
                "category": top_cat,
            }
        )
    rest = (an.person_uncl_reason.get(uid) or Counter()).get(OUTSIDE_LIMITS, 0.0)
    if rest > _MIN:
        reasons.append(
            {
                "kind": "negative",
                "text": f"${rest:,.0f} outside the analysis limits (beyond the top {an.S['repositoriesPerPerson']} "
                "repositories)",
                "category": UNCLASSIFIED,
            }
        )
    # Not beyond the limits: the evidence, reused from earlier in the hour, was read before these were first used.
    new = sum(billed for _v, key, _cat, billed, _pair in pairs if an.after_evidence(key))
    if new > _MIN:
        reasons.append(
            {
                "kind": "negative",
                "text": f"${new:,.0f} on repositories first used after the evidence was read: no evidence yet",
                "category": UNCLASSIFIED,
            }
        )


def _person_detail(
    an: Analysis,
    labels: Labels,
    uid: str,
    p: dict[str, Any],
    pairs: list[tuple],
    projects: Counter,
    group_names: dict[str, str],
) -> dict[str, Any]:
    proj_rows = _person_detail_projects(an, uid, p, projects)
    repos, bycat, reasons = _person_repos_and_categories(an, labels, uid, pairs, group_names)
    al = an.person_alloc.get(uid) or Counter()
    _person_extra_reasons(an, uid, pairs, al, reasons)
    sessions = int(sum(v[0] for v in bycat.values()))
    return {
        "projects": proj_rows,
        "repos": repos[:_PERSON_REPOS_IN_DETAIL],
        "by_category": [
            {
                "category": c,
                "sessions": v[0],
                "spend": r2(v[1]),
                "avg_prompts": round(v[2] / v[0], 1) if v[0] else 0,
                "net_lines": int(v[3]),
            }
            for c, v in sorted(bycat.items(), key=lambda kv: -kv[1][1])
        ],
        "reasons": reasons,
        "allocated": {c: r2(v) for c, v in al.items() if v > 0},
        "sessions": sessions,
        "net_lines": int(sum(v[3] for v in bycat.values())),
        "cli_charged_back": r2(p.get("cb", 0.0)),
        "cli_shared_no_budget": r2(p.get("sh", 0.0)),
        "cli_no_customer": r2(p.get("nc", 0.0)),
    }


# ---------------------------------------------------------------------------------------------------------------------
# Breakdowns
# ---------------------------------------------------------------------------------------------------------------------
def _breakdown_add_person(an: Analysis, out: dict[str, dict[str, Counter]], uid: str, pairs: list[tuple]) -> None:
    """Add one person's personally-paid CLI spend into the country and job counters, by category."""
    info = an.people_info.get(uid) or {}
    country, job = info.get("country") or "(unknown)", info.get("job") or "(unknown)"
    p = an.persons[uid]
    covered = 0.0
    for _v, _key, cat, billed, pair in pairs:
        paid = pair[2] + max(0.0, billed - pair[1] - pair[2])
        covered += paid
        out["country"][cat][country] += paid
        out["job"][cat][job] += paid
    rest = max(0.0, (p["spend"] - p["nc"] - p["cb"]) - covered)
    out["country"][UNCLASSIFIED][country] += rest
    out["job"][UNCLASSIFIED][job] += rest
    for cat, amount in (an.person_alloc_paid.get(uid) or {}).items():
        for dim, label in (("country", country), ("job", job)):
            out[dim][cat][label] += amount
            out[dim][UNCLASSIFIED][label] -= amount


def _breakdown_rows(counter: Counter) -> list[dict[str, Any]]:
    """The top 12 labels of one country/job counter, with the rest folded into an "Other" row."""
    items = sorted(((label, a) for label, a in counter.items() if a > _MIN), key=lambda x: -x[1])
    top, rest_items = items[:12], items[12:]
    rows = [{"label": label, "spend": r2(a)} for label, a in top]
    rest = sum(a for _, a in rest_items)
    if rest > _MIN:
        rows.append({"label": "Other", "spend": r2(rest)})
    return rows


def build_breakdown(an: Analysis) -> dict[str, Any]:
    """CLI spend paid from personal budgets by country and job title group (app DB profiles)."""
    out: dict[str, dict[str, Counter]] = {"country": defaultdict(Counter), "job": defaultdict(Counter)}
    for uid, pairs in an.person_pairs.items():
        _breakdown_add_person(an, out, uid, pairs)
    return {
        dim: {cat: _breakdown_rows(per_cat.get(cat) or Counter()) for cat in CATEGORIES} for dim, per_cat in out.items()
    }


# ---------------------------------------------------------------------------------------------------------------------
# All sections
# ---------------------------------------------------------------------------------------------------------------------
def build_sections(an: Analysis) -> dict[str, Any]:
    """Every section of the cached analysis. Detail sections are dicts of shard number -> {entity id: detail}."""
    labels = Labels(an)
    shared_rows, shared_details = build_shared(an, labels)
    group_rows, group_details = build_groups(an, labels)
    group_names = {g["id"]: group_name(an, labels, g) for g in an.groups.values() if len(g["members"]) > 1}
    people_rows, person_details = build_people(an, labels, group_names)
    summary = build_summary(an)
    summary["counts"]["shared_projects_listed"] = len(shared_rows)
    summary["counts"]["groups_listed"] = len(group_rows)
    summary["counts"]["people_listed"] = len(people_rows)
    return {
        SECTION_SUMMARY: summary,
        SECTION_SHARED: shared_rows,
        SECTION_GROUPS: group_rows,
        SECTION_PEOPLE: people_rows,
        SECTION_PEOPLE_WORK: build_people_work(an, labels, group_names),
        SECTION_BREAKDOWN: build_breakdown(an),
        SECTION_SHARED_DETAIL: dict(shared_details),
        SECTION_GROUP_DETAIL: dict(group_details),
        SECTION_PERSON_DETAIL: dict(person_details),
    }
