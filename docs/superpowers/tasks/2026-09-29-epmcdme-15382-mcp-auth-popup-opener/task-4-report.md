DONE_WITH_CONCERNS
commit: fae6133f2
paths:
- src/store/chatGeneration.ts
- src/store/__tests__/chatGeneration.promptAuth.test.ts
- src/store/__tests__/chatGeneration.test.ts
test_command: npx vitest run src/store/__tests__/chatGeneration.promptAuth.test.ts src/store/__tests__/chatGeneration.test.ts (48 pass); npm run lint, npm run typecheck clean; pre-commit full suite + sonar passed

files:
- chatGeneration.ts: openPromptSignIn (open outside mapper, watch), watcher map, stopAllPromptAuthWatchers action.
- chatGeneration.promptAuth.test.ts: mocks util modules; new sign-in window + watcher lifecycle tests.
- chatGeneration.test.ts: adapted 2 window.open tests to fake window, pending_initiate null.

renames: none
reused helpers: openSignInWindow, watchSignInWindow, message constants, getRecoverableAuthStatus
standards applied: TDD (RED seen: stopAllPromptAuthWatchers missing), no javascript: literals, fake windows, [mcp-auth] log not added (store had none).

Ruling: chatGeneration.test.ts is outside the named files but its old window.open assertions broke by design; edited minimally.
Ruling: SAML initiate now sets pending_initiate null (was undefined), matching hook.
Ruling: watcher stops on success/error/early close/cancel/new flow; rows dropped via clearAuthPrompts, error path, _removeOptimisticTurn stop watchers.
Ruling: chat clear/switch: store exposes stopAllPromptAuthWatchers(); nothing in my file scope calls it on switch. CONCERN: caller (chat switch/clear code, e.g. chats.ts or useChatAuthCallbacks) must invoke it.
Ruling: useChatAuthCallbacks live-ids exclusion of sign_in_window_closed rows not done (out of scope).
