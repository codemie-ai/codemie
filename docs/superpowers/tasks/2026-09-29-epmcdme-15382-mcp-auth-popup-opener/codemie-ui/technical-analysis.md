# Technical Research (UI)

**Task**: mcp auth oauth popup (EPMCDME-15382, codemie-ui only)
**Generated**: 2026-09-29
**Research path**: filesystem
**Repo**: /home/taras_spashchenko/EPAM/cm/codemie-ui (React 19.2.8, Valtio 2.1.5, vitest 1.6.1). Backend covered in ../technical-analysis.md.

---

## 1. Original Context

implement EPMCDME-15382 in the codemie-ui repo. Requirements: /home/taras_spashchenko/EPAM/cm/codemie/jira-mcp-auth-popup-closed-by-idp.md. Scope: UI only: src/hooks/useMCPAuthPrompt.ts, useAuthCallbackListener.ts, toolOAuthCallback.ts, src/store/chatGeneration.ts (~1211/1236), useChatAuthCallbacks.ts, MCPToolkitTest.tsx, MCPToolsSelectionStep.tsx, WorkflowDetailsPage.tsx, and any component rendering the auth prompt / error_context (for the 'Open sign-in in a new tab' action). Also: UI API client for GET /v1/mcp-auth/status and the diagnostics beacon, existing window/popup utils and where a shared open-window helper should live, existing tests and mocking patterns, lint/test commands, guides. Compute UI blast radius manually.

---

## 2. Codebase Findings

### Existing Implementations
- `src/hooks/useMCPAuthPrompt.ts` — `initiate` L94-153: non-oauth2 (SAML) path `window.open(payload.auth_url,'_blank')` L134, logs `popupBlocked`, but does NOT set the blocked message when null (row goes `authenticating` regardless). oauth2 path stores `pending_initiate`. `continueAuth` L155-192: `window.open` L162; `popup===null` sets `POPUP_BLOCKED_AUTH_MESSAGE` in `error_context`, else `authenticating`. Popup handle is discarded. `onError`/`onTimeout` (~L217-246) set `error_context` (`AUTH_CALLBACK_HINT_MESSAGE` on timeout); `useAuthCallbackListener` wired L251.
- `src/store/chatGeneration.ts` — `initiatePromptAuth` L1211 `window.open(...)` result discarded (no blocked detection); `continuePromptAuth` L1228-1255, `window.open` L1236 called INSIDE the `updatePromptRowsAtIndexes` row-mapper (side effect in a state updater); `popup===null` -> `POPUP_BLOCKED_AUTH_MESSAGE`. `markPromptAuthSuccess` L1273, error path sets `error_context` ~L1280-1291. Valtio store object, not a React hook: handle/timer for close-detection and status polling has no React lifecycle here.
- `src/hooks/useAuthCallbackListener.ts` (449 lines) — postMessage-only. Constants L21-36 (`AUTH_CALLBACK_EVENT_TYPE`, hint 60 s, acceptance 600 s, `AUTH_CALLBACK_HINT_MESSAGE`). `reportCallbackTimeoutDiagnostics` L157-185 is the ONLY diagnostics beacon: module-private, hardcodes `result:'timeout'`, `opener_present:false`, `phase:'awaiting_callback'`, url `${api.BASE_URL}/v1/mcp-auth/oauth2/callback-diagnostics`, `sendBeacon` with `fetch keepalive` fallback, `waited_ms` clamped to 3_600_000. Message handler L335-395: shape check, origin check vs `getApiOrigin()` (warn), untracked-id `console.warn` L370-376 (item 3 target), then `Received auth callback` info. Exports L442-449.
- `src/hooks/toolOAuthCallback.ts` — tool OAuth (`tool_oauth_callback`, `getToolOAuthCallbackOrigin`, `isToolOAuthCallbackMessage`); separate Jira/Confluence/GitLab flow; only imported by `useAuthCallbackListener` (origin logic) and tool OAuth hooks. No change needed unless origin helper is reused.
- `src/pages/chat/hooks/useChatAuthCallbacks.ts` L70 — `useAuthCallbackListener({trackedAuthConfigIds, liveAuthConfigIds, contextKey, ...handlers})`; chat consumer; mounted by `ChatPage.tsx`.
- `src/pages/workflows/WorkflowDetailsPage.tsx` L64 — `useAuthCallbackListener({ trackedAuthConfigIds })` only; opens no window itself.
- `MCPToolkitTest.tsx` L87 and `MCPToolsSelectionStep.tsx` L158 — each calls `useMCPAuthPrompt` (two concurrent listeners). `onAllAuthenticated` in MCPToolkitTest re-runs `runTest()` guarded by `isRetryingRef` (relevant to "re-run exactly once", AC 6). Both also have unrelated `window.open(brokerLoginUrl,'_blank','noopener,noreferrer')` (L148 / L296) — a different flow (broker login), not in scope.
- Prompt rendering: `src/pages/chat/components/AssistantAuthGate/AssistantAuthGateRow.tsx` renders `row.error_context` (L97-101, only for `authentication_required`/`session_expired`), Continue/Cancel/Authenticate buttons (`Button`, `ButtonType`, `ButtonSize`), `StatusBadge`. Callbacks `onAuthenticate/onContinue/onCancel`. Reused by `ChatAiMcpAuthPrompt.tsx` (chat; wires to `chatGenerationStore.*PromptAuth`), `MCPToolkitTest.tsx`, `MCPToolsSelectionStep.tsx`. Also `ChatAiAuthPrompt.tsx` wraps chat prompt. This is where an "Open sign-in in a new tab" action (new optional prop/callback) would render; it is one shared component for all three surfaces.
- Types: `src/types/entity/mcpAuth.ts` — `MCPAuthGateServer` (fields incl. `status`, `error_context`, `initiate_url`, `pending_initiate`, `recoverable_status`), `MCPAuthStatusResponse` (already typed: `status: MCPAuthResolvedStatus`, `initiate_url`, ...), `MCPAuthInitiateResponse`. Utils: `src/utils/mcpAuth.ts` (`isAuthenticatingGateRow`, `getLiveAuthConfigIds`, `normalizeMCPAuthGateServer`, `parseMCPAuthRequiredErrorPayload`), `src/utils/mcpAuthInitiate.ts` (`getPendingInitiate`, `getRecoverableAuthStatus`, `POPUP_BLOCKED_AUTH_MESSAGE`, `MISSING_REDIRECT_HOSTNAME_MESSAGE`).

### UI API client for /v1/mcp-auth/status and beacon
- NO client for `GET /v1/mcp-auth/status` exists in src (grep for `mcp-auth` outside tests hits only the beacon URL and `x-user-mcp-auth-location` header reads). The `MCPAuthStatusResponse` type exists but is unused by a fetcher. Generic client: `api` from `@/utils/api` (`api.get(url)` -> Response, `api.post`, `api.BASE_URL`); precedent `api.get(...).then(r => r.json())` in `src/store/assistants.ts` L285/333.
- Beacon: only the private function above (timeout only). Early-close beacon needs `result:'error'`, `phase:'window_closed_before_callback'` — requires generalizing/exporting that function (its params are currently fixed).

### Window/popup utilities
- `src/hooks/usePopupWindow.ts` — hook managing one popup, `open(url, {width,height,features}) => boolean`, polls `window.closed` every 500 ms via `usePolling`, `onClose`, closes on unmount; deliberately does not expose the `Window`; used by `useOAuth`, `useOAuthConnect`, `useToolOAuthTest`. Tested in `src/hooks/__tests__/usePopupWindow.test.ts`. Uses `window.open(url, ...)` with url directly (does not null the opener). Precedent for 500 ms close detection, but bound to React lifecycle and sets width/height features (MCP flow uses a tab, no features).
- No shared "open window with opener cut" helper exists. Likely locations by convention: `src/utils/` (flat utils dir; `mcpAuth.ts`, `mcpAuthInitiate.ts` already there). Usable from both hooks and the Valtio store (no React dependency needed).
- Other `window.open` callers in repo (not in scope; SharePoint explicitly out of scope): `SharePointReindexAuthPopup.tsx:182`, `useSharePointOAuth.ts:160`, `LoginSuccessPage.tsx`, `HelpPopup.tsx`, `ApplicationsPage.tsx`.

### Architecture and Layers Affected
Utils (new helper, mcpAuth), hooks (`useMCPAuthPrompt`, `useAuthCallbackListener`), Valtio store (`chatGeneration`), shared presentational component (`AssistantAuthGateRow`), consumers (`MCPToolkitTest`, `MCPToolsSelectionStep`, `ChatAiMcpAuthPrompt`, `useChatAuthCallbacks`), API client call (new status fetch).

### Blast Radius (manual grep, cross-checked with `codegraph explore` in codemie-ui: callers of useMCPAuthPrompt and AssistantAuthGateRow match; AssistantAuthGateRow rendered by MCPToolsSelectionStep, MCPToolkitTestProvider, ChatAiMcpAuthPrompt)
- `useMCPAuthPrompt` <- `MCPToolkitTest.tsx` (MCPToolkitTestProvider), `MCPToolsSelectionStep.tsx` only (codegraph-confirmed; the `chatGeneration.ts` grep hit is a name/comment match, not an importer). Tests: `MCPAuthPromptWiring.test.tsx`, `useMCPAuthPrompt.test.tsx`.
- `useAuthCallbackListener` (+ exported constants) <- `useMCPAuthPrompt`, `useChatAuthCallbacks`, `WorkflowDetailsPage`, `toolOAuthCallback.ts` (mention). Tests: `useAuthCallbackListener.test.tsx` (864 lines), `useChatAuthCallbacks.test.ts`, `WorkflowDetailsPage.authCallback.integration.test.tsx`, `ChatPage.test.tsx`, `ChatPage.integration.test.tsx`.
- `useChatAuthCallbacks` <- `ChatPage.tsx` (test `ChatPage.resize.test.tsx` mocks it).
- `AssistantAuthGateRow` <- `ChatAiMcpAuthPrompt.tsx`, `MCPToolkitTest.tsx`, `MCPToolsSelectionStep.tsx` (test `AssistantAuthGateRow.test.tsx`, `MCPAuthPromptWiring.test.tsx`, `ChatAiMcpAuthPrompt.test.tsx`, `ChatAiMessage.test.tsx`).
- `chatGeneration` store `initiatePromptAuth/continuePromptAuth` <- `ChatAiMcpAuthPrompt.tsx`; plus wide store importers (ChatPage etc.).
- `MCPAuthGateServer`/`error_context` types: `src/types/entity/mcpAuth.ts`, `utils/mcpAuth*.ts`.

### Patterns and Conventions
- License header on every file; `@/` alias; `[mcp-auth]` console prefix; timers/handlers held in `useRef`; latest-callback refs; best-effort beacon swallowing errors.
- Security guide `.ai-run/guides/development/security-patterns.md` (L58-74) requires origin equality to a configured value, check before state change, shape-checked payload for cross-window messages; also forbids passing untrusted strings to `window.open`/`location.href` (L48) — `auth_url` comes from backend response; assigning `w.location.href = authUrl` is a new sink the guide's rule should be reviewed against (scheme validation e.g. http/https).

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` in codemie-ui: README, project, quality-gates, testing/{testing-patterns,qa-strategy}, development/security-patterns, patterns/state-management, components/*, architecture/*. No guide covers MCP auth popup flow. AGENTS.md rule: run `npm ci` before trusting test/typecheck results.

### Architectural Decisions
Inline comments in `useAuthCallbackListener.ts` explain 60 s hint / 600 s acceptance rationale and the `waited_ms` ceiling; `toolOAuthCallback.ts` documents callback origin may differ from UI origin (so status polling, not postMessage, is needed cross-origin).

### Derived Conventions
Only `awaiting_callback` phase used today in the UI beacon.

---

## 4. Testing Landscape

### Existing Coverage
- `src/hooks/__tests__/useMCPAuthPrompt.test.tsx` (508 lines): `vi.mocked(window.open).mockReturnValue(window)` for success; default mock returns null (popup blocked); asserts `expect(window.open).toHaveBeenCalledWith('https://idp.example.com/saml/start', '_blank')` (L193) and `toHaveBeenCalledTimes(1)` — these break when the call becomes `window.open('', '_blank')` + href assignment. Note `mockReturnValue(window)` returns the real jsdom window, so `w.opener = null`/`w.location.href = ...` assignments would hit the test window (jsdom `location.href` set = not-implemented navigation); tests will need fake window objects.
- `src/hooks/__tests__/useAuthCallbackListener.test.tsx` (864 lines): `vi.useFakeTimers()` (L48), `vi.mock('@/utils/api')`, `vi.mock('@/store/appInfo')`, dispatches MessageEvents; covers timeouts and untracked warn paths.
- `src/store/__tests__/chatGeneration.promptAuth.test.ts` (296 lines) covers initiate/continue prompt auth; also `chatGeneration.test.ts`, `chatGeneration.renamePoll.test.ts`.
- Component/wiring: `AssistantAuthGateRow.test.tsx`, `ChatAiMcpAuthPrompt.test.tsx`, `ChatAiMessage.test.tsx`, `MCPAuthPromptWiring.test.tsx`, `useChatAuthCallbacks.test.ts`, `WorkflowDetailsPage.authCallback.integration.test.tsx`, `usePopupWindow.test.ts`.

### Testing Framework and Patterns
vitest 1.6.1 with workspace (`vitest.workspace.ts`: `unit` and `integration` projects; setupFiles `src/setupTests` + `.unit`/`.integration`); `window.open` is globally mocked as a vi.fn (default null; defined in setup files, not verified which). Fake timers for the 60 s/600 s windows; `api` module-mocked.

### Coverage Gaps
No test/impl for: open-window helper, `/status` fetch, `window.closed` polling, early-close message/action, diagnostics beacon beyond timeout (check for a beacon assertion in listener test), the `AssistantAuthGateRow` new action. No callback-page script test lives in the UI repo (backend).

---

## 5. Configuration and Environment

### Environment Variables
None specific found in UI for this flow. Runtime app-info values via `appInfoStore`: `getMcpAuthOrigin()`, `getMcpAuthTimeoutSeconds()` (hint override, treats non-positive as default). `api.BASE_URL` for beacon URL.

### Configuration Files
`package.json` scripts: `npm run lint` (eslint, `--ext .js,.jsx,.cjs,.mjs,.tsx,.ts`), `npm run lint:fix`, `npm run typecheck` (`tsc --noEmit`), `npm run test:unit` / `test:integration` / `test` (vitest run), `npm run license-check`, `npm run secrets:check`, `format` (prettier). Gate order per `.ai-run/guides/quality-gates.md`: lint, typecheck, license-check (only if deps change), secrets, tests. Run `npm ci` first.

### Feature Flags and Deployment Concerns
No flags. Backend `MCP_AUTH_CALLBACK_KEEP_TAB_OPEN` affects whether the callback tab closes (AC 4). `/status` needs `Depends(authenticate)` — UI `api` client attaches auth. UI and backend changes are independent deployables; UI polling works even with old callback page.

---

## 6. Risk Indicators

- Speculative: existing tests assert `window.open(url,'_blank')` and return the real `window`; switching to `open('', '_blank')` then `w.opener=null; w.location.href=url` breaks ~6+ assertions in `useMCPAuthPrompt.test.tsx` and `chatGeneration.promptAuth.test.ts`; needs a fake-window stub.
- `continuePromptAuth` performs `window.open` inside the Valtio row-mapper; adding handle storage / interval setup there is side-effect-in-updater and may run twice; likely needs restructuring.
- Chat path is a non-React store: close detection (500 ms) and 3 s `/status` polling have no hook lifecycle; cleanup on cancel/unmount/chat switch must be handled (leak risk). `useChatAuthCallbacks` (React) could host it instead.
- Two concurrent `useMCPAuthPrompt` instances on the assistant edit page: both would poll `/status` and both fire success -> duplicate `runTest()` re-runs (AC 6/7). Success handling must stay idempotent per flow.
- No existing `/status` client and no `MCPAuthStatusResponse` fetcher; the discovered-flow status behavior is unverified on the backend (ticket asks to verify), so UI success detection depends on it.
- Diagnostics beacon is module-private and timeout-hardcoded; needs generalizing without breaking existing timeout beacon tests.
- `initiatePromptAuth` and SAML `initiate` path ignore `popup===null` today; AC 3 (popup-blocked still shows the message) is only enforced for oauth2 continue paths; adding it there is a behavior change.
- `w.location.href = auth_url` assigns backend-provided URL; security guide requires scheme validation for `window.open`/`location.href` sinks.
- Early-close message needs `error_context` rendering in `AssistantAuthGateRow`, which only shows it for `authentication_required`/`session_expired`; status after early close is `getRecoverableAuthStatus(row)` so it renders, but the new action requires a new prop threaded through 3 consumers.
- The 60 s hint is currently shown even if popup closed; interplay of hint timer, close detection and polling within `useAuthCallbackListener`'s two-stage timers (864-line test file) is the highest-complexity change.
- Untracked-id warn downgrade (item 3) is trivial (one log line, L370) but existing tests may assert `console.warn`.

---

## 7. Summary for Complexity Assessment

Layers: a new util (open-window helper) plus status-fetch and generalized beacon; hooks (`useMCPAuthPrompt`, `useAuthCallbackListener`); the Valtio `chatGeneration` store; the shared `AssistantAuthGateRow` component and three consumers; `WorkflowDetailsPage`/`useChatAuthCallbacks` need at most the item-3 log change. Roughly 8-12 source files and 6-8 test files affected; importers are few and well-bounded (blast radius listed above), but the hooks and store are shared by assistant editor, chat and workflows.

Novelty: moderate. There is no existing opener-cut helper, `/status` client, close-detection outside `usePopupWindow` (which hides the Window), or non-timeout beacon. The chat store has no lifecycle for timers/handles, and two concurrent hook instances risk duplicate success handling. Existing tests are extensive and use `window.open` mocks that must be redesigned.

Test posture: strong existing coverage for hooks, store and components (about 1,700 lines across the three core test files), fake-timer and module-mock patterns already in place; gaps only for the new behavior. Key risks: idempotent success across listeners, polling cleanup, side effects in store updaters, and security review of the `href` assignment.

---

## 8. External References

- `/home/taras_spashchenko/EPAM/cm/codemie/jira-mcp-auth-popup-closed-by-idp.md` — resolved; requirements (helper snippet `const w = window.open('', '_blank'); if (w) { w.opener = null; w.location.href = authUrl }`, no `noopener` feature because it returns null; poll `/v1/mcp-auth/status?mcp_config_id=` ~3 s and on close; check `w.closed` ~500 ms; early-close message text; beacon `result:"error"`, `phase:"window_closed_before_callback"`; demote untracked warn to debug; ACs 1-9).
- `/home/taras_spashchenko/EPAM/cm/codemie/docs/superpowers/tasks/2026-09-29-epmcdme-15382-mcp-auth-popup-opener/technical-analysis.md` — resolved; backend orientation (read first 80 lines). Note its statement that the ticket's UI line numbers were unverified: verified here, they match (useMCPAuthPrompt L134/162, chatGeneration L1211/1236, listener warn L370-376, MCPToolkitTest L87, MCPToolsSelectionStep L158, useChatAuthCallbacks L70, WorkflowDetailsPage L64).
