# Complexity Assessment — EPMCDME-13556: Backend Ingest Endpoints

**Routing verdict: MEDIUM — single story, no split required.**

---

## Scope breakdown

| Component | Effort | Files |
|---|---|---|
| New ingest router (3 endpoints) | S | 1 new file (~150 LOC) |
| httpx forwarding for logs + metrics | S | within ingest_router.py |
| Hook events → OTel JSON conversion | M | within ingest_router.py (~60 LOC) |
| Config: `ANALYTICS_OTLP_ENDPOINT` | XS | 1 line in config.py |
| Main app router registration | XS | 1 line in main.py |
| Makefile `clickhouse-schema` target | XS | 1-2 lines in Makefile |
| Tests (3 endpoints, ~4 cases each) | S | 1 new file (~120 LOC) |

**Total estimated production LOC:** 200–250  
**Total estimated test LOC:** 100–130  
**Files touched:** 5–6

---

## Complexity signals

**LOW factors:**
- OTel Collector already running in docker-compose (EPMCDME-13558 complete)
- ClickHouse schema already defined and agreed (EPMCDME-13554 complete)
- `Depends(authenticate)` pattern is copy-paste from existing routers
- httpx is already a project dependency
- No database migrations, no model changes, no ES query changes

**MEDIUM factors:**
- OTLP/HTTP JSON envelope structure requires careful field mapping
- Hook event → OTel record conversion has a defined spec (ClickHouse schema drives attribute names) but is non-trivial to get exactly right
- Error handling for unreachable collector needs to be graceful (503)
- Two content-type branches for the forwarding logic (protobuf passthrough vs JSON construction)

**No SPLIT indicators:** All three endpoints share the same router, same auth, same collector target. Splitting would create more overhead than value.

---

## Risk summary

- **Blocking risk:** None — all prerequisites (EPMCDME-13553 auth proxy, EPMCDME-13558 OTel Collector, EPMCDME-13554 schema) are in place or on the same branch.
- **Non-blocking risk:** `ANALYTICS_OTLP_ENDPOINT` default value (`http://otelcol:4318`) only resolves inside Docker network. Local dev requires `.env` override. Document in config.py comment.

---

## Effort estimate

**2–3 focused hours** including TDD cycles. No external blockers.
