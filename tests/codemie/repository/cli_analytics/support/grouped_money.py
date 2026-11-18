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

"""A fake `get_cost_money` for handler tests: the Python pricer's figures shaped as the reader's `GroupedMoney`.

It prices the rows of `get_session_funding_costs` (a `funding_route` and a `personal_billed` flag are optional) the way
the SQL does: every row counts, the Personal-paid ones too, a blank model has no model group, `user` groupings keep the
sessions of `cost_daily`, and a grouping that matches no row has no row.
"""

from collections.abc import Iterable
from typing import Any

from codemie.repository.cli_analytics.ports import (
    G_MODEL,
    G_MODEL_PERSONAL,
    G_MODEL_ROUTE,
    G_PERSONAL,
    G_ROUTE,
    G_TOTAL,
    G_USER,
    G_USER_PERSONAL,
    G_USER_ROUTE,
    CostGroup,
    GroupedMoney,
)
from codemie.repository.cli_analytics.scope import FUNDING_ROUTES
from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer, TokenCounts
from codemie.service.analytics.handlers.session_money import token_counts

_SPEND_NAMES = (G_TOTAL, G_USER, G_MODEL)
# (name, keys, cost_daily sessions only)
_GROUPINGS = (
    (G_TOTAL, (), False),
    (G_USER, ("user",), True),
    (G_MODEL, ("model",), False),
    (G_ROUTE, ("funding_route",), False),
    (G_PERSONAL, ("personal_billed",), False),
    (G_USER_ROUTE, ("user", "funding_route"), True),
    (G_USER_PERSONAL, ("user", "personal_billed"), True),
    (G_MODEL_ROUTE, ("model", "funding_route"), False),
    (G_MODEL_PERSONAL, ("model", "personal_billed"), False),
)


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def price_groups(rows: Iterable[dict[str, Any]], developers: dict[str, str] | None = None) -> GroupedMoney:
    """Every grouping of `get_cost_money` from `rows`. `developers` maps a session to its developer and doubles as the
    `cost_daily` set: a session outside it is in no `user` grouping (all sessions when it is None)."""
    pricer = CatalogPricer()
    priced = []
    for row in rows:
        model, counts = _s(row.get("model_name")), token_counts(row)
        cost, missing = pricer.price_flagged(model, counts)
        route = _s(row.get("funding_route"))
        sid = _s(row.get("session_id"))
        priced.append(
            {
                "sid": sid,
                "raw": model,
                "model": _s(row.get("normalized_model_name", model)),
                "user": (developers or {}).get(sid, ""),
                "funding_route": route if route in FUNDING_ROUTES else "unknown",
                "personal_billed": bool(row.get("personal_billed")),
                "cost": cost,
                "cache": pricer.price(model, TokenCounts(cache_read=counts.cache_read)),
                "missing": missing,
                "priced": pricer.prices_some_usage(model, counts),
            }
        )
    groups: dict[str, list[CostGroup]] = {}
    for name, keys, daily in _GROUPINGS:
        buckets: dict[tuple, list[dict]] = {}
        for p in priced:
            if daily and developers is not None and p["sid"] not in developers:
                continue
            if "model" in keys and not p["model"]:
                continue
            buckets.setdefault(tuple(p[k] for k in keys), []).append(p)
        groups[name] = [
            CostGroup(
                keys=dict(zip(keys, key, strict=True)),
                cost_usd=sum(p["cost"] for p in members),
                cache_read_cost_usd=sum(p["cache"] for p in members),
                session_count=len({p["sid"] for p in members}),
                unpriced_models=tuple(sorted({p["raw"] for p in members if p["missing"]})),
                cost_incomplete=any(p["missing"] for p in members),
                has_priced=any(p["priced"] for p in members),  # as `cost_priced` in the SQL
            )
            for key, members in buckets.items()
        ]
    return GroupedMoney(groups)


def fake_cost_money(rows: Iterable[dict[str, Any]], developers: dict[str, str] | None = None):
    """An async `get_cost_money(f, rates, buckets=True)` that prices at call time (inside the test's catalog patch);
    without `buckets` it leaves the estimate groupings out, as the reader does."""
    rows = list(rows)

    async def get_cost_money(f, rates, buckets=True):
        money = price_groups(rows, developers)
        return money if buckets else without_buckets(money)

    return get_cost_money


def without_buckets(money: GroupedMoney) -> GroupedMoney:
    """`money` without the funding-route and Personal-billed estimate groupings."""
    return GroupedMoney({name: money.groups.get(name, []) for name in _SPEND_NAMES})
