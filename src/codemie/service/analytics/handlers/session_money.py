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

"""Catalog money of CLI Analytics sessions and dispatches, priced from their token sums."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer, TokenCounts

logger = logging.getLogger(__name__)


def _i(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else _i(value)


@dataclass
class SessionMoney:
    """Catalog money of one request, every value summed from the same priced (session, model) rows."""

    pricer: CatalogPricer
    total: float = 0.0
    cache_read: float = 0.0
    by_session: dict[str, float] = dataclass_field(default_factory=dict)
    cache_read_by_session: dict[str, float] = dataclass_field(default_factory=dict)
    by_model: dict[str, float] = dataclass_field(default_factory=dict)

    def of_sessions(self, session_ids: list[str]) -> float:
        return sum(self.by_session.get(sid, 0.0) for sid in session_ids)


def token_counts(row: dict[str, Any]) -> TokenCounts:
    return TokenCounts(
        input=_i(row.get("input_tokens")),
        output=_i(row.get("output_tokens")),
        cache_read=_i(row.get("cache_read_tokens")),
        cache_creation=_i(row.get("cache_creation_tokens")),
        cache_5m=_optional_int(row.get("cache_creation_5m_tokens")),
        cache_1h=_optional_int(row.get("cache_creation_1h_tokens")),
    )


def dispatch_money(pricer: CatalogPricer, row: dict[str, Any]) -> float:
    """A dispatch's money: its per-model token sums priced, a parent-window share divided among its ties."""
    money = sum(pricer.price(_s(m.get("model_name")), token_counts(m)) for m in row.get("models") or [])
    # ties is NULL for a dispatch that takes its own requests whole
    return money / (_i(row.get("ties")) or 1)


def price_session_models(rows: list[dict[str, Any]], pricer: CatalogPricer | None = None) -> SessionMoney:
    """Price each (session, model) row of `get_session_model_costs` once at the catalog and sum it every way.
    The warning covers whatever `pricer` priced before, so a caller pricing more rows passes its pricer."""
    money = SessionMoney(pricer=pricer or CatalogPricer())
    for row in rows:
        sid = _s(row.get("session_id"))
        model = _s(row.get("model_name"))
        counts = token_counts(row)
        cost = money.pricer.price(model, counts)
        cache_read = money.pricer.price(model, TokenCounts(cache_read=counts.cache_read))
        money.total += cost
        money.cache_read += cache_read
        money.by_session[sid] = money.by_session.get(sid, 0.0) + cost
        money.cache_read_by_session[sid] = money.cache_read_by_session.get(sid, 0.0) + cache_read
        normalized = _s(row.get("normalized_model_name"))
        if normalized:
            money.by_model[normalized] = money.by_model.get(normalized, 0.0) + cost
    if money.pricer.unpriced:
        logger.warning(
            "cli_analytics: no catalog price for some tokens of models %s - "
            "every money value (cost, cache-read cost, bloat, wasted) is understated",
            money.pricer.unpriced,
        )
    return money
