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

from collections.abc import Iterable
from typing import Any

from codemie.repository.cli_analytics.ports import CostGroup
from codemie.repository.cli_analytics.scope import FUNDING_ROUTES
from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer, unpriced_display_names
from codemie.service.analytics.handlers.session_money import token_counts


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def price_bucket_rows(rows: list[dict]) -> list[dict]:
    """Price each `get_session_funding_costs` row once with a pricer of its own, so the estimate buckets never add
    to the spend pricer behind `metadata.unpriced_models`."""
    pricer = CatalogPricer()
    priced = []
    for row in rows:
        model = _s(row.get("model_name"))
        counts = token_counts(row)
        cost, missing = pricer.price_flagged(model, counts)
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
                "priced": pricer.prices_some_usage(model, counts),  # a used kind with a rate: the evidence
            }
        )
    return priced


def _bucket(rows: list[dict]) -> dict:
    """One bucket's money: a partial figure carries `cost_incomplete` and its own unpriced models (normalised),
    never a bare 0."""
    unpriced = unpriced_display_names(r["model"] for r in rows if r["missing"])
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


def _group_bucket(group: CostGroup | None) -> dict:
    if group is None:
        return {"cost_usd": 0.0, "session_count": 0, "cost_incomplete": False, "unpriced_models": []}
    return {
        "cost_usd": float(group.cost_usd),
        "session_count": group.session_count,
        "cost_incomplete": group.cost_incomplete,
        "unpriced_models": unpriced_display_names(group.unpriced_models),  # a route reads as its plain name, once
    }


def buckets_from_groups(route_rows: list[CostGroup], personal_rows: list[CostGroup]) -> dict[str, Any]:
    """The `buckets()` shape from SQL-grouped rows: `route_rows` carry `funding_route`, `personal_rows` carry
    `personal_billed` (true and false rows; the true one is the bucket). A missing row is an empty bucket."""
    by_route = {g.keys.get("funding_route"): g for g in route_rows}
    personal = next((g for g in personal_rows if g.keys.get("personal_billed") is True), None)
    return {
        "funding": {route: _group_bucket(by_route.get(route)) for route in FUNDING_ROUTES},
        "personal_billed": _group_bucket(personal),
    }


# ── Same-scope pricing of one amount (EPMCDME-17152) ─────────────────────────
# `cost_incomplete`, `unpriced_models` and `cost_unknown` of ONE amount (a user, a repository, a branch...), computed
# over exactly the sessions behind that amount's `cost_usd`; never taken from the window or from a funding bucket.


EVIDENCE = "_priced"  # internal: whether the amount had a used token kind with a rate (True / False / None unknown)


def _pricing(incomplete: bool, names: Any, cost: float, has_priced: bool | None) -> dict[str, Any]:
    """`cost_unknown` is true for incomplete amounts when the supplied evidence indicates no priced usage.
    A positive cost is evidence of priced usage. For an incomplete zero, live pricing uses `has_priced`,
    while stored rollups supply False and classify the amount as unknown. If the source supplies None,
    an incomplete zero retains `cost_unknown`=None. The evidence rides along as `EVIDENCE`, for the roll-ups that
    merge amounts; `without_evidence` / `drop_evidence` take it off before a row is answered."""
    evidence = True if cost > 0 else has_priced
    unknown: bool | None = False
    if incomplete:
        unknown = None if evidence is None else not evidence
    return {
        "cost_incomplete": incomplete,
        "unpriced_models": unpriced_display_names(names) if incomplete else [],
        "cost_unknown": unknown,
        EVIDENCE: evidence,
    }


def without_evidence(pricing: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in pricing.items() if k != EVIDENCE}


def drop_evidence(rows: list[dict]) -> None:
    """Takes the merge evidence off rows that nest `branches` / `repositories` (in place)."""
    for row in rows:
        row.pop(EVIDENCE, None)
        for child in ("branches", "repositories"):
            drop_evidence(row.get(child) or [])


def group_pricing(group: CostGroup | None) -> dict[str, Any]:
    """The pricing of one SQL-grouped amount; no group (no money row) has no usage: complete, nothing priced."""
    if group is None:
        return without_evidence(_pricing(False, (), 0.0, False))
    return without_evidence(_pricing(group.cost_incomplete, group.unpriced_models, group.cost_usd, group.has_priced))


def row_pricing(row: dict) -> dict[str, Any]:
    """The pricing of one reader `group` row (`unpriced_models`, `cost_incomplete`, `cost_priced`, `cost_usd`), with
    the merge evidence."""
    return _pricing(
        bool(row.get("cost_incomplete")),
        row.get("unpriced_models") or (),
        float(row.get("cost_usd") or 0),
        None if row.get("cost_priced") is None else bool(row["cost_priced"]),
    )


def pricing_of_rows(rows: list[dict]) -> dict[str, Any]:
    """The pricing of the `price_bucket_rows` rows of one amount (already scoped to its sessions)."""
    missing = [r["model"] for r in rows if r["missing"]]
    cost = sum(r["cost_usd"] for r in rows)
    return without_evidence(_pricing(bool(missing), missing, cost, any(r["priced"] for r in rows)))


def session_index(priced: list[dict]) -> dict[str, list[int]]:
    """The positions of the `price_bucket_rows` rows by session, built once for the per-entity reads of one response."""
    index: dict[str, list[int]] = {}
    for position, row in enumerate(priced):
        index.setdefault(row["session_id"], []).append(position)
    return index


def rows_of(priced: list[dict], index: dict[str, list[int]], session_ids: Any) -> list[dict]:
    """The rows of `session_ids`, in the order of `priced` (so every sum adds in the order the linear scan did)."""
    return [priced[i] for i in sorted(i for sid in session_ids for i in index.get(sid, ()))]


def merge_pricing(children: Iterable[dict]) -> dict[str, Any]:
    """The pricing of an amount that is the sum of disjoint `children` amounts (branches of a repository...): incomplete
    when any child is, the unpriced names united, unknown only when no child carries priced evidence. A child's
    evidence is its own `EVIDENCE` (a complete child may be an empty one), None when it has none."""
    kids = list(children)
    evidence = [k.get(EVIDENCE) for k in kids]
    has_priced: bool | None = True in evidence
    if not has_priced and None in evidence:
        has_priced = None
    names = {name for k in kids for name in k["unpriced_models"]}
    return _pricing(any(k["cost_incomplete"] for k in kids), names, 0.0, has_priced)
