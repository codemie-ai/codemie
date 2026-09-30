DONE
commit: d33a49211
paths:
- src/hooks/useMCPAuthPrompt.ts
- src/types/entity/mcpAuth.ts
- src/hooks/__tests__/useMCPAuthPrompt.test.tsx
test_command: npx vitest run src/hooks/__tests__/useMCPAuthPrompt.test.tsx (26 pass); plus MCPAuthPromptWiring + useAuthCallbackListener suites (60 pass); npm run lint; npm run typecheck (clean)

Files:
- useMCPAuthPrompt.ts: openSignIn helper, watcher ref map, early-close handling, idempotent success.
- mcpAuth.ts: adds MCPAuthGateServer.sign_in_window_closed?: boolean.
- useMCPAuthPrompt.test.tsx: mocks open/watch utils; 14 new sign-in window tests.

Renames: none.
Reused helpers: openSignInWindow, watchSignInWindow, POPUP_BLOCKED/INVALID_AUTH_URL/SIGN_IN_WINDOW_CLOSED messages, getRecoverableAuthStatus, getLiveAuthConfigIds.
Standards applied: DRY (one openSignIn for both sites), fake window objects (no mockReturnValue(window)), [mcp-auth] log kept, timers/postMessage untouched.

Ruling: U9 - listener unchanged. Early close untracks the row so the existing effect clears hint+acceptance timers for non-retained ids. For a retained id (hint already expired) the acceptance timer would survive; fixed in the hook by excluding sign_in_window_closed rows from liveAuthConfigIds, which triggers the listener's existing live-drop clear (already covered by useAuthCallbackListener tests ~L538-542). Hook test asserts live ids drop after early close.
Ruling: Poll + postMessage double success: onSuccess no-ops when the row is missing or already authenticated, and stops the watcher; onAllAuthenticated fires once.
Ruling: invalid_url on oauth2 continue clears pending_initiate (retry would fail identically); blocked keeps it.
Ruling: handleAuthRequiredError also stops all watchers (rows replaced).
Ruling: cancel stops the row's watcher (per task) even if the row is still authenticating.
Ruling: first commit attempt was rejected by the sonar pre-commit gate (S1523 hotspot on a javascript: literal in my test); test now uses a non-http ftp:// URL since openSignInWindow is mocked. Second commit passed the hook. Other hotspots flagged (Dockerfiles) were pre-existing and did not block the second run.
