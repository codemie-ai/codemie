# Technical Research

**Task**: mcp auth oauth popup
**Generated**: 2026-09-29
**Research path**: codegraph

---

## 1. Original Context

implement EPMCDME-15382. The full ticket body (summary, root cause, scope 1-3, acceptance criteria 1-9, out of scope) is in /home/taras_spashchenko/EPAM/cm/codemie/jira-mcp-auth-popup-closed-by-idp.md — read it first; it is the requirements.
Short form: CodeMie opens the MCP OAuth sign-in window with window.open(authUrl,'_blank'); the EPAM IdP opener guard self-closes it when window.opener is cross-origin. Fix: (1) shared open-window helper that cuts the child's opener (const w = window.open('', '_blank'); w.opener = null; w.location.href = authUrl), detect completion via GET /v1/mcp-auth/status polling instead of postMessage, callback page no-opener branch tries window.close(); (2) early-close detection via w.closed, early-close message + 'Open sign-in in a new tab' action, diagnostics beacon phase window_closed_before_callback; (3) downgrade the 'Ignoring auth callback for untracked auth_config_id' warn to debug.
feature_area: mcp auth oauth popup
run_dir: /home/taras_spashchenko/EPAM/cm/codemie/docs/superpowers/tasks/2026-09-29-epmcdme-15382-mcp-auth-popup-opener
Repos: backend /home/taras_spashchenko/EPAM/cm/codemie (callback page src/codemie/enterprise/mcp_auth/_callback_pages.py, router.py /status, _diagnostics.py); UI /home/taras_spashchenko/EPAM/cm/codemie-ui (src/hooks/useMCPAuthPrompt.ts, useAuthCallbackListener.ts, toolOAuthCallback.ts, src/store/chatGeneration.ts ~1211/1236, useChatAuthCallbacks.ts, MCPToolkitTest.tsx, MCPToolsSelectionStep.tsx, WorkflowDetailsPage.tsx and their existing tests). The UI repo may not be in the codegraph index; if so cover it with Glob/Read.

---

## 2. Codebase Findings

### Existing Implementations

Backend (`/home/taras_spashchenko/EPAM/cm/codemie`, indexed):
- `src/codemie/enterprise/mcp_auth/_callback_pages.py` — `build_oauth2_callback_page_script_response()` (L177-285) returns a static JS string (f-string, `{{ }}` escaping) served at `/v1/mcp-auth/oauth2/callback-page.js`. Success branch: `if (!window.opener)` -> `sendDiagnostics({})` + `updateMessage(CALLBACK_SUCCESS_OPEN_CODEMIE_MESSAGE)` only (no `window.close()`). With opener and `authConfigId && targetOrigin`: `opener.postMessage({type:'mcp_auth_callback', status:'success', auth_config_id}, targetOrigin)`, beacon, then `window.close()` unless `CALLBACK_KEEP_TAB_OPEN` (from `config.MCP_AUTH_CALLBACK_KEEP_TAB_OPEN`), with a 300 ms fallback message. Error branch: postMessage only when opener present; else beacon only.
- `_callback_pages.py` `_build_callback_page` builds HTML with `data-callback-result`, `data-auth-config-id`, `data-target-origin`; CSP `script-src 'self'; connect-src 'self'` (`_constants.py` `_CALLBACK_SECURITY_HEADERS`), so the script must stay a same-origin external file.
- `src/codemie/enterprise/mcp_auth/_diagnostics.py` — `OAuth2CallbackDiagnostics` (pydantic, `extra="ignore"`): `result: Literal["success","error","timeout"]`, `opener_present: bool|None`, `waited_ms` (0..3_600_000), `phase: str|None` (max 64), etc. `build_oauth2_callback_diagnostics_response`: `timeout` -> WARNING "MCP OAuth2 callback never observed by client" (includes phase); `error` or `opener_present is False` or post_message_error -> WARNING; else INFO. Endpoint is unauthenticated; strings pass through `_sanitize_log_value`.
- `src/codemie/enterprise/mcp_auth/router.py` — `GET /v1/mcp-auth/status` (`mcp_auth_status_enabled`, L482-519), `mcp_config_id` query, `Depends(authenticate)`, `_check_mcp_config_access`. If `config.auth_config` is not a dict -> `build_discovered_auth_status_response` (`_initiate.py:504`) else `_evaluate_auth_status` via enterprise package. Response `MCPAuthStatusResponse.status` Literal: authenticated | authentication_required | session_expired | config_error. `build_discovered_auth_status_response` loads the discovered-flow snapshot by (user, session binding hash, mcp_config_id); the `config_error` branch is visible in source; the remainder (how `authenticated` is derived for discovered flows) was truncated in exploration and not verified (ticket asks to verify).
- `_oauth2_callback.py` — `build_oauth2_callback_response` logs "MCP OAuth2 callback received"; discovered/recovery callback variants store token via `_store_callback_token` then `_build_success_callback_response` (logs "MCP auth callback success page served"). Dispatch via `dependencies.py` re-exports.
- `_constants.py` — `_CALLBACK_EVENT_TYPE="mcp_auth_callback"`, `_CALLBACK_FALLBACK_DELAY_MS=300`, success/close messages, `_OAUTH2_CALLBACK_DIAGNOSTICS_PATH`, `_CALLBACK_STATE_MAX_AGE` 10 min.
- Separate, similar per-provider tool OAuth callback (Jira/Confluence/GitLab): `src/codemie/rest_api/routers/oauth_router_factory.py` (`/callback`, `/callback-page.js`, `build_tool_oauth_callback_script`) — uses `tool_oauth_callback` postMessage; not named in scope.

UI (`/home/taras_spashchenko/EPAM/cm/codemie-ui`, not indexed; read directly):
- `src/hooks/useMCPAuthPrompt.ts` — `initiate` (L94-153) and `continueAuth` (L155-192). Non-oauth2 path: `window.open(payload.auth_url,'_blank')` L134 with `popupBlocked: popup === null` log and row -> `authenticating`. oauth2 path: `initiate` only stores `pending_initiate` (via `getPendingInitiate`); `continueAuth` L162 opens the window, sets `POPUP_BLOCKED_AUTH_MESSAGE` `error_context` when `popup === null`. Rows are `MCPAuthGateServer[]`; `onSuccess` marks authenticated and calls `onAllAuthenticatedRef` in a microtask then `setRows([])`. `onError`/`onTimeout` set `status: getRecoverableAuthStatus(row)`. Wires `useAuthCallbackListener({trackedAuthConfigIds, liveAuthConfigIds, onSuccess, onError, onTimeout})`. Window handle `popup` is not stored anywhere today.
- `src/hooks/useAuthCallbackListener.ts` — postMessage-only completion. `window.addEventListener('message', ...)`; validates shape (`type==='mcp_auth_callback'`), origin against `getApiOrigin()` (`appInfoStore.getMcpAuthOrigin()` else `api.BASE_URL` origin else `window.location.origin`), then tracked/retained ids. Untracked warn at L366-376. Two-stage timers: hint 60 s (`mcpAuthTimeoutSeconds` override, message `AUTH_CALLBACK_HINT_MESSAGE`) and acceptance 600 s; `reportCallbackTimeoutDiagnostics` beacon (`result:'timeout'`, `phase:'awaiting_callback'`, `navigator.sendBeacon` with `fetch keepalive` fallback, URL `${api.BASE_URL}/v1/mcp-auth/oauth2/callback-diagnostics`). Late callbacks dispatched to captured `flowOriginsRef` handlers. Exports include `AUTH_CALLBACK_EVENT_TYPE`, `AUTH_CALLBACK_HINT_MESSAGE`, `AUTH_CALLBACK_ACCEPTANCE_MS`.
- `src/hooks/toolOAuthCallback.ts` — tool OAuth (GitLab/Jira/Confluence) postMessage plumbing (`tool_oauth_callback`, `getToolOAuthCallbackOrigin`). Documents that the callback origin can differ from UI origin. Different flow from MCP auth; touched only if the helper is reused.
- `src/store/chatGeneration.ts` — `initiatePromptAuth` (`window.open(payload.auth_url,'_blank')` L1211, result discarded, so no popup-blocked detection on this path) and `continuePromptAuth` (L1228-1255, `window.open` L1236 with `popup === null` -> `POPUP_BLOCKED_AUTH_MESSAGE`). State updated via `updatePromptRowsAtIndexes(chat, historyIndex, messageIndex, ...)`; `markPromptAuthSuccess(chatId, authConfigId)` at L1273 (zustand-style store object).
- `src/utils/mcpAuth.ts`, `src/utils/mcpAuthInitiate.ts` — `getPendingInitiate`, `getRecoverableAuthStatus`, `POPUP_BLOCKED_AUTH_MESSAGE`, `MISSING_REDIRECT_HOSTNAME_MESSAGE`, `isAuthenticatingGateRow`, `getLiveAuthConfigIds`, `parseMCPAuthRequiredErrorPayload`.
- Other `useAuthCallbackListener` mount points (per ticket; paths confirmed via Glob): `src/pages/assistants/components/AssistantForm/components/Toolkits/MCPToolkit/MCPToolkitTest.tsx`, `.../MCPToolkit/MCPToolkitForm/MCPToolsSelectionStep.tsx`, `src/pages/chat/hooks/useChatAuthCallbacks.ts`, `src/pages/workflows/WorkflowDetailsPage.tsx`. Line numbers in ticket not re-verified.
- No existing shared open-window helper found (Glob for open*Window/openAuth/openPopup returned nothing).

### Architecture and Layers Affected
- Backend: enterprise bridge layer `src/codemie/enterprise/mcp_auth/` (callback page script, diagnostics model/handler, `/status` router). Thin FastAPI router + module-level builder functions re-exported through `dependencies.py`.
- UI: hooks layer (`useMCPAuthPrompt`, `useAuthCallbackListener`), store layer (`chatGeneration`), utils (`mcpAuth*`), components (MCPToolkitTest, MCPToolsSelectionStep), pages (WorkflowDetailsPage, chat hooks).

### Integration Points
- UI -> `POST <initiate_url>` (returns `auth_url`, oauth2 also `redirect_uri` hostname data), `GET /v1/mcp-auth/status?mcp_config_id=`, `POST /v1/mcp-auth/oauth2/callback-diagnostics` (unauthenticated beacon).
- Callback page -> UI via `postMessage` (origin from `_derive_callback_target_origin`, `mcpAuthOrigin` / `CALLBACK_API_BASE_URL`).
- `/status` -> enterprise package `codemie_enterprise.mcp_auth.evaluate_auth_status` and discovered-flow store (Redis); TMS.
- `useMCPAuthPrompt` consumed by `MCPToolkitTest.tsx` and `MCPToolsSelectionStep.tsx` (two concurrent listener instances on the assistant edit page); chat uses `useChatAuthCallbacks` + `chatGeneration` store; workflows use `WorkflowDetailsPage`.

### Patterns and Conventions
- Callback script is a Python f-string of JS: braces doubled; constants injected from `_constants.py`.
- Diagnostics: beacon is best-effort, never throws; backend model fields bounded via pydantic `Field(max_length=...)`; `extra="ignore"`.
- UI: refs for latest callbacks, timers held in `useRef` records, `console.info/warn` with `[mcp-auth]` prefix, license header on every file, `@/` import alias.
- Backend guides: `.ai-run/guides/integration/mcp-integration.md` (keep MCP auth behaviour in existing routers/services).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/mcp-integration.md` — only covers config location and timeouts; nothing on the popup/callback flow.
- Other relevant guides exist (not all read): `development/logging-patterns.md`, `testing/testing-patterns.md`, `development/configuration-patterns.md`, `standards/code-quality.md`, `quality-gates.md`. UI repo has its own `AGENTS.md` (not read in detail).

### Architectural Decisions
- Inline comments in `useAuthCallbackListener.ts` record the 60 s hint / 600 s acceptance rationale (backend `_PKCE_TTL_SECONDS` 600 s, `_CALLBACK_STATE_MAX_AGE` 10 min, discovered flow TTL 900 s).
- `_diagnostics.py` docstring: timeout reported by parent window omits `opener_present`.

### Derived Conventions
- Diagnostics `phase` values in use: `awaiting_callback`. Ticket proposes `window_closed_before_callback` (fits max_length 64).

---

## 4. Testing Landscape

### Existing Coverage
Backend (`tests/enterprise/mcp_auth/`): `test_oauth2_callback_bridge.py`, `test_mcp_auth_status_bridge.py`, `test_oauth2_initiate_bridge.py`, `test_feature_gating.py`, plus saml/tms/trust/recovery bridge tests. No file named for callback-page script or diagnostics found by name (Glob `*callback_page*`/`*diagnostics*` empty); whether script/diagnostics tests live inside `test_oauth2_callback_bridge.py` or `test_feature_gating.py` was not verified.
UI:
- `src/hooks/__tests__/useMCPAuthPrompt.test.tsx`
- `src/hooks/__tests__/useAuthCallbackListener.test.tsx`
- `src/store/__tests__/chatGeneration.promptAuth.test.ts`
- `src/pages/chat/hooks/__tests__/useChatAuthCallbacks.test.ts`
- `src/pages/workflows/__tests__/WorkflowDetailsPage.authCallback.integration.test.tsx`

### Testing Framework and Patterns
Backend: pytest with `unittest.mock` (MagicMock/patch). UI: vitest-style `*.test.ts(x)` under `__tests__/` (framework/version not verified).

### Coverage Gaps
- No open-window helper exists, so no tests for it.
- `/status` bridge test exists; no coverage of a poller (does not exist yet).
- Diagnostics handler has no verified dedicated test for a new phase/result value.

---

## 5. Configuration and Environment

### Environment Variables
- `MCP_AUTH_CALLBACK_KEEP_TAB_OPEN` (`config.MCP_AUTH_CALLBACK_KEEP_TAB_OPEN`) — injected into callback script.
- `CALLBACK_API_BASE_URL` — callback/redirect origin (config.py redirect URI properties).
- UI runtime config via `appInfoStore`: `getMcpAuthOrigin()`, `getMcpAuthTimeoutSeconds()` (`mcpAuthTimeoutSeconds`); `VITE_API_URL` may be a relative path.

### Configuration Files
`src/codemie/configs/config.py` (Config, pydantic settings); `src/codemie/enterprise/mcp_auth/_constants.py`.

### Feature Flags and Deployment Concerns
MCP auth router split into disabled/enabled variants (`get_mcp_auth_router`; disabled `/status` returns 503 `MCPAuthDisabledResponse`). Callback script served as static external JS under CSP `script-src 'self'`. Cross-origin UI/callback deployments possible.

---

## 6. Risk Indicators

- Cross-origin completion detection: with `opener = null` the callback page cannot postMessage, so completion depends on `/status`; `/status` requires authenticated user (`authenticate`) and returns 503 when MCP auth disabled.
- `build_discovered_auth_status_response` (`_initiate.py:504`) authenticated derivation unverified; ticket flags it (discovered flows lack persisted `auth_config`, so `/status` uses this branch). Snapshot lookup is by binding hash and could raise via `_load_discovered_flow_snapshot_for_binding_or_error` (client error path logs WARNING).
- Duplicate handling risk: two `useMCPAuthPrompt` instances mounted on the assistant edit page, plus chat and workflow listeners; AC 6/7 require a single `onSuccess`/re-run. Existing listener dedupes via tracked-id clearing; a new poller must reuse the same path.
- `chatGeneration.initiatePromptAuth` currently ignores `window.open` return (no popup-blocked detection); ticket says existing detection must still work.
- Popup blocker behaviour with `window.open('', '_blank')` then async `location.href` assignment is same-gesture; `w.opener = null` and `w.closed` semantics are browser-dependent (needs manual browser verification; jsdom will not model it).
- Callback page no-opener `window.close()` may be blocked by browsers for non-script-opened tabs (ticket: "verify"); existing text fallback must remain.
- Two-stage timers in `useAuthCallbackListener` (hint/acceptance/retained ids/flow origins) are intricate; adding polling and early-close states touches them.
- Diagnostics `result` Literal has no value for early close; ticket suggests `result:"error"` with `phase`. Speculative: any new Literal value or phase-specific log line would need a matching change to the pydantic model and handler.
- Speculative: the "Open sign-in in a new tab" action may need a new row field or UI state in `MCPAuthGateServer` / prompt components; not verified which components render `error_context`.
- Tool-OAuth flows (`toolOAuthCallback.ts`, SharePoint) share the `window.open` pattern but are out of scope.
- Backend indexed file `_callback_pages.py` embeds JS inside a Python f-string; brace escaping errors are easy to introduce and only caught by tests that inspect the script text.
- Gaps: no verified dedicated tests for callback script and diagnostics handler on the backend; UI repo not indexed by codegraph, so blast radius unavailable for UI symbols.

---

## 7. Summary for Complexity Assessment

The change spans two repositories and three layers. Backend surface is small and contained in `src/codemie/enterprise/mcp_auth/`: the callback-page JS string in `_callback_pages.py` (no-opener branch) and possibly `_diagnostics.py` (a new early-close phase). `/status` already exists and returns the needed tri-state; only the discovered-flow `authenticated` behaviour needs verification. UI surface is larger: a new shared open-window helper, and edits to `useMCPAuthPrompt.ts` (two `window.open` sites), `chatGeneration.ts` (two sites), and `useAuthCallbackListener.ts` (add `/status` polling, early-close detection, downgrade the untracked log). Mount points (MCPToolkitTest, MCPToolsSelectionStep, useChatAuthCallbacks, WorkflowDetailsPage) consume the listener and may need result-shape or UI-message changes for the early-close action.

Technical novelty is moderate: cutting `window.opener` and polling `w.closed` are browser-behaviour dependent (popup blockers, `window.close()` policy) and cannot be fully verified in unit tests. Completion detection moves from push (postMessage) to pull (polling), while the existing two-stage timeout and dedupe logic must continue to yield exactly one success handling across multiple concurrent listener instances.

Test posture: UI has existing tests for every touched hook/store (`useMCPAuthPrompt`, `useAuthCallbackListener`, `chatGeneration.promptAuth`, `useChatAuthCallbacks`, workflow integration) that will need updating; the helper is new and untested. Backend has bridge tests for callback and status, but no verified tests for the callback script text or diagnostics handler. Key risks: cross-origin completion via `/status`, duplicate success handling, browser-specific behaviour, and JS-in-f-string escaping.

---

## 8. External References

- `/home/taras_spashchenko/EPAM/cm/codemie/jira-mcp-auth-popup-closed-by-idp.md` — resolved, read in full. Key facts:
  - Opener guard on `access.epam.com` (Keycloak realm `plusx`): if `window.opener` is cross-origin and origin not in `allowedOpeners` (`["https://miro.com","https://login.microsoftonline.com"]`), the page self-closes ~100 ms after landing.
  - Scope 1: helper `const w = window.open('', '_blank'); if (w) { w.opener = null; w.location.href = authUrl }`; do not use the `noopener` feature (returns null, breaks popup-blocked and close detection). Switch `useMCPAuthPrompt.ts:134` and `:162`, `chatGeneration.ts:1211` and `:1236`. Completion via `GET /v1/mcp-auth/status?mcp_config_id=` (`router.py:482`): check on window close and about every 3 s within the 600 s window; `status=authenticated` -> existing `onSuccess`. Verify `/status` returns `authenticated` for discovered flows once the callback stored the token (`build_discovered_auth_status_response`). Callback page: keep `opener.postMessage` path; with no opener try `window.close()`; keep `MCP_AUTH_CALLBACK_KEEP_TAB_OPEN`.
  - Scope 2: check `w.closed` about every 500 ms while `authenticating`; on close query `/status`; if authenticated treat as success, else stop spinner immediately and show: "The sign-in window closed before authentication finished. Your identity provider may require an extra step (for example a device compliance check). Open sign-in in a new tab to complete it." plus an **Open sign-in in a new tab** action that calls `initiate` again and opens the new `auth_url` in a normal tab (e.g. `<a target="_blank" rel="noopener">`), completion detected by `/status`. Beacon `POST /v1/mcp-auth/oauth2/callback-diagnostics` with e.g. `result:"error"`, `phase:"window_closed_before_callback"`, logged at WARNING. Apply to `useMCPAuthPrompt` and chat prompt auth.
  - Scope 3: `useAuthCallbackListener.ts:366-376` untracked-id `console.warn` -> `debug` (or silent when instance tracks nothing); keep warn for bad shape / unexpected origin. Instances: `MCPToolkitTest.tsx:87`, `MCPToolsSelectionStep.tsx:158`, `useChatAuthCallbacks.ts:70`, `WorkflowDetailsPage.tsx:64`.
  - Acceptance criteria 1-9: AC2 `window.opener` null on every page; AC3 keep `POPUP_BLOCKED_AUTH_MESSAGE`; AC4 success without postMessage incl. cross-origin callback and `KEEP_TAB_OPEN=true`; AC5 leave spinner within ~1 s of early close, backend WARNING; AC6 new-tab completion detected without reload, re-run exactly once; AC7 happy path success within ~2 s, single handling; AC8 no warn for untracked id; AC9 unit tests for helper, `useMCPAuthPrompt`, `useAuthCallbackListener`, chat prompt auth in `chatGeneration`, callback page script no-opener branch.
  - Out of scope: asking EPAM IAM to allow-list origins; SharePoint OAuth popups (`SharePointReindexAuthPopup.tsx:182`, `useSharePointOAuth.ts:160`); OPSWAT detection in automated browsers.
  - Backend log markers: `MCP OAuth2 initiate issued` -> `MCP OAuth2 callback received` -> `MCP auth callback success page served` -> `MCP OAuth2 callback client diagnostics`.
