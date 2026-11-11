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

"""Elasticsearch request bodies of the Usage Attribution analysis (architecture.md §2.3, A0–A9) and of the popups.

All requests are read-only aggregations (``size: 0``). Who pays is decided per record inside the aggregations:

- charged back: the LiteLLM customer is a project customer and there is no ``budget_fallback_from``;
- shared project without a budget: ``budget_fallback_from`` exists, or the customer is personal and the project is in
  the app DB's shared list. Since EPMCDME-16625 ``budget_fallback_from`` names the project the request was aimed at
  (a member's project, else their default project), not necessarily the project of the asset that ran: a
  non-member's run of another project's asset is aimed at, and reported under, their own default project;
- personal project: the customer is personal and the project is not shared (or missing);
- no customer: neither a customer nor a fallback; not billed through CodeMie budgets.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from codemie.service.usage_attribution.constants import (
    EDIT_TOOL_PATTERNS,
    F_ASSISTANT,
    F_BRANCH,
    F_CLI_REQUEST,
    F_CLIENT,
    F_CUSTOMER,
    F_DATASOURCE,
    F_FALLBACK,
    F_FALLBACK_KIND,
    FALLBACK_KIND_DEFAULT_PROJECT,
    F_LINES_ADDED,
    F_LINES_REMOVED,
    F_MCP_PROJECT,
    F_METRIC,
    F_MODEL,
    F_MONEY,
    F_PROJECT,
    F_PROMPTS,
    F_REPOSITORY,
    F_SESSION,
    F_SKILLS_PROJECT,
    F_TOOL_NAMES,
    F_TS,
    F_USER_ID,
    F_USER_NAME,
    F_WORKFLOW,
    FUNDED_MEMBERS_MAX,
    METRIC_CLI_PROXY,
    METRIC_CLI_SESSION,
    METRIC_CLI_TOOLS,
    METRIC_WEB_DATASOURCE,
    MR_TOOL_PATTERNS,
    PROJECT_CUSTOMER_PREFIX,
    WEB_METRICS,
    WORK_TRACKING_PATTERNS,
)

Part = tuple[int, int] | None


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------------------------------------
# Clauses
# ---------------------------------------------------------------------------------------------------------------------
def q_range(start: datetime, end: datetime) -> dict:
    return {"range": {F_TS: {"gte": iso(start), "lt": iso(end)}}}


def q_metric(*names: str) -> dict:
    if len(names) == 1:
        return {"term": {F_METRIC: names[0]}}
    return {"terms": {F_METRIC: list(names)}}


def q_cli(value: bool = True) -> dict:
    if value:
        return {"term": {F_CLI_REQUEST: True}}
    return {"bool": {"must_not": [{"term": {F_CLI_REQUEST: True}}]}}


def base(start: datetime, end: datetime, *filters: dict | None) -> dict:
    return {"bool": {"filter": [q_range(start, end)] + [x for x in filters if x]}}


def cli_query(start: datetime, end: datetime, *extra: dict | None) -> dict:
    return base(start, end, q_metric(METRIC_CLI_PROXY), q_cli(True), *extra)


def web_query(start: datetime, end: datetime, *extra: dict | None) -> dict:
    return base(start, end, q_metric(*WEB_METRICS.values()), *extra)


def any_wildcard(field: str, patterns: tuple[str, ...] | list[str]) -> dict:
    clauses = [{"wildcard": {field: {"value": p, "case_insensitive": True}}} for p in patterns]
    return {"bool": {"should": clauses, "minimum_should_match": 1}}


def sum_money() -> dict:
    return {"sum": {"field": F_MONEY}}


def money() -> dict:
    return {"spend": sum_money()}


def card(field: str, precision: int | None = None) -> dict:
    body: dict[str, Any] = {"field": field}
    if precision:
        body["precision_threshold"] = precision
    return {"cardinality": body}


def terms(
    field: str,
    size: int,
    part: Part = None,
    order: str | None = "spend",
    include: Any = None,
    missing: str | None = None,
) -> dict:
    t: dict[str, Any] = {"field": field, "size": max(1, int(size))}
    if missing is not None:
        t["missing"] = missing
    if order:
        t["order"] = {order: "desc"}
    if part:
        t["include"] = {"partition": part[0], "num_partitions": part[1]}
    if include is not None:
        t["include"] = include
    return t


def name_agg() -> dict:
    return {"name": {"terms": {"field": F_USER_NAME, "size": 1}}}


def has_fb() -> dict:
    """Records paid personally after their project could not pay (budget_fallback_from).

    A fallback charged to the user's default project is charged back there, so it does not count (EPMCDME-16625);
    records written before the kind existed have none and still count.
    """
    return {
        "bool": {
            "filter": [{"exists": {"field": F_FALLBACK}}],
            "must_not": [{"term": {F_FALLBACK_KIND: FALLBACK_KIND_DEFAULT_PROJECT}}],
        }
    }


def no_fb() -> dict:
    return {"bool": {"must_not": [has_fb()]}}


def shared_clause(shared: list[str]) -> dict:
    return {"terms": {F_PROJECT: shared}} if shared else {"match_none": {}}


def personal_clause(shared: list[str]) -> dict:
    """Records in a personal project: not in the app DB's shared list (a missing project included)."""
    return {"bool": {"must_not": [{"terms": {F_PROJECT: shared}}]}} if shared else {"match_all": {}}


def who_pays_filters(shared: list[str]) -> dict[str, dict]:
    cust_exists = {"exists": {"field": F_CUSTOMER}}
    cust_project = {"prefix": {F_CUSTOMER: PROJECT_CUSTOMER_PREFIX}}
    personal = personal_clause(shared)
    return {
        "charged_back": {"bool": {"filter": [cust_project], "must_not": [has_fb()]}},
        "shared_no_budget": {
            "bool": {
                "should": [has_fb(), {"bool": {"filter": [cust_exists], "must_not": [cust_project, personal]}}],
                "minimum_should_match": 1,
            }
        },
        "personal": {"bool": {"filter": [cust_exists, personal], "must_not": [cust_project, has_fb()]}},
        "no_customer": {"bool": {"must_not": [cust_exists, has_fb()]}},
    }


def wp_aggs(shared: list[str], names: tuple[str, ...] = ("cb", "sh", "nc")) -> dict:
    filters = who_pays_filters(shared)
    by_alias = {"cb": filters["charged_back"], "sh": filters["shared_no_budget"], "nc": filters["no_customer"]}
    return {n: {"filter": by_alias[n], "aggs": {"s": sum_money()}} for n in names}


def day_runtime_field(body: dict) -> dict:
    """Distinct active days, via a runtime field (one value per UTC day)."""
    body.setdefault("runtime_mappings", {})["ua_day"] = {
        "type": "long",
        "script": {
            "source": "if (doc[params.f].size() > 0) { ZonedDateTime z = doc[params.f].value; "
            "emit(z.getYear() * 1000L + z.getDayOfYear()); }",
            "params": {"f": F_TS},
        },
    }
    return {"cardinality": {"field": "ua_day"}}


# ---------------------------------------------------------------------------------------------------------------------
# A0, A1: sizes and totals
# ---------------------------------------------------------------------------------------------------------------------
def cards_body(start: datetime, end: datetime, shared: list[str]) -> dict:
    cli = {
        "users": card(F_USER_ID, 40000),
        "repos": card(F_REPOSITORY, 40000),
        "spend": sum_money(),
        "shared": {"filter": shared_clause(shared), "aggs": {"projects": card(F_PROJECT, 40000)}},
    }
    aggs = {
        "cli": {"filter": {"bool": {"filter": [q_metric(METRIC_CLI_PROXY), q_cli(True)]}}, "aggs": cli},
        "tools": {"filter": q_metric(METRIC_CLI_TOOLS), "aggs": {"users": card(F_USER_ID, 40000)}},
        "sessions": {"filter": q_metric(METRIC_CLI_SESSION), "aggs": {"users": card(F_USER_ID, 40000)}},
        "web": {
            "filter": q_metric(*WEB_METRICS.values()),
            "aggs": {"users": card(F_USER_ID, 40000), "projects": card(F_PROJECT, 40000), "spend": sum_money()},
        },
        "fb": {"filter": has_fb(), "aggs": {"projects": card(F_FALLBACK, 40000)}},
    }
    return {"size": 0, "query": base(start, end), "aggs": aggs}


def totals_body(start: datetime, end: datetime, shared: list[str]) -> dict:
    """A1: CLI, other proxied and Web Platform spend, with who pays for CLI."""
    return {
        "size": 0,
        "query": base(
            start,
            end,
            {
                "bool": {
                    "should": [q_metric(METRIC_CLI_PROXY), q_metric(*WEB_METRICS.values())],
                    "minimum_should_match": 1,
                }
            },
        ),
        "aggs": {
            "source": {
                "filters": {
                    "filters": {
                        "cli": {"bool": {"filter": [q_metric(METRIC_CLI_PROXY), q_cli(True)]}},
                        "other_proxy": {"bool": {"filter": [q_metric(METRIC_CLI_PROXY), q_cli(False)]}},
                        **{f"web_{kind}": q_metric(metric) for kind, metric in WEB_METRICS.items()},
                    }
                },
                "aggs": {
                    "spend": sum_money(),
                    "who_pays": {"filters": {"filters": who_pays_filters(shared)}, "aggs": {"spend": sum_money()}},
                },
            }
        },
    }


def web_funding_aggs(fplan: dict[str, Any]) -> dict:
    """Web Platform funding, records without fallback (funding.py): spend per member of the projects with a funded
    member, and, for members funded for only part of the range, the spend inside their funded intervals."""
    projects = fplan["projects"]
    aggs: dict[str, Any] = {
        "fp": {
            "filter": {"bool": {"filter": [no_fb(), {"terms": {F_PROJECT: projects}}]}},
            "aggs": {
                "p": {
                    "terms": terms(F_PROJECT, len(projects), order=None),
                    "aggs": {"u": {"terms": terms(F_USER_ID, FUNDED_MEMBERS_MAX, order=None), "aggs": money()}},
                }
            },
        }
    }
    groups = fplan["groups"]
    if groups:
        filters = {
            f"g{i}": {
                "bool": {
                    "filter": [
                        {"term": {F_PROJECT: project}},
                        {"terms": {F_USER_ID: uids}},
                        {
                            "bool": {
                                "should": [
                                    {"range": {F_TS: {"gte": a, "lt": b, "format": "epoch_millis"}}}
                                    for a, b in intervals
                                ],
                                "minimum_should_match": 1,
                            }
                        },
                    ]
                }
            }
            for i, (project, uids, intervals) in enumerate(groups)
        }
        size = max(len(uids) for _, uids, _ in groups)
        aggs["fg"] = {
            "filter": no_fb(),
            "aggs": {
                "g": {
                    "filters": {"filters": filters},
                    "aggs": {"u": {"terms": terms(F_USER_ID, size, order=None), "aggs": money()}},
                }
            },
        }
    return aggs


def web_pay_body(start: datetime, end: datetime, shared: list[str], fplan: dict[str, Any]) -> dict:
    """Web Platform who pays for /totals: fallback records, records in personal and in shared projects, and the
    funding of the shared ones (funding.py)."""
    aggs: dict[str, Any] = {
        "fb": {"filter": has_fb(), "aggs": money()},
        "personal": {"filter": {"bool": {"filter": [no_fb(), personal_clause(shared)]}}, "aggs": money()},
        "shared": {"filter": {"bool": {"filter": [no_fb(), shared_clause(shared)]}}, "aggs": money()},
    }
    if fplan["projects"]:
        aggs.update(web_funding_aggs(fplan))
    return {"size": 0, "query": web_query(start, end), "aggs": aggs}


# ---------------------------------------------------------------------------------------------------------------------
# A2–A9
# ---------------------------------------------------------------------------------------------------------------------
def persons_body(start: datetime, end: datetime, shared: list[str], part: Part, size: int, repos: int) -> dict:
    """A2: people x repositories, with who pays per person, repository and project."""
    sub: dict[str, Any] = {
        "spend": sum_money(),
        # Records without a repository field join the empty value: no repository, not outside the limits.
        "repos": {"terms": terms(F_REPOSITORY, repos, missing=""), "aggs": dict(money(), **wp_aggs(shared))},
        "projects": {"terms": terms(F_PROJECT, 3), "aggs": dict(money(), **wp_aggs(shared, ("cb", "sh")))},
        # Since release 2.57.0 a record paid personally carries the person's own project; the project it was aimed at
        # is kept only in budget_fallback_from (since EPMCDME-16625: a member's project or the default project).
        "fallback": {"filter": has_fb(), "aggs": {"t": {"terms": terms(F_FALLBACK, 3), "aggs": money()}}},
    }
    sub.update(wp_aggs(shared))
    sub.update(name_agg())
    return {
        "size": 0,
        "query": cli_query(start, end),
        "aggs": {"users": {"terms": terms(F_USER_ID, size, part), "aggs": sub}},
    }


def values_body(start: datetime, end: datetime, shared: list[str], part: Part, size: int) -> dict:
    """A3: repository values. Personal projects are aggregated apart: with several people on a repository, its top
    projects would otherwise always be personal ones, hiding the shared (and budgeted) projects it is used in."""
    sub: dict[str, Any] = {
        "spend": sum_money(),
        "people": card(F_USER_ID),
        "sessions": card(F_SESSION),
        "personal": {
            "filter": {"bool": {"filter": [personal_clause(shared)], "must_not": [has_fb()]}},
            "aggs": {"s": sum_money()},
        },
        "projects": {
            "filter": {"bool": {"filter": [shared_clause(shared)], "must_not": [has_fb()]}},
            "aggs": {"t": {"terms": terms(F_PROJECT, 3), "aggs": dict(money(), **wp_aggs(shared, ("cb",)))}},
        },
        "fb_projects": {"filter": has_fb(), "aggs": {"t": {"terms": terms(F_FALLBACK, 2), "aggs": money()}}},
        "branches": {"terms": terms(F_BRANCH, 8), "aggs": money()},
        "clients": {"terms": terms(F_CLIENT, 3), "aggs": money()},
        "models": {"terms": terms(F_MODEL, 3), "aggs": money()},
    }
    return {
        "size": 0,
        "query": cli_query(start, end),
        "aggs": {"repos": {"terms": terms(F_REPOSITORY, size, part), "aggs": sub}},
    }


def tools_body(start: datetime, end: datetime, part: Part, size: int, repos: int) -> dict:
    """A4: tool evidence per person and repository: sessions, edits, work-tracking and merge-request tools."""
    body: dict[str, Any] = {"size": 0, "query": base(start, end, q_metric(METRIC_CLI_TOOLS))}
    sub: dict[str, Any] = {
        "sessions": card(F_SESSION),
        "last": {"max": {"field": F_TS}},
        "days": day_runtime_field(body),
        "edit": {"filter": any_wildcard(F_TOOL_NAMES, EDIT_TOOL_PATTERNS), "aggs": {"s": card(F_SESSION)}},
        "work": {"filter": any_wildcard(F_TOOL_NAMES, WORK_TRACKING_PATTERNS), "aggs": {"s": card(F_SESSION)}},
        "mr": {"filter": any_wildcard(F_TOOL_NAMES, MR_TOOL_PATTERNS), "aggs": {"s": card(F_SESSION)}},
        "la": {"sum": {"field": F_LINES_ADDED}},
        "lr": {"sum": {"field": F_LINES_REMOVED}},
        "prompts": {"sum": {"field": F_PROMPTS}},
    }
    body["aggs"] = {
        "users": {
            "terms": terms(F_USER_ID, size, part, order=None),
            "aggs": {"repos": {"terms": terms(F_REPOSITORY, repos, order=None), "aggs": sub}},
        }
    }
    return body


def sessions_body(start: datetime, end: datetime, framework_regex: str, part: Part, size: int, repos: int) -> dict:
    """A5: session evidence: project-scoped MCP servers (any, work tracking) and delivery-framework skills."""
    sub: dict[str, Any] = {
        "sessions": card(F_SESSION),
        "mcp_any": {"filter": {"exists": {"field": F_MCP_PROJECT}}, "aggs": {"s": card(F_SESSION)}},
        "mcp_work": {"filter": any_wildcard(F_MCP_PROJECT, WORK_TRACKING_PATTERNS), "aggs": {"s": card(F_SESSION)}},
        "framework": {"filter": {"regexp": {F_SKILLS_PROJECT: framework_regex}}, "aggs": {"s": card(F_SESSION)}},
    }
    return {
        "size": 0,
        "query": base(start, end, q_metric(METRIC_CLI_SESSION)),
        "aggs": {
            "users": {
                "terms": terms(F_USER_ID, size, part, order=None),
                "aggs": {"repos": {"terms": terms(F_REPOSITORY, repos, order=None), "aggs": sub}},
            }
        },
    }


def web_types() -> dict:
    return {"types": {"terms": {"field": F_METRIC, "size": 3}, "aggs": money()}}


def web_projects_body(start: datetime, end: datetime, part: Part, size: int) -> dict:
    """A7: Web Platform per project, records without fallback."""
    sub = dict(money(), people=card(F_USER_ID))
    sub.update(web_types())
    return {
        "size": 0,
        "query": web_query(start, end, no_fb()),
        "aggs": {"projects": {"terms": terms(F_PROJECT, size, part), "aggs": sub}},
    }


def web_fb_body(start: datetime, end: datetime, part: Part, size: int) -> dict:
    """A7b: Web Platform fallback records, by the project they were aimed at (budget_fallback_from).

    Since EPMCDME-16625 that is the asset's project only for its members; a non-member's run is aimed at their own
    default project, so another project's shared asset does not show its non-member users here.
    """
    sub = dict(money(), people=card(F_USER_ID))
    sub.update(web_types())
    return {
        "size": 0,
        "query": web_query(start, end, has_fb()),
        "aggs": {"projects": {"terms": terms(F_FALLBACK, size, part), "aggs": sub}},
    }


def web_people_body(start: datetime, end: datetime, part: Part, size: int) -> dict:
    """A8: Web Platform per person: projects without fallback and fallback projects."""
    sub = money()
    sub.update(name_agg())
    sub["proj"] = {"filter": no_fb(), "aggs": {"t": {"terms": terms(F_PROJECT, 20), "aggs": money()}}}
    sub["fbp"] = {"filter": has_fb(), "aggs": {"t": {"terms": terms(F_FALLBACK, 10), "aggs": money()}}}
    return {
        "size": 0,
        "query": web_query(start, end),
        "aggs": {"users": {"terms": terms(F_USER_ID, size, part), "aggs": sub}},
    }


def web_funding_body(start: datetime, end: datetime, fplan: dict[str, Any]) -> dict | None:
    """A9: Web Platform funding of the projects with a funded member, records without fallback (funding.py)."""
    if not fplan["projects"]:
        return None
    return {
        "size": 0,
        "query": web_query(start, end, no_fb(), {"terms": {F_PROJECT: fplan["projects"]}}),
        "aggs": web_funding_aggs(fplan),
    }


def _shared_subs(shared: list[str], fallback_records: bool, repos: int) -> dict:
    repo_sub = money() if fallback_records else dict(money(), **wp_aggs(shared, ("sh",)))
    sub: dict[str, Any] = {
        "spend": sum_money(),
        "people_n": card(F_USER_ID),
        "repos": {"terms": terms(F_REPOSITORY, repos), "aggs": repo_sub},
        "people": {"terms": terms(F_USER_ID, 10), "aggs": dict(money(), **name_agg())},
        "clients": {"terms": terms(F_CLIENT, 6), "aggs": dict(money(), sessions=card(F_SESSION))},
        "models": {"terms": terms(F_MODEL, 6), "aggs": money()},
    }
    if not fallback_records:
        sub.update(wp_aggs(shared, ("cb", "sh")))
    return sub


def shared_cli_body(start: datetime, end: datetime, shared: list[str], part: Part, size: int, repos: int) -> dict:
    """A6: CLI in shared projects, records without fallback."""
    return {
        "size": 0,
        "query": cli_query(start, end, no_fb(), shared_clause(shared)),
        "aggs": {"projects": {"terms": terms(F_PROJECT, size, part), "aggs": _shared_subs(shared, False, repos)}},
    }


def shared_cli_fb_body(start: datetime, end: datetime, shared: list[str], part: Part, size: int, repos: int) -> dict:
    """A6b: CLI fallback records, by the project they were aimed at (budget_fallback_from).

    Since EPMCDME-16625 that is the header project only for its members; otherwise it is the default project.
    """
    return {
        "size": 0,
        "query": cli_query(start, end, has_fb()),
        "aggs": {"projects": {"terms": terms(F_FALLBACK, size, part), "aggs": _shared_subs(shared, True, repos)}},
    }


# ---------------------------------------------------------------------------------------------------------------------
# Popups (per entity, at request time)
# ---------------------------------------------------------------------------------------------------------------------
def person_cli_detail_body(start: datetime, end: datetime, uid: str, sessionless: list[str], shared: list[str]) -> dict:
    """Models, coding agents, projects, active days and recent sessions of one person (CLI)."""
    body: dict[str, Any] = {"size": 0, "query": cli_query(start, end, {"term": {F_USER_ID: uid}})}
    recent = {
        "last": {"max": {"field": F_TS}},
        "first": {"min": {"field": F_TS}},
        "spend": sum_money(),
        "repo": {"terms": {"field": F_REPOSITORY, "size": 1}},
        "branch": {"terms": {"field": F_BRANCH, "size": 1}},
        "client": {"terms": {"field": F_CLIENT, "size": 1}},
    }
    with_sessions = {"bool": {"must_not": [{"terms": {F_CLIENT: sessionless}}]}} if sessionless else {"match_all": {}}
    body["aggs"] = {
        "spend": sum_money(),
        "days": day_runtime_field(body),
        "models": {"terms": terms(F_MODEL, 8), "aggs": money()},
        "clients": {
            "terms": terms(F_CLIENT, 8),
            "aggs": dict(
                money(), sessions=card(F_SESSION), sessionful={"filter": with_sessions, "aggs": {"n": card(F_SESSION)}}
            ),
        },
        "projects": {
            "terms": terms(F_PROJECT, 10),
            "aggs": dict(
                money(),
                sessions={"filter": with_sessions, "aggs": {"n": card(F_SESSION)}},
                **wp_aggs(shared, ("cb", "sh")),
            ),
        },
        "fb_projects": {"filter": has_fb(), "aggs": {"t": {"terms": terms(F_FALLBACK, 10), "aggs": money()}}},
        "recent": {
            "filter": with_sessions,
            "aggs": {"t": {"terms": {"field": F_SESSION, "size": 8, "order": {"last": "desc"}}, "aggs": recent}},
        },
    }
    return body


def person_web_detail_body(start: datetime, end: datetime, uid: str) -> dict:
    """Assistants and workflows one person used on the Web Platform."""
    sub = money()
    for key, field in (("assistants", F_ASSISTANT), ("workflows", F_WORKFLOW)):
        sub[key] = {
            "terms": terms(field, 10),
            "aggs": dict(money(), last={"max": {"field": F_TS}}, proj={"terms": {"field": F_PROJECT, "size": 1}}),
        }
    sub["models"] = {"terms": terms(F_MODEL, 8), "aggs": money()}
    return {"size": 0, "query": web_query(start, end, {"term": {F_USER_ID: uid}}), "aggs": sub}


def sessions_extra_body(start: datetime, end: datetime, session_ids: list[str]) -> dict:
    """Prompts and project skills of a few sessions (recent sessions in the person popup)."""
    return {
        "size": 0,
        "query": base(start, end, q_metric(METRIC_CLI_TOOLS, METRIC_CLI_SESSION), {"terms": {F_SESSION: session_ids}}),
        "aggs": {
            "s": {
                "terms": {"field": F_SESSION, "size": max(1, len(session_ids))},
                "aggs": {
                    "prompts": {"sum": {"field": F_PROMPTS}},
                    "skills": {"terms": {"field": F_SKILLS_PROJECT, "size": 20}},
                },
            }
        },
    }


def project_web_detail_body(start: datetime, end: datetime, project: str) -> dict:
    """Web Platform assets, models and people of one shared project (records in it and fallback records of it)."""

    def subs() -> dict:
        sub: dict[str, Any] = {
            "spend": sum_money(),
            "people": {"terms": terms(F_USER_ID, 12), "aggs": dict(money(), **name_agg())},
            "people_n": card(F_USER_ID),
            "models": {"terms": terms(F_MODEL, 8), "aggs": money()},
        }
        for key, field in (("assistants", F_ASSISTANT), ("workflows", F_WORKFLOW)):
            sub[key] = {
                "terms": terms(field, 10),
                "aggs": dict(money(), users=card(F_USER_ID), last={"max": {"field": F_TS}}),
            }
        sub["datasources"] = {
            "filter": q_metric(METRIC_WEB_DATASOURCE),
            "aggs": {"t": {"terms": terms(F_DATASOURCE, 6), "aggs": dict(money(), last={"max": {"field": F_TS}})}},
        }
        sub["types"] = {"terms": {"field": F_METRIC, "size": 3}, "aggs": money()}
        return sub

    return {
        "size": 0,
        "query": web_query(
            start,
            end,
            {
                "bool": {
                    "should": [{"term": {F_PROJECT: project}}, {"term": {F_FALLBACK: project}}],
                    "minimum_should_match": 1,
                }
            },
        ),
        "aggs": {
            "p": {"filter": {"bool": {"filter": [no_fb(), {"term": {F_PROJECT: project}}]}}, "aggs": subs()},
            "f": {"filter": {"bool": {"filter": [has_fb(), {"term": {F_FALLBACK: project}}]}}, "aggs": subs()},
        },
    }
