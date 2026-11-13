# EPMCDME-16095 Assistant Icon as Entity File (revision) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Anyone who can READ an assistant can fetch its uploaded icon from `GET /v1/files/<token>`. Fixes the 404 for non-uploaders without the creator-owns-file guard, which broke reuse, clones and editor uploads.

**Architecture:** Mirror the workflow-schema pattern (Rule B). On assistant save, an uploaded-file icon is copied to `assistants/{assistant_id}/icon<ext>` under `owner=config.CODEMIE_STORAGE_BUCKET_NAME`, and `icon_url` is rewritten to the copy. A new Rule F grants that name to anyone with `Ability.can(READ, assistant)`. The current creator-owns-file Rule F is removed.

**Tech Stack:** Python, FastAPI, SQLModel, pytest + pytest-mock.

**Spec:** inline requirements (Jira EPMCDME-16095) + `technical-analysis.md` in this task dir.

Commit per task using the repository's existing convention. Revert by new commits; do not rewrite history.

## Acceptance criteria

- [ ] A user who can READ an assistant gets its icon (not 404) when the icon was uploaded via `POST /v1/files/bulk`, including after clone, icon reuse, and an editor (non-creator) upload.
- [ ] No broader access: Rule F grants only `assistants/{id}/icon*` blobs, and only for readers of that assistant. Failures deny (404).
- [ ] Rules A, B, C, D, D2, E behave as before. `can_download` signature and `routers/files.py` match `main`.
- [ ] Assistants only. Live verification is manual, performed by the flow's verification stage. It is not a plan task.

## Decisions and known limitations

- **Unreadable or non-image source file on save:** reject with HTTP 400 and a clear message. Silently dropping the icon would hide data loss.
- **Unchanged `icon_url` on update** (equals the stored value) passes through untouched. Otherwise an editor saving an assistant with a legacy upload icon they cannot read would be blocked. The backfill (Task 4) converts those legacy icons.
- **Base portability:** only the token segment after the last `/v1/files/` is replaced. The submitted URL's prefix (scheme, host, API root) is kept byte-for-byte. The new token is `FileObject.to_encoded_url()`, the same form the frontend appends.
- **Create ordering:** the id is assigned on `assistant.save()`. The icon copy and `icon_url` rewrite happen right after save and before `create_initial_version`. The request's `icon_url` is updated too, so the version snapshot agrees.
- **Known limitation:** sub-assistants of global parents are visible without READ on the sub-assistant. Their icons still 404 under a READ-based rule.
- A changed icon with a different extension leaves the previous `icon.<ext>` blob orphaned. This is accepted.

## Global Constraints

- Fail closed. Rule F logs `logger.warning` with no token and no PII, then returns False.
- Authorization goes through `Ability(user).can(Action.READ, assistant)` only.
- Layering: router calls service, service calls repository/model. The copy logic lives in a service, not the router.

---

### Task 1: Remove the creator-owns-file Rule F and its plumbing

**Files:**
- Modify: `src/codemie/service/file_service/download_rules.py` (drop `_assistant_creator_id`, `_is_readable_assistant_icon`, the `allow_assistant_icon` keyword and its docstring, and the Rule F call, lines 72-94 and 164-214)
- Modify: `src/codemie/rest_api/routers/files.py:218` (call `can_download(...)` without the flag)
- Modify: `src/codemie/rest_api/models/assistant.py:1198-1218` (restore `get_by_icon_url` to its `00b964548` form; use `git show 00b964548:src/codemie/rest_api/models/assistant.py`; drop imports that become unused)
- Modify: `agent_workspace_service` docstring that mentions `allow_assistant_icon` (find it with grep) so it matches `main`.
- Test: delete or trim the tests added by commits `0254e7709`, `5906744a5`, `1020d3a02` (`git show --stat <sha>` lists them), including the router icon test and `tests/codemie/rest_api/models/test_assistant_icon_lookup.py`.

**Test-first: no** — pure removal. The existing suite for the other rules is the regression check.

- [ ] Make the removals. Run `poetry run pytest tests/codemie/service/file_service tests/codemie/rest_api/routers/test_files.py tests/codemie/rest_api/models -q`. Expected: PASS. Then run `grep -rn "allow_assistant_icon\|_is_readable_assistant_icon" src tests`. Expected: no hits.

### Task 2: New Rule F — entity icon readable by assistant readers

**Files:**
- Modify: `src/codemie/service/file_service/download_rules.py` (new helper beside `_is_readable_workflow_schema`; call after Rule D2 and before Rule E)
- Test: `tests/codemie/service/file_service/test_download_rules.py`; one router case in `tests/codemie/rest_api/routers/test_files.py` next to the workflow-schema tests (~lines 1599-1647)

**Interfaces:**
- Produces: `_is_readable_assistant_icon(file_object: FileObject, user: User) -> bool`. It requires a non-empty `config.CODEMIE_STORAGE_BUCKET_NAME` and `owner == bucket`. The name must match `^assistants/([^/]+)/icon[^/]*$`. It then loads the assistant with `Assistant.find_by_id(id)` and returns `Ability(user).can(Action.READ, assistant)`. Any exception logs a warning and returns False.
- Produces: constants `ASSISTANT_ICON_PREFIX = "assistants/"` and `assistant_icon_name(assistant_id, ext)`, public so Task 3 uses the same shape.

**Test-first: yes** — `test_reader_can_download_assistant_icon` fails because `can_download` returns False for a bucket-owned `assistants/a1/icon.png` requested by a non-owner.

- [ ] Write failing tests with `mocker.patch` on `download_rules.Assistant.find_by_id` and `download_rules.Ability`:
  - reader allowed;
  - non-reader denied;
  - assistant not found denied;
  - `find_by_id` raising denied (fail closed);
  - malformed names denied: `assistants//icon.png`, `assistants/a1/other.png`, `assistants/a1/x/icon.png`, `assistants/icon.png`;
  - a non-bucket owner with an `assistants/...` name is denied;
  - `workflows/w1.svg` is still handled by Rule B and never consults `Assistant.find_by_id`;
  - a memory-snapshot name in the bucket namespace is denied.
- [ ] Run the two test files. Expected: new tests FAIL, existing PASS. Implement, mirroring Rule B's style and docstring. Re-run. Expected: PASS.

### Task 3: Save-time icon localization (create, update, clone)

**Files:**
- Create: `src/codemie/service/assistant/assistant_icon_service.py`
- Modify: `src/codemie/rest_api/routers/assistant/management.py` (`_save_new_assistant` at lines 144-152: call right after `assistant.save(refresh=True)`; `update_assistant` at lines 248-299: call before `repository.update(...)`). Clone goes through create via `source_assistant_id`, so it is covered.
- Test: `tests/codemie/service/assistant/test_assistant_icon_service.py` (new); wiring cases in the existing management router tests

**Interfaces:**
- Produces: `AssistantIconService.localize_icon(assistant_id: str, icon_url: str | None, current_icon_url: str | None, saver: User) -> str | None`. It returns the URL to persist and raises `ExtendedHTTPException(400)` on rejection.
- Produces: `AssistantIconService.copy_as_entity_icon(assistant_id, file_object, icon_url) -> str`, which is reused by Task 4.
- Behavior:
  - Passthrough unchanged for empty or external URLs, for `icon_url == current_icon_url`, and for a token that already decodes to this assistant's own `assistants/{id}/icon*` blob under the bucket owner.
  - Otherwise parse the token after the last `/v1/files/` (try raw, then `unquote`) with `FileObject.from_encoded_url`. Require `can_download(file_object, saver, None, token, workspace_repo)` and an `image/*` MIME type, or raise 400.
  - Read the bytes with `FileRepositoryFactory().get_current_repository().read_file(...)`. Write them to `assistant_icon_name(assistant_id, ext)` under `owner=config.CODEMIE_STORAGE_BUCKET_NAME`, with the extension from `mimetypes.guess_extension`.
  - Return `prefix + new_file.to_encoded_url()`, where `prefix` is the submitted URL up to and including the last `/v1/files/`.

**Test-first: yes** — `test_owned_file_is_copied_and_url_rewritten` fails because the service does not exist.

- [ ] Write failing service tests (patch the repository factory and `can_download` at the service import path):
  - own-file copy: the written name and owner are correct, and the prefix `https://h/api/v1/files/` is preserved;
  - file readable by the saver (a foreign owner, `can_download` True) is copied;
  - unreadable file raises 400 and nothing is written;
  - non-image raises 400;
  - external URL, `None`, and `""` pass through;
  - already-entity-icon passes through with no read or write;
  - unchanged `current_icon_url` passes through;
  - URL-encoded token is accepted;
  - a missing source file raises 400.
- [ ] Write router wiring tests:
  - create saves, then calls `localize_icon` with the new id before `create_initial_version`, and persists the result;
  - update passes the stored `icon_url` as `current_icon_url`;
  - a 400 on update leaves the assistant unmodified;
  - a clone request (`source_assistant_id` set, `icon_url` = the source's entity icon URL) copies it to the new id.
- [ ] Run `poetry run pytest tests/codemie/service/assistant/test_assistant_icon_service.py tests/codemie/rest_api/routers/assistant -q`. Expected: new tests FAIL. Implement, re-run. Expected: PASS.

### Task 4: Idempotent backfill for existing assistants

**Files:**
- Create: `src/codemie/service/assistant/assistant_icon_backfill.py`, following the one-off `main()` and `python -m` pattern of `src/codemie/repository/cli_analytics/postgres/backfill.py:140-166`. There is no Alembic file-copy migration.
- Test: `tests/codemie/service/assistant/test_assistant_icon_backfill.py` (new)

**Interfaces:**
- Produces: `backfill_assistant_icons(dry_run: bool = False) -> BackfillResult`, a counts dataclass (`converted`, `skipped`, `missing`). `main(argv)` takes a `--dry-run` flag and logs a summary line.
- Behavior:
  - Iterate assistants in batches whose `icon_url` contains `/v1/files/`.
  - Skip entity icons and external URLs.
  - For each remaining assistant, decode the token and require an image. Read the original owner's file, then call `AssistantIconService.copy_as_entity_icon` and persist the new `icon_url` with `assistant.update()`.
  - If the file is missing or unreadable, leave `icon_url` unchanged, log a warning without the token, and count it as `missing`.
  - It runs as a system job, so no saver readability check applies.
  - A second run converts nothing, because converted rows are entity icons.

**Test-first: yes** — `test_backfill_converts_uploaded_icon_and_second_run_is_noop` fails because the module does not exist.

- [ ] Write failing tests:
  - convert one;
  - rerun is a no-op;
  - missing file leaves the URL unchanged and counts it as `missing`;
  - external URL skipped;
  - non-image skipped;
  - `--dry-run` writes nothing.
- [ ] Run the new test file, expecting FAIL. Implement, re-run, expecting PASS. Document the command (`poetry run python -m codemie.service.assistant.assistant_icon_backfill [--dry-run]`) in the module docstring.

## Negative-constraint pass

- "Not broader file access" (AC2): Task 2 grants only bucket-owned `assistants/{id}/icon*` names to READ-holders of that assistant. It has negative tests for malformed names, non-bucket owners and memory snapshots.
- "Existing rules unchanged" (AC3): Task 1 restores the `can_download` signature, and the Task 1 and Task 2 regression runs keep the existing tests.
- "Must not collide with Rule B / memory snapshots": Task 2 uses a disjoint `assistants/` prefix and has the `workflows/` and snapshot tests.
- "Do not rewrite history": Task 1 reverts through new commits.
- "No Alembic file-copy migration": Task 4 uses a script module.
- "Assistants only, no workflows": no task touches workflow code.
- "Live verification is manual, no automation": no task.
