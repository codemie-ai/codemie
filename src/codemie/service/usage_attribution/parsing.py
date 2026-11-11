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

"""Parse aggregation buckets into the compact inputs of the analysis.

Each Elasticsearch response is parsed as soon as it arrives and then dropped, so the worker never holds all raw
responses at once. The compact inputs are plain JSON (they are cached gzipped between computations):

- ``persons``: ``{uid: {name, spend, cb, sh, nc, pairs: {value: [spend, cb, sh, nc]}, projects: {p: [spend, cb, sh]}}}``
- ``values``: ``{value: {spend, people, sessions, personal, branches, clients, models, projects, projects_cb}}``
- ``tool``: ``[[uid, value, sessions, edit, work, mr, lines_added, lines_removed, prompts, days, last]]``
- ``sess``: ``[[uid, value, sessions, mcp_any, mcp_work, framework]]``
- ``web_proj`` / ``web_fb``: ``{project: {spend, people, types: {metric: spend}}}``
- ``web_people``: ``{uid: {name, spend, proj: {project: spend}, fbp: {project: spend}}}``
- ``shared_cli``: ``{project: {spend, cb, sh, fb, people_n, repos: {value: [spend, sh]}, people: {uid: [spend, name]},
  clients: {client: [spend, sessions]}, models: {model: spend}}}``

Who pays inside the aggregations: ``cb`` charged back, ``sh`` a shared project without a budget, ``nc`` no LiteLLM
customer (not billed through CodeMie budgets); the personal part is ``spend - cb - sh - nc``.
"""

from __future__ import annotations

from typing import Any


def agg_value(node: Any, *path: str) -> Any:
    """The node at ``path``; a metric aggregation (``{"value": ...}``) is unwrapped to its value."""
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if isinstance(node, dict) and "value" in node:
        return node["value"]
    return node


def num(node: Any, *path: str) -> float:
    value = agg_value(node, *path)
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def bucket_list(node: Any, *path: str) -> list[dict]:
    value = agg_value(node, *(path + ("buckets",)))
    return value if isinstance(value, list) else []


def spend_map(node: Any, *path: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for b in bucket_list(node, *path):
        key = str(b.get("key"))
        out[key] = out.get(key, 0.0) + num(b, "spend")
    return out


def first_key(node: Any, *path: str) -> str | None:
    items = bucket_list(node, *path)
    return str(items[0]["key"]) if items else None


def ts_value(node: Any, *path: str) -> str | None:
    """A max/min aggregation on @timestamp, as an ISO string."""
    target = node
    for part in path:
        if not isinstance(target, dict):
            return None
        target = target.get(part)
    if not isinstance(target, dict):
        return None
    text = target.get("value_as_string")
    return str(text) if text else None


# ---------------------------------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------------------------------
def parse_persons(buckets: list[dict], into: dict[str, dict] | None = None) -> dict[str, dict]:
    """A2, people x repositories (CLI proxy records)."""
    out = into if into is not None else {}
    for b in buckets:
        uid = str(b["key"])
        p = {
            "name": first_key(b, "name") or uid,
            "spend": num(b, "spend"),
            "cb": num(b, "cb", "s"),
            "sh": num(b, "sh", "s"),
            "nc": num(b, "nc", "s"),
            "pairs": {},
            "projects": {},
            # Projects that records paid personally were aimed at (budget_fallback_from): {project: spend}
            "fallback": spend_map(b, "fallback", "t"),
        }
        for r in bucket_list(b, "repos"):
            p["pairs"][str(r["key"])] = [num(r, "spend"), num(r, "cb", "s"), num(r, "sh", "s"), num(r, "nc", "s")]
        for r in bucket_list(b, "projects"):
            p["projects"][str(r["key"])] = [num(r, "spend"), num(r, "cb", "s"), num(r, "sh", "s")]
        out[uid] = p
    return out


def parse_values(buckets: list[dict], into: dict[str, dict] | None = None) -> dict[str, dict]:
    """A3, repository values: people, sessions, personal-project spend, shared projects, branches, clients, models."""
    out = into if into is not None else {}
    for b in buckets:
        projects = spend_map(b, "projects", "t")  # shared projects, records without fallback
        for project, amount in spend_map(b, "fb_projects", "t").items():  # fallback records: the shared project
            projects[project] = projects.get(project, 0.0) + amount
        out[str(b["key"])] = {
            "spend": num(b, "spend"),
            "people": num(b, "people"),
            "sessions": num(b, "sessions"),
            "personal": num(b, "personal", "s"),
            "branches": spend_map(b, "branches"),
            "clients": spend_map(b, "clients"),
            "models": spend_map(b, "models"),
            "projects": projects,
            "projects_cb": {str(x["key"]): num(x, "cb", "s") for x in bucket_list(b, "projects", "t")},
        }
    return out


def parse_tools(buckets: list[dict], into: list[list] | None = None) -> list[list]:
    """A4, tool evidence per person and repository value."""
    out = into if into is not None else []
    for u in buckets:
        uid = str(u["key"])
        for r in bucket_list(u, "repos"):
            out.append(
                [
                    uid,
                    str(r["key"]),
                    num(r, "sessions"),
                    num(r, "edit", "s"),
                    num(r, "work", "s"),
                    num(r, "mr", "s"),
                    num(r, "la"),
                    num(r, "lr"),
                    num(r, "prompts"),
                    num(r, "days"),
                    ts_value(r, "last"),
                ]
            )
    return out


def parse_sessions(buckets: list[dict], into: list[list] | None = None) -> list[list]:
    """A5, session evidence: project-scoped MCP servers and delivery-framework skills."""
    out = into if into is not None else []
    for u in buckets:
        uid = str(u["key"])
        for r in bucket_list(u, "repos"):
            out.append(
                [
                    uid,
                    str(r["key"]),
                    num(r, "sessions"),
                    num(r, "mcp_any", "s"),
                    num(r, "mcp_work", "s"),
                    num(r, "framework", "s"),
                ]
            )
    return out


def _shared_cli_spend(d: dict, b: dict, fallback_records: bool) -> None:
    spend = num(b, "spend")
    d["spend"] += spend
    if fallback_records:
        d["sh"] += spend
        d["fb"] += spend
    else:
        d["cb"] += num(b, "cb", "s")
        d["sh"] += num(b, "sh", "s")
    d["people_n"] = max(d["people_n"], num(b, "people_n"))


def _shared_cli_repos(d: dict, b: dict, fallback_records: bool) -> None:
    for r in bucket_list(b, "repos"):
        rr = d["repos"].setdefault(str(r["key"]), [0.0, 0.0])
        rr[0] += num(r, "spend")
        rr[1] += num(r, "spend") if fallback_records else num(r, "sh", "s")


def _shared_cli_people(d: dict, b: dict) -> None:
    for u in bucket_list(b, "people"):
        pp = d["people"].setdefault(str(u["key"]), [0.0, first_key(u, "name") or str(u["key"])])
        pp[0] += num(u, "spend")


def _shared_cli_clients(d: dict, b: dict) -> None:
    for c in bucket_list(b, "clients"):
        cc = d["clients"].setdefault(str(c["key"]), [0.0, 0.0])
        cc[0] += num(c, "spend")
        cc[1] += num(c, "sessions")


def _shared_cli_models(d: dict, b: dict) -> None:
    for m in bucket_list(b, "models"):
        d["models"][str(m["key"])] = d["models"].get(str(m["key"]), 0.0) + num(m, "spend")


def _accumulate_shared_cli_bucket(into: dict[str, dict], b: dict, fallback_records: bool) -> None:
    d = into.setdefault(
        str(b["key"]),
        {
            "spend": 0.0,
            "cb": 0.0,
            "sh": 0.0,
            "fb": 0.0,
            "people_n": 0.0,
            "repos": {},
            "people": {},
            "clients": {},
            "models": {},
        },
    )
    _shared_cli_spend(d, b, fallback_records)
    _shared_cli_repos(d, b, fallback_records)
    _shared_cli_people(d, b)
    _shared_cli_clients(d, b)
    _shared_cli_models(d, b)


def parse_shared_cli(buckets: list[dict], fallback_records: bool, into: dict[str, dict]) -> dict[str, dict]:
    """A6, CLI in shared projects: records without fallback by project, fallback records by the fallback project."""
    for b in buckets:
        _accumulate_shared_cli_bucket(into, b, fallback_records)
    return into


# ---------------------------------------------------------------------------------------------------------------------
# Web Platform
# ---------------------------------------------------------------------------------------------------------------------
def parse_web_projects(buckets: list[dict], into: dict[str, dict] | None = None) -> dict[str, dict]:
    """A7, Web Platform per project (records without fallback), or per fallback project (fallback records)."""
    out = into if into is not None else {}
    for b in buckets:
        out[str(b["key"])] = {"spend": num(b, "spend"), "people": num(b, "people"), "types": spend_map(b, "types")}
    return out


def parse_web_people(buckets: list[dict], into: dict[str, dict] | None = None) -> dict[str, dict]:
    """A8, Web Platform per person: projects without fallback and fallback projects."""
    out = into if into is not None else {}
    for b in buckets:
        uid = str(b["key"])
        out[uid] = {
            "name": first_key(b, "name") or uid,
            "spend": num(b, "spend"),
            "proj": spend_map(b, "proj", "t"),
            "fbp": spend_map(b, "fbp", "t"),
        }
    return out


def parse_web_funding(aggs: dict | None, fplan: dict[str, Any]) -> dict[str, dict[str, list[float]]]:
    """A9 (and /totals), records without fallback: ``{project: {user id: [spend, charged back]}}`` for the projects with
    a funded member. Charged back is the spend while the project's platform budget paid for that member (funding.py)."""
    aggs = aggs or {}
    full = {project: set(uids) for project, uids in (fplan.get("full") or {}).items()}
    out: dict[str, dict[str, list[float]]] = {}
    for pb in bucket_list(aggs, "fp", "p"):
        project = str(pb["key"])
        for ub in bucket_list(pb, "u"):
            uid = str(ub["key"])
            spend = num(ub, "spend")
            out.setdefault(project, {})[uid] = [spend, spend if uid in full.get(project, ()) else 0.0]
    groups = agg_value(aggs, "fg", "g", "buckets") or {}
    for i, (project, _uids, _intervals) in enumerate(fplan.get("groups") or []):
        for ub in bucket_list(groups.get(f"g{i}") or {}, "u"):
            row = out.setdefault(project, {}).setdefault(str(ub["key"]), [0.0, 0.0])
            row[1] = num(ub, "spend")
            row[0] = max(row[0], row[1])
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Totals (A1)
# ---------------------------------------------------------------------------------------------------------------------
def parse_totals(aggs: dict | None) -> dict[str, Any]:
    src = agg_value(aggs or {}, "source", "buckets") or {}
    out: dict[str, Any] = {
        "cli": {"spend": 0.0, "charged_back": 0.0, "shared_no_budget": 0.0, "personal": 0.0, "no_customer": 0.0},
        "other_proxy": 0.0,
        "web": {"assistant": 0.0, "workflow": 0.0, "datasource": 0.0},
    }
    cli = src.get("cli") or {}
    out["cli"]["spend"] = num(cli, "spend")
    for state, b in (agg_value(cli, "who_pays", "buckets") or {}).items():
        out["cli"][state] = num(b, "spend")
    out["other_proxy"] = num(src.get("other_proxy") or {}, "spend")
    for kind in ("assistant", "workflow", "datasource"):
        out["web"][kind] = num(src.get("web_" + kind) or {}, "spend")
    return out
