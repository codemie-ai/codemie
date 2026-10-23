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

"""The Usage Attribution analysis: identity, repository keys, groups, classification, money and allocation.

It runs in the worker process over the compact inputs (see ``parsing``). Two windows may be involved:

- **money**: exactly the selected range (``persons``); every dollar amount comes from it;
- **evidence**: at least ``minimumEvidenceDays`` ending at the range end (``ev_persons``, ``values``, ``tool``,
  ``sess``); categories, identity contexts and groups come from it. Reused from earlier in the hour, it ends up to
  45 minutes before the range: a repository first used after that has no evidence yet.

When the range is long enough, both are the same data. ``K`` holds the repositories built from the evidence window
(used to classify), ``KM`` the money of each repository in the selected range (used to display).
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter, defaultdict
from typing import Any

from codemie.service.usage_attribution import classifier, grouping, money
from codemie.service.usage_attribution.constants import NO_EVIDENCE, NO_REPOSITORY, OUTSIDE_LIMITS, UNCLASSIFIED
from codemie.service.usage_attribution.identity import (
    AZURE,
    REMOTE,
    IdentityRules,
    glob_any,
)

logger = logging.getLogger(__name__)

TICKET_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]{1,11})-(\d{1,6})(?!\d)")


def ticket_prefixes(branch: str | None, generic: set[str]) -> set[str]:
    found = set()
    for prefix, _number in TICKET_RE.findall(branch or ""):
        up = prefix.upper()
        if up in generic or sum(ch.isalpha() for ch in up) < 2:
            continue
        found.add(up)
    return found


class Analysis:
    """State of one analysis run. Steps run in order from ``run()``."""

    def __init__(self, inputs: dict[str, Any], settings: dict[str, Any]):
        self.S = settings
        self.inputs = inputs
        self.stats: Counter = Counter()
        self.timing: dict[str, float] = {}
        self._setup_settings(settings)
        self._setup_window(inputs)
        self._setup_app_db(inputs.get("app_db") or {})
        self._setup_people(inputs)
        self._setup_web(inputs)

    def _setup_settings(self, settings: dict[str, Any]) -> None:
        self.identity = IdentityRules(settings)
        self.generic_tickets = {x.upper() for x in settings["genericTicketPrefixes"]}
        self.name_res = {
            cat: re.compile(r"(?:^|[^a-z0-9])(?:" + pat + r")(?:$|[^a-z0-9])")
            for cat, pat in settings["namePatterns"].items()
        }

    def _setup_window(self, inputs: dict[str, Any]) -> None:
        window = inputs.get("window") or {}
        self.days = float(window.get("days") or 30.0)
        self.scale = self.days / 30.0

    def _setup_people(self, inputs: dict[str, Any]) -> None:
        self.persons: dict[str, dict] = inputs.get("persons") or {}
        ev = inputs.get("ev_persons")
        self.ev_persons: dict[str, dict] = ev if ev is not None else self.persons
        self.same_windows = ev is None
        self.values: dict[str, dict] = inputs.get("values") or {}
        self.tool = self._build_tool(inputs.get("tool") or [])
        self.sess = self._build_sess(inputs.get("sess") or [])

    @staticmethod
    def _build_tool(rows: list) -> dict[tuple[str, str], dict]:
        tool: dict[tuple[str, str], dict] = {}
        for r in rows:
            tool[(r[0], r[1])] = {
                "sessions": r[2],
                "edit": r[3],
                "work": r[4],
                "mr": r[5],
                "la": r[6],
                "lr": r[7],
                "prompts": r[8],
                "days": r[9],
                "last": r[10],
            }
        return tool

    @staticmethod
    def _build_sess(rows: list) -> dict[tuple[str, str], dict]:
        sess: dict[tuple[str, str], dict] = {}
        for r in rows:
            sess[(r[0], r[1])] = {"sessions": r[2], "mcp_any": r[3], "mcp_work": r[4], "framework": r[5]}
        return sess

    def _setup_web(self, inputs: dict[str, Any]) -> None:
        self.web_proj: dict[str, dict] = inputs.get("web_proj") or {}
        self.web_fb: dict[str, dict] = inputs.get("web_fb") or {}
        self.web_people: dict[str, dict] = inputs.get("web_people") or {}
        # {project: {user id: [spend, charged back]}}: records without fallback, funding.py (A9).
        self.web_funding: dict[str, dict[str, list[float]]] = inputs.get("web_funding") or {}
        self.shared_cli: dict[str, dict] = inputs.get("shared_cli") or {}
        self.totals: dict[str, Any] = inputs.get("totals") or {}

    # ------------------------------------------------------------------ app DB facts
    def _setup_app_db(self, db: dict[str, Any]) -> None:
        self.shared_projects: set[str] = set(db.get("shared") or [])
        self.project_info = self._build_project_info(db.get("projects") or [])
        self.budgets = self._build_budgets(db.get("budgets") or [])
        self.datasource_projects = self._build_datasource_projects(db.get("datasources") or [])
        self.datasource_count: Counter = Counter(p for projects in self.datasource_projects.values() for p in projects)
        self.members, self.member_of = self._build_memberships(db.get("members") or [])
        self.people_info = self._build_people_info(db.get("people") or [])
        self.has_app_db = bool(db)

    @staticmethod
    def _build_project_info(rows: list) -> dict[str, dict]:
        project_info: dict[str, dict] = {}
        for name, deleted, chargeback, attribution, cost_center in rows:
            project_info[name] = {
                "deleted": bool(deleted),
                "chargeback": bool(chargeback),
                "attribution": attribution or "",
                "cc": cost_center or "",
            }
        return project_info

    @staticmethod
    def _build_budgets(rows: list) -> dict[str, dict[str, dict]]:
        budgets: dict[str, dict[str, dict]] = {}
        for project, category, mode, assigned_at, max_budget, duration in rows:
            budgets.setdefault(project, {})[category] = {
                "mode": mode,
                "since": assigned_at,
                "max": max_budget,
                "duration": duration,
            }
        return budgets

    @staticmethod
    def _build_datasource_projects(rows: list) -> dict[str, set[str]]:
        # Already normalized by the loader (app_db.datasource_rows): owner/repo or _git/<repo>. Normalizing it again
        # would read the owner as a host and lose every datasource.
        datasource_projects: dict[str, set[str]] = defaultdict(set)
        for project, key in rows:
            if key:
                datasource_projects[key].add(project)
        return datasource_projects

    @staticmethod
    def _build_memberships(rows: list) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
        members: dict[str, set[str]] = defaultdict(set)
        member_of: dict[str, set[str]] = defaultdict(set)
        for uid, project in rows:
            members[project].add(uid)
            member_of[uid].add(project)
        return members, member_of

    @staticmethod
    def _build_people_info(rows: list) -> dict[str, dict]:
        people_info: dict[str, dict] = {}
        for row in rows:
            uid, name, first, last, country, city, job, skill = row[:8]
            if uid in people_info:
                continue
            email = row[8] if len(row) > 8 else ""
            full = (f"{first or ''} {last or ''}").strip() or (name or "")
            people_info[uid] = {
                "name": full,
                "email": email or "",
                "country": country or "",
                "city": city or "",
                "job": job or "",
                "skill": skill or "",
            }
        return people_info

    def is_personal_project(self, project: str | None) -> bool:
        """A personal project: any project the app DB does not list as shared (and no project at all)."""
        return not project or project not in self.shared_projects

    # ------------------------------------------------------------------ run
    def run(self) -> Analysis:
        t0 = time.perf_counter()
        for name, step in (
            ("folder_like", self.detect_folder_like),
            ("identity", self.resolve_identities),
            ("keys", self.build_keys),
            ("groups", lambda: grouping.build_groups(self)),
            ("classify", lambda: classifier.classify(self)),
            ("money", lambda: money.compute_money(self)),
        ):
            st = time.perf_counter()
            step()
            self.timing[name] = round((time.perf_counter() - st) * 1000.0, 1)
        self.timing["total_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        logger.info(
            "usage attribution analysis done in %.2f s: %d people, %d repository values, %d repositories, %d groups",
            self.timing["total_ms"] / 1000.0,
            len(self.persons),
            len(self.values),
            len(self.K),
            sum(1 for g in self.groups.values() if len(g["members"]) > 1),
        )
        return self

    # ------------------------------------------------------------------ identity
    def detect_folder_like(self) -> None:
        self.identity.detect_folder_like(self.ev_persons)

    def _context(self, uid: str) -> dict[str, Any]:
        ctx = self.ctx.get(uid)
        if ctx is None:
            p = self.persons.get(uid) or {}
            values = [v for v, _ in sorted((p.get("pairs") or {}).items(), key=lambda kv: -kv[1][0])]
            ctx = self.identity.person_context(p.get("name") or "", values)
            self.ctx[uid] = ctx
        return ctx

    def resolve_identities(self) -> None:
        """Identity contexts come from the evidence window, which contains the money window."""
        self.ctx: dict[str, dict] = {}
        self.res: dict[tuple[str, str], tuple] = {}
        for uid, p in self.ev_persons.items():
            values = [v for v, _ in sorted(p["pairs"].items(), key=lambda kv: -kv[1][0])]
            ctx = self.identity.person_context(p["name"], values)
            self.ctx[uid] = ctx
            for v in values:
                self.res[(uid, v)] = self.identity.resolve(uid, v, ctx)
        if not self.same_windows:
            for uid, p in self.persons.items():
                ctx = self._context(uid)
                for v in p["pairs"]:
                    if (uid, v) not in self.res:
                        self.res[(uid, v)] = self.identity.resolve(uid, v, ctx)

    def resolution(self, uid: str, value: str) -> tuple:
        res = self.res.get((uid, value))
        if res is None:
            res = self.identity.resolve(uid, value, self._context(uid))
            self.res[(uid, value)] = res
        return res

    def key_for(self, uid: str, value: str) -> str | None:
        return self.resolution(uid, value)[0]

    # ------------------------------------------------------------------ repositories (keys)
    def new_key(self, key: str, kind: str) -> dict[str, Any]:
        owner = name = None
        if kind == REMOTE and "/" in key:
            owner, name = key.split("/", 1)
        elif kind == AZURE:
            name = key.rsplit(":", 1)[-1] if key.startswith("azure:") else key[5:]
        else:
            name = key.split(":", 2)[-1]
        return {
            "key": key,
            "kind": kind,
            "owner": owner,
            "name": name,
            "uid": key.split(":", 2)[1] if key.startswith(("folder:", "personal:", "azure:")) else None,
            "spend": 0.0,
            "cb": 0.0,
            "sh": 0.0,
            "nc": 0.0,
            "persons": Counter(),
            "variants": {},
            "branches": Counter(),
            "clients": Counter(),
            "models": Counter(),
            "projects": Counter(),
            "projects_cb": Counter(),
            "personal_spend": 0.0,
            "tool": {
                "sessions": 0.0,
                "edit": 0.0,
                "work": 0.0,
                "mr": 0.0,
                "la": 0.0,
                "lr": 0.0,
                "prompts": 0.0,
                "days_max": 0.0,
                "person_days": 0.0,
                "last": None,
            },
            "sess": {"sessions": 0.0, "mcp_any": 0.0, "mcp_work": 0.0, "framework": 0.0},
            "excluded": kind == REMOTE and glob_any(key, self.S["excludedRepositories"]),
            "learning": kind == REMOTE and glob_any(key, self.S["learningSources"]),
            "group": None,
        }

    def build_keys(self) -> None:
        """Repositories of the evidence window, with their evidence; ``KM`` holds their money in the range."""
        repos: dict[str, dict] = {}
        self.ignored: dict[str, dict] = {}
        self.rule_stats: dict[str, dict] = defaultdict(
            lambda: {"values": set(), "pairs": 0, "spend": 0.0, "keys": set()}
        )
        for uid, p in self.ev_persons.items():
            for v, pair in p["pairs"].items():
                self._add_pair(repos, uid, v, pair)
        self._attach_evidence(repos)
        for k in repos.values():
            k["billed"] = k["spend"] - k["nc"]
            k["pe"] = max(0.0, k["billed"] - k["cb"] - k["sh"])
            k["people_n"] = sum(1 for a in k["persons"].values() if a > 0)
            k["tickets"] = Counter()
            for branch, amount in k["branches"].items():
                for prefix in ticket_prefixes(branch, self.generic_tickets):
                    k["tickets"][prefix] += amount
        self.K = repos
        self.KM = self._money_keys()

    def _add_pair(self, repos: dict[str, dict], uid: str, v: str, pair: list[float]) -> None:
        key, kind, rule, args = self.res[(uid, v)]
        spend, cb, sh, nc = pair
        billed = spend - nc
        rs = self.rule_stats[rule]
        rs["values"].add(v.lower())
        rs["pairs"] += 1
        rs["spend"] += billed
        if key is None:
            ig = self.ignored.setdefault(v, {"spend": 0.0, "people": set()})
            ig["spend"] += billed
            ig["people"].add(uid)
            return
        rs["keys"].add(key)
        k = repos.get(key)
        if k is None:
            k = repos[key] = self.new_key(key, kind)
        k["spend"] += spend
        k["cb"] += cb
        k["sh"] += sh
        k["nc"] += nc
        k["persons"][uid] += spend
        var = k["variants"].setdefault((v, rule), {"args": args, "people": set(), "spend": 0.0})
        var["people"].add(uid)
        var["spend"] += billed
        vv = self.values.get(v)
        if vv and vv["spend"] > 0:
            # A repository value's branches, clients, models and projects are split between people by spend.
            share = min(1.0, spend / vv["spend"])
            k["personal_spend"] += vv["personal"] * share
            for fld in ("branches", "clients", "models", "projects", "projects_cb"):
                for x, amount in vv[fld].items():
                    k[fld][x] += amount * share

    def _attach_evidence(self, repos: dict[str, dict]) -> None:
        """CLI tool and session metrics of each (person, value), added to the repository they resolve to."""
        for (uid, v), ev in self.tool.items():
            self._attach_tool_evidence(repos, uid, v, ev)
        for (uid, v), ev in self.sess.items():
            self._attach_sess_evidence(repos, uid, v, ev)

    def _repo_for_evidence(self, repos: dict[str, dict], uid: str, v: str, miss_stat: str) -> dict | None:
        key = self.key_for(uid, v)
        k = repos.get(key) if key else None
        if k is None:
            self.stats[miss_stat] += 1
        return k

    def _attach_tool_evidence(self, repos: dict[str, dict], uid: str, v: str, ev: dict) -> None:
        k = self._repo_for_evidence(repos, uid, v, "tool_pairs_without_spend")
        if k is None:
            return
        t = k["tool"]
        for fld in ("sessions", "edit", "work", "mr", "la", "lr", "prompts"):
            t[fld] += ev[fld]
        t["days_max"] = max(t["days_max"], ev["days"])
        t["person_days"] += ev["days"]
        if ev["last"] and (t["last"] is None or ev["last"] > t["last"]):
            t["last"] = ev["last"]

    def _attach_sess_evidence(self, repos: dict[str, dict], uid: str, v: str, ev: dict) -> None:
        k = self._repo_for_evidence(repos, uid, v, "session_pairs_without_spend")
        if k is None:
            return
        for fld in ("sessions", "mcp_any", "mcp_work", "framework"):
            k["sess"][fld] += ev[fld]

    def _money_keys(self) -> dict[str, dict]:
        """Money per repository in the selected range (the same numbers as K when the windows are equal), and whether
        the evidence window saw any of its values (``in_evidence``)."""
        money_keys: dict[str, dict] = {}
        for uid, p in self.persons.items():
            for v, pair in p["pairs"].items():
                key = self.resolution(uid, v)[0]
                if key is None:
                    continue
                spend, cb, sh, nc = pair
                m = money_keys.get(key)
                if m is None:
                    m = money_keys[key] = {
                        "spend": 0.0,
                        "cb": 0.0,
                        "sh": 0.0,
                        "nc": 0.0,
                        "persons": Counter(),
                        "in_evidence": False,
                    }
                m["spend"] += spend
                m["cb"] += cb
                m["sh"] += sh
                m["nc"] += nc
                m["persons"][uid] += spend
                m["in_evidence"] = m["in_evidence"] or v in self.values
        for m in money_keys.values():
            m["billed"] = m["spend"] - m["nc"]
            m["pe"] = max(0.0, m["billed"] - m["cb"] - m["sh"])
            m["people_n"] = sum(1 for a in m["persons"].values() if a > 0)
        return money_keys

    def key_money(self, key: str) -> dict[str, Any]:
        return self.KM.get(key) or {
            "spend": 0.0,
            "cb": 0.0,
            "sh": 0.0,
            "nc": 0.0,
            "billed": 0.0,
            "pe": 0.0,
            "people_n": 0,
            "persons": Counter(),
            "in_evidence": False,
        }

    def after_evidence(self, key: str | None) -> bool:
        """A repository of the range first used after the evidence was read: none of its values is in the evidence.
        Evidence reused from earlier in the hour ends before the range does."""
        return key is not None and key not in self.K and not self.key_money(key)["in_evidence"]

    def category_of(self, key: str | None) -> tuple[str, str | None]:
        """The category of a repository, and why it is unclassified. Missing from the evidence's repositories, it is
        outside the limits (beyond each person's top repositories), or has no evidence when first used after it."""
        if key is None:
            return UNCLASSIFIED, NO_REPOSITORY
        k = self.K.get(key)
        if k is None:
            return UNCLASSIFIED, NO_EVIDENCE if self.after_evidence(key) else OUTSIDE_LIMITS
        return k["cat"], k["reason"]
