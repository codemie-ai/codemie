# Fix: CLI Analytics — Wasted Cost Percentage

**Ticket:** EPMCDME-15207  
**Type:** Bug · Priority: Minor · Due: 2026-09-30  
**Reporter:** Anzhalika Staliarova · **Assignee:** Oleh Prusak  
**Epic:** EPMCDME-13580

## Problem

CLI Analytics → Efficiency → Dead Sessions: **WASTED COST** subtitle shows `199.69% of spend`
instead of the correct `21.48% of spend`. The dollar amount (`$0.63`) is correct.

## Root Cause

The `/efficiency` API response did not include `total_cost_usd`. The frontend
(`EfficiencyView.tsx`) therefore used `cache_read_cost_usd` as the denominator:

```
wasted_cost_usd / cache_read_cost_usd * 100 = 0.6324 / 0.3167 * 100 = 199.69%  ❌
wasted_cost_usd / total_cost_usd     * 100 = 0.6324 / 2.9435 * 100 = 21.48%   ✓
```

The backend `get_efficiency()` handler already computes `total_cost` (line 802 of
`cli_analytics_handler.py`) but never exposed it in the serialized response.

## Changes

| Repo | File | Change |
|------|------|--------|
| `codemie` | `src/codemie/rest_api/models/cli_analytics.py` | Add `total_cost_usd: float = 0.0` to `LocalAnalyticsEfficiencyKPIs` |
| `codemie` | `src/codemie/service/analytics/handlers/cli_analytics_handler.py` | Add `"total_cost_usd": total_cost` to `kpis` dict in `get_efficiency()` |
| `codemie-ui` | `src/types/cliAnalytics.ts` | Add `total_cost_usd: number` to `CliAnalyticsEfficiencyKPIs` |
| `codemie-ui` | `src/pages/analytics/components/cli-analytics/views/EfficiencyView.tsx` | Change guard and denominator from `cache_read_cost_usd` → `total_cost_usd` in `wastedCostSubtitle()` |
| `codemie-ui` | `src/pages/analytics/components/cli-analytics/__tests__/EfficiencyView.test.tsx` | Add `total_cost_usd: 10.0` to both KPI fixtures |

## Verification

**Regression values** (from `API_EVIDENCE_SANITIZED.json` attached to ticket):
- `wasted_cost_usd = 0.6323673999999999`
- `total_cost_usd = 2.9434545999999995`
- Expected subtitle: `21.48% of spend` (`_pct()` rounds to 1 decimal → `21.5` internally)

**Manual:** CodeMie Preview → CLI Analytics → Efficiency → developer `bc64ff25` →
period 2026-09-21–23 → Dead Sessions → WASTED COST must show `21.48% of spend`.

**API:** `GET /v1/analytics/cli-analytics/efficiency` response must now include
`data.kpis.total_cost_usd`.

## Edge Cases

- `total_cost_usd = 0` → guard `kpis.total_cost_usd <= 0` returns `'0% of spend'` ✓
- `wasted_cost_usd = 0` → 0 / total × 100 = 0.0% ✓
- No dead sessions → `wasted = 0`, `dead_count = 0` ✓

## Non-Breaking Notes

- New field `total_cost_usd` is additive — existing consumers of `/efficiency` are unaffected.
- `bloat_pct` already used `total_cost` correctly; unchanged.
- Related bug EPMCDME-15214 (line-change metrics = 0) is a separate issue; not touched.
