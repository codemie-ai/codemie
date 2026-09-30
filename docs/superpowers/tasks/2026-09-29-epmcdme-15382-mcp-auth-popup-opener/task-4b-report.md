DONE
commit: 9aac14a11e42fdda44a7470948ad6bab73d00db3
paths: /home/taras_spashchenko/EPAM/cm/codemie-ui (branch EPMCDME-15382-mcp-auth-popup-opener)
test_command: npx vitest run src/pages/chat/hooks src/pages/chat/__tests__ (log: test.local.log); pre-commit hook full suite: unit 620 files/6359 pass, integration 48 files/547 pass, sonar scan OK

files:
- src/pages/chat/hooks/useChatAuthCallbacks.ts: stop watchers on contextKey change/unmount; exclude closed rows from live ids
- src/pages/chat/hooks/__tests__/useChatAuthCallbacks.test.ts: tests for closed-row exclusion, switch, same-chat update, unmount
- src/pages/chat/__tests__/ChatPage.test.tsx: store mock gains stopAllPromptAuthWatchers
- src/pages/chat/__tests__/ChatPage.integration.test.tsx: store mock gains stopAllPromptAuthWatchers

renames: none
reused helpers: getLiveAuthConfigIds, chatGenerationStore.stopAllPromptAuthWatchers
standards applied: TDD (3 new tests seen RED, then GREEN); lint + typecheck clean; @/ alias; no timer/postMessage changes; why-comment only

Ruling: (a) useEffect cleanup keyed on contextKey (chat id) in useChatAuthCallbacks; runs on chat switch (new/cleared chat gets new id) and unmount; no chat-store change needed.
Ruling: stopAllPromptAuthWatchers is global (all chats), matching the requested behavior; same-chat history updates do not stop watchers (tested).
Ruling: first commit attempt failed pre-commit because ChatPage tests mock chatGenerationStore without the new method; mocks updated (2 extra test files).
