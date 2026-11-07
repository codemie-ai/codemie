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

"""Funding and personal-billed estimate buckets of the Local Analytics responses.

The estimate sits next to the spend and never feeds it: its rows are priced by a pricer of their own.
"""

from __future__ import annotations

from typing import Any

from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer
from codemie.service.analytics.handlers.session_money import token_counts

FUNDING_ROUTES = ("api_based", "subscription", "unknown")


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def price_bucket_rows(rows: list[dict]) -> list[dict]:
    """Price each `get_session_funding_costs` row once with a pricer of its own, so the estimate buckets never add
    to the spend pricer behind `metadata.unpriced_models`."""
    pricer = CatalogPricer()
    priced = []
    for row in rows:
        model = _s(row.get("model_name"))
        cost, missing = pricer.price_flagged(model, token_counts(row))
        route = _s(row.get("funding_route"))
        priced.append(
            {
                "session_id": _s(row.get("session_id")),
                "model": model,
                "normalized_model_name": _s(row.get("normalized_model_name")),
                "funding_route": route if route in FUNDING_ROUTES else "unknown",
                "personal_billed": bool(row.get("personal_billed")),
                "cost_usd": cost,
                "missing": missing,
            }
        )
    return priced


def _bucket(rows: list[dict]) -> dict:
    """One bucket's money: a partial figure carries `cost_incomplete` and its own unpriced models, never a bare 0."""
    unpriced = sorted({r["model"] for r in rows if r["missing"]})
    return {
        "cost_usd": sum(r["cost_usd"] for r in rows),
        "session_count": len({r["session_id"] for r in rows}),
        "cost_incomplete": bool(unpriced),
        "unpriced_models": unpriced,
    }


def buckets(priced: list[dict], session_ids: Any = None, model: str | None = None) -> dict[str, Any]:
    """`funding` (client-asserted estimate, by the session's first claim) and `personal_billed` (by derived billing
    class) over the priced rows of `session_ids` and normalised `model` (every row when None)."""
    # ponytail: a linear scan per entity; index the rows by session if per-row buckets show up in profiles
    sessions = None if session_ids is None else set(session_ids)
    rows = [
        r
        for r in priced
        if (sessions is None or r["session_id"] in sessions) and (model is None or r["normalized_model_name"] == model)
    ]
    return {
        "funding": {route: _bucket([r for r in rows if r["funding_route"] == route]) for route in FUNDING_ROUTES},
        "personal_billed": _bucket([r for r in rows if r["personal_billed"]]),
    }


def models_without_spend(priced: list[dict], spend_models: Any) -> list[dict]:
    """Rows for the models the estimate priced but the spend did not (a personal-billed-only model): `cost_usd` 0 and
    its buckets, in a field of their own so the spend `cost_by_model` keeps its membership. No blank model."""
    names = sorted({r["normalized_model_name"] for r in priced} - set(spend_models) - {""})
    return [{"model_name": name, "cost_usd": 0.0, **buckets(priced, model=name)} for name in names]
