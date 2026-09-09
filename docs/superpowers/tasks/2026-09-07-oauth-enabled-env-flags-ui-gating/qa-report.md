# QA Report — OAuth env flags → UI gating

**Result: PASSED.** Two-repo change; feature-verification skipped (`ui` not set — no opt-in, and the
gate is a boolean UI capability covered by unit tests).

## Backend (codemie)

| Gate | Command | Result |
|---|---|---|
| Lint | `ruff check` (changed files) | ✅ All checks passed |
| Format | `ruff format --check` (changed files) | ✅ 2 files already formatted |
| Tests | `pytest` customer_config unit + service + router | ✅ 64 passed |

## Frontend (codemie-ui)

| Gate | Command | Result |
|---|---|---|
| Lint | `eslint` (8 changed files) | ✅ exit 0 (only the pre-existing React-version warning) |
| Typecheck | `tsc --noEmit` | ✅ exit 0 |
| Unit — flags | `vitest run integration.test.ts` + `utils/featureFlags.test.ts` | ✅ 7 + 13 passed |
| Unit + integration — integrations area | `vitest run …SettingsForm.oauth …NewIntegrationPopup src/pages/integrations` | ✅ 17 files / 120 passed |

## Notes

- Secrets: no secret/token handling introduced; the husky pre-commit gitleaks scan reported no leaks
  on each FE commit.
- `vite.config.ts` remains an unrelated local change (excluded from all commits by user consent).
- The two repos must land together (shared `features:*Oauth` id contract): FE MR !1798, BE MR !4139.
