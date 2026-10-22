# Spec: Clone MCP configuration (backend)

Scope: this repository (API contract) only. The UI (Clone action, redirect into edit view, error presentation) is out of scope for this repo and this run; it is handled in a separate codemie-ui task that consumes this API contract (see UI contract notes).

## Revision history

- **2026-10-05, initial spec.** Secure-by-default clone: secret stripping with placeholders, optional `copy_values` request body, `stripped_fields` in the response, `placeholder_value_present` warning on create/update/clone.
- **2026-10-07, revision (this version).** Requirements changed (see the scope-change comment on EPMCDME-15538): the clone is a full copy and carries the source's values, including secrets, verbatim. Stripping, `copy_values`, `stripped_fields`, the placeholder warning and the clone-policy drift guard were removed, since nothing used `copy_values`. Only `auth_config.id` is regenerated. Implemented by `docs/superpowers/tasks/2026-10-07-clone-mcp-full-copy-remove-stripping/plan.md`.
- Acceptance-criteria numbers are stable across revisions. Removed criteria stay listed and are marked, so earlier references (reviews, task reports, the MR) still resolve.

## Goal

Let an admin/maintainer create an independent copy of an existing MCP configuration through one server-side call, so a working configuration can be varied without rebuilding it.

## Behavior

### Endpoint
- `POST /v1/mcp-configs/{id}/clone`, in `src/codemie/rest_api/routers/mcp_config.py`, guarded by `authenticate` + `admin_access_only` (same as create/update/delete). Non-privileged users get the existing 403 (AC5).
- No request body.
- `response_model` is the existing `MCPConfigResponse` (plain config object, status 201). No wrapper envelope and no extra fields: the UI store treats the create result as a plain config object (`store/mcp.ts` `createConfig` unshifts the result into the list).
- Source id not found: 404 (ExtendedHTTPException, message/details/help); nothing is created.
- Any existing config is a valid clone source, including system-provided ones (is_system=True); the clone itself always has is_system=False.
- Clone is all-or-nothing: on any failure nothing is persisted and the original is untouched.
- Errors are typed `ExtendedHTTPException` (message, details, help) with a clear message (AC4).
- Order of checks: permission, then source lookup, then write.

### Service path
- `MCPConfigService.clone(source_id, user)` builds the row. It does NOT call `create`, which forces `is_system=True`/`is_public=True`.
- Clone fields: `is_system=False`, `is_public` copied from the source, `usage_count=0`, `is_active=True`, `user_id` and `created_by` set to the cloning user.
- Everything else is deep-copied as stored: description, URLs, logo, categories, `required_env_vars` and the whole `config` (url, command, args, headers, env, `auth_token`, tools, audience, `allow_issuer_prefix_match`, `auth_config`). Nothing is stripped, blanked or replaced (AC7).
- `auth_config` is copied as stored, including the already-encrypted `client_secret`. Only `auth_config.id` is regenerated, unconditionally, because it has a unique index. The stored form is copied verbatim: no re-validation, no re-encryption (re-running the save-time encryption would double-encrypt the secret).
- An `IntegrityError` on the unique `auth_config.id` index maps to 409; any other `IntegrityError` is re-raised after rollback.
- The clone is a deep copy. Later edits to the clone or the original never affect each other (AC3).
- The clone logs source id, clone id and user id at info level; never config values.

### Naming (AC2)
- Name is `Copy N of <base>`, where `<base>` is the source name, truncated if needed so the final name stays within 255 characters, and N is the lowest positive integer not already used. First clone is `Copy 1 of X`, then `Copy 2 of X`.
- Truncation reserves room for the `Copy N of ` prefix using a fixed maximum width for N, so `<base>` is decided before the scan and is the same base used for both the scan pattern and the final name.
- Scan (portable SQL only; no Postgres-specific regex operators):
  1. Select ONLY the `name` column (projection), never whole `MCPConfig` rows (rows carry the large config JSON).
  2. Filter in SQL by the cloning user's `user_id` AND a `LIKE` pattern for `Copy % of <base>`, with `%`, `_` and the escape character escaped in `<base>` (explicit `ESCAPE` clause).
  3. In Python, apply the strict regex `^Copy (\d+) of <escaped base>$` over the returned names and ignore non-matching names (`LIKE` is only a pre-filter). The lowest positive integer not in the matched set is N.
- The scan and the insert run in one transaction; any failure rolls back everything.
- No lock is taken.
- Names of clones of clones are built from the immediate source's name (no stripping of a prior "Copy N of" prefix).
- Name uniqueness is enforced only in application code (see accepted limitation under Risks).
- No migration and no new constraint.

### Removed in the 2026-10-07 revision
The following initial-spec behavior no longer exists and must not be reintroduced without a new requirement:
- Optional `MCPConfigCloneRequest { copy_values }` body and its dotted-path grammar and 422 validation.
- Secret stripping: the text placeholder `<REQUIRED: re-enter value>` and the URL token `REQUIRED_REENTER_VALUE`; blanking of header, env, query and args values; URL userinfo/fragment removal; clearing of `auth_token`; dropping of a confidential OAuth2 `auth_config`; removal of `client_secret` from other auth types.
- `MCPConfigCloneResponse.stripped_fields` (the response is plain `MCPConfigResponse`).
- Field-level clone-policy metadata (`drop` / `copy` / `blank_values`) and its drift-guard test.
- The `placeholder_value_present` warning on create, update and clone responses.
- The info-level audit log of copied paths (nothing is selected any more).

### UI contract notes (for the separate codemie-ui task; UI stays a non-goal)
Facts from the codemie-ui repo:
- The MCP management page is under `/settings/administration`.
- "Edit view" is a modal (`src/pages/settings/administration/components/MCPServerModal`) that edits the whole config as ONE raw JSON textarea (`modalHelpers.ts` JSON.stringify/JSON.parse), validated only as parseable JSON.
- The UI's `MCPConfig` type has no `warnings` or `auth_config` fields yet.
- Errors use `ExtendedHTTPException` (message, details, help), which the UI already reads via `error.response.data.message`.
- Permission: the backend returns 403 for non-admin/maintainer. The UI run confirms how non-admins are kept off the administration page and disables/hides Clone accordingly. There is no capabilities endpoint.

Superseded by the 2026-10-07 revision (do not build): the copy-values dialog with per-key checkboxes, the `stripped_fields` banner, handling of `placeholder_value_present`, and posting `copy_values`. The Clone action posts with no body and opens the returned config for editing.

## Acceptance criteria
1. Admin calls clone on an existing MCP and gets a new persisted config, with configuration, connection settings, secrets, `is_public` and metadata copied, and the response is a plain `MCPConfigResponse` usable to open the new entity for editing. *(Reworded 2026-10-07: full copy; `MCPConfigCloneResponse` removed.)*
2. Clone name is `Copy N of <base>` with the lowest free N for the cloning user, within 255 characters.
3. Edits to the clone and to the original are independent.
4. On failure (DB error, unknown id) a clear error is returned, no row is created, the original is unchanged. *(Reworded 2026-10-07: no validation 422 any more.)*
5. A non-admin/maintainer receives 403 from the endpoint.
6. The clone has `is_system=False`, `usage_count=0`, `is_active=True`, and the cloner as `user_id`/`created_by`.
7. ~~With a missing body or empty `copy_values`, no secret value appears in the clone; all headers/env values equal the text placeholder.~~ **Removed 2026-10-07**, superseded by: no value of `config` is altered, except that `auth_config.id` is regenerated when `auth_config` is present.
8. ~~`stripped_fields` is a top-level list of dotted paths, names only, and excludes copied paths.~~ **Removed 2026-10-07** (`stripped_fields` no longer exists).
9. ~~URL handling: scheme, host, port and path unchanged; userinfo replaced; fragment removed; query values replaced.~~ **Removed 2026-10-07** (the URL is copied unchanged).
10. ~~Create, update and clone responses carry a non-blocking `placeholder_value_present` warning.~~ **Removed 2026-10-07** (create/update behave as before the feature; no new warning).
11. ~~The model drift-guard test fails for an unannotated new field.~~ **Removed 2026-10-07** (clone-policy metadata removed).
12. ~~Cloning a confidential OAuth2 MCP yields a clone with `auth_config` None.~~ **Replaced 2026-10-07**: cloning any MCP with an `auth_config`, confidential OAuth2 included, copies it as stored (encrypted `client_secret` unchanged) with a newly generated `auth_config.id`.
13. Name allocation handles base names containing `%`, `_` and backslash (wildcards escaped), a long base name truncated so the final name is at most 255 characters with the scan using the same base, gaps in numbering (lowest free N reused), and the user's non-matching configs (other base, other user, malformed names), which are ignored.
14. The name scan selects only the `name` column, never full rows.
15. ~~Selected `headers.<Key>`, `env.<KEY>`, `url.query.<key>` and `args[<index>]` values are copied; all other values blanked.~~ **Removed 2026-10-07** (`copy_values` removed).
16. ~~A non-copyable path in `copy_values` returns 422.~~ **Removed 2026-10-07** (`copy_values` removed).
17. ~~An unknown/invalid or non-existent `copy_values` path returns 422.~~ **Removed 2026-10-07** (`copy_values` removed).
18. ~~Duplicate `copy_values` entries are tolerated.~~ **Removed 2026-10-07** (`copy_values` removed).
19. ~~The info-level audit log contains the copied paths and no values.~~ **Removed 2026-10-07** (`copy_values` removed).
20. ~~A copied value containing JSON-special characters survives the JSON round trip.~~ **Removed 2026-10-07** (values are deep-copied unchanged; nothing is rewritten).
21. Permission is checked before the write, and the source lookup happens before any write. *(Reworded 2026-10-07: the `copy_values` validation step no longer exists.)*
22. *(Added 2026-10-07.)* A conflict on the unique `auth_config.id` index returns 409 and rolls back; other integrity errors are re-raised after rollback.

## Non-goals
- UI work (Clone button, redirect, error presentation); a separate codemie-ui task handles it.
- Any value stripping, blanking or placeholder replacement; `copy_values`; `stripped_fields`; placeholder warnings.
- Carrying over usage history, usage counters, or audit metadata.
- Re-linking assistants/workflows to the clone.
- Bulk clone.
- Telemetry or clone-event tracking (no Assistant-style `clone_count`).
- Any change to the existing `create` semantics (forced system/public flags stay).
- Broadening MCP creation permission beyond `admin_access_only`.
- A DB migration or a new unique constraint on `(name, user_id)`, including a unique index with a dedupe/rename migration for existing duplicates (separate follow-up ticket).
- Locking of any kind for name allocation.
- A capabilities endpoint for the UI.
- A wrapper response envelope for the clone result.

## Risks and notes
- ACCEPTED (2026-10-07, confirmed requirement): the clone carries the source's secrets verbatim: `auth_token`, header/env/query/args values, URL credentials and the encrypted `client_secret`. Admin-only access (`admin_access_only`, same as create/update/delete) is the only guard.
- ACCEPTED: the regenerated `auth_config.id` means stored OAuth tokens of the source are not shared. The clone must authorize on its own.
- ACCEPTED LIMITATION: name uniqueness is enforced only in application code (the `(name, user_id)` index is non-unique), and create/update have the same exposure today. Two simultaneous clones of the same MCP by the same user can both get the same N and produce duplicate names. A lock would protect only the clone path, not create/rename, and would be Postgres-specific. Mitigation: the UI should disable the Clone button while a request is in flight (codemie-ui task).
- The explicit list of fields copied by `clone` (description, `server_home_url`, `source_url`, `logo_url`, `categories`, `required_env_vars`, `is_public`, `config`) is maintained by hand. With the drift guard removed, a new `MCPConfig` field is not copied unless `clone` is updated.

## Testing
- Service tests (`tests/codemie/service/test_mcp_config_service_clone.py`): flags/owner/descriptive fields, full copy with no stripping (headers and `auth_token` unchanged), deep-copy independence, system source cloneable, unknown source returns 404 and opens no session, `auth_config` copied with a new id and unchanged stored `client_secret` (source untouched), no `auth_config` stays empty, 409 mapping with rollback, other integrity errors re-raised.
- Naming tests (`tests/codemie/service/mcp/test_clone_naming.py`): gaps reuse the lowest free N; base names with `%`, `_` and backslash; a truncated long base (scan base equals final-name base, total at most 255); other users' and non-matching configs ignored; only the `name` column is selected.
- Router tests (`tests/codemie/rest_api/routers/test_mcp_config.py`): 403 for a non-admin with the service not called, the service called as `clone(id, user)` with no body, a flat response without `stripped_fields`, typed 404/409 error bodies.
