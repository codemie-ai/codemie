# Remove the CodeMie Callback Base URL field from OAuth integrations

**Date:** 2026-09-11
**Status:** Design — pending review
**Applies to:** `codemie` (backend), `codemie-ui` (frontend)
**Integrations affected:** GitLab OAuth, Jira (Atlassian 3LO) OAuth, Confluence (Atlassian 3LO) OAuth

## Problem

When a user creates or edits a GitLab / Jira / Confluence OAuth integration, the form asks
them to fill in a **CodeMie Callback Base URL** (`callback_base_url`, placeholder
`https://your-codemie-host`). The value is the CodeMie deployment's own base URL — the host the
OAuth provider redirects back to after consent.

Requiring the user to type CodeMie's own address is redundant and error-prone: the deployment
already knows its own base URL through the `CALLBACK_API_BASE_URL` configuration value. A wrong or
mistyped entry produces a `redirect_uri` that does not match the one registered on the provider,
which fails the OAuth flow for reasons that are opaque to the user.

## Goal

- Remove the **CodeMie Callback Base URL** field from the integration form (all three OAuth
  providers).
- On the backend, always derive the OAuth `redirect_uri` from the deployment's configured
  `CALLBACK_API_BASE_URL`, without accepting or storing a per-integration value.

Non-goals: no change to where `CALLBACK_API_BASE_URL` itself is configured; no request-header /
`Host`-based derivation of the callback host; no change to the OAuth flow, token storage, or the
provider-side app registrations.

## Current behavior

The redirect URI is already resolvable without user input. `ensure_callback_base_url_allowed()`
(`codemie/src/codemie/service/oauth/constants.py`) falls back to `config.CALLBACK_API_BASE_URL`
whenever the supplied `callback_base_url` is empty:

```python
def ensure_callback_base_url_allowed(url: str | None) -> str:
    if not url or not url.strip():
        return normalize_callback_base_url(config.CALLBACK_API_BASE_URL)
    normalized = normalize_callback_base_url(url)
    if normalized not in allowed_callback_base_urls():
        raise ValueError(f"Callback base URL {normalized!r} is not in the allowed list.")
    return normalized
```

So the field is required today only because:

1. **The settings validator forces it.** `_validate_oauth_authentication` requires
   `callback_base_url` for folded GitLab OAuth
   (`service/settings/settings_request_validator.py`).
2. **It is a stored app-credential key.** `GITLAB_OAUTH_APP_KEYS`, `JIRA_OAUTH_APP_KEYS`, and
   `CONFLUENCE_OAUTH_APP_KEYS` (`service/settings/settings.py`) list it, and
   `_APP_KEYS` in `service/oauth/settings_base.py` preserves it across updates.
3. **The OAuth routers read and require it.** `_build_app_credentials` in
   `rest_api/routers/oauth_router_factory.py` pulls `callback_base_url` from the stored credentials
   and rejects the request when it is blank; `InitiateOAuthRequest` accepts it as an override; the
   three provider routers name it in their "Please configure…" messages.
4. **The UI renders and sends it.** `settingsUIConfig.ts` defines the field for all three
   providers and `OAuthTestAction.tsx` sends it in the test payload.

Because the empty-value fallback already exists, the change is to stop collecting, storing,
requiring, and forwarding the value — the fallback then becomes the only code path.

## Design

### Target behavior

For every OAuth initiate / connect / test flow, the `redirect_uri` is built from
`CALLBACK_API_BASE_URL` (via the existing empty-input fallback). The value is never taken from user
input or from stored credentials.

### Backend changes (`codemie`)

1. **`service/settings/settings_request_validator.py`** — remove `callback_base_url` from the
   required-fields loop in `_validate_oauth_authentication`, and update the module/function
   docstrings that enumerate the required OAuth app credentials so they no longer list it.

2. **`service/settings/settings.py`** — remove `callback_base_url` from:
   - `GITLAB_OAUTH_APP_KEYS`
   - `JIRA_OAUTH_APP_KEYS`
   - `CONFLUENCE_OAUTH_APP_KEYS`

3. **`service/oauth/settings_base.py`** — remove `callback_base_url` from `_APP_KEYS` so it is no
   longer preserved when an integration is updated.

4. **`rest_api/routers/oauth_router_factory.py`**
   - `_build_app_credentials`: stop adding `callback_base_url` to the `app` dict, and drop it from
     the required-keys check (`all(app[key] for key in (...))`).
   - `InitiateOAuthRequest`: remove the `callback_base_url` field so the `/initiate` (test) endpoint
     no longer accepts an override.
   - Stop passing a `callback_base_url` into the flow service; the flow defaults it from
     `CALLBACK_API_BASE_URL`.

5. **`rest_api/routers/gitlab_oauth.py`, `jira_oauth.py`, `confluence_oauth.py`** — remove
   `callback_base_url` from the "Please configure …" `missing_app_credentials_message` strings.

6. **`service/oauth/constants.py`** — keep `ensure_callback_base_url_allowed`,
   `allowed_callback_base_urls`, and `OAUTH_CALLBACK_ALLOWED_BASE_URLS` unchanged. With no
   user-supplied value reaching the flow, `ensure_callback_base_url_allowed(None)` always returns the
   configured default; the allowlist is retained as a defensive guard and for any future
   non-form caller, but goes dormant for normal use. (Optionally, `flow_engine.initiate_flow` may
   keep its `callback_base_url` parameter for signature stability; it will only ever receive
   `None` from the routers.)

### Frontend changes (`codemie-ui`)

1. **`src/utils/settingsUIConfig.ts`** — remove the three `callback_base_url` field definitions
   (GitLab, Jira, Confluence), including their `label` ("CodeMie Callback Base URL") and
   `placeholder` ("https://your-codemie-host").

2. **`src/pages/integrations/components/OAuthTestAction.tsx`** — remove `callback_base_url` from the
   test-action payloads (the three `value('callback_base_url')` sends).

3. **`src/types/entity/dataSource.ts`** — remove the now-unused `callback_base_url?` members from
   the OAuth credential types.

4. Any other UI reads of `callback_base_url` surfaced by
   `grep -rn "callback_base_url" src` are removed.

### Data flow after change

```
User submits OAuth integration (client_id, client_secret[, instance_url])
        │  (no callback_base_url collected)
        ▼
Settings validator: requires client_id + client_secret (+ instance_url optional for GitLab)
        ▼
Stored credential_values: client_id, client_secret (encrypted)[, instance_url]  — no callback_base_url
        ▼
Initiate / Connect / Test:
   _build_app_credentials → { client_id, client_secret[, instance_url] }
        ▼
   flow_engine.initiate_flow(callback_base_url=None)
        ▼
   ensure_callback_base_url_allowed(None) → CALLBACK_API_BASE_URL
        ▼
   redirect_uri = adapter.build_redirect_uri(CALLBACK_API_BASE_URL)
```

## Backward compatibility

- **Existing integrations** that already stored a `callback_base_url` are unaffected: the value is
  simply no longer read, and it is dropped from the preserved keys on the next save. No data
  migration is required.
- **`CALLBACK_API_BASE_URL`** must be set correctly in every deployment — it already is, because it
  is the fallback the current flow uses whenever the field is blank and is the value registered as
  the redirect URI on the provider apps. This change makes correct configuration of that one value
  the single source of truth instead of a per-integration copy.
- **Split UI/API host deployments** (UI served from a different host than the API): the callback
  host is `CALLBACK_API_BASE_URL`, which must match the redirect URI registered on the provider —
  unchanged from today's fallback behavior. This is an admin/config concern, not per-integration
  user input.

## Testing

Backend:
- Validator: OAuth integrations validate successfully with `callback_base_url` absent; it is no
  longer a required field.
- Settings/app-keys: `callback_base_url` is not stored in `credential_values` and not preserved on
  update.
- Router: `_build_app_credentials` succeeds without a stored `callback_base_url`; the built
  `redirect_uri` derives from `CALLBACK_API_BASE_URL`.
- Update the existing OAuth tests that reference `callback_base_url`
  (`test_settings_request_validator.py`, `test_folded_oauth_type.py`,
  `test_gitlab_oauth_connect.py`, `test_flow_engine.py`, and the router/settings tests) to reflect
  the removal.

Frontend:
- `settings.test.ts`, `SettingsForm.oauth.test.tsx`, `OAuthTestAction.test.tsx` — remove
  assertions/interactions that reference the "CodeMie Callback Base URL" field and its placeholder,
  and confirm the test payload no longer includes `callback_base_url`.

## Rollout

- Backend and UI changes ship together. If they ship separately, backend-first is safe: the UI's
  `callback_base_url` becomes an ignored input before the field is removed, and the backend no
  longer requires it.
