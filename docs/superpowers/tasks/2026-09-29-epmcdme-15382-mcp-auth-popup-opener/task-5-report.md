DONE
commit: 5a11bb453
paths:
- /home/taras_spashchenko/EPAM/cm/codemie-ui/src/pages/chat/components/AssistantAuthGate/AssistantAuthGateRow.tsx
- /home/taras_spashchenko/EPAM/cm/codemie-ui/src/pages/chat/components/AssistantAuthGate/__tests__/AssistantAuthGateRow.test.tsx
- /home/taras_spashchenko/EPAM/cm/codemie-ui/src/hooks/useAuthCallbackListener.ts
- /home/taras_spashchenko/EPAM/cm/codemie-ui/src/hooks/__tests__/useAuthCallbackListener.test.tsx
test_command: npx vitest run src/pages/chat/components/AssistantAuthGate/__tests__/AssistantAuthGateRow.test.tsx src/hooks/__tests__/useAuthCallbackListener.test.tsx src/pages/chat/components/ChatHistory/ChatAiMessage/__tests__/ChatAiMcpAuthPrompt.test.tsx src/pages/chat/hooks/__tests__/useChatAuthCallbacks.test.ts src/pages/workflows/__tests__/WorkflowDetailsPage.authCallback.integration.test.tsx

Results: RED seen first (3 failures: row label tests expected new label, listener test expected console.debug with 0 calls). GREEN: 5 files, 60 tests passed. npm run lint EXIT=0, npm run typecheck EXIT=0. Pre-commit hook (full suite 668 files / 6911 tests, sonar quality gate PASSED) passed; commit created.

Files:
- AssistantAuthGateRow.tsx: closed sign-in window shows "Open sign-in in a new tab" label.
- AssistantAuthGateRow.test.tsx: label per status, click calls onAuthenticate, default unchanged.
- useAuthCallbackListener.ts: untracked auth_config_id log lowered from warn to debug.
- useAuthCallbackListener.test.tsx: untracked asserts debug not warn; wrong-origin warn guard added.

Renames: none.
Reused helpers: existing onAuthenticate handler and Button; existing createRow test factory; existing dispatchMessage test helper.
Standards applied: test-first RED then GREEN; constant for repeated label (DRY); no javascript: literals; timers and postMessage validation untouched; only four named files staged.

Ruling: Bad-shape messages never warned in current source (silent return before the origin check), so the test pins wrong-origin warn once and no warn for a malformed payload instead of "bad-shape still warns"; source trusted over brief.
Ruling: The new-tab label applies to both authentication_required and session_expired rows when sign_in_window_closed is true; pending_initiate (Continue/Cancel) rendering and disabled={!row.initiate_url} left unchanged.
Ruling: The untracked test asserts warn was not called with the untracked message rather than never called, because unrelated acceptance-window warnings legitimately fire in the same test.
Ruling: Extra untracked file /home/taras_spashchenko/EPAM/cm/codemie-ui/HOOK-TIMING-REPORT.md (hook timing breakdown, requested by the user) is deliberately not committed; total hook time was 26m44s.
