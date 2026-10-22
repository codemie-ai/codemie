# Spec: MCP Server Clone — Form-Based Flow (Backend) — EPMCDME-15538

## Problem / Goal

Cloning an MCP server config is today a single irreversible backend action: a no-body
`POST /v1/mcp-configs/{id}/clone` immediately creates a verbatim copy with an auto-generated name,
bypassing create's validation/encryption, and hardcoding `is_system=False` regardless of source. A
new frontend form (separate workstream, out of scope here) will let admins review and edit the
clone before it's created. This spec defines only the backend contract that form needs: a
read-only name-suggestion lookup, and a clone-from-form create path that takes a full submitted
payload instead of creating blind.

## Scope

1. New read-only endpoint: `GET /v1/mcp-configs/{id}/suggested-clone-name`.
2. Replacement of `POST /v1/mcp-configs/{id}/clone` (same path) to accept a request body and
   create exactly one new record from submitted values — removes today's no-body
   `MCPConfigService.clone` and its direct-`Session` persistence path entirely.
3. Bug fix: `is_system` on the new record copies from source instead of being hardcoded.

## Suggested-name endpoint

- `GET /v1/mcp-configs/{config_id}/suggested-clone-name`, gated identically to today's clone route
  (`authenticate` + `admin_access_only`).
- Looks up the source record (404 if missing, same as clone today), then calls
  `clone_naming.compute_base` and `allocate_name` verbatim (no reimplementation) against the
  **calling user's** id — same per-user scoping `allocate_name` already uses.
- Pure read: no DB write, no name reservation.
- Response: `{"suggested_name": str}`.

## Clone-from-form endpoint

- `POST /v1/mcp-configs/{config_id}/clone`, same gating as today (`authenticate` +
  `admin_access_only`).
- Request body: new `MCPConfigCloneRequest` model, field-identical to `MCPConfigCreateRequest`
  (`name`, `description`, `config` — including `headers`/`env`/`args`/`url`/`auth_token`/
  `auth_config`/etc. — `categories`, `required_env_vars`, `server_home_url`, `source_url`,
  `logo_url`). It carries **no** `is_system` or `is_public` field: those are never client-supplied
  for a clone, only ever derived server-side from the source record (see below).
- Service method: new `MCPConfigService.clone_from_form(config_id, request, user)`, replacing
  `clone(source_id, user)` entirely. Looks up the source record (404 if missing).
- Behaves like `create` for everything the request supplies: duplicate-name check, same
  validation pipeline (`_strip_response_only_auth_metadata` → `_validate_auth_config_for_save`),
  same error shapes (422 invalid auth_config, 409 duplicate name / auth_config_id conflict) as
  `create` and `update` today.
- **`is_system` / `is_public`**: both copied from the source record's current values, never from
  the request body and never a fixed constant. (Today `is_public` already copies correctly via
  `_CLONE_COPIED_FIELDS`; `is_system` is hardcoded `False` at mcp_config_service.py:385 — this is
  the bug to fix.)
- **`auth_config.id`**: unconditionally regenerated to a fresh value for the new record, exactly as
  today's `clone` already does at lines 368-370. This must be an explicit step in
  `clone_from_form`, not a call to `_ensure_auth_config_id` (which only fills a *missing* id and
  would leave an existing id — the source's — in place, guaranteeing a 409 on
  `ix_mcp_configs_auth_config_id`).
- **Secret carry-over**: `clone_from_form` calls `_prepare_auth_config_for_persistence` the
  **update-style** way — passing both `existing_encrypted_secret=_get_stored_encrypted_secret(source.config)`
  and `incoming_auth_config=` the request's raw auth_config — never the create-style
  no-argument call (which always re-encrypts and would double-encrypt an already-encrypted
  secret). Effect: if the submitted `auth_config.client_secret` is blank/omitted (masked field
  untouched), the source's original encrypted secret is carried over unchanged; if the admin
  typed a new value, it is encrypted fresh. The source record is read-only throughout — never
  mutated, never re-saved.
- **New-record metadata**: fresh `id`; `created_by`/`user_id` = the cloning user (not source's
  owner); `is_active=True`; `usage_count=0`; fresh `date`/`update_date`. Same as today's `clone`.
- **Persistence**: use `create`'s `.save()`-based path (not direct `Session` construction), since
  `clone_from_form` now reuses `create`'s validation/encryption helpers and error-mapping
  conventions; the existing `IntegrityError` → `_is_auth_config_id_conflict` → 409 mapping must
  still apply around the save call.
- Response: existing `MCPConfigResponse` / `_to_response`, unchanged — `client_secret` stripped,
  `has_client_secret` set, same as every other mutating endpoint today.
- No record is created by any step before this endpoint is called — the suggested-name lookup is
  read-only and there is no separate "start clone" call.

## Acceptance Criteria

- `GET .../suggested-clone-name` returns the correct next `Copy {N} of {Name}` for the calling
  user, using unmodified `clone_naming` logic, with no DB write and the same access gating as
  clone.
- `POST .../{id}/clone` with a submitted payload creates exactly one new record with the submitted
  field values; no record exists before this call succeeds.
- A clone of a private source is private (`is_system=False`); a clone of a system/public source is
  system/public (`is_system=True`) — value always taken from source, regardless of request body
  content.
- `is_public` continues to copy from source correctly (regression check, not new behavior).
- Submitting without changing a confidential OAuth2 `client_secret` yields a clone whose decrypted
  secret equals the source's; the source record's stored secret is byte-for-byte unchanged.
- Submitting a new `client_secret` value yields a clone whose decrypted secret equals the new
  value; the source record's stored secret is unchanged.
- The new record's `auth_config.id` (when present) always differs from the source's.
- A duplicate-name submission returns the same error shape/status as `create`'s duplicate-name
  error.
- An invalid `auth_config` submission returns the same 422 as `create`/`update`.
- The old no-body `MCPConfigService.clone` method and its direct-`Session` persistence code are
  removed; no route calls it.

## Non-goals

- Any frontend/UI change (modal, form, prefill, name-suggestion fetch) — separate workstream.
- Any change to how `client_secret` is displayed or edited for plain `update`/Edit — only clone's
  cross-record carry-over is new.
- Any new masking of `headers`/`env`/`args`/`auth_token`/`url` — these remain plaintext everywhere,
  as today.
- Re-adding `copy_values`/clone-policy/field-stripping — superseded by the 2026-10-07 full-copy
  change and not reintroduced here.
- Atomic reservation of the suggested name — it is a submit-time prefill only; a collision at
  submit time surfaces the standard duplicate-name error, with no auto-increment retry.
- Any SAML or other non-OAuth2 auth-type carry-over handling — confirmed out of scope; SAML has no
  secret fields.
- Any change to the suggested-name endpoint's naming scope (remains per-user, matching
  `allocate_name`'s existing behavior) — not globalizing it.
