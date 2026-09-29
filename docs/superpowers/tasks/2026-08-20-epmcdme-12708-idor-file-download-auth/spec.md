# EPMCDME-12708 — IDOR: Cross-User File Disclosure via GET /v1/files/{file_name}

## Problem

`GET /v1/files/{file_name}` has no authentication and no ownership check. The `file_name`
parameter is a base64-encoded `[mime_type, owner, name]` triplet produced by `StringSerializer`
— unsigned, fully forgeable. Any attacker with a valid session can read any other user's files
by crafting a token with the victim's `owner` field.

The edge proxy blocks fully-unauthenticated requests, so exploitation requires a valid
authenticated session. Severity: Major.

## Acceptance criteria

- **AC #1** — `GET /v1/files/{file_name}` requires a valid session. Unauthenticated requests
  return 401.
- **AC #2** — An authenticated user can only access files they own, files in workspaces they
  own, or files explicitly granted via a share token scoped to the shared conversation.
  Forged tokens that name another user's owner are rejected with 404.
  Decision D-2: authorization enforcement satisfies AC #2's intent once ownership is enforced.
  Named exception: blobs under `MCP_IMAGES_SUBDIR` stay readable by any authenticated user
  (Rule C, recorded residual — see A-4). `workflows/` SVGs are owner-scoped through `Ability`.
- **AC #3** — Authenticated share recipients continue to see attachments of conversations
  shared with them (Rule E). Interpretation A confirmed; see D-7 for the full rationale and
  the vacuous non-regression clause.

## Delivery

Stories 1 and 3 ship as **one deployable unit**. There is no intermediate state where auth
enforcement is live without the share grant (Rule E) — that would 404 every attachment in every
shared conversation.

Story 2 scoped to: recorded residual risk in Rule C (D-1). No capability token. No HMAC
signing. One new repository method (`find_by_blob`).

Story 3 has no remaining backend work beyond what this story covers (D-7).

## Approved design

The full approved design — authorization rules, handler sketch, 404 parity, service layer,
new repository methods, share token delivery, complexity note, recorded decisions D-1 through
D-8, open pre-conditions PRE-1 through PRE-3, test plan, and files touched — is in
`design-section-2-final.md` in this directory. That document is the canonical specification.
Reproduce the code blocks verbatim; do not redesign.

### Summary of changes

**`src/codemie/rest_api/routers/files.py`**
- Add `dependencies=[Depends(authenticate)]` to the `GET /files/{file_name}` decorator
- Add `share_token: str | None = Query(default=None)` parameter
- Add `_workspace_repo = AgentWorkspaceRepository()` module-level instance
- Add `_raise_file_not_found()` helper (identical body for denial and genuine 404)
- Add `_authorize_file_access(file_object, user, share_token, file_name_param, workspace_repo)` — 5 rules (A–E); runs before the storage try block
- Replace existing `except FileNotFoundError` branch to call `_raise_file_not_found()`
- Replace existing `except Exception` branch to remove `str(e)` leak
- Replace existing token-decode path with `try/except ValueError` → 400
- Add `Query` to `from fastapi import` line

**`src/codemie/service/file_service/file_service.py`**
- Add `load_content(file_object: FileObject) -> FileObject` — takes already-decoded object

**`src/codemie/repository/agent_workspace_repository.py`**
- Add `find_by_blob(workspace_id, blob_owner, blob_name, mime_type=None)` — no `deleted_at`
  filter (D-4)

**`src/codemie/service/share_conversation_service.py`**
- Add `"share_token": share_token` to the `get_shared_conversation` response dict

**`tests/codemie/rest_api/routers/test_files.py`**
- Retrofit 18 existing unauthenticated GET tests with auth headers and owner-matching fixtures
- Add 15 new test cases per the test plan in `design-section-2-final.md`
