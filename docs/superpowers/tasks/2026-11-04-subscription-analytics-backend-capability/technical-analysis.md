# Technical Research (backend slice: codemie)

**Task**: EPMCDME-16849 subscription analytics with private-session isolation; backend (`codemie`) slice only
**Generated**: 2026-11-04 (sliced from parent technical-analysis.md; no new research)
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-16849 "[CLI Analytics] Track subscription-funded work in project analytics with private-session isolation". Authoritative story: `C:/codemie-dev/_local/epmcdme-16849/jira.json` (ACs 1-10). Technical notes: `C:/codemie-dev/_local/epmcdme-16440/subscription-analytics-technical.md`.
Backend-relevant requirements: personal-paid sessions of regular users may be placed in an explicitly connected project (check membership/enablement in the destination project); personal sessions owner-only where personal storage exists, never fall back to a shared project; expose API-based vs subscription consumption separately, preserving corporate/personal/unknown classification; project attribution, funding and visibility independent; subscription estimates from model/token consumption at catalog prices; personally funded consumption excluded from project budget spend; missing prices reported, not free; no relocation of existing history or broadening of access to previously private records; old clients must not gain broader visibility; no separate personal toggle. Out of scope: Control Plane, UI work, ticket attribution, Cost per Point, pet-project detection.

---

## 2. Codebase Findings

### Repository classification
- `codemie` (backend): **affected** (this slice). Destination routing for regular users' Personal-paid sessions, category aggregates, missing-price reporting.
- `codemie-code` (CLI): reference only, no write scope.
- `codemie-ui`: not_affected.

### Resolved baselines
- Backend `origin/main` = `25a3458506ab12b3c93ce2540d096480cd2fac94`. Findings read only from this ref. It already contains the 16440 classification/routing/read work (`service/analytics/cli_classification.py`). Checked-out repo sits on a stale 16025 branch; dirty files and 16025 branches are not imported.
- (CLI baseline for reference: `origin/main` `916710fbeb309fcaf07fb412c2e08b9711eb1755`.)

### Existing Implementations

**Project link honoured by backend**
- `cli_analytics_project_gate.py` `_shared_route`: `PROJECT_SOURCES = {linked_folder, profile}`; header project is routed only with such a source and only if usable/accessible; else note `linked_project_unavailable` -> registered repository -> default project.

**Funding classification (backend)**
- Backend derives the class itself (`cli_classification.py` `classify`, `policy_version`; corporate orgs from `customer_config` `cliAnalytics.corporateOrganizations`); the client class is used only for the stale-policy 409 (`cli_analytics_ingest_route.py`).

**Routing and visibility**
- `cli_analytics_project_gate.py:170 resolve_destination`: if `_owns_personal_project(user, subscription_type)` (`personal` and user type not excluded, :222) -> ALWAYS the user's own personal project (`_owned_personal_project`, provisioned via `personal_project_service.ensure_personal_project_async`; failure -> retryable `personal_destination_unavailable`), header project ignored. For user types excluded from personal projects (`user_type_validator.py:41 is_personal_project_excluded`: external, service account) a Personal-paid session is routed like any other (linked project -> registered repo -> default) and no personal project is created. A shared destination needs `cli_analytics_enabled`; a personal one needs ownership only. **No `personalIngestEnabled` switch on main.** No default project -> retryable 503 (`default_project_unavailable`), never rerouted.
- `EvidenceGate` (:355) and `postgres/session_binding.py`: the first accepted evidence request binds owner + destination (`subscription_type`, `project_origin`, `route_note`, `project_name`) in `session_dims`; later requests stored under the binding; `session_owner_conflict` / `session_project_conflict` discards (`ports.py:138`). The legacy `_ProjectIngestGate` (header project only; checks project active, user access, live membership, `cli_analytics_enabled`) is used when no evidence header is present (`cli_analytics_ingest_route.py:101`).
- Visibility: `repository/cli_analytics/scope.py` truth table. Private row P = `project_origin 'personal'`, or `subscription_type 'personal'` with a non-shared origin. A Personal-paid session routed to a WORK project (shared origin) is class N: follows project read permissions "while its cost stays out of spend" (`sql_personal_billed`, `scope.py:107`, used at `reader.py:89`). Readers apply `sql_private` (`reader.py:977`) and `sql_subscription_type`; lists carry `subscription_types` and `project_origins` (`reader.py:93-95`). Visibility, attribution and funding are already independent columns; the excluded-user path already behaves as AC1/AC6 describe.

**Consumption and cost**
- `service/analytics/handlers/coding_agent_pricing.py` catalog lookup with an `unpriced` set (models with no catalog entry contribute 0 and are listed, :100-142); the reader prices per-model token sums at query time (`reader.py:540,1069`). Exposure of `unpriced` in analytics API responses was not found.
- `usage_attribution/funding.py` concerns platform-budget funding of Web Platform members (CLI records "carry the customer they were billed to").
- Budget spend: `sql_personal_billed` keeps Personal-billed CLI sessions out of spend in readers. A link to `project_budget_service` / `spend_collector_service` was not found by grep.
- No API-vs-subscription category or dimension found in readers or routers (`cli_insights*` "classification" is workflow/category). No funding-category aggregates by project/user/repo/model.

**Session binding**
- Backend binding makes a session's owner/project fixed after the first accepted request.

**Other touched code**: backend `repository/cli_analytics/postgres/{ingestor,otlp,rollups,reader}.py`, `service/project/personal_project_service.py`.

### Decisions Already in the Code
- Personal-paid of a personal-capable user always goes to the personal project, header project ignored (`resolve_destination`); alternative = honour an explicit linked project. Recorded reason: private owner-only storage ("spec 5 and 9"). Excluded user types are the existing exception.
- A Personal-paid session in a work project keeps funding (`subscription_type`), origin and spend separate (`scope.py` docstring). The convention to follow.
- Backend derives the class; client claim advisory (stale-policy only). Fail closed: absent scope denies (`scope.py`).
- Backend never discards for a missing destination: retryable 503, CLI retains its spool ~24h (`resolve_destination` AC9 comment).
- Old clients: absence of every `EVIDENCE_HEADERS` item selects the legacy gate.

### Task Statements Checked Against the Code
- "Personal-classified sessions of regular users are routed to their personal projects even when concerning a work project": holds (`_owns_personal_project`, `resolve_destination`); consumers `EvidenceGate`, `session_binding`, readers via `project_origin='personal'`.
- "Personal sessions owner-only; unavailable personal storage never falls back to a shared project": holds (`_owned_personal_project` raises retryable; excluded types route to work projects by design).
- "Exclude personally funded consumption from project budget spend": holds for readers (`sql_personal_billed`); budget-service link unverified.
- "Missing prices reported, not free": partly; `unpriced` exists in the calculator, API exposure not found.
- "API vs subscription separately": nothing found in backend code serving it.
- "No separate personal-analytics toggle": holds on main.
- "Old clients must not gain broader visibility": legacy gate keyed on project header + membership; unchanged by absence of new fields.
- "No Control Plane changes": nothing in this repo; unverified outside.

### Consumers of Changed Contracts
- Headers consumed (producers: codemie-code (reference only, no write scope), `header-injection.plugin.ts`, `evidence-send.ts`): `X-CodeMie-Subscription-Type/-Organization/-Project/-Project-Source/-Repo-Remote/-Policy-Version/-Routed-Via-CodeMie/-Session-Id`; backend consumers `cli_analytics_ingest_route.py`, `cli_analytics_project_gate.py`, `postgres/ingestor.py`, `otlp.py`, `session_binding.py`, `ports.py`.
- `resolve_destination` / `DestinationDecision` (`project`, `project_type`, `project_origin`, `route_note`, `reason`): `EvidenceGate`, ingestor, `session_dims` columns, `scope.py` row classes P/N/M, `reader.py` list/detail/aggregate queries, usage-attribution readers. A change to the personal rule moves new rows between P and N.
- Already stored sessions: P rows stay private; binding set once, so only new bindings are affected.
- UI cli-insights consumers read response fields; additive-only safety unverified.
- CLI-produced contracts (codemie-code (reference only, no write scope)): `project_source` values `linked_folder | profile | none` computed at stamp time and frozen in the evidence record; `organization` sent only when `oauthAccountPays()`; no field says API vs subscription; CLI caches policy via `policy-cache.ts` from the backend policy endpoint.

### Architecture and Layers Affected
Backend: ingest router -> gate/destination -> ingestor/session binding (Postgres, alembic `alembic_cli_analytics`) -> rollups/reader -> analytics API routers; project/personal-project services; budget/spend.

### Integration Points
- Inbound: CLI ingest POST (OTLP and plugin events) with `X-CodeMie-*` headers; backend serves policy endpoint (organizations, version) cached by the CLI.
- Backend `customer_config` feature settings (`cliAnalytics.*`); Postgres analytics store; ES for legacy insights.

### Patterns and Conventions
- Backend ports/adapters (`IngestGate`), typed `TelemetryStorageUnavailableError`, alembic chain per store (chain onto latest head; a1c1a0000007 is 16440's). Fail closed.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- Backend: `.ai-run/guides/integration/cli-analytics-events.md` (contract), `cli-analytics-storage.md`.
- Prior stories/evidence: `C:/codemie-dev/_local/epmcdme-16440/`.

### Architectural Decisions
Spec numbering (D7, spec 4/5/9) referenced in code comments for 16440; spec text lives in 16440 task folders (not read).

### Derived Conventions
Contract agreed before implementation; backward compatibility via absence of evidence headers; owner-scoped reads for private sessions.

---

## 4. Testing Landscape

### Existing Coverage
- Backend (pytest): `tests/codemie/service/analytics/test_cli_analytics_project_gate.py`, `tests/codemie/rest_api/routers/test_cli_analytics_project_gate.py`, `tests/codemie/repository/cli_analytics/`.

### Testing Framework and Patterns
pytest (`pytest.ini`), fixtures/mocks for DB sessions.

### Coverage Gaps
Nothing found testing: personal-funded session landing in a connected project for a regular user; API vs subscription category; missing-price reporting at aggregate level.

---

## 5. Configuration and Environment

### Environment Variables
Backend `CLI_METRICS_CUTOFF_DATE`, `ENABLE_USER_MANAGEMENT`.

### Configuration Files
`customer_config` `cliAnalytics.corporateOrganizations`; project column `cli_analytics_enabled`.

### Feature Flags and Deployment Concerns
No `personalIngestEnabled` switch on origin/main. Alembic chain for the cli_analytics store (`alembic_cli_analytics`) applies to any schema change.

---

## 6. Risk Indicators

- Keeps a mechanism in place: `resolve_destination` always routes a personal-capable user's Personal-paid session to the personal project, header project ignored; AC1 needs the explicit linked project to win for regular users. Consumers: `EvidenceGate`, `session_binding`, `scope.py` P/N/M rows, readers.
- Speculative: changing the rule moves new sessions from P to N; old sessions stay P because binding is set once.
- Gap: API vs subscription not distinguishable beyond `organization` presence; no stored dimension, no aggregates. Speculative: needs a new backend column/aggregate and a CLI field; a client signal is not billing proof.
- Gap: `unpriced` exists in `coding_agent_pricing.py` but not found exposed by the analytics API; a 0 cost can read as free.
- Unverified: link between CLI session cost and project budget spend beyond `sql_personal_billed` (`project_budget_service`, `spend_collector_service` not linked by grep).
- `project_source='profile'` counts as an explicit source for backend routing; "global profile alone must not authorize" depends on the CLI-side folder allowlist, not the backend.
- Old-client compatibility rests on the legacy-gate path; clients sending evidence headers without a new field must not be treated as connected-folder (speculative).
- Client-reported class is advisory only; any new client-reported funding field has the same trust limit (speculative).
- Landing order (speculative): backend accepts new fields before the CLI sends them.

---

## 7. Summary for Complexity Assessment

Backend slice. Origin/main (`25a34585`) already contains classification, session binding, private/shared scope, `sql_personal_billed` spend exclusion and the folder-link source, so visibility, attribution and funding separation is largely built. Remaining backend change surface: one routing rule in `resolve_destination` for personal-capable users with an explicit link (with membership/enablement checks), a stored funding-category dimension with aggregates, and missing-price exposure in the API.

Technical novelty is moderate: privacy-critical routing and a contract extension with old-client compatibility, on existing seams. Coverage is strong for classification, routing, scope and session binding; nothing found covers the new behaviours.

Key risks: privacy regressions at the P/N boundary, additive-only contract, unverified budget-spend linkage.

---

## 8. External References

- `C:/codemie-dev/_local/epmcdme-16849/jira.json`: ACs 1-10.
- `C:/codemie-dev/_local/epmcdme-16440/subscription-analytics-technical.md`: backend personal-paid placement in an explicitly connected project also for regular users, check membership/enablement in destination project; no fallback to shared project for users without personal space; client signals are not independent billing confirmation; order: CLI first, agree rule with Nikita, then repository tasks.
- Root `C:/codemie-dev/AGENTS.md`, backend AGENTS.md read.

## Facts Added by Review

Researched for the names the spec relies on, after the analysis above was written. Read like the facts layer; where a line here contradicts one above, this one was read later.

- Does today: built per request in `_ingest_route` (`routers/cli_analytics.py:173`, passes `build_ingest_gate` as the legacy builder). If none of `EVIDENCE_HEADERS` (:54, 8 headers) is present, `self.gate = build_legacy_gate(user, X-CodeMie-CLI-Project)` and `evidence_session_id` stays None (:101-103). Otherwise: session id required (400 `session_id_required`), `corporate_organizations()` (customer_config feature `cliAnalytics` / `corporateOrganizations`), `routed` from the routed-via-CodeMie header, `client_type` from the subscription-type header, `_organization()` (400 on conflict/invalid/required), `derived = classify(routed, organization, organizations)`, `stale_policy_version` set only when client states both class and version, version differs and `client_type != derived` (:117), then `build_evidence_gate(user, header_project, project_source, repo_remote, subscription_type=derived, client_subscription_type, client_policy_version)` (:120). Property `sender` = user email.
- Consumers: `_ingest_otlp` (`routers/cli_analytics.py:191`, used by `/logs`, `/metrics`, `/traces` at :429-440) passes `route.gate`, `route.sender`, `route.evidence_session_id`, `route.stale_policy_version` to `ingestor.ingest_otlp`; `ingest_event_hooks` (:444-475) passes the same to `ingestor.ingest_hook_events`. `corporate_organizations` is also imported by `routers/cli_analytics.py:87` (classification-policy endpoint, :164-170).
- Sources of figures: `litellm_spend_logs` ledger (`src/codemie/enterprise/litellm/spend_ledger.py`, `SpendLogsRepository`, `repository/spend_logs_repository.py`) and `DefaultSpendLedger`/`LegacyLedger`/`CutoverSpendLedger` (`service/spend/default_ledger.py`, `legacy_ledger.py`, `cutover.py`) that read the ES metric store (`MetricName`, `attributes.cli_request` at `default_ledger.py:115`).
- Consumers: `rest_api/routers/analytics/spending.py:55,107`, `routers/projects/crud.py:61,175`, `agents/tools/platform/platform_tool.py:76,669`, `service/analytics/handlers/assistant_handler.py:298,906,1192`, `handlers/budget_handler.py:60,272`, `budget_usage_service.py:29`, `member_spend_service.py:38`.
- Consumer: only `rest_api/routers/analytics/spending.py:343` (budget-usage endpoint; admin `user_id` target variant).
- Consumers: `routers/analytics/spending.py:408,423,459`, `budget_usage_service.py:281`.
- Personal-paid CLI sessions are excluded from CLI-analytics aggregate spend on the CLI-analytics side only: `scope.py:107 sql_personal_billed`, applied by `reader.py:89` (`_spend`) when `get_session_model_costs(..., spend_only=True)` (`reader.py:538-557`; callers in `cli_analytics_handler.py:472,576,647,732,1246`). Alternative: none found; reason: scope.py docstring (:17-19) "its cost stays out of spend". Direction: stored in `session_dims.project_origin/subscription_type`, read by SQL at query time.
- "No budget/spend reader reads cli_analytics / session_dims rows" (AC6): holds. Evidence: (1) grep of `src/codemie/service/spend/`, `budget_usage_service.py`, `member_spend_service.py`, `budget_handler.py`, `service/budget/` for `cli_analytics|session_dims|CliAnalytics|opentelemetry` returned no hits; (2) importers of `repository.cli_analytics` / `get_cli_analytics_storage` / `session_dims` in `src/` are only the 5 CLI-analytics files plus `postgres_sources.py`, `project.py`, `config.py` listed above (none is a budget or spend reader); (3) metric stores traced: `spend_service` ledgers read (a) `litellm_spend_logs` via `SpendLogsRepository` and (b) the Elasticsearch metric index via `MetricsElasticRepository` (`default_ledger.py` `_METRIC_NAME_FIELD`, `attributes.cli_request` written by `llm_proxy_monitoring_service` from the CLI header on proxied calls); (4) the CLI-analytics ingestor/reader contain no Elasticsearch, `MetricsElastic` or spend-port calls (grep: only comments in `reader.py:964,983`). So CLI OTel/hook data lands only in the CLI-analytics Postgres (`session_dims` and per-session tables) and is never an input to budget enforcement or spend ledgers; the CLI-routed money in budgets comes from proxy metrics/spend logs.
- `IngestRoute` attributes `gate`, `sender`, `evidence_session_id`, `stale_policy_version`: read by `_ingest_otlp` and `ingest_event_hooks` only (`routers/cli_analytics.py:199-206, 461-468`); a new attribute is ignored by both until they pass it on. Tests patch `build_ingest_gate` via `_ingest_route` (:174 comment).
- `build_evidence_gate` keyword set (`cli_analytics_project_gate.py`) is called only from `IngestRoute.__init__` (`cli_analytics_ingest_route.py:120`) among the routes; check tests in `tests/` before adding arguments.
