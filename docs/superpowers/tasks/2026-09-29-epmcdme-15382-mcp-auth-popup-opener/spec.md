# EPMCDME-15382 Spec: MCP OAuth sign-in window survives the EPAM IdP opener guard

Requirements source: `/home/taras_spashchenko/EPAM/cm/codemie/jira-mcp-auth-popup-closed-by-idp.md` (ACs 1-9 apply verbatim). Repos: codemie-ui (primary), codemie backend (small). Size L.

## Problem
`window.open(authUrl,'_blank')` leaves a cross-origin `window.opener`; the IdP guard self-closes the window when it renders a page (e.g. OPSWAT). CodeMie then waits 60 s for a callback that never comes.

## Approach
Cut the child's opener, keep the parent's handle, and move completion detection from push (postMessage) to pull (`GET /v1/mcp-auth/status`). Ruled out: `noopener` feature (returns null, breaks blocked/close detection); SSE/websocket push (new infra); changing `usePopupWindow` (hides the Window, React-bound, adds width/height features).

## UI design (codemie-ui)

**U1. `openSignInWindow(authUrl)` util** (`src/utils/`, no React dependency, usable from hooks and the Valtio store). Opens `window.open('', '_blank')` synchronously (inside the user gesture), sets `w.opener = null`, validates `authUrl`, then sets `w.location.href`. Returns a discriminated result: `opened` (with the `Window`), `blocked` (null handle), `invalid_url`.
- Scheme validation: parse with `URL`; only `http:`/`https:` allowed (security-patterns.md sink rule). On failure close the blank window and return `invalid_url`; callers show a new error message constant in `mcpAuthInitiate.ts`, never navigate.

**U2. `watchSignInWindow` util** (same area). Inputs: window handle, `mcpConfigId`, callbacks `onAuthenticated`, `onClosedEarly`. Returns `stop()`.
- Polls `w.closed` every 500 ms; polls `/status` every 3 s while open, and once immediately on close. Hard cap 600 s (matches `AUTH_CALLBACK_ACCEPTANCE_MS`), then stops silently (the listener's timeout path owns that outcome).
- `authenticated` -> `onAuthenticated`. Closed and not authenticated -> `onClosedEarly`. Non-authenticated statuses and transient errors while the window is open are "not yet"; no throw.
- Needs a new status fetcher `fetchMCPAuthStatus(mcpConfigId)` using `api.get` and the existing `MCPAuthStatusResponse` type.
- Diagnostics: `reportCallbackTimeoutDiagnostics` (`useAuthCallbackListener.ts:157-185`) is generalized and exported as a beacon taking `result`, `phase`, `waited_ms`. Existing timeout beacon output stays byte-identical. Early close sends `result:"error"`, `phase:"window_closed_before_callback"`, `opener_present` omitted.

**U3. Idempotent success across listener instances.** postMessage and poller both feed the existing success handler (`onSuccess(authConfigId)` in `useMCPAuthPrompt`, `markPromptAuthSuccess` in chat). That handler no-ops when the id is no longer tracked/authenticating, so first signal wins and the second is dropped. Only the instance that opened the window starts a watcher, so the two `useMCPAuthPrompt` instances on the assistant edit page do not double-poll each other's flows. `MCPToolkitTest`'s `isRetryingRef` guard stays the last line of defense for the single `runTest()` re-run (AC 6/7).

**U4. Watcher lifecycle.**
- Hooks (`useMCPAuthPrompt`): watcher `stop()` held in a ref map keyed by `auth_config_id`; stopped on success, error, early close, cancel, re-authenticate and unmount.
- Chat store (`chatGeneration.ts`): module-level `Map<authConfigId, stop>` beside the store; stopped on `markPromptAuthSuccess`, prompt error, cancel, row removal and chat clear/switch. Tests assert no watcher remains after each.
- `continuePromptAuth` must call `openSignInWindow` outside the `updatePromptRowsAtIndexes` mapper (the mapper becomes pure); the open happens first (gesture), then rows update from its result.

**U5. Popup-blocked handling.** All four call sites (`useMCPAuthPrompt` initiate L134 and continue L162; `chatGeneration` initiatePromptAuth L1211 and continuePromptAuth L1236) use the util. `blocked` sets `POPUP_BLOCKED_AUTH_MESSAGE` and a recoverable status on every path, including the two that ignore it today (SAML initiate, `initiatePromptAuth`). This is a deliberate behavior fix extending AC 3.

**U6. Early close.** `onClosedEarly` sets the row to `getRecoverableAuthStatus(row)`, sets `error_context` to the ticket's early-close message (constant, text verbatim from the ticket), sets a new optional row field `sign_in_window_closed: true` on `MCPAuthGateServer`, and sends the beacon. Spinner leaves within ~1 s (500 ms poll + immediate status check).

**U7. "Open sign-in in a new tab" action reaches the row without new props.** `AssistantAuthGateRow` renders that label on its existing authenticate button when `row.sign_in_window_closed` is set, wired to the existing `onAuthenticate` callback. Authenticate already runs initiate again then opens through U1 (Continue step for oauth2 unchanged), so all three consumers (`ChatAiMcpAuthPrompt`, `MCPToolkitTest`, `MCPToolsSelectionStep`) need no change. The field is cleared when a new flow starts. Completion of the reopened window is detected by U2 (AC 6). Because U1 already cuts the opener, the reopened tab is equivalent to a normal tab for the IdP.

**U8. Untracked-id log.** `useAuthCallbackListener.ts:366-376`: `console.warn` becomes `console.debug`. Bad-shape and origin warns stay. Existing assertions on that warn are updated.

**U9. Untouched:** timers (60 s hint / 600 s acceptance) and postMessage handling in the listener. On early close the row leaves the tracked set, so the listener's timers for that id are cleared by the existing effect; the plan must verify this and add the clear if it does not happen.

## Backend design (codemie)

**B1. Callback page script** (`_callback_pages.py`, `build_oauth2_callback_page_script_response`). Success with no opener: send the diagnostics beacon, then `window.close()` unless `MCP_AUTH_CALLBACK_KEEP_TAB_OPEN`, keeping the 300 ms fallback text so a blocked close still shows a message. The opener path is unchanged. JS lives in an f-string: braces stay doubled.

**B2. `/status` for discovered flows: verified, no change.** `build_discovered_auth_status_response` (`_initiate.py:504-562`) returns `authenticated` iff `evaluate_discovered_auth_status` (`codemie-enterprise .../status_evaluator.py:76-89`) can `tms.retrieve(user, discovered_auth_id)`. The callback stores the token in TMS before serving the success page, so `/status` flips as soon as the callback completes. Residual: a token existing before the flow would report `authenticated` immediately, which the UI treats as success (desired). Add a bridge test if none covers the discovered `authenticated` case.

**B3. Diagnostics: no model change.** `result:"error"` already logs WARNING (`_diagnostics.py`); `phase` fits max_length 64. Add a test that `phase=window_closed_before_callback` yields WARNING with the phase in the log.

## Acceptance criteria
ACs 1-9 of the ticket, plus:
- Watchers are stopped on every terminal path (success, error, early close, cancel, unmount, chat clear); none leak.
- Non-http(s) `auth_url` never reaches `location.href`.
- Two concurrent listeners or poll plus postMessage produce exactly one success handling.
- Tests: helper and watcher (fake window objects, fake timers; `mockReturnValue(window)` is no longer used), `useMCPAuthPrompt`, listener, `chatGeneration.promptAuth`, `AssistantAuthGateRow`, callback script no-opener branch, diagnostics phase. Existing `window.open(url,'_blank')` assertions are reworked.

## Non-goals
- No change to EPAM IAM `allowedOpeners`.
- No change to SharePoint popups or tool-OAuth flows (`toolOAuthCallback.ts`, `usePopupWindow`).
- No new backend endpoints, status values or diagnostics model fields.
- No change to the 60 s / 600 s timers or postMessage validation.
- No change to broker-login `window.open` calls in `MCPToolkitTest`/`MCPToolsSelectionStep`.
- No OPSWAT detection work.

## Risks left standing
- `w.opener = null`, `w.closed` and callback-page `window.close()` are browser-dependent; jsdom cannot model them. Manual Chrome verification against a guard page is required.
- Poll load: one `/status` request per 3 s per open flow, capped at 600 s.
