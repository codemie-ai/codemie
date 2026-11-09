# ruff: noqa: T201
"""EPMCDME-16522 blast radius over recorded CLI sessions.

Reads per-model token sums from the local CLI analytics tables (same union as reader._token_rows), loads the LiteLLM
catalog from {LITE_LLM_URL}/model/info parsed by the backend's own mapper, and prices each model with the backend's
CatalogPricer, so every verdict is the one the running backend would give. Read-only.
Run from the repo root: poetry run python <this file>
"""

import json
from unittest.mock import patch

import httpx
from sqlalchemy import create_engine, text

from codemie.configs import config
from codemie.configs.llm_config import ModelType
from codemie.enterprise.litellm.models import get_model_type, map_litellm_to_llm_model
from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer, TokenCounts

SCHEMA = config.CLI_ANALYTICS_PG_SCHEMA
SQL = f"""
SELECT t.model_name,
       count(DISTINCT t.session_id)            AS sessions,
       sum(t.input_tokens)::bigint             AS input_tokens,
       sum(t.output_tokens)::bigint            AS output_tokens,
       sum(t.cache_read_tokens)::bigint        AS cache_read_tokens,
       sum(t.cache_creation_tokens)::bigint    AS cache_creation_tokens,
       sum(t.cache_creation_5m_tokens)::bigint AS cache_5m,
       sum(t.cache_creation_1h_tokens)::bigint AS cache_1h
FROM (
  SELECT c.session_id, c.model_name, c.input_tokens, c.output_tokens, c.cache_read_tokens,
         c.cache_creation_tokens, c.cache_creation_5m_tokens, c.cache_creation_1h_tokens
  FROM {SCHEMA}.cost_daily c
  UNION ALL
  SELECT u.session_id, u.model, coalesce(u.input_tokens, 0), coalesce(u.output_tokens, 0),
         coalesce(u.cache_read_tokens, 0), coalesce(u.cache_creation_tokens, 0),
         u.cache_creation_5m_tokens, u.cache_creation_1h_tokens
  FROM {SCHEMA}.session_usage u WHERE u.attrs->>'token_source' = 'subagent_usage'
) t
GROUP BY t.model_name ORDER BY sessions DESC
"""

url = config.PG_URL or (
    f"postgresql+psycopg://{config.POSTGRES_USER}:{config.POSTGRES_PASSWORD}"
    f"@{config.POSTGRES_HOST}:{config.POSTGRES_PORT}/{config.POSTGRES_DB}"
)
with create_engine(url).connect() as conn:
    rows = [dict(r._mapping) for r in conn.execute(text(SQL))]
total_sessions_sql = f"SELECT count(DISTINCT session_id) FROM {SCHEMA}.cost_daily"
with create_engine(url).connect() as conn:
    total_sessions = conn.execute(text(total_sessions_sql)).scalar()

resp = httpx.get(
    f"{config.LITE_LLM_URL.rstrip('/')}/model/info",
    headers={"Authorization": f"Bearer {config.LITE_LLM_MASTER_KEY}"},
    timeout=60,
)
resp.raise_for_status()
catalog = [map_litellm_to_llm_model(m) for m in resp.json()["data"] if get_model_type(m) == ModelType.CHAT]

MODULE = "codemie.service.analytics.handlers.coding_agent_pricing"
out = []
with (
    patch(f"{MODULE}.llm_service.get_all_llm_model_info", return_value=catalog),
    patch(f"{MODULE}.llm_service.catalog_source", return_value="litellm"),
):
    for r in rows:
        model = r["model_name"] or ""
        pricer = CatalogPricer()
        counts = TokenCounts(
            input=r["input_tokens"] or 0,
            output=r["output_tokens"] or 0,
            cache_read=r["cache_read_tokens"] or 0,
            cache_creation=r["cache_creation_tokens"] or 0,
            cache_5m=r["cache_5m"],
            cache_1h=r["cache_1h"],
        )
        cost = pricer.price(model, counts)
        cfg = pricer._cost(model)
        if cfg is None:
            verdict = "NOT_IN_CATALOG (API flags it; UI shows $0.00)"
        elif not cfg.input and not cfg.output:
            verdict = "ZERO_RATES (silent $0, the ticket's bug)"
        elif pricer.unpriced:
            verdict = "PARTLY_PRICED (API flags it; cost understated, UI shows a number)"
        else:
            verdict = "PRICED"
        out.append({**r, "cost_usd": round(cost, 6), "verdict": verdict})

print(json.dumps({"total_sessions": total_sessions, "models": out}, indent=1, default=str))
