# Technical Research (sliced from parent: codemie repository)

## 1. Original Context
Child of EPMCDME-16440; see ../../technical-analysis.md Section 1.

## 2. Codebase Findings
- Router `rest_api/routers/cli_analytics.py`, prefix `/v1/analytics/cli-analytics`; ingest routes `event-hooks`, `logs`, `metrics`, `traces`; `_ensure_enabled` uses customer_config feature `cliAnalytics`. Ingest reads `X-CodeMie-Project` and calls `build_ingest_gate(user, header)`.
- `service/analytics/cli_analytics_project_gate.py` (dirty in working copy): blank header, explicit-body conflict, unknown/inaccessible app → discard HTTP 200; `ENABLE_USER_MANAGEMENT` live membership check for non-admins; `application.cli_analytics_enabled=false` → discard; policy lookup failure → 503 (CLI retries). Discards are final.
- `Application.cli_analytics_enabled`: per-project, default false; set by `service/project/cli_analytics_policy.py`.
- Personal project: `service/project/personal_project_service.py` (name = email, `project_type='personal'`, member non-admin, creation non-blocking, may fail). Default: `rest_api/security/user.py` `resolve_default_project`/`resolve_membership_default`, `User.default_project`; LiteLLM proxy path uses it (`enterprise/litellm/proxy_router.py` ~435-460).
- Storage: `repository/cli_analytics/postgres/` (ingestor ~259 stamps `canonical_project`, migrations, session_dims derived from hook_events). Typed hook keys fixed by `.ai-run/guides/integration/cli-analytics-events.md` rule 8; rule 4 missing string = "".
- Admin list mechanism: dynamic customer config (`service/customer_config_declarations.py` FieldType INPUT/TEXTAREA/SWITCH; `configs/customer_config.py` `get_feature_setting`, TTL 60s; `PUT /customer-config/...` guarded by `require_customer_config_write`).
- Identity: authenticated `User` on the request is the only owner source.

## 3. Documentation Findings
`cli-analytics-events.md`: contract-first ("agree it before a sender or server implements it"); "default project never inferred" must be amended here.

## 4. Testing Landscape
Existing tests: `tests/codemie/service/analytics/test_cli_analytics_project_gate.py`, `tests/codemie/rest_api/routers/test_cli_analytics_project_gate.py` (both modified in working copy). No coverage of subscription/origin.

## 5. Configuration and Environment
Env `ENABLE_USER_MANAGEMENT`; customer-config feature `cliAnalytics`; new dynamic setting for corporate organizations (no default entries).

## 6. Risk Indicators
- Gate rule reversal ("default never inferred", discards final) vs 16025 in-flight edits.
- Personal/default destination with `cli_analytics_enabled=false`, missing membership, creation failure.
- Typed-column contract change, migration, legacy rows.
- Consent/opt-out (16396, 16394) must keep working.
- Unverified: exact `backfill.reclassify` replay semantics.

## 7. Summary for Complexity Assessment
Backend-only change spanning gate, destination resolution, settings, storage migration and contract doc; high novelty, security/privacy sensitive; tests exist for gate, none for new behavior.

## 8. External References
Produces contract for `cli-classification-routing`.
