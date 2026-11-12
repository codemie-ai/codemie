# EPMCDME-16625 — Assistant Budget Attribution Matrix (design)

Source of truth for rules, matrix (19 rows), acceptance criteria and decisions D1–D11: the ticket text. This document records the design choices made on top of it. Items marked **[call]** are the author's decisions, not the user's; everything else was stated by the user or is in the ticket.

## Intent

Every LLM request (chat, workflow, skill, generation, indexing, CLI/IDE) is charged by one rule: pick a target, then walk **target → D → P** and charge the first *funded* step (skip repeats). D = user's default project, P = user's personal budget. An exhausted budget is never rerouted. Closes EPMCDME-16466 and supersedes the EPMCDME-15109 "membership wins" rule for marketplace assets.

## Goals

- One resolver decides attribution; web and CLI both call it (today two hand-synced implementations).
- All 19 matrix rows and all acceptance criteria hold.
- Web and CLI fall back to the **same** personal budget; **username is canonical** (user decision, 2026-11-01: personal budgets are provisioned in LiteLLM by `db_user.username`, so no migration).
- Analytics: charged project is the project attribute; originally targeted project and fallback kind (default vs personal) are recorded.

Ticket text (matrix, AC, D1–D11) is in `ticket.md` next to this file.

## Resolved after builder check

- **P is terminal and unchecked:** if target and D are unfunded, P is charged without a funded check, flagged as fallback (ticket decision chart). No error path at P.
- **Cache key unchanged:** `(project_name, category, user_id)`; the resolver calls the funded check once per candidate project (target, then D). Admin default/allocation changes are not actively invalidated; ≤60 s staleness (including a stale negative entry) is accepted per the ticket AC. **[call]**
- **IDP-claims mode:** funded check is name-based on the budget tables, so a personal project with no assignment row is simply unfunded and the chain ends at P. **[call]**
- **`LLM_PROXY_SHARED_ASSET_PROJECT_BUDGET_ROUTING_ENABLED`:** the matrix wins. Flag code stays but can no longer cause a non-member's asset project to be charged; affected tests are rewritten. **[call]**
- **Nested assets:** each asset's own LLM calls are charged per that asset's resolution for the starting user; the parent's calls stay on the parent's resolution. Multi-hop chains resolve each hop independently; context is restored after each nested build/run. Builder must audit every `set_llm_context` call site, not only `build_agent_for_workflow`. **[call]**
- **Analytics storage:** metrics attributes are free-form; no index/schema change expected. Legacy rows with `budget_fallback_from` and no kind are read as personal. Builder verifies no mapping change is needed and updates only readers that interpret `budget_fallback_from` (usage_attribution, budget_usage_service, CLI analytics postgres). **[call]**
- **Personal id (user decision, supersedes the earlier email answer):** username. CLI and web agent paths already use it; only web paths without an agent move from the auth-time email to username. Their past spend on email-keyed customers stays (no re-attribution). Users without a username fall back to email. **[call]**

## Non-goals / out of scope

Personal-project budget data migration; rerouting on exhaustion; user-selectable billing project; UI changes; changing how defaults are assigned; searching other projects beyond D; re-attributing history; moving spend already recorded on email-keyed customers by agent-less web paths.

## Scope decisions (user)

- **Base:** self-contained on top of `main`. EPMCDME-16466/16566 branches are *not* a dependency; reuse their ideas (default-project fallback step, casefold personal-project matching, member-preferred-over-orphan pick) by reimplementing.
- **Delivery:** one MR. CLI/IDE is in scope (rows 14–16, D3, D9).
- **Identity:** username is the canonical personal-budget id on both paths (matches LiteLLM provisioning).

## Design

### 1. Shared resolver **[call: location/shape]**
New module under `service/budget` (e.g. `attribution_resolver.py`), pure decision logic plus one injected "is funded for user" check, with sync and async entry points.

- Input: asset kind (assistant / workflow / datasource / none / CLI), asset project, `is_global`, user (email, memberships incl. admin projects, default project), optional CLI explicit project, personal-credentials flag.
- Output: `charged` (project name or personal), `targeted` (original target), `fallback_kind` (`None | default_project | personal`), reason.
- Target selection per the ticket's decision chart: marketplace/global → D; project asset + member → asset project; non-member → D; personal-project asset (owner) → D; no asset → D; CLI header member → that project, non-member → D (ignore, log). **[Superseded: EPMCDME-16276 answers 403 for a project the caller cannot use. Only an admin or maintainer who is not a member has the header ignored, logged and charged D → P, and the header project never reaches the credentials lookup (AR-2).]**
- Membership and "personal project" comparisons are case-insensitive (casefold); a real project is preferred over an exact-email orphan when picking membership. **[Superseded: membership is exact-case (CR-008); only a CLI header maps a single case-insensitive variant to the member spelling, and the walk de-duplicates by exact name (AR-6).]**
- Chain: skip steps equal to an earlier step. If D == P (no assigned default, personal), chain is target → P.
- Personal credentials (row 18) bypass the resolver entirely (unchanged).

### 2. "Funded"
Funded = active budget assignment for the project + allocation for this user (D6). Spend/exhaustion is not considered (so exhausted ≠ unfunded, D4; hard/soft limit enforcement stays in `check_user_budget`/LiteLLM). **[call]** Replace the three copies of the funded query (`project_budget_repository`, `llm_factory._probe_direct_project_budget_scopes`, `budget_resolution_service.resolve_sync`) with one shared implementation, keeping the existing 60 s `_resolution_cache` (incl. negative entries) — normal-delay behaviour in the AC.

### 3. Default project D
Admin default → user's personal project → alphabetically first project (reuse `resolve_default_project`). IDP-claims mode (User Management disabled): **[Superseded: the default is never derived at login or stored. `attribution_input_for_user` derives it per request, and the UI and CLI analytics use the same derivation (`User.effective_default_project`, AR-7).]** D is derived at login (`LegacyJwtUserProvider`) as the email-named personal project, else the username-named workspace that mode creates, else the first project by name. In that mode the username workspace also counts as the personal budget, so a user with no team default ends on P unflagged, as D8 requires. **[call, revised during implementation: the username workspace is IDP mode's personal project]**

### 4. Web path
`set_llm_context` (and `_resolve_effective_project`) delegate to the resolver, replacing the is_global-only branch and the target→P-only chain. `LiteLLMContext` gains `budget_fallback_kind`; the original target is carried in the existing `budget_fallback_from` whenever a fallback happened (no separate `budget_target_project` field) **[call, revised during implementation]**. `llm_factory` direct path consumes the resolver result instead of re-deriving fallback (`_record_direct_budget_attribution`). Personal customer id = username (the id budget provisioning uses).

### 5. CLI/IDE path
`proxy_router` calls the same resolver. Explicit header project honoured only for members; otherwise ignored, logged (`budget_event=… component=…` style), and not shown in analytics. **[Superseded: EPMCDME-16276 answers 403 for a project the caller cannot use. Only an admin or maintainer who is not a member has the header ignored, logged and charged D → P, and the header project never reaches the credentials lookup (AR-2).]** `_flag_personal_budget_fallback` gains the D step. Personal / `_codemie_cli` / `_codemie_premium_models` customer ids stay built from **username** (unchanged on the CLI).

### 6. Nested and asset-less flows
- Workflow node / sub-assistant using a saved assistant: resolved per that assistant for the *starting user* (D2); the context set for a nested asset must not leak back to the parent (scope/restore the contextvar around `build_agent_for_workflow`).
- Skills, generation, workflow execution (no asset): target = D.
- Datasource indexing: project-asset rows (D10), both processors.

### 7. Triggers
`resolve_trigger_user` and the bare `User(id=…)` stubs (webhook workflow handler, datasource trigger actor) load the owner's real project memberships, admin projects and default project through the same loader normal auth uses, so attribution equals an interactive run by the owner (D7).

### 8. Analytics
- Project attribute = project whose budget was charged (web and CLI).
- Existing `budget_fallback_from` = originally targeted project (kept).
- New `budget_fallback_kind` = `default_project | personal`.
- `usage_attribution` readers (and `budget_usage_service`) currently treat `budget_fallback_from` as "paid personally"; they must treat it as personal only when kind is `personal` (or kind absent on legacy rows) so D-fallbacks aren't misread.
- Emitted from the same shared place for web and CLI monitoring.

### 9. Interactions to preserve
`LLM_PROXY_SHARED_ASSET_PROJECT_BUDGET_ROUTING_ENABLED` and shared-asset credentials override: keep personal-credentials-wins semantics; builder to verify the flag doesn't re-introduce charging a non-member's asset project **[call: flag stays, behaviour subordinate to the matrix]**.

## Constraints

- Sync and async parity; lazy imports where the existing code uses them to avoid cycles.
- No new dependencies; no schema migration expected.
- Log style `budget_event=<name> component=<name> key=value`; no secrets/emails beyond what current budget logs already include.
- MR touches security-relevant budget enforcement: MR description must ask a reviewer to post `/sanity` (AGENTS.md).
- Do not commit `.codemie/codemie-cli.config.json`.

## Anti-goals

Charging a project the user is not a member of; silently charging personal while a funded D exists; rerouting exhausted budgets; attribution differing between web and CLI for the same user and asset.

## Left to the builder

Module/function names, exact dataclass shapes, contextvar scoping mechanism, how the single funded query is exposed to sync and async callers, test layout (a table-driven matrix test covering rows 1–19 is expected), OpenAPI baseline update only if an API shape changes.

## Decisions after the architecture review (2026-11-09, revised after the final review 2026-11-10)

All items in this section are **[call]**: the author's decisions after the review, not yet confirmed by the requester.

- **[call] IDP claims snapshot table (`idp_user_snapshots`, migration `b5c6d7e8f901`).** This replaces "no schema migration expected" above. IDP-claims mode keeps no users rows, so D7 (triggers run as the owner with real memberships) needs the claims of the owner's last login. A row holds the email, name and project claims. It is written only by a sign-in through an IdP that declares `validates_token` (the JWKS wrapper and the enterprise providers; never `LocalIdp`, and no new IdP until it declares it), read only for trigger owners and signed loopback calls. Memberships are trusted for `IDP_USER_SNAPSHOT_MAX_AGE_HOURS` (default 24); the owner's username, email and name are kept for `IDP_USER_SNAPSHOT_IDENTITY_RETENTION_DAYS` (default 30), and the row is pruned after that. It never restores project-admin rights (AR-5, AR-9; final review AR-1, AR-5). Precondition: IDP-claims mode runs only where token signatures are verified (`JWKS_VALIDATION_ENABLED` or a validating gateway). The enterprise providers keep `validates_token` on rather than deriving it from `JWKS_VALIDATION_ENABLED`, because gateway deployments verify the token before it reaches the backend; turning it off there would stop snapshots and send every trigger run to a stand-in. Startup logs `idp_snapshot_trust` when IDP-claims mode runs without JWKS validation (code review CR-002).
- **[call] Owner whose memberships are stale (rows 17, D7, D9).** Past the membership trust window the owner keeps their identity but no memberships: the run is charged to their own personal budget (the username-keyed customer, as on web and CLI), not to the asset project or the default project. The requester may raise `IDP_USER_SNAPSHOT_MAX_AGE_HOURS` (for example to 72) so weekend runs still reach those projects; a revoked member's background runs then keep their old projects for that long (final review AR-1).
- **[call] Owner who cannot be loaded (row 17).** No snapshot at all (no sign-in since rollout) or one past the identity retention: runs as a stand-in with no memberships and is charged to the personal budget under the id-named customer, reported under that name as a personal charge. Each such run is logged as `budget_event=stand_in_owner` (AR-3).
- **[call] Unknown funding.** A failed funded check counts as unfunded at decision time, so the walk continues to D, then P, and is logged with `funding_unknown` (AR-1). The runtime then checks every project step again from the target, on web and CLI alike, so a passing error does not move the charge to P. A check that fails again at runtime fails the request, as before this change. On the CLI a connection-pool checkout timeout is not counted as unknown funding: every later step would wait for the same pool, so the request fails at once with HTTP 503, both in the decision walk and in the runtime walk; a pool that recovers within the window no longer rescues the request. The user's budget ids are read once per request (final review AR-6).
- **[call] Runtime re-check.** Model construction (web) and the proxy stream (CLI) walk the decision's project steps before P, so D5 holds for a model in another category than the one probed (AR-1). Web walks every step; the CLI, whose decision already used the request's model, starts at the charged step. An empty walk (no target, no default) goes straight to P without probing the personal project.
- **[call] Deployment tags.** `x-litellm-tags` keeps following the project the request runs for (the asset's), not the charged project, so attribution never moves a request to another project's deployments.
- **[call] Virtual assistants without a project (rows 12, 14).** The IDE and the inline (`/assistants/virtual/model`) assistant keep their project on the credentials rule (`request.project or user.current_project`), so own keys, DIAL settings, deployment tags and usage records stay as before. Without a named project the request is billed as asset-less, to the default the profile shows (`effective_default_project`), as the CLI does (final review AR-4).
- **[call] Indexing with native embeddings (row 13, D5).** `LLM_PROXY_EMBEDDINGS_DISABLED` no longer exempts indexing from attribution: its summary and document chat models still go through LiteLLM, so they walk target, then D, then P. The funded check then reads the platform's default chat model, since no embedding reaches a budget (final review AR-3).
- **[call] A built model's payer is kept (analytics).** An agent builds its model once and sets the context again before every call. When such a re-decision cannot read funding, the payer the model construction recorded is kept. Fallbacks decided while funding was unreadable carry `budget_funding_unknown=true` in metrics, web and CLI. Routed chats whose candidate models fall in different budget categories report the last built model's payer; spend logs stay per model. One helper, `charge_outcome`, names every outcome for the decision, model construction and the CLI runtime walk (final review AR-2). The root-cause fix the review proposed (each built model carries its own outcome, the context is restored on exit, the router reports the candidate that served) is not planned under this ticket; the product owner chose not to open follow-up tickets for review findings, so the routed-chat limitation is accepted as documented (code review CR-001).
