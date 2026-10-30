---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: 7608413e916562f53ef129ab5f095e29eb00ae1c
---

# Code review — 2026-10-27-epmcdme-16441

**Decision:** approve (final round, confidence medium)  
**Reviewed head:** `7608413` · **Rounds:** final

All four lenses and standards ran; no blocking findings survived. 19 candidates dismissed as noise or by-design behavior (the dropped _scope on cost_daily is equivalent to the new sel-scoped filter; detail resolving cost-less sessions is required by the spec; NULL-model and test-structure concerns are speculative or below the emission floor). AC-G execution evidence (live and existing suites) is left to Stage 7.

## Open — 0

## Coverage

| Acceptance criterion | Status | Notes |
|---|---|---|
| Design 1: subagent_usage rows UNIONed into session model costs, priced at query time | pass | _token_rows used by list and detail |
| Design 2: discard log line, counter, HMAC user hash | pass | gate_discards, ingestor call sites, dedicated key |
| Design 3: cost-less sessions listed with cost_available | pass | LEFT JOIN with include_costless; KPIs unchanged |
| AC-G: live-DB and existing cli_analytics suites pass | pending-stage-7 | execution evidence owned by Stage 7 |

Standards: commit-format pass · code-quality pass · security pass
