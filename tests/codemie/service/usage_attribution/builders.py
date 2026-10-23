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

"""Builders of synthetic analysis inputs (the compact format of service/usage_attribution/parsing.py)."""

from __future__ import annotations

import copy
from typing import Any

from codemie.service.usage_attribution.app_db import datasource_rows
from codemie.service.usage_attribution.pipeline import Analysis
from codemie.service.usage_attribution.settings import build_defaults, set_path


def settings_with(**dotted: Any) -> dict[str, Any]:
    """Default settings with some values replaced (dotted keys, ``__`` stands for ``.``)."""
    values = copy.deepcopy(build_defaults({}).values)
    for key, value in dotted.items():
        set_path(values, key.replace("__", "."), value)
    return values


def person(
    name: str,
    pairs: dict[str, tuple],
    projects: dict[str, tuple] | None = None,
    extra: tuple = (),
    fallback: dict[str, float] | None = None,
) -> dict:
    """A person of A2: ``pairs`` maps a repository value to (spend, cb, sh, nc); ``extra`` is money outside the top
    repositories (spend, cb, sh, nc); ``fallback`` maps the shared project of records paid personally since release
    2.57.0 to their spend."""
    p = {
        "name": name,
        "spend": 0.0,
        "cb": 0.0,
        "sh": 0.0,
        "nc": 0.0,
        "pairs": {},
        "projects": {},
        "fallback": {k: float(v) for k, v in (fallback or {}).items()},
    }
    for value, money in pairs.items():
        spend, cb, sh, nc = (list(money) + [0.0, 0.0, 0.0])[:4]
        p["pairs"][value] = [float(spend), float(cb), float(sh), float(nc)]
        p["spend"] += spend
        p["cb"] += cb
        p["sh"] += sh
        p["nc"] += nc
    if extra:
        spend, cb, sh, nc = (list(extra) + [0.0, 0.0, 0.0])[:4]
        p["spend"] += spend
        p["cb"] += cb
        p["sh"] += sh
        p["nc"] += nc
    for project, money in (projects or {}).items():
        p["projects"][project] = [float(x) for x in (list(money) + [0.0, 0.0])[:3]]
    return p


def value(
    spend: float,
    people: int = 1,
    personal: float | None = None,
    branches: dict | None = None,
    projects: dict | None = None,
    projects_cb: dict | None = None,
    clients: dict | None = None,
    models: dict | None = None,
) -> dict:
    return {
        "spend": float(spend),
        "people": float(people),
        "sessions": 0.0,
        "personal": float(spend if personal is None else personal),
        "branches": branches or {},
        "clients": clients or {"codemie-claude": spend},
        "models": models or {"claude-sonnet": spend},
        "projects": projects or {},
        "projects_cb": projects_cb or {},
    }


def tool(uid: str, val: str, sessions: float, edit: float = 0, days: float = 1, work: float = 0, mr: float = 0) -> list:
    return [
        uid,
        val,
        float(sessions),
        float(edit),
        float(work),
        float(mr),
        100.0,
        10.0,
        5.0,
        float(days),
        "2026-10-10T10:00:00.000Z",
    ]


def inputs(
    persons: dict[str, dict],
    values: dict[str, dict] | None = None,
    tools: list | None = None,
    sess: list | None = None,
    web_proj: dict | None = None,
    web_fb: dict | None = None,
    web_people: dict | None = None,
    shared_cli: dict | None = None,
    app_db: dict | None = None,
    days: float = 30.0,
    ev_persons: dict | None = None,
    web_funding: dict | None = None,
) -> dict:
    return {
        "window": {"from": "2026-09-13T00:00:00Z", "to": "2026-10-13T00:00:00Z", "days": days},
        "persons": persons,
        "ev_persons": ev_persons,
        "values": values or {},
        "tool": tools or [],
        "sess": sess or [],
        "web_proj": web_proj or {},
        "web_fb": web_fb or {},
        "web_people": web_people or {},
        "web_funding": web_funding or {},
        "shared_cli": shared_cli or {},
        "totals": {},
        "app_db": app_db
        or {"shared": [], "projects": [], "budgets": [], "datasources": [], "members": [], "people": []},
    }


def run(data: dict, settings: dict | None = None) -> Analysis:
    return Analysis(data, settings or settings_with()).run()


def app_db(
    shared: list[str] | None = None,
    budgets: list[tuple[str, str]] | None = None,
    datasources: list[tuple[str, str]] | None = None,
    members: list[tuple[str, str]] | None = None,
    people: list[list] | None = None,
) -> dict:
    shared = shared or []
    return {
        "shared": shared,
        "projects": [[p, False, False, "project", ""] for p in shared],
        "budgets": [[p, c, "equal", None, 100.0, ""] for p, c in (budgets or [])],
        # As the loader caches them: raw links become normalized owner/repo keys (app_db.datasource_rows).
        "datasources": datasource_rows(datasources or []),
        "members": [list(x) for x in (members or [])],
        "people": people or [],
    }
