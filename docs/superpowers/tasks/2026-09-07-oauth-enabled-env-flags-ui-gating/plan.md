# OAuth-enabled env flags → UI gating — Implementation Plan

> **For agentic workers:** implemented inline via `superpowers:test-driven-development` under
> `sdlc-factory:sdlc-light` Stage 4. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface the backend env booleans `GITLAB_OAUTH_ENABLED` / `JIRA_OAUTH_ENABLED` /
`CONFLUENCE_OAUTH_ENABLED` to the UI through `GET /v1/config`, and hide the "Use OAuth 2.0 sign-in"
toggle for a provider whose flag is off.

**Architecture:** Reuse the existing env→`/v1/config` bridge — `CustomerConfig._get_runtime_config()`
already maps env booleans (`ENABLE_USER_MANAGEMENT`, `CHAT_CONTEXTUAL_NAMING_ENABLED`, …) into
runtime `Component`s that are never YAML/DB-overridable. Add three `features:*Oauth` components. On the
FE, read them through the existing boolean `FEATURE_FLAGS` + `useFeatureFlag` path and AND the result
into `SettingsForm`'s `showOAuthToggle`. Presence = enabled (disabled runtime components are omitted
from `/v1/config`).

**Tech Stack:** Python 3 / FastAPI / pydantic (BE); React 18 / TypeScript / Valtio / Vitest (FE).

**Spec:** `docs/superpowers/tasks/2026-09-07-oauth-enabled-env-flags-ui-gating/technical-analysis.md`

## Global Constraints

- **Component-id contract (byte-for-byte, both repos):** `features:gitlabOauth`, `features:jiraOauth`,
  `features:confluenceOauth`. FE `FEATURE_FLAGS` keys: `GITLAB_OAUTH`, `JIRA_OAUTH`, `CONFLUENCE_OAUTH`.
- Two repos, must land together (same coupling as the existing OAuth-folding work):
  BE `codemie` branch `EPMCDME-14587_reuse-oauth-integration-types` (MR !4139);
  FE `codemie-ui` branch `EPMCDME-14587_remove-new-types-fix-bugs` (MR !1798).
- This only hides UI; the OAuth routers already 503 on the same env vars — no router change.
- Do **not** add `SettingDeclaration`s (that is the admin-editable/DB-backed path). Runtime components only.
- Do not touch `vite.config.ts` (unrelated local change, left dirty by user consent).
- BE commit gate: run `ruff check` **and** `ruff format --check` before committing. No MR/reviewer refs
  in commit messages. FE commit subject: `EPMCDME-XXXX: Capital sentence` (single ticket + colon).

## Clarification assumptions

- Toggle is **hidden** (not disabled) when a provider flag is off — mirrors the accepted
  EPMCDME-14582 hide-when-unsupported behavior.
- `git` base type maps to the GitLab flag (GitLab is the only Git OAuth provider).

---

## Task 1 (BE): Expose OAuth env flags as runtime config components

**Files:**
- Modify: `src/codemie/configs/customer_config.py` — `CONFIG_IDS` (~line 22), `_get_runtime_config()` (~line 131)
- Test: `tests/codemie/configs/test_customer_config.py`

**Interfaces:**
- Produces: three runtime `Component`s with ids `features:gitlabOauth` / `features:jiraOauth` /
  `features:confluenceOauth`, `settings.enabled` = the respective `config.*_OAUTH_ENABLED` bool.
  Emitted by `get_runtime_components()` and filtered by enabled in `resolve_components()`.

- [ ] **Step 1: Write the failing test** — add to `TestCustomerConfig` (mirrors `test_runtime_features_*`):

```python
@patch("codemie.configs.customer_config.Path.read_text")
@patch("codemie.configs.customer_config.version")
@patch("codemie.configs.customer_config.config")
def test_runtime_features_oauth_flags(self, mock_config, mock_version, mock_read_text):
    """OAuth env flags surface as enabled-only runtime components."""
    mock_read_text.return_value = yaml.dump(self.valid_yaml)
    mock_version.side_effect = PackageNotFoundError("codemie-enterprise")
    mock_config.ENABLE_USER_MANAGEMENT = False
    mock_config.IDP_PROVIDER = "local"
    mock_config.CALLBACK_API_BASE_URL = "http://localhost:8080"
    mock_config.CHAT_CONTEXTUAL_NAMING_ENABLED = False
    mock_config.BUDGET_SOFT_LIMIT_NOTIFICATION_ENABLED = False
    mock_config.GITLAB_OAUTH_ENABLED = True
    mock_config.JIRA_OAUTH_ENABLED = False
    mock_config.CONFLUENCE_OAUTH_ENABLED = True

    config = CustomerConfig()

    # enabled provider present, disabled provider omitted (presence == enabled)
    self.assertTrue(config.is_component_enabled("features:gitlabOauth"))
    self.assertTrue(config.is_component_enabled("features:confluenceOauth"))
    self.assertFalse(config.is_component_enabled("features:jiraOauth"))

    components = config.get_enabled_components()
    ids = {c.id for c in components}
    self.assertIn("features:gitlabOauth", ids)
    self.assertIn("features:confluenceOauth", ids)
    self.assertNotIn("features:jiraOauth", ids)
```

- [ ] **Step 2: Run — expect FAIL**

Run: `poetry run pytest tests/codemie/configs/test_customer_config.py::TestCustomerConfig::test_runtime_features_oauth_flags -v`
Expected: FAIL (`is_component_enabled` returns False for `features:gitlabOauth` — id not produced).

- [ ] **Step 3: Implement** — in `customer_config.py`, extend `CONFIG_IDS`:

```python
CONFIG_IDS = {
    "enterpriseEdition": "features:enterpriseEdition",
    "userManagement": "features:userManagement",
    "idpProvider": "idpProvider",
    "mcpAuthOrigin": "mcpAuthOrigin",
    "chatContextualNaming": "features:chatContextualNaming",
    "budgetSoftLimitNotification": "features:budgetSoftLimitNotification",
    "gitlabOauth": "features:gitlabOauth",
    "jiraOauth": "features:jiraOauth",
    "confluenceOauth": "features:confluenceOauth",
}
```

and append to `_get_runtime_config()` before `return runtime_config`:

```python
        runtime_config.append(
            Component(
                id=CONFIG_IDS["gitlabOauth"],
                settings=ComponentSetting(enabled=config.GITLAB_OAUTH_ENABLED),
            )
        )
        runtime_config.append(
            Component(
                id=CONFIG_IDS["jiraOauth"],
                settings=ComponentSetting(enabled=config.JIRA_OAUTH_ENABLED),
            )
        )
        runtime_config.append(
            Component(
                id=CONFIG_IDS["confluenceOauth"],
                settings=ComponentSetting(enabled=config.CONFLUENCE_OAUTH_ENABLED),
            )
        )
```

- [ ] **Step 4: Run new test — expect PASS**

Run: `poetry run pytest tests/codemie/configs/test_customer_config.py::TestCustomerConfig::test_runtime_features_oauth_flags -v`
Expected: PASS.

- [ ] **Step 5: Fix existing runtime tests broken by the new config reads**

The runtime tests mock `config` as a `MagicMock`; the new `config.*_OAUTH_ENABLED` reads now return
truthy mocks that break `ComponentSetting(enabled=…)` and/or the length assertions. In **every** test
in this file that patches `codemie.configs.customer_config.config` and reaches `_get_runtime_config()`
(`test_get_enabled_components`, `test_runtime_features_enterprise_installed`,
`test_runtime_features_enterprise_not_installed`, `test_is_feature_enabled_runtime_features`,
`test_runtime_features_override_yaml`, `test_is_component_enabled_runtime_features`,
`test_disabled_runtime_features_excluded_like_yaml`, and the idpProvider/mcpAuthOrigin tests), set:

```python
    mock_config.GITLAB_OAUTH_ENABLED = False
    mock_config.JIRA_OAUTH_ENABLED = False
    mock_config.CONFLUENCE_OAUTH_ENABLED = False
```

With all three False the disabled components are omitted, so existing `len(...)` assertions stay
correct.

- [ ] **Step 6: Run the full file — expect PASS**

Run: `poetry run pytest tests/codemie/configs/test_customer_config.py -v`
Expected: all PASS (new test + previously-green tests).

- [ ] **Step 7: Lint + format check**

Run: `poetry run ruff check src/codemie/configs/customer_config.py tests/codemie/configs/test_customer_config.py`
Run: `poetry run ruff format --check src/codemie/configs/customer_config.py tests/codemie/configs/test_customer_config.py`
Expected: both clean.

- [ ] **Step 8: Commit**

```bash
git add src/codemie/configs/customer_config.py tests/codemie/configs/test_customer_config.py
git commit -m "EPMCDME-14587: Expose OAuth enabled flags via runtime config"
```
(Test-first: yes — `test_runtime_features_oauth_flags` fails before the `CONFIG_IDS`/`_get_runtime_config` change.)

---

## Task 2 (FE): Add OAuth feature-flag constants + variant→flag map + helpers

**Files:**
- Modify: `src/constants/featureFlags.ts` — add three flags
- Modify: `src/constants/integration.ts` — add `OAUTH_VARIANT_FEATURE_FLAG` map
- Modify: `src/utils/featureFlags.ts` — add `isGitlabOauthEnabled` / `isJiraOauthEnabled` / `isConfluenceOauthEnabled`
- Modify: `src/hooks/useFeatureFlags.ts` — add `useGitlabOauthEnabled` / `useJiraOauthEnabled` / `useConfluenceOauthEnabled`
- Test: `src/constants/__tests__/integration.test.ts` (map), `src/utils/__tests__/featureFlags.test.ts` (if present; else add helper test alongside)

**Interfaces:**
- Produces: `FEATURE_FLAGS.GITLAB_OAUTH = 'features:gitlabOauth'` (+ JIRA_OAUTH, CONFLUENCE_OAUTH);
  `OAUTH_VARIANT_FEATURE_FLAG: Record<string,string>` mapping `'gitlaboauth'|'jiraoauth'|'confluenceoauth'`
  → the matching `features:*Oauth` id.

- [ ] **Step 1: Write the failing test** — in `src/constants/__tests__/integration.test.ts`:

```ts
import { OAUTH_VARIANT_FEATURE_FLAG } from '@/constants/integration'
import { FEATURE_FLAGS } from '@/constants/featureFlags'

describe('OAUTH_VARIANT_FEATURE_FLAG', () => {
  it('maps each OAuth variant to its provider feature flag', () => {
    expect(OAUTH_VARIANT_FEATURE_FLAG.gitlaboauth).toBe(FEATURE_FLAGS.GITLAB_OAUTH)
    expect(OAUTH_VARIANT_FEATURE_FLAG.jiraoauth).toBe(FEATURE_FLAGS.JIRA_OAUTH)
    expect(OAUTH_VARIANT_FEATURE_FLAG.confluenceoauth).toBe(FEATURE_FLAGS.CONFLUENCE_OAUTH)
  })
})
```

- [ ] **Step 2: Run — expect FAIL**

Run: `npm run test:unit -- src/constants/__tests__/integration.test.ts`
Expected: FAIL (`OAUTH_VARIANT_FEATURE_FLAG` undefined).

- [ ] **Step 3: Implement**

In `src/constants/featureFlags.ts`, add inside `FEATURE_FLAGS`:

```ts
  GITLAB_OAUTH: 'features:gitlabOauth',
  JIRA_OAUTH: 'features:jiraOauth',
  CONFLUENCE_OAUTH: 'features:confluenceOauth',
```

In `src/constants/integration.ts`, add (near `OAUTH_VARIANT_BY_BASE_TYPE`):

```ts
import { FEATURE_FLAGS } from '@/constants/featureFlags'

// OAuth variant credential type -> the runtime feature flag that gates its availability.
export const OAUTH_VARIANT_FEATURE_FLAG: Record<string, string> = {
  [GITLAB_OAUTH_CREDENTIAL_TYPE]: FEATURE_FLAGS.GITLAB_OAUTH,
  [JIRA_OAUTH_CREDENTIAL_TYPE]: FEATURE_FLAGS.JIRA_OAUTH,
  [CONFLUENCE_OAUTH_CREDENTIAL_TYPE]: FEATURE_FLAGS.CONFLUENCE_OAUTH,
}
```

In `src/utils/featureFlags.ts` add:

```ts
export const isGitlabOauthEnabled = (): boolean => isFeatureEnabled(FEATURE_FLAGS.GITLAB_OAUTH)
export const isJiraOauthEnabled = (): boolean => isFeatureEnabled(FEATURE_FLAGS.JIRA_OAUTH)
export const isConfluenceOauthEnabled = (): boolean =>
  isFeatureEnabled(FEATURE_FLAGS.CONFLUENCE_OAUTH)
```

In `src/hooks/useFeatureFlags.ts` add:

```ts
export const useGitlabOauthEnabled = (): FeatureFlagResult =>
  useFeatureFlag(FEATURE_FLAGS.GITLAB_OAUTH)
export const useJiraOauthEnabled = (): FeatureFlagResult =>
  useFeatureFlag(FEATURE_FLAGS.JIRA_OAUTH)
export const useConfluenceOauthEnabled = (): FeatureFlagResult =>
  useFeatureFlag(FEATURE_FLAGS.CONFLUENCE_OAUTH)
```

- [ ] **Step 4: Run — expect PASS**

Run: `npm run test:unit -- src/constants/__tests__/integration.test.ts`
Expected: PASS.

- [ ] **Step 5: Typecheck**

Run: `npm run typecheck`
Expected: clean (watch for import/order — the PostToolUse hook auto-fixes; do not re-run lint).

- [ ] **Step 6: Commit**

```bash
git add src/constants/featureFlags.ts src/constants/integration.ts src/utils/featureFlags.ts src/hooks/useFeatureFlags.ts src/constants/__tests__/integration.test.ts
git commit -m "EPMCDME-14587: Add OAuth provider feature flags"
```
(Test-first: yes — the `OAUTH_VARIANT_FEATURE_FLAG` map test fails before the constants exist.)

---

## Task 3 (FE): Gate the OAuth toggle on the provider flag in SettingsForm

**Files:**
- Modify: `src/pages/integrations/components/SettingsForm/SettingsForm.tsx` (~line 305-312, `showOAuthToggle`)
- Test: `src/pages/integrations/components/SettingsForm/__tests__/SettingsForm.oauth.test.tsx`
- Test (mock fix): `src/pages/integrations/components/NewIntegrationPopup/__tests__/NewIntegrationPopup.test.tsx`

**Interfaces:**
- Consumes: `OAUTH_VARIANT_FEATURE_FLAG` (Task 2), `useFeatureFlag` (existing).
- Produces: `showOAuthToggle` additionally requires the current provider's flag to be enabled.

- [ ] **Step 1: Write the failing test** — add to `SettingsForm.oauth.test.tsx`. First extend the
  `@/store/appInfo` mock so `useFeatureFlag` can read config (add to the existing `appInfoStore` mock):

```ts
    isConfigFetched: true,
    configs: [
      { id: 'features:gitlabOauth', settings: { enabled: true } },
      { id: 'features:jiraOauth', settings: { enabled: true } },
      { id: 'features:confluenceOauth', settings: { enabled: true } },
    ],
```

Then add a hidden-when-disabled test:

```ts
it('hides the OAuth toggle when the provider feature flag is disabled', () => {
  // jira flag absent from configs → provider unsupported → toggle hidden
  vi.mocked(appInfoStore).configs = [
    { id: 'features:gitlabOauth', settings: { enabled: true } },
  ] as never
  vi.mocked(appInfoStore).isConfigFetched = true

  render(
    <SettingsForm
      credentialType="jira"
      settingType="user"
      disableType
      editing={false}
      onSubmit={vi.fn()}
      onClose={vi.fn()}
      submitText="Save"
    />
  )

  expect(screen.queryByRole('switch', { name: /Use OAuth 2.0 sign-in/i })).toBeNull()
})
```

(If direct `vi.mocked(appInfoStore).configs =` reassignment does not stick with the current mock
shape, instead parametrize the `configs` array via a mutable module-level `let` referenced in the
`vi.mock('@/store/appInfo')` factory, reset in `beforeEach` — matching how other suites toggle config.)

- [ ] **Step 2: Run — expect FAIL**

Run: `npm run test:unit -- SettingsForm.oauth.test.tsx`
Expected: the new "hides…" test FAILS (toggle still shown — flag not yet gated). Existing
"enabled when locked" tests may also fail until the appInfoStore mock carries the three enabled configs.

- [ ] **Step 3: Implement** — in `SettingsForm.tsx`, import and gate:

```ts
import { OAUTH_VARIANT_FEATURE_FLAG } from '@/constants/integration'
import { useFeatureFlag } from '@/hooks/useFeatureFlags'
```

```ts
  const [gitlabOAuthEnabled] = useFeatureFlag(FEATURE_FLAGS.GITLAB_OAUTH)
  const [jiraOAuthEnabled] = useFeatureFlag(FEATURE_FLAGS.JIRA_OAUTH)
  const [confluenceOAuthEnabled] = useFeatureFlag(FEATURE_FLAGS.CONFLUENCE_OAUTH)
  const oauthProviderEnabled: Record<string, boolean> = {
    [GITLAB_OAUTH_CREDENTIAL_TYPE]: gitlabOAuthEnabled,
    [JIRA_OAUTH_CREDENTIAL_TYPE]: jiraOAuthEnabled,
    [CONFLUENCE_OAUTH_CREDENTIAL_TYPE]: confluenceOAuthEnabled,
  }
  const showOAuthToggle =
    !!oauthVariantType &&
    CREDENTIAL_VALUES_MAPPING[oauthVariantType] !== undefined &&
    !!oauthProviderEnabled[oauthVariantType]
```

Add the needed imports (`FEATURE_FLAGS` from `@/constants/featureFlags`; the `*_OAUTH_CREDENTIAL_TYPE`
constants from `@/constants/integration` if not already imported). The three `useFeatureFlag` calls are
unconditional (hook-rules safe).

- [ ] **Step 4: Run — expect PASS**

Run: `npm run test:unit -- SettingsForm.oauth.test.tsx`
Expected: all PASS (hidden-when-disabled + enabled-when-locked across git/jira/confluence + edit tests).

- [ ] **Step 5: Fix the NewIntegrationPopup test mock**

`NewIntegrationPopup.test.tsx` renders through `SettingsForm`, so its `@/store/appInfo` mock also needs
`isConfigFetched: true` and `configs` containing the three enabled `features:*Oauth` entries; otherwise
its "toggle enabled" assertion breaks under the new gate.

Run: `npm run test:unit -- NewIntegrationPopup.test.tsx`
Expected: PASS.

- [ ] **Step 6: Typecheck + full integration suite for the two dirs**

Run: `npm run typecheck`
Run: `npm run test:unit -- SettingsForm NewIntegrationPopup integration.test.ts`
Expected: clean / green.

- [ ] **Step 7: Commit**

```bash
git add src/pages/integrations/components/SettingsForm/SettingsForm.tsx \
  src/pages/integrations/components/SettingsForm/__tests__/SettingsForm.oauth.test.tsx \
  src/pages/integrations/components/NewIntegrationPopup/__tests__/NewIntegrationPopup.test.tsx
git commit -m "EPMCDME-14587: Hide OAuth toggle when provider flag disabled"
```
(Test-first: yes — the hidden-when-disabled test fails before the `showOAuthToggle` flag gate is added.)

---

## Self-review

- **Coverage:** BE exposure (Task 1), FE constants/map/helpers (Task 2), FE gate (Task 3) — the full
  data flow in technical-analysis.md is covered.
- **Placeholders:** none — every step carries real code.
- **Type consistency:** `features:gitlabOauth`/`features:jiraOauth`/`features:confluenceOauth` used
  identically in BE `CONFIG_IDS`, FE `FEATURE_FLAGS`, `OAUTH_VARIANT_FEATURE_FLAG`, and the SettingsForm
  gate. Variant keys `gitlaboauth`/`jiraoauth`/`confluenceoauth` match `OAUTH_VARIANT_BY_BASE_TYPE`.
