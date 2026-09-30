DONE
commit: 7a2fb095d
paths:
- /home/taras_spashchenko/EPAM/cm/codemie-ui/src/utils/openSignInWindow.ts
- /home/taras_spashchenko/EPAM/cm/codemie-ui/src/utils/__tests__/openSignInWindow.test.ts
- /home/taras_spashchenko/EPAM/cm/codemie-ui/src/utils/mcpAuthInitiate.ts
test_command: npx vitest run src/utils/__tests__/openSignInWindow.test.ts

Result: 4/4 tests passed; npm run lint and npm run typecheck clean; husky pre-commit (lint-staged, license, secrets, full vitest + sonar-local) passed.

files:
- openSignInWindow.ts: opens blank window, cuts opener, validates http(s), navigates
- openSignInWindow.test.ts: covers opened, blocked, invalid_url (closes window, no navigation)
- mcpAuthInitiate.ts: adds INVALID_AUTH_URL_MESSAGE, SIGN_IN_WINDOW_CLOSED_MESSAGE constants

renames: none
reused helpers: none (sits beside existing POPUP_BLOCKED_AUTH_MESSAGE constant)
standards: license header, discriminated union result, no magic values (ALLOWED_PROTOCOLS), small single-purpose functions, comment explains why
