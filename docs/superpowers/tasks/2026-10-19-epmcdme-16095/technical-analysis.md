# Technical Research

**Task**: files download-authorization assistant icon
**Generated**: 2026-10-19
**Research path**: filesystem

---

## 1. Original Context

Jira EPMCDME-16095 (Bug, Major) — "Uploaded assistant icons are visible only to the uploader (other users get 404)"

Summary: Icons uploaded for an assistant via the assistant form are stored as regular platform files owned by the uploader. The file download authorization rules (can_download) do not grant access to other users who view that assistant, so the icon returns 404 for everyone except the uploader. This bug has been identified purely from code analysis and has NOT been reproduced live yet.

Description: This affects any case where an assistant's icon_url points to a file uploaded through POST v1/files/bulk. Since can_download has no rule covering "file is the icon_url of an assistant/workflow the requester can read," only the original uploader can fetch the icon. For shared or marketplace assistants, every other viewer gets a 404 on GET /v1/files/<token> and sees a broken image instead of the custom icon (frontend Avatar.tsx has no onError fallback — frontend is a separate repo, codemie-ui, out of scope for this backend repo).

Steps: User A uploads an image as assistant icon (POST v1/files/bulk), returned file_url (.../v1/files/<encoded token>) saved as assistant icon_url. User A shares/publishes the assistant. User B views it. GET /v1/files/<token> returns 404 for User B.

Expected: An image referenced as icon_url of an assistant that the requesting user can see should be downloadable by that user — e.g. a can_download rule granting access when the file is the icon_url of an assistant/workflow the requester can read. Note: Assistant.get_by_icon_url already exists in src/codemie/rest_api/models/assistant.py but is currently unused — it could support such a rule. Alternatively, icons could be stored in a public/shared namespace.

Affected (backend): src/codemie/rest_api/routers/files.py (read_file), src/codemie/service/file_service/download_rules.py (can_download) — existing rules: (A) requester is file owner, (B) workflow schema readable by user, (C) MCP screenshot shared namespace, (D/D2) workspace blobs, (E) share grant. No rule exists for assistant/workflow icon_url. Related: workflow form only exposes a URL input for icon_url (no upload).

Acceptance Criteria:
1. Given an assistant's icon_url points to a file uploaded via POST v1/files/bulk, when another user who can view that assistant requests the icon URL, then the file is returned (not 404).
2. The fix does not grant broader file access than required for the assistant's (or workflow's) icon_url use case.
3. Existing can_download rules (owner, workflow schema, MCP screenshot, workspace blobs, share grant) continue to work unchanged.
4. This has not been reproduced live; live reproduction/verification is part of closing this bug.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/service/file_service/download_rules.py` — `can_download(file_object, user, share_token, file_name_param, workspace_repo) -> bool`. Order: `is_safe_blob_ref` guard, then A (`owner == user.id`), B (`_is_readable_workflow_schema`: owner == `config.CODEMIE_STORAGE_BUCKET_NAME`, name `workflows/{id}.svg`, `WorkflowConfig.find_by_id` + `Ability(user).can(Action.READ, wf)`, try/except -> False with `logger.warning`), C (`owner == MCP_IMAGES_SUBDIR`), D (`workspace-` owner prefix), D2 (source blob referenced by user's workspace), E (`_has_share_grant`). No icon rule.
- `src/codemie/rest_api/routers/files.py` — `read_file(file_name, share_token, user)`: `FileObject.from_encoded_url(file_name)` (400 on ValueError), then `_authorize_file_access` outside the storage try; any non-HTTP exception in authorization -> logged + 404 (fail closed, 404 parity for denial vs missing). Image MIME types served inline; SVG served with `Content-Disposition: attachment` + CSP sandbox.
- `write_files_bulk` (POST `/v1/files/bulk`) and `write_file` (POST `/v1/files/`) write with `owner=user.id`, `name=file.filename` (client-supplied, not uuid-prefixed here), and return `file_url=result.to_encoded_url()` — the bare token, not a full URL.
- Token format (`src/codemie_tools/base/file_object.py:147`, `string_serializer.py`): `base64("<len>~<mime><len>~<owner><len>~<name>")` — standard base64 (may contain `+`, `/`, `=`). The token fully determines mime/owner/name; nothing ties a token to an assistant.
- `Assistant.get_by_icon_url(icon_url)` (`src/codemie/rest_api/models/assistant.py:1198`) — exact equality `select(cls).where(cls.icon_url == icon_url)`, returns all matches, no permission filter. Not referenced anywhere in `src/` or `tests/`.
- `AssistantBase.icon_url` (`assistant.py:652`) — `SQLField(default=None, index=True)` (b-tree index on `assistants.icon_url`).
- `WorkflowConfigBase.icon_url` (`src/codemie/core/workflow_models/workflow_config.py:89`) — plain `Optional[str]`, not indexed; no `get_by_icon_url` analogue on `WorkflowConfig`. Workflow icon_url can also come from YAML (`workflow_config.py:208`, `:407`).

### How icon_url is stored (frontend evidence, codemie-ui, read-only)
- `codemie-ui/src/components/form/ImageUpload/hooks/useFileUpload.ts:62` — `filesStore.getFileURL(uploadResult.files[0].file_url)`; `codemie-ui/src/store/files.ts:140` — ``return `${api.BASE_URL}/v1/files/${fileName}` `` (no URL-encoding of token). So the persisted `icon_url` is `<BASE_URL>/v1/files/<token>`, where BASE_URL is deployment/environment-specific (host, scheme and any API root path vary). Exact-match lookup by a URL rebuilt server-side is therefore unreliable; the request only carries the token.

### Architecture and Layers Affected
- API/router: `routers/files.py::read_file` (calls service rule; likely unchanged).
- Service: `service/file_service/download_rules.py` (rule chain).
- Model/repository: `Assistant` (`get_by_icon_url`), `WorkflowConfig` (no icon lookup).
- Authorization core: `src/codemie/core/ability.py` — `Assistant` and `WorkflowConfig` READ = `[SHARED_WITH, OWNED_BY, MANAGED_BY, ADMIN]`.

### Integration Points
- Visibility semantics: `Assistant.is_shared_with` (`assistant.py:857`) — `is_global` -> True for internal users; external users only if `project in EXTERNAL_USER_ALLOWED_PROJECTS + user.project_names`; else `project in user.project_names and shared`. `is_owned_by` uses `created_by.id` or `creator`; `is_managed_by` = project in `admin_project_names`. `WorkflowConfig.is_shared_with` (`workflow_config.py:314`) — `is_global` or `project in project_names and shared`.
- Sub-assistant icons surface via marketplace (`routers/assistant/marketplace.py:151`) and `Assistant.get_by_ids` grants sub-assistants of a global parent without READ check — a viewer may see a sub-assistant icon without READ on the sub-assistant itself.
- icon_url is echoed in conversations, user preferences, guardrails, share-conversation listings (`conversation_service.py`, `share_conversation_service.py:138`, `user_preferences_service.py`, `guardrail_service.py`) — all paths where non-owners render the icon.
- Storage: `FileRepositoryFactory().get_current_repository()`; owner namespace = uploader user id.

### Patterns and Conventions
- Each rule is a private `_predicate(file_object, user, ...) -> bool` with a docstring "Rule X: ...", invoked in order in `can_download` with a comment; rule B is the model for a DB lookup + `Ability(user).can(Action.READ, entity)` wrapped in try/except that fails closed with `logger.warning` (no PII/token in log).
- Imports at module top (WorkflowConfig, Conversation, SharedConversation already imported in download_rules).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/` exist (security-patterns, testing-patterns, repository-patterns) but none mention `can_download`/`v1/files`.
- Prior task docs: `docs/superpowers/tasks/2026-08-20-epmcdme-12708-idor-file-download-auth/` (spec.md, technical-analysis.md) — the IDOR fix that introduced the rule chain; useful precedent for rule design and 404 parity.

### Architectural Decisions
- Inline in `download_rules.py`/`files.py`: authorization runs before storage access; fail closed; denial is indistinguishable from missing (404); Rule C documents an "accepted residual risk" (any authenticated user with the name) — the explicit precedent for a broad rule and why it is narrowed.

### Derived Conventions
- Authorization checks use `Ability(user).can(Action.READ, instance)`, not ad-hoc project checks.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/file_service/test_download_rules.py` (102 lines) — direct `can_download` tests with helpers `_user()`, `_file(owner, name)`, `_repo()` MagicMock; covers own id, own workspace, share grant (mocker.patch on `download_rules.SharedConversation/Conversation/collect_message_file_tokens`), foreign owner reject, unsafe shape, D2 source blob.
- `tests/codemie/rest_api/routers/test_files.py` (1966 lines) — router-level: wrong owner 404 (`:1562`), workflow schema allow/deny via `_setup_workflow_schema_mock` (`:1599`–`:1647`), MCP image (`:1670`), workspace owner/non-owner, 404 parity (`:1907`), auth-outside-try (`:1946`).

### Testing Framework and Patterns
- pytest + pytest-mock (`mocker`), async router tests with `authenticated_user` fixture; models patched at the `download_rules` import path.

### Coverage Gaps
- No tests for `Assistant.get_by_icon_url` (unused).
- No tests for any icon-based access; no test that a foreign-owned image is denied when no assistant references it (beyond generic foreign-owner reject).

---

## 5. Configuration and Environment

### Environment Variables
- `CODEMIE_STORAGE_BUCKET_NAME` (Rule B owner), `FILES_STORAGE_MAX_UPLOAD_SIZE` (upload), `EXTERNAL_USER_ALLOWED_PROJECTS` (marketplace visibility for external users), `API_ROOT_PATH` (affects full URLs built server-side, e.g. mermaid endpoint).

### Configuration Files
- `src/codemie/configs` (`config`) — no icon-specific settings.

### Feature Flags and Deployment Concerns
- None found. Frontend BASE_URL differs per deployment, so stored icon_url host varies; existing rows were saved with whatever BASE_URL was active.

---

## 6. Risk Indicators

- Matching: request has only the token; stored `icon_url` is `<BASE_URL>/v1/files/<token>`. `get_by_icon_url` does exact equality, so it cannot be used as-is without knowing the stored prefix. Speculative: a suffix match (`icon_url LIKE '%/v1/files/<token>'`) or endswith-check is needed; LIKE wildcards `%`/`_` in token are impossible for base64 but the leading `%` defeats the b-tree index (full scan on `assistants`; `workflows.icon_url` unindexed).
- Token variants: stored token might be URL-encoded or differ from the path param (FastAPI decodes `%2F`; base64 `/` in a token may not route as one path segment). Comparing decoded `(owner, name)` rather than raw string may be more robust. Speculative.
- Over-grant (AC2): any user can set icon_url of their own assistant to another user's file token they learned; a rule "file is icon of an assistant I can read" would then expose the victim's file to everyone who can read the attacker's assistant. Speculative mitigation: require the referencing assistant's owner/creator == `file_object.owner`, and restrict to image MIME types.
- Multiple assistants may share one icon_url; rule must check READ on any of them (loop with Ability), fail closed on errors like Rule B.
- Sub-assistants of global parents are visible without READ on the sub-assistant; their icons would still 404 under a READ-based rule.
- Workflows: no upload path in UI and no lookup method; scope of workflow support is a design choice.
- Live reproduction not done (AC4); frontend Avatar has no onError fallback (out of scope).

---

## 7. Summary for Complexity Assessment

The change surface is small and well-contained: the service-layer rule chain in `src/codemie/service/file_service/download_rules.py` (add one rule following the Rule B pattern of DB lookup + `Ability(user).can(Action.READ, ...)` with fail-closed try/except), plus possibly a model query on `Assistant` (existing unused `get_by_icon_url`, exact-match only) and optionally `WorkflowConfig` (no icon lookup, column unindexed). The router `read_file` already delegates to `can_download` and needs no structural change.

Technical novelty is low but there are two design subtleties: (1) the stored icon_url is a full `<BASE_URL>/v1/files/<token>` built by the frontend with a deployment-specific base, while the endpoint only sees the token, so the existing exact-match helper does not fit and a suffix/decoded match is needed; (2) AC2 requires the rule not to let anyone expose another user's file by pointing their own assistant's icon_url at it — tying the grant to the assistant creator owning the file is the obvious guard.

Test posture is good: `test_download_rules.py` has lightweight mock helpers and `test_files.py` has router-level patterns (workflow schema mock) to mirror; regression of existing rules is already covered. Gaps: no tests for `get_by_icon_url` or icon access. Live verification is required to close the bug.

---

## 8. External References

None named by the task. (Supplementary, read-only: `codemie-ui/src/store/files.ts:140` and `codemie-ui/src/components/form/ImageUpload/hooks/useFileUpload.ts:62` confirm icon_url = `${api.BASE_URL}/v1/files/${token}`.)
