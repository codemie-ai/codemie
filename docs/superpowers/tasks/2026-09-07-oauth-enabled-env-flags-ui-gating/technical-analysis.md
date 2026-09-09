# Technical Analysis — Expose OAuth-enabled env flags to the UI and gate the OAuth toggle

**Task:** Surface the backend env booleans `GITLAB_OAUTH_ENABLED` / `JIRA_OAUTH_ENABLED` /
`CONFLUENCE_OAUTH_ENABLED` to the frontend via the customer-config (`GET /v1/config`) channel, and
hide the "Use OAuth 2.0 sign-in" toggle for a provider whose flag is off.

**Feature area:** oauth, customer-config, feature-flags, integrations.

**Repos / branches (both ride existing MRs):**
- Backend `codemie` — branch `EPMCDME-14587_reuse-oauth-integration-types` (MR !4139).
- Frontend `codemie-ui` — branch `EPMCDME-14587_remove-new-types-fix-bugs` (MR !1798).

> Research method: conducted inline with tool-verified file reads (not a fabricated summary). Every
> path and line below was opened and confirmed during this session.

## Codebase Findings

### The env-var → `/v1/config` bridge already exists (this is the whole task's backbone)

`CustomerConfig._get_runtime_config()` (`src/codemie/configs/customer_config.py:131`) maps `config.py`
env booleans into customer-config `Component`s. It already does this for three flags:

```python
Component(id=CONFIG_IDS["userManagement"],              settings=ComponentSetting(enabled=config.ENABLE_USER_MANAGEMENT))
Component(id=CONFIG_IDS["chatContextualNaming"],        settings=ComponentSetting(enabled=config.CHAT_CONTEXTUAL_NAMING_ENABLED))
Component(id=CONFIG_IDS["budgetSoftLimitNotification"], settings=ComponentSetting(enabled=config.BUDGET_SOFT_LIMIT_NOTIFICATION_ENABLED))
```

- `CONFIG_IDS` is the id map at `customer_config.py:22`.
- `get_runtime_components()` (line 190) returns these and they are, per the code comment,
  **"never overridable from YAML or the database"** — so the env var stays the single source of
  truth. This is the correct path (NOT a `SettingDeclaration`, which is the admin-editable/DB-backed
  path in `customer_config_declarations.py` and would wrongly make OAuth admin-toggleable).

### Serialization / delivery (no changes needed downstream of `_get_runtime_config`)

- `resolve_components()` (`src/codemie/service/customer_config_service.py:147`) appends
  `enabled_runtime = [c for c in customer_config.get_runtime_components() if c.settings.enabled]`.
  **Only enabled components are returned** — a disabled flag is simply *absent* from `/v1/config`
  (identical to how `chatContextualNaming` behaves when off).
- Router `GET /v1/config` (`src/codemie/rest_api/routers/customer_config.py:46`) returns
  `jsonable_encoder(await resolve_components())` as `List[Component]`,
  `response_model_exclude_none=True`.
- The env flags themselves: `GITLAB_OAUTH_ENABLED` / `JIRA_OAUTH_ENABLED` / `CONFLUENCE_OAUTH_ENABLED`
  are `bool = False` in `src/codemie/configs/config.py:441/456/461`.

### Frontend consumption path (verified)

- FE fetches `GET v1/config` in `appInfoStore.fetchCustomerConfig()` → `appInfoStore.configs`
  (`src/store/appInfo.ts:177`).
- **Boolean** flags are read via `FEATURE_FLAGS` (`src/constants/featureFlags.ts`) — the `features:*`
  namespace — through:
  - non-reactive `isFeatureEnabled(id)` — `src/utils/featureFlags.ts:44`
    (`configs.find(c => c.id === id)?.settings?.enabled ?? false`).
  - reactive hook `useFeatureFlag(id): [isEnabled, isLoaded]` — `src/hooks/useFeatureFlags.ts:61`
    (valtio `useSnapshot`, auto re-renders when config arrives).
- **String** runtime values use a *different* registry, `CONFIG_KEYS` (`src/constants/configKeys.ts`),
  read via `appInfoStore.getIdpProvider()` etc. OAuth flags are booleans → they belong in
  `FEATURE_FLAGS`, not `CONFIG_KEYS`.
- Presence = enabled: because `/v1/config` omits disabled runtime components, `find(...)` returns
  `undefined` for a disabled provider and `?? false` yields "off". No FE special-casing needed.

### The gate point in the UI

- The OAuth toggle is `<Switch id="useOAuth">` in
  `src/pages/integrations/components/SettingsForm/SettingsForm.tsx` (~line 597), currently
  `disabled={editing}` after the EPMCDME-14582 fix. Base credential type → provider mapping:
  `git → gitlab`, `jira → jira`, `confluence → confluence`
  (`OAUTH_VARIANT_BY_BASE_TYPE` in `src/constants/integration.ts`).
- Gating means: for the current base type, if its provider flag is off, do **not** render the toggle
  (mirrors the existing "hide when unsupported" behavior accepted in EPMCDME-14582).

### Data flow (end to end)

```
config.py env bool
  → _get_runtime_config() Component(id="features:<provider>Oauth", enabled=<bool>)
  → resolve_components()               [drops it entirely when disabled]
  → GET /v1/config
  → appInfoStore.configs               (fetchCustomerConfig)
  → useFeatureFlag('features:<provider>Oauth') → settings.enabled ?? false
  → render/hide the OAuth <Switch> in SettingsForm.tsx
```

## Component-id contract (must match byte-for-byte on both sides)

Follow the `features:` convention used by every boolean flag:

| Env flag | Component id | FE `FEATURE_FLAGS` key |
|---|---|---|
| `GITLAB_OAUTH_ENABLED` | `features:gitlabOauth` | `GITLAB_OAUTH` |
| `JIRA_OAUTH_ENABLED` | `features:jiraOauth` | `JIRA_OAUTH` |
| `CONFLUENCE_OAUTH_ENABLED` | `features:confluenceOauth` | `CONFLUENCE_OAUTH` |

## Risk Indicators

- **auth/oauth surface** — user-facing capability gate; getting the id string wrong on one side
  silently hides or shows the toggle. Mitigated by shared constants + tests on both sides.
- **Two-repo contract** — the `features:*Oauth` id spans BE and FE and must land together (same
  coupling as the existing OAuth-folding work on these branches).
- **Complements, does not replace, the backend 503** — OAuth routers already gate on the same env
  vars (`oauth_security.py`, `jira_oauth.py:41`, `gitlab_oauth.py:50`, `confluence_oauth.py:40`).
  This change only hides the UI so users don't reach a 503. No behavior change to the routers.
- **No migration, no DB, no new endpoint, no `SettingDeclaration`.** Blast radius is one BE function
  + one id map, and one FE constants file + one component gate.
- Scope is narrow (well under the 5-file/5-layer "broad scope" threshold).

## Files in scope

**Backend (`codemie`):**
- `src/codemie/configs/customer_config.py` — `CONFIG_IDS` (add 3), `_get_runtime_config()` (append 3).

**Frontend (`codemie-ui`):**
- `src/constants/featureFlags.ts` — add 3 `features:*Oauth` flags.
- `src/utils/featureFlags.ts` + `src/hooks/useFeatureFlags.ts` — optional convenience helpers
  (`isGitlabOauthEnabled` / `useGitlabOauthEnabled`, etc.) mirroring `isMcpEnabled` / `useMcpEnabled`.
- `src/pages/integrations/components/SettingsForm/SettingsForm.tsx` — gate the OAuth `<Switch>` on the
  current base type's provider flag.
