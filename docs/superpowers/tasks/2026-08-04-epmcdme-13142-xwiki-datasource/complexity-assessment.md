# Implementation Analysis: EPMCDME-13142 — xWiki datasource

**Generated:** 2026-08-04
**Grounding:** `technical-analysis.md` (861 lines, §9 verification addendum authoritative)
**Mode:** initial

## Size: XL (27/36) — SPLIT RECOMMENDED

### Dimension Scores

| Dimension            | Score | Label | Red flag applied |
|----------------------|-------|-------|------------------|
| Component Scope      | 5     | XL    | — |
| Requirements Clarity | 4     | L     | yes (M→L) |
| Technical Risk       | 5     | XL    | yes (L→XL) |
| File Change Estimate | 6     | XXL   | — |
| Dependencies         | 2     | S     | — |
| Affected Layers      | 5     | XL    | — |
| **Total**            | **27**| **XL**| |

---

## Component Scope: XL (5)

- **Affected:** new xWiki loader, new xWiki processor, Alembic migration, `DatasourceTypes` /
  `FullDatasourceTypes` enums, `datasources_config.py` + `datasources-config.yaml`,
  `rest_api/models/index.py` (5 distinct edits), `rest_api/routers/index.py` (POST + PUT + 3 import
  blocks), `datasource_health_check_service.py` (3 edits), `SettingsService.get_xwiki_creds`,
  `SettingsTester` handler, webhook deny-list, `trigger_models.py`, `actors/datasource.py`
  (reindex actor + `_RESUME_DISPATCH`), `bindings/cron.py` — plus a new React form component in
  `codemie-ui`.
- **Layers:** DB-Persistence + Repository + Service + API + Workflow/Triggers + External + UI.
- **Why XL not L:** far past "3–4 components across 2–3 layers". This is a full-stack change with
  4+ components across 8 subsystems (§9.9 inventory). Not XXL because it introduces no new
  abstraction and changes no shared contract — every touchpoint is an existing extension point
  (`BaseDatasourceProcessor`, `BaseDatasourceLoader`, `IndexInfo.new()`, the cron `elif` chain).

## Requirements Clarity: L (4) — red flag applied

- **Status:** scope boundary is crisp (one Space; attachments and true delta reindex explicitly out
  of v1), the donor is named, the 16-file registration inventory is verified, and the ingest →
  chunk → embed → ES → search path was proven end-to-end on this machine today. Base score M (3).
- **Open decisions with rework cost:**
  1. **Raw `xwiki/2.1` markup vs rendered text** (risk 11). `renderedContent` is `None` over REST;
     the `/bin/get/...?xpage=plain` alternative was never probed. Indexing raw markup embeds
     `[[link>>Main.WebHome]]` and `|=Col A|` noise permanently. Cheap now, requires a full reindex
     later.
  2. **`_process_chunk` metadata whitelist** (risk 12). `modified` / `version` / `author` are
     dropped by default; the base class re-applies only `chunk_num`. Wrong choice = full reindex.
  3. **Is the `SettingsTester` handler in scope?** §2.6c calls it "a product call", but the
     integration's Test-connection button raises `SettingsTesterHandlerNotFound` today, so the
     datasource is not usable end-to-end without it.
  4. **What should the health check actually probe**, given `/rest/wikis` returns 200 anonymously
     and with a wrong password.
- **Red flag — "similar to X but different":** the task is framed as "modelled on the Azure DevOps
  Wiki donor", but the analysis establishes the loader is *not* a transcription and that two donor
  behaviours must be **inverted** (`fetch_remote_stats()` swallowing all exceptions and reporting 0;
  reusing `_request()`, which never raises on 4xx). The framing already produced one materially
  false claim in the hand-off briefing (incremental refresh "free and inherited" — §9.1). Bumped
  M (3) → L (4).

## Technical Risk: XL (5) — red flag applied

- **De-risking already banked:** the framework does the heavy lifting — only 3 members of
  `BaseDatasourceProcessor` are truly abstract; batching, chunking, guardrails, retries, stats and
  cron scheduling are inherited from the 1083-line base. Alembic head is confirmed single
  (`t1u2v3w4x5y6`). A live xWiki with six adversarial fixture pages is running and reachable from
  the backend container. Rollback is easy (additive, feature-isolated).
- **Genuine novelty (base L — new approach, no exact precedent):**
  - No total-count field in any xWiki list response → `fetch_remote_stats()` must **enumerate**,
    unlike Confluence's cheap `totalSize`. That number drives both the progress-bar denominator and
    `_check_docs_health()`.
  - Page summaries omit **both** `content` and `modified` → a second GET per page is mandatory
    (N+1), and cheap delta detection from the list endpoint is unavailable.
  - `/spaces/{S}/pages` excludes descendant spaces → indexing "a Space" requires recursive descent.
  - `KB.Onboarding` is simultaneously a page in `KB` **and** a space whose home is
    `KB.Onboarding.WebHome`. A "has children ⇒ is a space" walk duplicates or drops content.
  - Offset pagination with no `next` link and no total; `%20` / Cyrillic path encoding; empty pages
    that must be skipped before `_validate_indexing_result()` sees zero chunks.
  - `_split_documents` keys chunk identity on `metadata["source"]` — collisions silently overwrite.
    Mitigation: use `xwikiAbsoluteUrl`.
- **Red flag — affects authentication/authorization → bump L (4) → XL (5):** verified live that
  `/rest/wikis` returns **200 anonymously and with a wrong password**, so the existing
  `_healthcheck()` green-lights broken credentials; per-space 403s then surface mid-load, where
  `_request()` returns the 403 body as if it were page content. Correct credential validation and
  authz-failure propagation (`UnauthorizedException` / `ConnectionException` so `index.set_error()`
  renders a readable message) is core new work, not a copy.
- **Considered and NOT applied:** "Performance/Scalability as primary concern" — the N+1 GET and
  enumeration cost are real and bounded, but performance is not this ticket's primary concern.
  "Requires data migration" — schema-only, no backfill. "Changes DB schema significantly" — one
  additive nullable JSONB column, byte-for-byte the shape of the ADO Wiki donor migration.

## File Change Estimate: XXL (6)

- **Backend:** 16 files — 3 new (Alembic migration, loader ~150–250 lines, processor ~180 lines)
  and 13 edited, spanning `core/`, `service/`, `datasource/`, `rest_api/`, `triggers/`, `config/`,
  `external/alembic/`.
- **Tests:** new greenfield directory `tests/codemie/datasource/xwiki/` (~2 files); no test exists
  for any xWiki datasource code today.
- **Frontend (`codemie-ui`, separate repo):** 11–12 files including one **new** component
  `IndexTypeXWiki.tsx` (~126 lines by analogy).
- **Totals: ~24 modified, ~6 new, across two repositories** → 16+ modified and 6+ new, which is XXL
  on raw counts. Backend-only this dimension would score XL (5); the ticket as described includes
  the frontend, so it scores 6.
- Three touchpoints (the `xwiki_loader:` YAML block, `get_xwiki_creds`, the `_RESUME_DISPATCH`
  entry) are absent from the original briefing and each fails **silently or at boot**, not in tests —
  a missing YAML block is a Pydantic validation error at import time that stops the whole app
  booting.

## Dependencies: S (2)

- **New packages:** none. Reuses `httpx`, the existing LangChain/Elasticsearch stack, and the
  existing `XWikiConfig` credential model.
- **Config additions:** new `xwiki_loader:` block in `config/datasources/datasources-config.yaml`,
  a new `BaseModel` + `LoadersConfig` field + module constant in `datasources_config.py`.
- **Naming hazard:** `XWikiConfig` is already taken by the credential model — the datasources-config
  class must be `XWikiDatasourceConfig` or imports clash.
- Matches "no new packages, minor config additions; existing library used in a new place".

## Affected Layers: XL (5)

- **Layers changed:** UI (`codemie-ui`) + API (models + router) + Service (settings, health-check,
  processor) + DB-Persistence (Alembic + `IndexInfo` JSONB column) + Workflow/Triggers (actor, cron
  binding, resume dispatch) + External (xWiki REST) + Config.
- **Schema/migration:** yes — one additive nullable JSONB column on `index_info`.
- **Cross-system:** partially — two repositories, one external API.
- **Why XL not L:** the trigger/cron/resume layer pushes this past a plain UI+API+Service+DB
  full-stack change. **Why not XXL:** no infrastructure/deployment changes, a single external API,
  and the FE/BE repo split is the normal shape of every datasource type in this product — not a
  microservice fan-out.
- **Considered and NOT applied:** "Integration with new external service" (would bump Component
  Scope + Affected Layers). xWiki is **not** a new external service to the platform —
  `CredentialTypes.XWIKI`, `XWikiConfig`, `_build_auth_headers()`, `build_spaces_path()` and 24 tool
  classes already exist and are reused. This ticket adds a datasource path over an already-integrated
  service. Applying the flag would double-count integration work that is already done.

---

## Red Flags Summary

**Applied (2):**
1. Requirements Clarity M (3) → L (4): "similar to X but different" — donor-modelled, but the loader
   is not a transcription and two donor behaviours must be inverted; the briefing already contained
   one materially false claim.
2. Technical Risk L (4) → XL (5): affects authorization — `/rest/wikis` returns 200 with wrong
   credentials (verified), and per-space 403 handling is core new loader work.

**Considered and deliberately not applied (4):** new-external-service integration (xWiki already
integrated below the datasource layer); significant DB schema change (one additive nullable column);
data migration (none); performance as primary concern (bounded, not primary).

---

## Routing: SPLIT RECOMMENDED

27/36 sits one point over the L/XL boundary. Per the borderline rule, the tie-break leans **higher**
because both Technical Risk and Component Scope are at XL (5) — the "lean lower" escape requires
Technical Risk ≤ M (3), which is not the case. The closest calibration anchor, EPMCDME-10303
(integrate new cloud service, 26 → XL), was split into 3 stories.

**Why not attempt this as one story:** ~30 files across two repositories, 8 layers, a DB migration,
cron wiring and a genuinely novel loader with six verified API hazards — under a TDD constraint,
against greenfield test coverage. It is hard to review as a single MR, and three of its registration
points fail silently or at boot rather than in tests, so a large diff is exactly the wrong shape for
catching them.

### Suggested decomposition (by dependency, then by layer)

| # | Story | Est. | Content |
|---|-------|------|---------|
| 1 | **xWiki loader + processor (ingest engine)** | L | `xwiki_loader.py`, `xwiki_datasource_processor.py`, `datasources_config.py` + YAML block, both enums, Alembic migration, `tests/codemie/datasource/xwiki/`. Owns all the novel work: recursive space descent, enumeration-based `fetch_remote_stats()`, page-vs-space collision, encoding, empty-page skip, typed exceptions, and the raw-vs-rendered + metadata decisions. Drivable end-to-end against the live fixture wiki with no REST endpoint. |
| 2 | **REST + settings surface** | M | `rest_api/models/index.py` (5 edits), `rest_api/routers/index.py` (POST + PUT), `SettingsService.get_xwiki_creds`, `_check_docs_health` + `health_check_xwiki` arm + `get_invalid_field`, `SettingsTester` handler, webhook deny-list. Includes fixing the credential-validation hole so the health check cannot green-light a wrong password. |
| 3 | **Scheduled reindex** | S–M | `trigger_models.py`, `reindex_xwiki` actor, `bindings/cron.py` branch, `_RESUME_DISPATCH` entry. Ships with the explicit written statement that v1 refresh is a **full delete-and-rebuild**, identical to ADO Wiki / Confluence / SharePoint — not incremental. |
| 4 | **Frontend (`codemie-ui`)** | M | `IndexTypeXWiki.tsx` (new), constants, types, `indexing.ts`, `dataSourceUtils.ts`, `DataSourceDetails.tsx`, `DataSourceForm.tsx`, `IndexTypeField/index.ts`, `useCreateIndex.ts`, `useEditPopupForm.ts`, `store/dataSources.ts`, `ToolkitIcon.tsx`, tests. Separate repo, separate MR regardless. |

Stories 1 → 2 → 3 are strictly ordered by dependency. Story 4 can start once story 2 fixes the
request/response contract. Story 1 is the one carrying real risk and deserves its own review pass.

**Correction to carry into every downstream artifact:** the hand-off's claim that incremental
refresh is "free and inherited" is **false**. The cron actor for every comparable type calls
`reprocess()`, which sets `is_full_reindex = True`, deletes the entire ES index and rebuilds it.
True delta reindex would require `_cleanup_data_for_incremental_reindex()` plus switching the actor
to `incremental_reindex` — a separate ticket.
