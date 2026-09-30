DONE
commit: c28642c2f
paths:
- src/utils/watchSignInWindow.ts
- src/utils/mcpAuthStatus.ts
- src/utils/__tests__/watchSignInWindow.test.ts
- src/utils/__tests__/mcpAuthStatus.test.ts
- src/hooks/useAuthCallbackListener.ts
- src/hooks/__tests__/useAuthCallbackListener.test.tsx
test_command: npx vitest run src/utils/__tests__/watchSignInWindow.test.ts src/utils/__tests__/mcpAuthStatus.test.ts src/hooks/__tests__/useAuthCallbackListener.test.tsx  (EXIT=0, 39 passed; RED seen first: missing modules/export). npm run lint and npm run typecheck clean. Pre-commit hook (full unit+integration suite) passed.

Files:
- watchSignInWindow.ts: 500 ms closed check, 3 s status poll, 600 s cap, returns stop.
- mcpAuthStatus.ts: fetchMCPAuthStatus via api.get v1/mcp-auth/status, quiet errors.
- useAuthCallbackListener.ts: beacon generalized into exported reportCallbackDiagnostics.
- tests: fake-timer watcher cases, fetcher URL, beacon omit/cap.

Renames: reportCallbackTimeoutDiagnostics(authConfigId, waitedMs) -> reportCallbackDiagnostics({result, phase, waitedMs, authConfigId?, openerPresent?}); sole call site in useAuthCallbackListener.ts updated; timeout payload byte-identical (existing test unchanged, passes).
Reused: MCPAuthStatusResponse type, api.get (skipErrorHandling, no leading slash like initiate_url use), DIAGNOSTICS_MAX_WAITED_MS cap.
Standards: license headers, @/ alias, [mcp-auth] prefix, fake window objects, timers/postMessage untouched.
Added beyond spec: optional authConfigId param (timeout beacon needs it); stop-guard drops in-flight results after stop().

Ruling: backend OAuth2CallbackDiagnostics has auth_config_id optional (str|None), opener_present optional, result in {success,error,timeout}, phase free string max 64. watchSignInWindow only knows mcpConfigId, so it sends no auth_config_id and no opener_present (result 'error', phase 'window_closed_before_callback', waited_ms = elapsed since watch start).
