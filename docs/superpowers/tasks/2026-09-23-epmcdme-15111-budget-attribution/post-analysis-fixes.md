# EPMCDME-15111 — Post-analysis fixes

Source: whole-story analysis of EPMCDME-15109 and its sub-tasks (2026-09-25), checked against the
Jira acceptance criteria of all four tickets. Each entry: finding → AC it breaks → fix → proof.

## F1 (P0) Default project never reached real logins

- **Finding.** `default_project` was populated in `AuthenticationService.load_user_for_auth()` and
  `LocalIdp`. `load_user_for_auth()` has no callers. The path every persistent / IdP login takes,
  `_finalize_authentication()`, rebuilt `project_names` from the DB but never set `default_project`,
  so for every non-local-IdP user all attribution tiers saw "no default".
- **Breaks.** AC2, AC4, AC8 in production auth modes.
- **Fix.** `_finalize_authentication()` sets `default_project` from the same membership rows it
  already loads (`src/codemie/service/user/authentication_service.py`).
- **Proof.** `TestFinalizeAuthenticationDefaultProject` (default present / absent).

## F2 (P1) Fallback spend reported against the unfunded project

- **Finding.** When the charged project (e.g. the default) has no budget allocation for the user,
  billing falls back to personal/default, but `LiteLLMContext.current_project` and the proxy's
  `request_info[project]` kept the unfunded project. Metrics/analytics read the project from there,
  so analytics said the default project paid while the personal budget paid. The INFO log only
  fired on a resolution-cache miss; cache hits logged at DEBUG.
- **Breaks.** AC6 ("fallback visible as a fallback"), AC10 ("analytics show the project whose
  budget was charged").
- **Fix.**
  - Web path: `llm_factory._mark_personal_budget_fallback()` — when a real project has no budget
    scopes, the context is re-attributed to the user's personal identity and the original project
    is kept in the new `LiteLLMContext.budget_fallback_from`. One INFO
    `budget_event=budget_attribution_fallback` line per request.
  - Every metric emitted in that context gets `budget_fallback_from=<project>`
    (`base_monitoring_service._add_budget_fallback_attribute`, applied in `send_log_metric` and
    `send_count_metric`).
  - Proxy path: `proxy_router._flag_personal_budget_fallback()` marks `request_info` (project left
    unchanged — credential/runtime lookups still key on it) and logs the same INFO event;
    `LLMProxyMonitoringService.track_usage` then reports the personal identity as `project` with
    `budget_fallback_from` set.
  - Applies to any request whose named project is unfunded, not only default-derived ones: AC10
    is unconditional. Billing is unchanged; only attribution is corrected.
- **Proof.** `TestPersonalBudgetFallbackAttribution` (llm_factory), `TestFlagPersonalBudgetFallback`
  (proxy_router), `TestTrackUsageBudgetFallback`, `test_budget_fallback_attribute.py`.
- **Known limit.** Metrics emitted before the model is built (e.g. "agent started") still carry the
  pre-decision project; token/money metrics, which analytics bill from, are emitted after.

## F3 (P1) No-default fallback moved personal spend to an arbitrary project

- **Finding.** `User.current_project` fell back to `sorted(project_names)[0]`. The personal project
  (named after the email, always a membership in UM mode) was previously the first-inserted row and
  so the de-facto pick; alphabetical order made the pick depend on email spelling vs project names
  — the same harm that got the backfill migration dropped (`edca7d818`).
- **Breaks.** Story intent "predictable" behind AC5; consistency with the resolved AC3 rule
  ("no default → personal").
- **Fix.** No default → personal project when it is a membership → otherwise sorted first →
  `DEMO_PROJECT` (`src/codemie/rest_api/security/user.py`).
- **Proof.** `test_current_project_prefers_personal_project_without_default`,
  `test_current_project_default_beats_personal_project`.

## F4 (P2) Leftover "first entry" call site

- `plugin_tools_info_service` still used `user.project_names[0]`; now uses `user.current_project`.

## Not changed — decided

- **CLI personal identifier (`username`) vs web (`email`).** The two are also the LiteLLM customer
  keys each path bills to; unifying them re-keys customers and moves spend, which the story forbids
  ("previously recorded spend is not moved"). AC8's tier order is identical on both paths.
- **Auth-cache staleness for AC11/AC12.** Covered by F-series fix in EPMCDME-15110 (cache
  invalidation on membership/default changes).

## Verification

- Affected suites: `tests/codemie/{rest_api/security,repository,service/user,service/monitoring,
  service/llm_service,core/test_models_extended.py,rest_api/routers/test_user_*}`, `tests/enterprise/`
  — 2081 passed, 64 failed; all 64 fail identically on the untouched HEAD (63 `enterprise/mcp_auth`
  + `enterprise/switchyard`, 1 `ENV=local`-dependent `TestLoadUserForAuth` assertion).
- `ruff format` / `ruff check` clean on all changed files.

## F5 (P1, found in live test) Assistant-chat analytics still showed the unfunded project

- **Finding.** In the live run, a marketplace chat by a user whose default project had no budget
  was charged to the personal budget, but `conversation_assistant_usage` still reported
  `project=<default>` with no fallback marker. Agents re-create the LLM context
  (`langgraph_agent.py` calls `set_llm_context` repeatedly) and build the model in a different
  context copy than the one the chat metric reads, so F2's model-build-time marking never reached
  that metric.
- **Fix.** The fallback is decided when the context is created: `set_llm_context` →
  `_unfunded_project()` probes the project's budget scopes (same cached probe the factory uses)
  and creates the context already re-attributed (`current_project=<email>`,
  `budget_fallback_from=<project>`). Skipped for own-key requests, the personal project, and
  non-`lite_llm` proxy modes; a failed probe keeps the original attribution. The F2 factory
  marking stays as a safety net.
- **Proof.** `TestUnfundedProjectAtContextCreation` (7 tests); live re-run below.

## Live end-to-end billing verification (2026-09-26)

Local stack: backend image with `codemie-enterprise` 2.3.42, `LLM_PROXY_MODE=lite_llm`, LiteLLM
`litellm-database:1.93.0` on DIAL (`gpt-4.1-mini`). Fixture: `bt-proj-a`, `bt-proj-b`,
`bt-proj-mkt` with platform + CLI project budgets (synced to LiteLLM as project keys and per-member
customers), `bt-proj-c` without any budget. Charges read from `LiteLLM_SpendLogs` per request;
analytics from the emitted metrics.

| Case | User / setup | Flow | Charged (LiteLLM) | Analytics `project` | AC |
|---|---|---|---|---|---|
| P1 | alice, default b | proxy, no project | bt-proj-b platform | bt-proj-b | 8 |
| P2 | alice | proxy, explicit bt-proj-a | bt-proj-a platform | bt-proj-a | 9 |
| P3 | bob, no default | proxy | personal `bt-bob` | bt-bob | 3 |
| P4 | carol, default c (no budget) | proxy | personal `bt-carol` | bt-carol + `budget_fallback_from=bt-proj-c` | 6, 10 |
| P5/P6 | alice / dave, CLI client | proxy | default's **CLI** budget | default | 8 |
| W1–W4 | alice/bob/carol/dave | skill generation (unbound) | b / personal / personal+flag / a | same as charged | 4, 5, 6 |
| M1 | dave, member of bt-proj-mkt, default a | marketplace chat | bt-proj-mkt | bt-proj-mkt | 1, 7 |
| M2 | alice, non-member, default b | marketplace chat | bt-proj-b | bt-proj-b | 2 |
| M3 | bob, non-member, no default | marketplace chat | personal | bt-bob | 3 |
| M4 | carol, non-member, unfunded default | marketplace chat | personal | bt-carol + flag (after F5) | 6, 10 |
| C1/C2 | alice default b→a | proxy + skill right after switch | bt-proj-a; prior bt-proj-b spend unchanged | bt-proj-a | 11 |
| R1–R3 | alice removed from bt-proj-a (her default) | proxy, skill, marketplace | personal | bt-alice | 12 |
| E1/E2 | bob default = personal project | proxy + skill | personal, no fallback flag | bt-bob | 15110 F1 |
| D1–D3 | dave ×3 | proxy | bt-proj-a every time | bt-proj-a | 5 |

38 calls, $0.008 total DIAL spend. LiteLLM customer and project-key balances reconcile with the
per-request rows; `bt-proj-c` never accrued spend.

Pre-existing, unrelated: `budget_assignment_mirror_failed` (FK to a missing `default` budget row in
the local `budgets` table) logs on personal-budget requests; personal enforcement in LiteLLM is
unaffected.

## F6 (P2, found in live pass 2) Factory-level fallback marker misfired when username ≠ email

- **Finding.** With users whose username differs from their email, the F2 safety net in
  `llm_factory` logged `budget_attribution_fallback` for bob's *personal* project. Routers set the
  factory's `user_email` from `user.username`, while personal projects are named by email, so
  `current_project != user_email` was true for a plain personal request.
- **Fix.** The factory-level marker is removed; F5 (decided at context creation with `user.email`)
  is the single source. Analytics attribute plumbing unchanged.
- **Note (pre-existing, out of scope).** The same username/email assumption sits in the factory's
  `has_real_project_context` check; harmless for budgets today (a personal project never has
  budget scopes) but worth aligning when the CLI/web personal identifier is unified.

## Live pass 2 (2026-09-26, real local auth)

Fixture: `p2-a`/`p2-b`/`p2-mkt` with platform+CLI budgets, `p2-c` unfunded; alice default b,
carol default c, dave default a, bob none; marketplace assistant in `p2-mkt` (dave member);
project-owned assistant in `p2-a`. Charges from `LiteLLM_SpendLogs` per request; analytics from
emitted metrics; member spend from `/v1/analytics/user-project-spending`.

| Case | Charged | Analytics | AC |
|---|---|---|---|
| proxy alice no header → p2-b platform; explicit p2-a → p2-a; CLI → p2-b CLI | ✓ | ✓ | 8, 9 |
| proxy bob (no default) → personal; bob CLI explicit p2-b → p2-b CLI | ✓ | ✓ | 3, 9 |
| proxy carol (unfunded default) → personal | ✓ | `project=p2-carol`, `budget_fallback_from=p2-c` | 6, 10 |
| proxy carol explicit non-member p2-mkt → personal | ✓ | fallback_from=p2-mkt | 6 |
| skill/assistant generation alice/dave → default; carol → personal + flag | ✓ | ✓ | 4, 5, 6 |
| marketplace dave (member) → p2-mkt; alice (non-member, default b) → p2-b; bob → personal; carol → personal + flag | ✓ | ✓ (carol flagged after F5; bob unflagged after F6) | 1, 2, 3, 6, 7, 10 |
| project-owned assistant: alice and carol → p2-a (owner project, default ignored) | ✓ | ✓ | out-of-scope rule preserved |
| default switch mkt→a then call → p2-a immediately (auth cache invalidated) | ✓ | ✓ | 11 |
| membership removed → personal | ✓ | ✓ | 12 |
| bob ×3 → identical | ✓ | ✓ | 5 |

`/v1/analytics/user-project-spending` totals reconcile with the per-request rows (e.g. alice
p2-b platform = 6.8e-06 + 0.0011924 + 5e-05 = 0.0012492). Personal-budget usage rows are empty in
this environment because the local `budgets` table lacks the predefined `default` row
(`budget_assignment_mirror_failed`, pre-existing). Workflow generation is disabled here
(`WORKFLOW_GENERATION_ENABLED=false`, 405) — not exercised.

## Third-pass review (2026-09-26, `docs/superpowers/tasks/2026-09-26-epmcdme-15109-ac-review/`)

Verdicts on the independent review's findings, with my verification:

- **R01 — partially funded default (accepted, verified live).** My F5 treated *any* funded
  category as "funded"; a project with only a CLI budget therefore hid the fallback for web /
  non-CLI requests, which are charged to the platform category. Review fix: the context-time
  check resolves the category for the request's model and flags a fallback when that category is
  unfunded; the factory records the runtime outcome (`_record_direct_budget_attribution`) and
  resolves the original project for routed/premium models; the proxy flags only after the actual
  category produced no project runtime. Live with `p2-cli` (CLI budget only) as bob's default:
  web skill → personal, `project=p2-bob@…`, `budget_fallback_from=p2-cli`; non-CLI proxy →
  personal, `project=p2-bob`, `budget_fallback_from=p2-cli`; CLI proxy → `p2-cli` CLI budget, no
  flag. Regression subset (alice→p2-b, carol→personal+flag, dave marketplace→p2-mkt) unchanged.
  Added `TestUnfundedProjectIsCategoryAware`. Minor: `llm_proxy_requests_total` (a count, no
  money) still reports the routing project alongside the flag; the usage/money metric is correct.
- **D02 — inactive budgets accepted by the sync probe (pre-existing, fixed here).** Added
  `AND b.is_active = TRUE` to the synchronous scope probe so it matches the async batch probe;
  regression test `TestProjectScopeProbeIgnoresInactiveBudgets`.
- **Q06 — personal identifier spelling (agreed, withdrawn).** Same conclusion as F-series notes:
  username vs email is the provider customer key vs the personal project name; not re-keyed.
- **Test hygiene.** The review's changes were shipped without running tests: `set_llm_context`
  now sets `personal_project=user.email`, which broke five `MagicMock`-user tests (pydantic
  rejects a mock as `str | None`); fixed by giving those mocks an email.

Why my passes missed R01: both fixtures funded every project in the same categories the flows
used (platform + CLI) or not at all — the budget-*category* dimension was never varied.
