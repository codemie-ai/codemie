# Spec: backend capability for explicit-connect routing, funding route and incomplete-price flag (EPMCDME-16849, backend child) - rev 3

Baseline `origin/main` 25a34585. Size L (25/36). Story `story.md`; parent D1-D8. Inputs: `technical-analysis.md` (+ facts top-up), CLI spike `proposed-shared-contract.md` rev 2 (reported; names below are the agreed contract), arch-review AR-1..7 (rev 3 answers the second brief; tags below). Delivered from an isolated worktree; the dirty `cli_analytics_project_gate.py` and its tests are preserved, not imported. CLI work and E2E belong to the CLI child. The review found an incomplete spec, not a reproduced disclosure.

## Behaviour

1. **Parse, switch and cheap holds before the body (AR-5).** One `PublicationContract(version, consent, funding)` value type and parser. A function `check_publication_contract(request, user)` runs first in `_ingest_otlp` and `ingest_event_hooks` (`cli_analytics.py:191, 444`; after `_ensure_enabled`, before the 415/413/400 checks and `request.body()`), so all four routes share it; `IngestRoute` receives its result. It uses only headers, the rollout setting, `classify` and the user type: no DB. Size and content-type safeguards stay where they are. Headers are read with `getlist`; legacy only when all three new headers are absent; a new header with `Publication-Contract` absent, empty, repeated or unknown-valued is 422. DB-dependent checks (item 3) run under the session lock after the body is read and decoded, so a held session re-uploads on each retry (bounded by CLI backoff and retention); that is a stated limit, not mitigated here.
2. **Regimes and one privacy decision (AR-1).** Executor: `IngestRoute` -> `EvidenceGate` -> `resolve_session` under the lock -> `guard`/`bind` (`session_binding.py`).

| Regime | Selected by | Gate and route | Stored |
|---|---|---|---|
| L0 | no evidence header, no new header | `_ProjectIngestGate`, header project, never binds; unchanged | none |
| L1 | evidence headers, no new header | `EvidenceGate` + `resolve_destination`; unchanged | NULL triple |
| N0 | contract `1`, no consent | `EvidenceGate`. Derived `codemie`/`corporate`: today's route, all user types. Derived `personal`/`unknown`: private. Personal-capable user -> own personal project (failure stays retryable `personal_destination_unavailable`); excluded type (`user_type_validator.py:41`) -> `503 personal_storage_unsupported` hold from user type and class alone (no DB, before the body), nothing provisioned, never linked/registered/default | (1, no, funding) |
| N1 | contract `1` + `explicit-folder` + non-blank project | new `ConsentGate` behind the same ports; `EvidenceGate` untouched | (1, yes, funding) |

   Derived `unknown` stays `unknown` (stored class unchanged, not relabelled personal, not excluded from spend); only its destination is private. A missing organization list is not evidence that disclosure is allowed. Privacy is decided once: `private_classes(contract_version, work_consent)` in `scope.py` (both layers already import it): legacy/NULL -> {personal}; (1, no) -> {personal, unknown}; (1, yes) -> {}. Every consumer reads it: `EvidenceGate.__init__` computes `self._private` from it (gate:380; used by `_personal_anchor_conflict` and the legacy-adoption branch `if legacy_project and not self._private`); `resolve_destination` gets a keyword `private: bool | None` replacing its internal `_owns_personal_project(user, subscription_type)` call (gate:195; `None` keeps the legacy test, so L1 and existing callers are unchanged); `private_personal(binding)` (`session_binding.py:121`, used by `_resolve_session` for anchor conflict and legacy adoption) becomes `origin == personal and class in private_classes(binding.contract_version, binding.work_consent)`; `validate_destination` (gate:349) applies its "private project never gets a shared origin" check for the same set. Legacy bindings carry NULL, so all four callers behave as today. Tests use the real `EvidenceGate` + ingestor path: N0 unknown for a regular and an excluded user, an N0 unknown request on a session with legacy rows or bound elsewhere (conflict, never adoption), L1 unchanged.
3. **ConsentGate** (exactly the header's project, any class and user type; `Project-Source` ignored; no personal/shared/default fallback, no `linked_project_unavailable`). It keeps the context-conflict check and shares `_usable`, `validate_destination`, `same()`; the stale-policy 409 is computed by `IngestRoute` as today. Under the lock:
   - Bound: stored (contract, consent) must equal the request, else `session_contract_conflict`; header must equal the bound project case-insensitively, else `session_project_conflict`; then existing `validate_destination(bound, True)`. A bound destination that is deleted or revoked is the final, history-keeping `session_project_unavailable`; disabled is final `cli_analytics_disabled`. Never re-resolves the header, reroutes, or runs the personal-anchor check.
   - Unbound with legacy rows naming another project: `session_project_conflict`; never adopts it.
   - Unbound: `_usable` on the header. Not usable (missing, inactive, no access, no membership; one answer, no existence leak) -> `503 project_unavailable` hold. Usable personal-type project -> `422 consent_personal_project` final. Usable, `cli_analytics_enabled` false -> `503 project_analytics_not_enabled` hold. Else bind `application.name` (canonical), origin `linked_folder`, class = derived. Consent is read from `work_consent`, never inferred from the origin.
   - Hold vs loss: an UNBOUND session's unusable destination is a bounded hold (CLI retention window ~24h, then suspend); a BOUND session's is a final discard that keeps history. Holds store nothing in the backend. Lookup failure: existing retryable.
4. **Funding (AR-6)** is a session-scoped client assertion used as a usage estimate: first-wins, so model or auth changes mid-session do not reclassify earlier or later rows, and a mixed session is bucketed wholly under its first claim. Parsed with the existing routed boolean (`1/true/yes`, route:110):

| Class (derived) | Parsed routed | Claim | Stored |
|---|---|---|---|
| `codemie` | true | `api_based` | `api_based` (D8) |
| `codemie` | true | `subscription` | `unknown` (contradiction) |
| any | any | missing, empty, repeated, unknown-valued | `unknown` |
| `corporate`/`personal`/`unknown` | false | valid value | that value |

   Stored in nullable `session_dims.funding_route` by the existing first-binding write (bound rows are never updated). Never compared; never affects access, routing, consent, binding or visibility. A later differing claim is accepted, ignored and counted in one counter keyed by the bounded reason `funding_claim_differs` (no session, project or email labels). NULL reads `unknown`. Contract consistency, not a reproduced loss.
5. **Binding** adds nullable `contract_version`, `work_consent`, `funding_route` (alembic `alembic_cli_analytics`, after the latest head; `BINDINGS_SQL`, `_BINDING_COLUMNS`, `SessionBinding`, `stored_binding`). Compare (contract, consent) only, one helper at the start of both gates' `resolve_session`; L1 is (none, none), so legacy vs new conflicts both ways -> final `session_contract_conflict` (existing 200 discard, `SESSION_DISCARDS`). Old rows NULL = legacy, never rewritten. A consent request on a personally bound session conflicts; private history never moves or widens.
6. **Readers (AC1), read.** Aggregates and lists: `_resolve` -> `ViewerScope` (`cli_analytics.py:340-399`, `scope.py`) behind `_admin_gate`; `/sessions` and `/sessions/{id}`: `authorize_session` and `detail_gate` on meta, cost, scalars, tools, events (content), dispatches, skills (`reader.py:992-1175`). None gates by funding or subscription. A consented session is class N under its canonical project: visible to super admins and that project's admins, as every work-project session today. This task adds no ordinary-member or owner access to shared project sessions; the owner reads it only if admin. Owner-only personal analytics are unchanged; private rows stay out of work-project reads.
7. **Buckets and excluded estimate (AR-4).** Existing spend responses keep `get_session_model_costs(..., spend_only=True)` and their pricer, so the records feeding existing totals do not change and `unknown` and `codemie` sessions count as spend as today. A new reader method returns per-(session, model) token rows of the same `_sessions_cte` scope with `funding_route` and the personal-billed flag (`sql_personal_billed`), without the spend filter; the handler prices them with its own pricer per bucket. Responses add `funding: {api_based, subscription, unknown}` (labelled client-asserted estimate) and `personal_billed`, each `{cost_usd, session_count, cost_incomplete, unpriced_models}` describing only that bucket's models. The two are different dimensions; `personal_billed` is by derived class. Existing `metadata.unpriced_models` (`_metadata`, `cli_analytics.py:402`; detail :748) stays from the spend pricer and gains `cost_incomplete = bool(unpriced_models)`. A partial figure carries the flag, never a bare 0; pricing metadata (`coding_agent_pricing.py:96-142`) reused. Additive fields only: `cost_by_model` keeps its spend-model membership (no zero-spend or blank-model rows); a model only the estimate priced (e.g. personal-billed only) is served in the separate `models_without_spend` field of `/cost` (same row shape, `cost_usd` 0 plus buckets, no blank model). Helpers live in `handlers/funding_buckets.py`.
8. **Budget isolation (AC6) - dataflow record.** `budget_usage_service.get_budget_usage` (`spending.py:343`) and `member_spend_service` (`spending.py:408,423,459`) take every figure from `spend_service`, whose ledgers read `litellm_spend_logs` and the ES metric index fed by proxied calls (`default_ledger.py:115`). CLI rows land only in the CLI-analytics Postgres. `usage_attribution/postgres_sources.py` is the one other reader of them: `get_framework_sessions` (`reader.py:957`) returns per person/repo session counts of non-private sessions, and `skill_names_by_session` returns names; neither carries money, and `framework_sessions.py` has no cost field. A subscription session does not pass the CodeMie proxy; `Routed-Via-CodeMie` sessions are class `codemie`. No exclusion code is added. Evidence: (a) a reader test on a real fixture that a personal-billed session is in `personal_billed` and absent from the existing `cost_usd` totals, and that existing responses are unchanged by the new rows (golden compare); (b) a per-entity partition check, `spend + personal_billed == priced total`, as a consistency check of the grouping only, not as independent proof.
9. **Rollout and pause (AR-2).** Order: alembic migration first (nullable, additive; old code ignores the columns and writes the NULL triple), then code with `CLI_ANALYTICS_PUBLICATION_CONTRACTS_ENABLED` (operator setting, default false). The policy endpoint lists `publication_contracts: ["1"]` only when it is true. While false, any contract-`1` request gets `503 publication_contract_unavailable`, hold, from the item-1 check; legacy L0/L1 are unchanged. A pause holds, never destroys history. Once bindings exist, pausing is the supported control and the fix path is forward; the new code stays deployed. Rolling back to a pre-contract handler is unsupported unless new-contract traffic is blocked or drained before it reaches that handler: a cached capability or the response echo cannot stop an old handler's write or discard. No topology is assumed and no orchestration is added. The echo detects a wrong response only after a write. L0/L1 have no sunset here.
   Rollout guidance for operators: a new CLI without explicit work consent keeps derived `unknown` and `personal` sessions in owner-only personal storage (excluded user types are held), and derived `corporate`/`codemie` sessions on today's route; with no `corporateOrganizations` configured, every unrouted session is `unknown` and so private until the user consents. Legacy clients and existing bindings are unchanged.

## Wire contract (hand-off to the CLI child)

Headers on the existing evidence-mode POSTs, each at most once:

| Header | Values | Rule |
|---|---|---|
| `X-CodeMie-Publication-Contract` | `1` | New contract; not consent. Sent only if advertised. |
| `X-CodeMie-Work-Consent` | `explicit-folder` | Only with contract `1` and a non-blank `X-CodeMie-Project`. Client-asserted; the CLI must keep it user-local and revocable, with an acceptance test. |
| `X-CodeMie-Funding-Route` | `api_based` `subscription` `unknown` | With contract `1`; client assertion only. |

Other headers, the stale-policy `409` and `policy_version` are unchanged. `GET /v1/analytics/cli-analytics/classification-policy` gains `"publication_contracts": ["1"]` (absent = unsupported; never inferred from a 2xx).

Non-success carries `{"reason", "disposition": "retry"|"hold"|"final"}`; none binds or acknowledges:

| Status | reason | disposition |
|---|---|---|
| 422 | `publication_contract_unsupported` (present-and-invalid contract) | final, after one capability refresh |
| 422 | `publication_contract_missing`, `consent_invalid`, `consent_without_project`, `consent_personal_project` | final |
| 503 | `publication_contract_unavailable` (operator pause), `personal_storage_unsupported`, `project_unavailable`, `project_analytics_not_enabled`, `destination_unsupported` (a new-contract non-personal destination whose canonical name is not latin-1, so it cannot be echoed as a header value; checked after authorization and before any write, in N0 and N1, new and bound sessions; the reason names no project) | hold: bounded by the CLI retention window, then suspend. The CLI shows a notice at the FIRST hold, naming the reason (pause: nothing to do; unavailable: check name/access; not enabled: ask an admin) |
| 503 | `personal_destination_unavailable`, `default_project_unavailable`, storage errors | retry (spool ~24h) |
| 200 discard body (existing) | `session_owner_conflict`, `session_project_conflict`, `session_contract_conflict`, `session_project_unavailable` and `cli_analytics_disabled` (bound) | final discard |

Accepted new-contract responses (OTLP and hook, as headers) carry `X-CodeMie-Publication-Contract: 1`, `X-CodeMie-Destination: project|personal` and, for project, `X-CodeMie-Bound-Project: <canonical name>`. Missing header (including on a 200 discard), `personal` for a consent session, or a project differing from the frozen request (case-insensitive) is `response_unverified`. The echo confirms only; the backend verified before writing. No redirects. Neither side logs credentials.

CLI obligations relied on (not backend work): consent keyed by canonical folder + backend identity + project, never from cloned config; folder and revocation checked per hook; resume/folder change/unknown scope suspends fail-closed; no tracking while collection is disabled; an unadvertised capability is held, not sent as legacy; first-hold notice. Unresolved CLI TUI/E2E cases stay with the CLI child.

## Acceptance criteria

- AC1: N1, usable and enabled project -> stored under the canonical name, class N, readable (list, detail, events content) by super admin and that project's admins; admin of another project and non-admin denied; any class and user type. Unusable -> `project_unavailable` hold; not enabled -> `project_analytics_not_enabled` hold; personal-type project -> 422; no binding, no fallback.
- AC2/AC3: N0 per table, through the real gate and ingestor: personal/unknown -> owner-only personal storage or hold, for regular and excluded users, also on sessions with legacy rows or bound elsewhere; codemie/corporate keep today's route; no provisioning for excluded types; private rows invisible in work-project reads.
- AC5: funding buckets (client-asserted estimate) by project/user/repo/model through the real reader; the class x routed x claim table holds; classification stays distinguishable; later claims change nothing.
- AC6: item 8. AC7: item 7.
- AC9/AC11: L0 and L1 unchanged (one test per regime); bound sessions with deleted/revoked destinations keep their final discard; the consent path never meets the personal-anchor conflict nor adopts another legacy project; (contract, consent) checked.
- AC-neg: malformed or contradictory funding and consent headers never grant visibility and funding stays `unknown`; they without the contract are 422; `api_based` is never a permission bypass. Class inputs are client-sent too; the backend does not prove them.
- AC-order: an invalid contract header with a body over the size cap answers 422, not 413, on each of the four routes and the body is not read (cap lowered in the test, no large fixture).
- AC-rollout: switch off -> contract-`1` requests hold 503 and legacy is unchanged; migration precedes code.

## Non-goals

- CLI changes, Control Plane, UI, ticket attribution, Cost per Point, pet-project detection.
- Relocating or reclassifying existing history, or moving existing corporate/API analytics into private storage; changing existing bindings.
- Ordinary-member or owner read access to shared project sessions; a user-facing personal-analytics toggle; a new profile-name rule; a new tracking or provider framework.
- Backend proof of billing, class inputs or consent; funding route as access input.
- Changing the corporate/personal classification or `policy_version`; refactoring `EvidenceGate` or unrelated gate code; an orchestration subsystem for rollback.

## Decisions

- Funding stored first-wins as a client-asserted estimate dimension over a member of the compared triple; only (contract, consent) is compared.
- One `PublicationContract` value type and parser.
- One `private_classes(contract_version, work_consent)` predicate read by every privacy consumer over per-site class tests.
- `resolve_destination` takes an explicit `private` argument over recomputing it.
- Derived `unknown` stays `unknown` with a private destination in N0 over relabelling it personal or keeping today's route.
- N0 keeps today's route for derived codemie/corporate over private storage for every class.
- Contract/switch/excluded-user holds run before the body through one function, DB-dependent holds after it, over claiming all checks precede the body.
- Operator setting default off; while off, contract-`1` ingest is a 503 hold over a final discard or legacy handling.
- Pause and forward-fix are supported; rollback to a pre-contract handler is unsupported without blocking or draining new-contract traffic.
- Uniform `503 project_unavailable` hold for an unusable unbound destination over a final `403 project_access_denied`; bound deleted/revoked stays a final history-keeping discard.
- `503 project_analytics_not_enabled` hold over a final discard.
- Consent naming a personal-type project rejected `422 consent_personal_project` over normalising it.
- Separate `ConsentGate` over extending `EvidenceGate`; consent read from `work_consent`.
- Consent origin `linked_folder` over a new origin value.
- Existing spend responses keep `spend_only` SQL filtering; buckets come from a separate row query and per-bucket pricer over regrouping existing rows.
- `personal_billed` as its own field over deriving it from funding buckets.
- `cost_incomplete` beside the existing `metadata.unpriced_models`.
- Existing super-admin/project-admin read scope kept over adding owner or member access.
- Funding counter keyed by reason only over per-session labels.
- Conflict reasons use the existing 200 discard over new 4xx.
- Three headers plus `publication_contracts` over one JSON header.
- Consent or funding header without contract rejected 422 over legacy handling.
- Empty or repeated header treated as present-and-invalid over absent.
- 422 over 400/426 for invalid input.
- Unsupported personal storage answers 503 hold over final rejection or default-project routing.
- Confirmation by response headers over body echo.
- Triple stored in new nullable `session_dims` columns, NULL = legacy, over a side table.
- `policy_version` unchanged.
- No budget-exclusion code over re-implementing it.

## Open risks and assumptions

- A held session re-uploads its body on each retry (up to the size cap) for the retention window; not mitigated here.
- A genuinely denied user holds until the CLI retention window ends, then suspends; there is no final access-denied reason on unbound sessions.
- A non-admin owner cannot see their own consented session in the UI (existing scope).
- Conflict discards are 200s; the CLI child must treat a 200 without echo headers as non-success.
- Class inputs (organization, routed) are client-sent; a client that omits them is `unknown` and, without consent, private.
- Not traced: UI tolerance of additive fields; replica topology and CLI policy-cache TTL; the largest tenant's class distribution; the echo is exercised only synthetically until the CLI ships.
- Assumption: no supported CodeMie model route is subscription-backed; the backend never trusts the client value.

## Handoff notes: wording to sync (parent and child files are outside this edit; use the established sync path)

- Child `story.md`: AC1 "follows project permissions" -> "follows the existing super-admin/project-admin read scope; no ordinary-member or owner access is introduced"; AC1 failure -> bounded holds (`project_unavailable`, `project_analytics_not_enabled`, `publication_contract_unavailable`), personal-type project 422; AC2/AC3 "new-contract personal session" -> "new-contract session of derived class personal or unknown without consent (unknown stays unknown); codemie/corporate keep today's route"; AC5 add client-asserted first-wins funding, not compared; AC6 add `personal_billed` and the spend_only retention; AC7 `metadata.cost_incomplete`; AC9/AC11 "(contract, consent) checked, funding first-wins"; Decisions "Contract (draft)" -> spike reported rev 2; add rollout order, operator setting (hold when off) and the rollback limit.
- Parent: the decision/AC that sends a session without consent to private storage -> "for derived personal or unknown; existing codemie/corporate analytics keep their route"; add the read-scope clarification above; keep the approved private-session rule; D8 unchanged.
