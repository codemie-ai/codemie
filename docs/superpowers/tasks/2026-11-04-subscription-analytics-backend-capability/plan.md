# EPMCDME-16849 Backend Capability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Explicit-consent routing (N1), private N0 for derived personal/unknown, first-wins funding, reader buckets and incomplete-price flag, behind an operator switch. Spec: `spec.md` rev 3 (do not modify); item numbers below are its Behaviour items.

**Architecture:** Additive nullable `session_dims` triple (contract_version, work_consent, funding_route); one `PublicationContract` parser run before the body on all four ingest routes; one `private_classes` predicate read by every privacy consumer; new `ConsentGate` behind the same ports; separate reader row query priced per bucket.

**Tech Stack:** Python, FastAPI, SQLModel/Postgres, alembic (`src/external/alembic_cli_analytics`), pytest.

## Global Constraints

- Commit per task using the repository's existing convention: prefix `EPMCDME-16849:` per `.ai-run/guides/standards/git-workflow.md`. Git writes: `git -C C:/codemie-dev/_local/epmcdme-16849/codemie add -- <explicit files>` then, as a separate call, `git -C <same> commit -F <message file written with Write> -- <same files>`. No push, no MR.
- Migration before code; setting `CLI_ANALYTICS_PUBLICATION_CONTRACTS_ENABLED` default false; legacy L0/L1 behaviour unchanged (one test per regime).
- Exact-project echo matching is mandatory for N1 only. N0 corporate/codemie keeps today's `resolve_destination` fallbacks; do not compare it to a guessed header project.
- Keep existing guards (size/content-type 415/413/400, stale-policy 409, `_usable`, `validate_destination`, schema checks). Additive response fields only. Dirty 16025 gate files are not imported.
- Tests go through the real `EvidenceGate`/ingestor/reader paths with small fixtures; run targeted `pytest` only. Windows: `pyroscope-io` is not installable; if imports fail, stub it for the test run via a `conftest`-free `sys.modules['pyroscope']=MagicMock()` in the command (`python -c`/env), never in committed code. Load `.ai-run/guides/testing/testing-patterns.md` and `quality-gates.md` first.
- Counter `funding_claim_differs` keyed by reason only; funding never affects access/routing/consent/binding/visibility.

---

### Task 1: Migration and binding columns
**Files:** Create alembic revision in `src/external/alembic_cli_analytics/versions/` (down_revision = latest head); Modify `repository/cli_analytics/ports.py` (`SessionBinding`), `repository/cli_analytics/postgres/session_binding.py` (`stored_binding`, `BINDINGS_SQL`, `_BINDING_COLUMNS`) and affected ingestor callsites; confirm definitions in source before edits. Test `tests/codemie/repository/cli_analytics/`.
**Produces:** nullable `contract_version`, `work_consent`, `funding_route` on `session_dims`, fields on `SessionBinding`.
Test-first: yes — a binding written then read returns the three fields; a legacy row reads NULL/None; old insert without them still works.
- [ ] Failing test on `stored_binding` round trip; add revision + columns + dataclass fields; run test and the alembic head check used by existing tests; commit.

### Task 2: Rollout setting and capability advertisement (item 9)
**Files:** Modify config (`CLI_ANALYTICS_PUBLICATION_CONTRACTS_ENABLED`, default false, follow `.ai-run/guides/development/configuration-patterns.md`), classification-policy endpoint `routers/cli_analytics.py:164-170`; Test `tests/codemie/rest_api/routers/`.
Test-first: yes — policy response has `publication_contracts: ["1"]` only when setting true; key absent when false.
- [ ] Failing test both states; implement; commit.

### Task 3: Contract parser and pre-body check on all four routes (item 1)
**Files:** Create `PublicationContract(version, consent, funding)` + `parse_publication_contract(headers)` in the existing `rest_api/routers/cli_analytics_ingest_route.py` or a minimal shared sibling at the appropriate layer; Modify `routers/cli_analytics.py:191,444` (`_ingest_otlp`, `ingest_event_hooks`: call `check_publication_contract(request, user)` after `_ensure_enabled`, before 415/413/400 and `request.body()`), `IngestRoute` to accept the result; Test route + parser tests.
**Produces:** `check_publication_contract(request, user) -> PublicationContract | None` (None = legacy). Uses headers, setting, `classify`, user type only (no DB). Rules: `getlist`; legacy only when all three new headers absent; new header without a single valid `Publication-Contract: 1` -> 422 (`publication_contract_missing`/`_unsupported`); `Work-Consent` other than `explicit-folder` -> `consent_invalid`; consent without non-blank project -> `consent_without_project`; setting off + contract -> `503 publication_contract_unavailable` hold; N0 + derived personal/unknown + excluded type (`user_type_validator.py:41`) -> `503 personal_storage_unsupported` hold. Body `{"reason","disposition"}`.
Test-first: yes — (a) parametrised parser cases (empty, repeated, unknown, consent/funding without contract); (b) per route (logs, metrics, traces, hooks) invalid contract with body cap lowered returns 422 not 413 and body not read; (c) switch off holds 503 and legacy unchanged.
- [ ] Failing tests; implement; run; commit.

### Task 4: `private_classes` and its four consumers (item 2)
**Files:** Modify `repository/cli_analytics/scope.py` (`private_classes(contract_version, work_consent)`: NULL -> {personal}; (1,no) -> {personal, unknown}; (1,yes) -> {}), `cli_analytics_project_gate.py` (`EvidenceGate.__init__` ~:380 `self._private`; `resolve_destination(..., private: bool | None = None)` replacing the `_owns_personal_project` call at ~:195, None keeps legacy test; `validate_destination` ~:349 same set), `session_binding.py:121 private_personal(binding)`; Test gate and session_binding tests.
Test-first: yes — predicate truth table; real `EvidenceGate`+ingestor: N0 unknown for a regular user lands in personal project, for an excluded user is held (Task 3); N0 unknown on a session with legacy rows or bound elsewhere -> conflict, never adoption; L1 unchanged (NULL triple).
- [ ] Failing tests; implement; commit.

### Task 5: N0 wiring and (contract, consent) compare (items 2, 5)
**Files:** Modify `IngestRoute` -> `build_evidence_gate` (add contract args; check existing tests before changing the keyword set), `session_binding.py` (one helper next to `same()` comparing (contract, consent), called at the start of both gates' `resolve_session`; L1 is (none, none)), ingestor first-binding write stores the triple. Derived codemie/corporate N0 uses today's route.
Test-first: yes — legacy-bound session + contract request -> `session_contract_conflict` final discard and the reverse; consent request on a personally bound session conflicts; N0 codemie/corporate resolves via existing fallback (not compared to header project); first binding stores (1, no).
- [ ] Failing tests; implement; commit.

### Task 6: ConsentGate (item 3)
**Files:** Create `ConsentGate` in `cli_analytics_project_gate.py` (same ports; `EvidenceGate` untouched), selected by `build_evidence_gate` for N1; reuse `_usable`, `validate_destination`, `same()`, context-conflict check; stale-policy 409 stays in `IngestRoute`; Test gate + ingestor.
Behaviour: bound -> contract/consent mismatch `session_contract_conflict`, header != bound project (case-insensitive exact echo) `session_project_conflict`, deleted/revoked `session_project_unavailable`, disabled `cli_analytics_disabled`, no re-resolve or personal-anchor check. Unbound with legacy rows naming another project -> `session_project_conflict`. Unbound: unusable -> `503 project_unavailable`; personal-type project -> `422 consent_personal_project`; `cli_analytics_enabled` false -> `503 project_analytics_not_enabled`; else bind canonical `application.name`, origin `linked_folder`, class derived, `work_consent` stored. `Project-Source` ignored. Response headers on accepted new-contract requests (OTLP and hook): `X-CodeMie-Publication-Contract: 1`, `X-CodeMie-Destination`, `X-CodeMie-Bound-Project`.
Test-first: yes — table of the cases above for any class/user type; real list/detail/event-content reader/handler paths allow super/project admin and deny other-project admin/non-admin, while private-owner scope stays intact (not only a manually assembled ViewerScope/helper assertion); echo headers present on accept, absent on hold/discard.
- [ ] Failing tests; implement; commit.

### Task 7: Funding first-wins (item 4)
**Files:** Modify `IngestRoute` (funding value via the existing routed boolean parse, `route:110`), first-binding write stores `funding_route`; later differing claim increments the one `funding_claim_differs` counter; Test route + ingestor.
Test-first: yes — class x routed x claim table (codemie+true+api_based -> api_based; codemie+true+subscription -> unknown; missing/empty/repeated/unknown -> unknown; non-codemie+false+valid -> value); second request with a different claim leaves stored value unchanged and counts once; funding never changes access or routing.
- [ ] Failing tests; implement; commit.

### Task 8: Reader buckets, personal_billed, cost_incomplete, budget isolation evidence (items 6-8)
**Files:** Modify `repository/cli_analytics/postgres/reader.py` (new method returning per-(session, model) token rows of the same `_sessions_cte` scope with `funding_route` and `sql_personal_billed` flag, no spend filter; leave `get_session_model_costs(..., spend_only=True)` untouched), `service/analytics/handlers/cli_analytics_handler.py` (price per bucket with its own pricer, reuse `coding_agent_pricing.py:96-142`), `routers/cli_analytics.py:402,748` (`_metadata` gains `cost_incomplete = bool(unpriced_models)`; responses add `funding: {api_based, subscription, unknown}` and `personal_billed`, each `{cost_usd, session_count, cost_incomplete, unpriced_models}`); Test reader/handler.
Also modify `rest_api/models/cli_analytics.py`: define the additive bucket models/fields on each affected response and row plus `LocalAnalyticsResponseMetadata.cost_incomplete`. `_respond` validates through these Pydantic models before model_dump; handler-only keys would be discarded. Inspect the existing public-contract/schema guards and update only intended deltas.
Test-first: yes — (a) real-fixture reader test: a personal-billed session appears in `personal_billed` and is absent from existing spend totals; existing monetary fields are unchanged, while session/token/activity counts correctly include the added session; on an unchanged dataset, existing fields retain baseline values; (b) per-entity check `spend + personal_billed == priced total` (grouping consistency only); (c) unpriced model yields `cost_incomplete: true`, not a bare 0, with flags scoped per bucket; (d) read scope unchanged: private rows stay out of work-project reads, no member/owner access added; (e) verify the real route/_respond serialized JSON retains all new bucket and metadata fields, including defaults/empty results, rather than checking handler dictionaries alone.
- [ ] Failing tests; implement; commit.

### Task 9: Handoff deliverable and wording sync notes (non-code)
**Files:** Create `C:/codemie-dev/_local/epmcdme-16849/evidence/backend-contract-handoff.md` and `C:/codemie-dev/_local/epmcdme-16849/evidence/wording-sync-notes.md`.
Test-first: no — documentation only.
- [ ] Handoff: concrete wire contract as implemented (three headers, values, status/reason/disposition table, echo headers, capability key, N0/N1 regimes, rev-3 semantics: first-wins funding, uniform 503 holds, bound final discards, read scope, rollback limit) plus outstanding CLI acceptance cases (consent user-local and revocable, per-hook folder check, first-hold notice, treat 200 without echo as non-success, unadvertised capability held, synthetic-only echo evidence, UI additive-field tolerance untraced). Sync notes: copy the spec's "Handoff notes" child story.md and parent edits as a proposed diff list; administrative, not a product-owner run; do not edit parent/child files. Commit repo files only (evidence folder is outside the repo, so nothing to commit for this task).

## Negative-constraint pass

negative-constraints checked against spec:
- No budget-exclusion code added: Task 8 adds evidence tests only.
- No ordinary-member/owner access: Task 8 test (d); no scope.py allow change.
- No fallback/relocation for consent or personal: Tasks 4-6 (no default/registered/linked fallback in ConsentGate; no history rewrite, NULL stays legacy).
- Funding never an access input, not compared: Tasks 5 (compare only contract, consent) and 7.
- No change to classification or `policy_version`; `EvidenceGate` not refactored (Tasks 4-6 touch only the listed consumers).
- No rollback orchestration, no UI/CLI/Control Plane work: none planned.
- Consent/funding header without contract is 422, empty/repeated is invalid: Task 3.
- No git push/MR, no whole-suite gate or browser verification task (owned by the flow).
