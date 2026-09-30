# EPMCDME-15382 MCP OAuth sign-in window opener fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Stop the EPAM IdP opener guard closing the MCP OAuth window; detect completion via `/status` polling and surface early close.
**Architecture:** Opener-cutting `openSignInWindow` util plus `watchSignInWindow` (500 ms `w.closed`, 3 s `/status`, 600 s cap). Success stays idempotent through existing handlers. Backend: no-opener callback branch closes the tab.
**Tech Stack:** codemie-ui (React 19, Valtio, vitest 1.6) in `/home/taras_spashchenko/EPAM/cm/codemie-ui` (new feature branch off main, same ticket name); backend Python/pytest in `/home/taras_spashchenko/EPAM/cm/codemie` (branch EPMCDME-15382-mcp-auth-popup-opener).
**Spec:** `/home/taras_spashchenko/EPAM/cm/codemie/docs/superpowers/tasks/2026-09-29-epmcdme-15382-mcp-auth-popup-opener/spec.md`

Commit per task using the repository's existing convention (each repo's `.ai-run/guides/standards/git-workflow.md` or equivalent).

## Global Constraints
- UI commands (run in codemie-ui): test `npx vitest run <file>`; per task also `npm run lint` and `npm run typecheck`. Every new file carries the license header; `@/` alias; `[mcp-auth]` console prefix.
- Backend commands: `poetry run pytest <file>`; `poetry run ruff format && poetry run ruff check`.
- Only `http:`/`https:` `auth_url` may reach `location.href`.
- Tests use fake window objects, never `mockReturnValue(window)`.
- Do not change 60 s / 600 s timers, postMessage validation, `usePopupWindow`, SharePoint/tool-OAuth flows, broker-login `window.open`.
- Early-close text verbatim from the ticket: "The sign-in window closed before authentication finished. Your identity provider may require an extra step (for example a device compliance check). Open sign-in in a new tab to complete it."

## Review Focus
- `auth_url` like `javascript:` or malformed: never navigated, blank window closed.
- `/status` request throwing or non-authenticated while window open: no throw, keeps polling.
- Poll and postMessage both fire success: one handling.
- Cancel, unmount, chat clear: no watcher or interval left.
- Reopen after early close clears `sign_in_window_closed`.

---

### Task 1 [codemie-ui]: `openSignInWindow` util
**Files:** Create `src/utils/openSignInWindow.ts`, `src/utils/__tests__/openSignInWindow.test.ts`; Modify `src/utils/mcpAuthInitiate.ts` (add `INVALID_AUTH_URL_MESSAGE`, `SIGN_IN_WINDOW_CLOSED_MESSAGE` constants).
**Interfaces:** Produces `type OpenSignInResult = {status:'opened'; window: Window} | {status:'blocked'} | {status:'invalid_url'}`; `openSignInWindow(authUrl: string): OpenSignInResult`.
Test-first: yes — with a stub `window.open` returning a fake window: opened result has `opener === null` and `location.href === authUrl`; null handle gives `blocked`; `javascript:alert(1)` and `not a url` give `invalid_url`, fake `close` called, href never set.
- [ ] Write failing tests; run `npx vitest run src/utils/__tests__/openSignInWindow.test.ts` (FAIL).
- [ ] Implement: `window.open('', '_blank')` synchronously, set `opener = null`, validate via `new URL` protocol, then set href.
- [ ] Run tests, lint, typecheck (PASS).

### Task 2 [codemie-ui]: status fetcher, beacon, `watchSignInWindow`
**Files:** Create `src/utils/watchSignInWindow.ts`, `src/utils/mcpAuthStatus.ts`, tests under `src/utils/__tests__/`; Modify `src/hooks/useAuthCallbackListener.ts:157-185` (generalize and export beacon), test `src/hooks/__tests__/useAuthCallbackListener.test.tsx`.
**Interfaces:** Produces `fetchMCPAuthStatus(mcpConfigId: string): Promise<MCPAuthStatusResponse>` (`api.get('/v1/mcp-auth/status?mcp_config_id=...')` then `.json()`); `reportCallbackDiagnostics({result, phase, waitedMs, openerPresent?})` (timeout caller passes `result:'timeout'`, `phase:'awaiting_callback'`, `opener_present:false`, payload byte-identical); `watchSignInWindow({window: Window, mcpConfigId: string, onAuthenticated: () => void, onClosedEarly: () => void}): () => void` (stop).
Test-first: yes — with fake timers: authenticated status at 3 s calls `onAuthenticated` once and stops; `closed=true` triggers one immediate status check, then `onClosedEarly` if unauthenticated (with beacon `result:'error'`, `phase:'window_closed_before_callback'`, no `opener_present`) or `onAuthenticated` if authenticated; fetch rejection is swallowed; nothing fires after 600 s or after `stop()`; existing timeout-beacon test still passes unchanged.
- [ ] Write failing tests; run them (FAIL).
- [ ] Implement beacon generalization, fetcher, watcher.
- [ ] Run the two test files, lint, typecheck (PASS).

### Task 3 [codemie-ui]: `useMCPAuthPrompt` integration
**Files:** Modify `src/hooks/useMCPAuthPrompt.ts:94-192` (both `window.open` sites, L134 and L162), `src/types/entity/mcpAuth.ts` (`MCPAuthGateServer.sign_in_window_closed?: boolean`); Test `src/hooks/__tests__/useMCPAuthPrompt.test.tsx` (rework `window.open` assertions ~L193).
**Interfaces:** Consumes Task 1-2 signatures. Produces row field `sign_in_window_closed`.
Test-first: yes — blocked popup on SAML initiate and oauth2 continue sets `POPUP_BLOCKED_AUTH_MESSAGE` and recoverable status; `invalid_url` sets `INVALID_AUTH_URL_MESSAGE`; watcher `onAuthenticated` marks success once; `onClosedEarly` sets recoverable status, `SIGN_IN_WINDOW_CLOSED_MESSAGE`, `sign_in_window_closed: true`; new flow clears the flag; watcher stops (ref map by `auth_config_id`) on success, error, early close, cancel, re-authenticate and unmount.
- [ ] Rework/add failing tests (FAIL), implement, run `npx vitest run src/hooks/__tests__/useMCPAuthPrompt.test.tsx src/components` wiring test `MCPAuthPromptWiring.test.tsx`, lint, typecheck (PASS).
- [ ] Verify the listener's timers for an id are cleared once the row leaves the tracked set on early close (spec U9); add the clear with a test if not.

### Task 4 [codemie-ui]: chat store `chatGeneration`
**Files:** Modify `src/store/chatGeneration.ts:1205-1291` (`initiatePromptAuth`, `continuePromptAuth`, `markPromptAuthSuccess`, error/cancel/clear paths); Test `src/store/__tests__/chatGeneration.promptAuth.test.ts`.
**Interfaces:** Consumes Tasks 1-3; module-level `Map<string, () => void>` of watcher stops keyed by authConfigId.
Test-first: yes — `continuePromptAuth` opens the window before and outside `updatePromptRowsAtIndexes` (mapper pure, `openSignInWindow` called once); blocked on `initiatePromptAuth` and `continuePromptAuth` sets `POPUP_BLOCKED_AUTH_MESSAGE`; early close sets message and `sign_in_window_closed`; the watcher map is empty after success, error, cancel, row removal and chat clear/switch.
- [ ] Failing tests (FAIL), implement, run `npx vitest run src/store/__tests__/chatGeneration.promptAuth.test.ts src/store/__tests__/chatGeneration.test.ts`, lint, typecheck (PASS).

### Task 5 [codemie-ui]: gate-row "Open sign-in in a new tab"
**Files:** Modify `src/pages/chat/components/AssistantAuthGate/AssistantAuthGateRow.tsx` (authenticate button label, near L97-101); Test `AssistantAuthGateRow.test.tsx` in its `__tests__` dir.
Test-first: yes — row with `sign_in_window_closed: true` and recoverable status renders the button "Open sign-in in a new tab" that calls the existing `onAuthenticate`; without the flag the label is unchanged. No consumer changes.
- [ ] Failing test, implement, run the row test plus `ChatAiMcpAuthPrompt.test.tsx`, lint, typecheck.

### Task 6 [codemie-ui]: untracked-id log downgrade
**Files:** Modify `src/hooks/useAuthCallbackListener.ts:366-376` (`console.warn` to `console.debug`); Test `src/hooks/__tests__/useAuthCallbackListener.test.tsx` (update warn assertions).
Test-first: yes — callback for an untracked `auth_config_id` calls `console.debug` and never `console.warn`; bad-shape and wrong-origin messages still warn.
- [ ] Update test (FAIL), change level, run listener, `useChatAuthCallbacks.test.ts` and `WorkflowDetailsPage.authCallback.integration.test.tsx`, lint, typecheck.

### Task 7 [backend]: callback page no-opener branch and tests
**Files:** Modify `src/codemie/enterprise/mcp_auth/_callback_pages.py:177-285` (success no-opener branch: beacon, then `window.close()` unless `CALLBACK_KEEP_TAB_OPEN`, 300 ms fallback message via `_CALLBACK_FALLBACK_DELAY_MS`; keep doubled braces; opener path unchanged); Test `tests/enterprise/mcp_auth/test_oauth2_callback_bridge.py` (script-text tests live here), diagnostics test in the same file, `/status` test in `tests/enterprise/mcp_auth/test_mcp_auth_status_bridge.py`.
Test-first: yes — script text with `KEEP_TAB_OPEN` false contains `window.close()` inside the `!window.opener` success branch plus the fallback message; with true it does not close; opener branch still contains `postMessage`; diagnostics with `result="error"`, `phase="window_closed_before_callback"` logs WARNING containing the phase; discovered-flow `/status` returns `authenticated` when TMS has the token (add only if no such test exists).
- [ ] Failing tests (`poetry run pytest tests/enterprise/mcp_auth/test_oauth2_callback_bridge.py tests/enterprise/mcp_auth/test_mcp_auth_status_bridge.py` FAIL), implement, re-run (PASS), `poetry run ruff format && poetry run ruff check`.

## Negative-constraint pass
- No `noopener` feature: Task 1 uses `opener = null`.
- No `usePopupWindow`/SharePoint/tool-OAuth/broker-login change: no task touches them.
- No new endpoints/model fields/status values: Tasks 2 and 7 reuse `/status` and diagnostics as is.
- No timer or postMessage-validation change: Task 6 changes one log level only.
- Non-http(s) never navigated: Task 1.
