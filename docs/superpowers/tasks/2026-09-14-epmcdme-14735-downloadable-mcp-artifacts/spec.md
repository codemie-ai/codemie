# Spec: EPMCDME-14735 — downloadable chat artifacts (back end, consolidated)

**Ticket**: [EPMCDME-14735](https://jiraeu.epam.com/browse/EPMCDME-14735) · **Branch**: `EPMCDME-14735_downloadable-mcp-artifacts` · **MR**: !4252
**Consolidated**: 2026-11-03 from four sdlc-standard runs (see [Run history](#run-history-and-superseded-decisions)). On a conflict the later run wins.
**Updated 2026-11-03 (after the code review meeting)**: ticket AC #8 (explicit attach tool) is dropped and `attach_file_to_conversation` is removed. Files written on an MCP host are delivered through MCP-Connect's explicit export, which is a separate change (see [Follow-up](#follow-up-mcp-connect-captures-generated-files)).
**Updated 2026-11-12 (MR review)**: `write_file`'s `is_base64` argument is removed, so the FileSystem toolkit is as on `main`. An `http(s)` `resource_link` is passed on as a plain markdown link. Follow-up tickets are listed under [Follow-up tickets](#follow-up-tickets).
**Story** (run 4, outside the repo): `~/codemie-dev/docs/stories/2026-10-22-mcp-artifact-publishing/children/backend-artifact-publisher/story.md`. Its ACs are copied below.
**Companion files in this directory**: `plan.md` and `technical-analysis.md` (one section per run). All other files (complexity, decisions, gate run, task reports, review records) are from run 4. Earlier runs' records are in git history (before the consolidation commit).
**UI counterpart**: `codemie-ui/docs/superpowers/tasks/2026-09-21-…`, `2026-09-23-…`, `2026-10-02-…` (not merged here).

---

## Problem

Agents generate non-image files (xlsx, csv, json, zip, pdf) through MCP or the FileSystem toolkit, but the chat returns a dead local `filesystem:` path instead of a download. Images work because their bytes are ingested and turned into a `sandbox:/v1/files/...` link. The ticket's repro is the RADAR CLI shell assistant losing `closed_positions_analysis.xlsx`.

The runs found these concrete causes:
- The MCP post-processing handled only `image` items. Real MCP-Connect sends the spec shapes (embedded `resource` with `uri`/`mimeType`/`blob|text`, and `resource_link`), but CodeMie parsed only a flat `{type:resource, data}` shape, so files arrived empty and were skipped (review B1).
- FileSystem toolkit admin tools (`write_file`, the shell tool) were never wired to storage. A shell-generated file had no delivery mechanism at all.
- Each producer stored and named files on its own. The results: bare `<uuid>.<ext>` names (B3), only "File could not be saved" on failure (B2), a world-readable shared namespace when no user was known, an enterprise plugin path that never passed a user (B4), and nothing reached "Browse files" (B5).
- The shared-conversation download rule (Rule E, from EPMCDME-12708) granted any token that appeared in the history, with no ownership check (B9).

## Current design

### 1. MCP content model (`src/codemie/service/mcp/models.py`)
- `MCPToolContentItem` has an optional `resource` (`MCPResourceContents`: `uri`, `mimeType`, `blob`, `text`) and top-level `uri`/`name` for `resource_link`. `data` is used for `image` items only.
- **Deliverable** means an embedded resource with `blob` or `text`. `text` is encoded as UTF-8.
- **File name** is the percent-decoded last segment of `uri`, else the item `name`, else `artifact`. A missing extension is added from the MIME type (`file_name()`).
- Every link label built from a file or item name escapes `[`, `]` and `\` and folds whitespace (`_md_label`), so a name such as `data[1].csv` cannot end or splice the markdown link (review 2026-11-12). The stored name is unchanged.
- Backticks and control characters are dropped from every artifact name (`strip_unsafe_name_chars`, used by `file_name()` and the publisher), so a name cannot break out of the code span in a refusal or "not delivered" text (review 2026-11-12).
- If the publisher cannot be set up (storage factory or workspace service raises), each image or resource item becomes "`<name>` could not be saved because of a storage error" and the rest of the MCP result is kept (review 2026-11-12).
- *Review decision (2026-11-12):* the MCP result post-processing helpers stay in `service/mcp/toolkit.py`, where image post-processing already lived on `main`; moving them to a new module is declined as churn outside this ticket.
- A `resource_link` whose URI is `http` or `https` is already reachable by the user: it yields a plain markdown link `[<name>](<uri>)` (label is the item `name` without brackets, else the file name; parentheses and whitespace in the URI are percent-encoded). Nothing is stored and no sandbox chip is made.
- Any other resource link (`test://`, a server's own scheme; `file:///` links are resolved by MCP-Connect before they arrive), or a resource without content, yields plain text "`<name>` stays on the MCP server and was not delivered" and no link.
- The legacy flat `{type:resource, data}` shape is **not** deliverable (story D1: the production bridge never sends it). `is_resource()` is removed.
- `MCPExecutionContext` carries local-only `request_uuid` (`exclude=True`, like `conversation_id`). `get_mcp_server_tools(..., request_uuid=None)`, and `toolkit_service` passes it in.

### 2. One publisher (`src/codemie/service/artifact_publisher.py`, `ArtifactPublisherService`)
- Types live in `src/codemie_tools/base/artifact_publishing.py`: the frozen `PublishContext(user_id, conversation_id, turn_id)`, `PublishResult(link, name, size, mime | reason)`, the `ArtifactPublisher` Protocol, and `ZIP_CONTAINER_EXTENSIONS` (`.docx .odp .ods .odt .pptx .xlsb .xlsm .xlsx .zip`). This module has no `codemie.*` import. Tools depend only on the Protocol, which is injected.
- `publish(ctx, name, content|path, mime) -> PublishResult` **never raises**. Every failure reason names the file.
- Location: `artifacts/<turn-id>/<last-segment-of-name>` in the conversation workspace (`_get_or_create_workspace`), created through `AgentWorkspaceService.publish_artifact_file`. The link comes from `get_file_sandbox_url`. The turn id is the chat turn's `request_uuid` (story D2).
- A second file with the same name in one turn becomes `<name> (2).<ext>`, then `(3)`, and so on.
- Check order:
  1. Size against `FILES_STORAGE_MAX_UPLOAD_SIZE` (100 MB default, no new knob). Base64 is measured by its encoded length, a disk file by `os.stat`. Nothing is decoded or read before this check.
  2. Strict `b64decode(validate=True)` after removing whitespace. Line-wrapped base64 (RFC 2045, as some servers send it) is accepted; any other byte outside the base64 alphabet is refused (amended 2026-11-12, review CR-002).
  3. `zipfile.is_zipfile` for `ZIP_CONTAINER_EXTENSIONS`.
- Empty content: an empty **base64** payload (MCP `blob=""`, an image) is refused with "`<name>` was not saved: it is empty". A real empty file given by `path` or as bytes (a code-executor export of an empty CSV) is stored and linked, as on `main` (review CR-003, 2026-11-12).
- `is_artifact_path` also reserves the exact path `artifacts`, so a file of that name cannot take the folder's blob name (review CR-004).
- With no conversation known, the file is stored for the invoking user only (per-user store, `<uuid>_<name>`) and a WARNING is logged. With no user known, nothing is stored anywhere and a reason is returned. The shared `mcp_images` namespace is never written again. `MCP_ARTIFACTS_SUBDIR` no longer exists.
- Immutability: `is_artifact_path` (in `workspace_path_rules.py`, next to `is_reserved_workspace_path`) refuses `edit_file`, delete, `upsert_text_file`, `upsert_binary_file` and script sync under `artifacts/`. Only the publisher's own method skips the guard.
- Registered once: the row created at publish has the same `(owner, name, mime)` key that `register_generated_files` dedupes on, and a test pins "exactly once in Browse files".
- *Post-run fix (commit b439c21c8a):* workspace sync and generated-file registration skip any blob the user may not open (`_user_can_open` → `can_download`). The reason: a workspace row itself grants the blob (workspace reads, share Rule E).

### 3. Producers
- **MCP** (`service/mcp/toolkit.py`): resources and images publish through the publisher (images as `image.<ext>`). On failure the result carries the reason, never the inline `data:` URL. `_convert_mcp_response_to_tool_message(tool_result, ctx: PublishContext)` replaces the `owner` argument. `_save_screenshot_to_storage` and `_save_artifact_to_storage` are gone; `_convert_resource_to_url` now goes through the publisher.
- **Enterprise plugin** (`enterprise/plugin/dependencies.py`): module-level `convert_mcp_response(enterprise_response, ctx)`. The adapter closure only builds the context from `user`/`request`, and `request_uuid` is threaded through `get_plugin_tools` (or, if that is impossible, a fresh uuid4 turn id is used and logged). The path never runs with "no owner". The core side is unit-tested with a stub. The contract test (`tests/codemie/enterprise/plugin/test_mcp_converter.py`, `pytest.importorskip("codemie_enterprise.plugin")`) builds the real enterprise response from a spec-shaped payload. *It is currently xfail-marked for **EPMCDME-16353**: codemie-enterprise 2.3.47 `ToolConsumer` rebuilds items without `resource`, `uri` and `name`.*
- **`write_file`** (`WriteFileTool`): unchanged from `main` (text only). It does not publish and adds no link (story D3). *The binary path (`is_base64`, strict decode, zip check, empty-overwrite guard) was removed 2026-11-12: it served only the dropped attach tool, and the FileSystem toolkit is out of scope.*
- **`FileExportService`** (code-executor export) and the V1 image tool in `file_system` call the publisher. `GenerateWorkspaceImageToolV2` is unchanged (the agent chooses its workspace path).
- `ToolkitSettingService.get_file_system_toolkit` builds the publisher bound to a `PublishContext` (it already holds `request_uuid`, `user` and `request.conversation_id`) and injects it where `file_repository` used to go. A tool with no injected publisher returns "not delivered: storage not configured". `store_artifact` is replaced by the thin `publish_artifact(publisher, ctx, name, **kw)` in `codemie_tools/base/artifact_storage.py`.
- Tools still do their primary job when no publisher is present. They only omit the link and give the reason.

### 4. Shared-conversation downloads (Rule E, `service/file_service/download_rules.py`)
A file is granted to a share recipient when either:
- (i) the blob is registered in the sharer's workspace for the shared conversation (`find_by_blob`, found by conversation id, which equals the execution id for workflow runs), or
- (ii) the token is in the conversation's message tokens **and** `file_object.owner == shared.shared_by_user_id`.

A foreign `workspace-<other>` token and a foreign user's token pasted into history match neither. Rules A, B, C, D, D2 and the `is_safe_blob_ref` pre-check are untouched, so old `mcp_images` and `<uuid>_` links keep working (ticket AC #7, story D4).

One link pattern: the leaf module `service/file_service/sandbox_links.py` (no service imports) holds `SANDBOX_FILE_RE` (with the backtick exclusion) and `collect_message_file_tokens(messages)`. `agent.py`, the share service and `download_rules` import it, which also removes the `download_rules -> share_conversation_service` import. Thought scanning (`_thought_tokens`) is removed (story D5), because workspace registration covers the same files. The UI regex copy (`sharedFileGrants.ts`) is not touched. It only has to keep accepting a superset of valid tokens.

### 5. Download name (`rest_api/routers/files.py`)
The display name is the last path segment (`PurePosixPath(name).name`, after the uuid-prefix strip) before `_safe_disposition`, so `artifacts/<turn>/report.xlsx` downloads as `report.xlsx`. `_safe_disposition` still deletes separators.

### 6. Outside the MR (local working dirs, not git repos)
- `~/codemie-dev/mcp-mock/` (stand-in for MCP-Connect, `POST /bridge`): emits nested `resource {uri, mimeType, blob}` and resource links, with names like `closed_positions_analysis.xlsx`.
- `~/codemie-dev/mcp-proxy/`: `radar_client.py` forwards the spec shapes without flattening them. `tests/test_translate.py` and the README are updated. `RADAR_MCP_URL` is repointed in `.env` by the user.
- The MR description lists these as separate steps and asks a reviewer to post `/sanity` (security-relevant code).

## Acceptance criteria

### Ticket (Jira, as edited by the PO 2026-11-12)
1. A generated non-image artifact (xlsx, csv, json, zip, pdf) is ingested into user-facing downloadable storage. *Out of scope per the ticket (2026-11-12): a resource with only a uri, links that are not `file:///`, and plugin MCP tools until codemie-enterprise is fixed (EPMCDME-16353).*
2. The chat shows a Download chip with filename, MIME type and a working URL. **Size is dropped** (ticket comment 2026-09-22): `FileObject` has no size field, so adding one would change the shared storage model, which is an image-behaviour regression risk. The publisher returns `size`, but displaying it is not planned: the ticket lists size as out of scope.
3. The RADAR repro's `closed_positions_analysis.xlsx` downloads.
4. With no blob, or when ingestion fails, no fake `filesystem:` link is shown.
5. xlsx, csv, json, zip and pdf are supported.
6. Artifacts are scoped to opted-in servers that already reach `/home/codemie`. MCP-Connect exports and delivers links only from the folder of the person who made the call, `/home/codemie/u-<sha256(initiator id)[:16]>/c-<sha256(conversation_id)[:16]>`, so neither another user nor another conversation of the same user can supply a same-named file; the backend sends `user_context.id` as the outbound `user_id` and the `conversation_id`, so a global assistant or workflow is keyed on its runner, not its creator. A server that itself reaches `/home/codemie` can still read other users' folders (same uid). EPMCDME-16903 owns per-user OS identity and server processes, with MCP-Connect's process cache keyed by the initiator and each server's working folder created inside the user's folder at spawn; the intermediate-symlink check; and whether the `CODEMIE_EXPORT_FILES` opt-in stays. (Narrowed by the PO 2026-11-12; per-user folders after the PO's 2026-11-13 "not accepted"; initiator key after the round-3 review of !314, 2026-11-17.)
7. Existing image artifact behavior is unchanged. *The PO's 2026-11-12 edit removed this line from the ticket, apparently by accident (it replaced the old AC #6 and #7 with the new AC #6); the branch still meets it and restoring it is requested.*
8. ~~(Preferred) An explicit attach-file tool exists: `attach_file_to_conversation`.~~ **Dropped 2026-11-03** (code review meeting; the PO will update the ticket). The tool is removed. An MCP server delivers a file by returning its content (for example `read_media_file`), and MCP-Connect explicit export (`codemie_export_files`) is the follow-up.

### Story (run 4, normative)
**MCP content (B1, B2)**
- An embedded resource with binary content, a file name in its URI and a MIME type is published under that name. The result text has a working link.
- An embedded resource with text content is published the same way, encoded as UTF-8.
- A name without an extension gets one matching its MIME type.
- An `http(s)` resource link is passed on as a plain markdown link (added 2026-11-12). Any other resource link, or a resource without content, gives plain text that the named file stays on the MCP server and was not delivered. No sandbox link.
- The legacy flat shape is not treated as deliverable.
- Tests parse real MCP-Connect wire JSON, not hand-built items.

**One publisher (B5)**
- Every producer (MCP results including images, code-executor export, image generation) stores into the conversation workspace at `artifacts/<turn-id>/<name>` and gets back a link, size and MIME type, or a reason.
- Two files with the same name in one turn both survive. The second is `<name> (2).<ext>`.
- Workspace edit, delete, upsert and script sync under `artifacts/` are refused.
- Over-limit content is refused before decode or read (encoded length or size on disk), with a reason.
- An invalid zip-container payload (xlsx, docx, …) is refused with a reason. The validation lives in the publisher.
- With no conversation known, the file is stored for the invoking user only, explicitly and with a log entry.
- A published file appears exactly once in "Browse files" after post-turn registration (pinned by a test).

**Ownership and failure visibility (B2, B4)**
- With no invoking user, nothing is stored in any shared namespace and the result says why.
- The enterprise plugin path publishes for the invoking user's conversation, the same as regular MCP.
- Any publish failure names the file and the reason, not just "File could not be saved".
- Image links already on `main` still render and download.

**Producers (B7, B8, B10, B11)**
- `write_file` does not publish or link. *(It pointed to attach until 2026-11-03; its binary path and zip-format warning were removed 2026-11-12.)*
- No helper remains that only tests use.

**Shared-conversation downloads (B9)**
- A recipient can download the shared conversation's own workspace artifacts (or its workflow execution's).
- A link to another workspace's file, pasted into the conversation, is refused.
- A link to a user-owned file whose owner is not the sharer, pasted into the conversation, is refused.
- One shared link pattern is used. The collector and the agent no longer define their own.
- Grants no longer depend on scanning tool thoughts.

**Downloaded file name**
- The browser's suggested name is the last path segment, not `artifacts/<turn-id>/…`.

### Spec additions (run 4)
- MCP tests use captured spec-shape wire JSON.
- An enterprise contract test and a stub-based adapter test (contract test xfail until EPMCDME-16353).
- Tests for foreign-workspace and foreign-user tokens refused through a share, and for the sharer's workspace artifact allowed.
- `/v1/files` returns `report.xlsx` for `artifacts/<turn>/report.xlsx`.
- `make ruff` passes.

### Still-valid criteria from earlier runs
- xlsx, csv, json, zip and pdf round-trip through the MCP path (run 2, AC 4; the `write_file` binary part was removed 2026-11-12).
- No `filesystem:` path or raw base64 reaches the user from the MCP path (run 1, AC 3).

## Non-goals
- UI changes (sibling child story `ui-workspace-artifact-links`, done in codemie-ui), including the UI regex copy.
- Workflow tool-node post-processing (B6, EPMCDME-16432).
- `resources/read` fetching of link-only resources with other schemes (`test://`, server-specific). MCP-Connect's `/bridge` already supports `resources/read`; no ticket yet.
- Audio content items. *Deferred finding:* an MCP `audio` item still falls through to the JSON dump, so its base64 enters the model context. This is pre-existing.
- Retention or cleanup of artifacts (handled by cloud storage lifecycle rules, per the ticket's developer note).
- Showing file size in the UI.
- Changing `GenerateWorkspaceImageToolV2` or tightening Rule C.
- The `codemie_enterprise` package itself (only the contract test touches it, see EPMCDME-16353).
- Auto-capturing files after shell commands (working-directory diffing) in the CodeMie backend. Rejected in run 2 as too risky. For MCP servers the model exports files explicitly through MCP-Connect instead (see Follow-up).
- Delivering files made by CodeMie's own FileSystem toolkit (`run_command_line_tool`, `write_file`). The ticket covers MCP-generated files only, and attach (AC #8) is dropped. See EPMCDME-8709 and EPMCDME-10854.
- Making `mcp-mock`/`mcp-proxy` part of the MR.

## Risks
- Dropping thought scanning removes grants for links that appear only in thoughts. Workspace registration is expected to cover them.
- The enterprise delegate may not carry `request_uuid` (fallback: fresh uuid4, logged). The enterprise `ToolConsumer` currently strips `resource`/`uri`/`name` (EPMCDME-16353).
- The `ResourceLink` wire field names were taken from the MCP spec, not from SDK source.
- Until MCP-Connect explicit export ships and the model uses it, a file reaches the user only if the model reads it with a tool that returns its content (`read_media_file`). Verified 2026-11-03: in the RADAR repro (shared conversation `geRh9NHtq5Pj`) and locally, "give me a link" made the model call `get_file_info` and invent a link (no chip, nothing published). "Can you open it?" made it call `read_media_file`, and a working xlsx chip appeared.

## Follow-up tickets
| Gap | Ticket |
|---|---|
| Enterprise plugin MCP tools drop `resource`/`uri`/`name` | EPMCDME-16353 |
| AC #6 residual (per-user and per-conversation folders shipped in !314): per-user OS identity and server processes, with MCP-Connect's process cache keyed by the initiator and each server's working folder created inside the user's folder at spawn; the intermediate-symlink check; and whether the `CODEMIE_EXPORT_FILES` opt-in stays. | EPMCDME-16903 |
| MCP artifacts from workflow tool nodes | EPMCDME-16432 |
| One UI module for the sandbox link format | EPMCDME-16355 |
| `resources/read` for non-`file`, non-`http(s)` links | none yet |
| File size on the chip | none: dropped from the ticket (out of scope) |

A `resource` with only a `uri` needs no ticket: the MCP spec requires embedded contents to carry `text` or `blob`, so "not delivered" is the correct result.

## Follow-up: MCP-Connect captures generated files
Requested at the code review meeting (2026-11-03). The first plan (MCP-Connect captures files automatically) was replaced by explicit export, implemented in [MCP-Connect MR !314](https://gitbud.epam.com/epm-cdme/codemie-mcp-connect-service/-/merge_requests/314). MCP-Connect captures nothing by itself. A stdio server opted in with `CODEMIE_EXPORT_FILES=true` in its env gets an extra `codemie_export_files` argument (a list of file paths) injected into its `tools/list` schemas. The model names the files to export in a `tools/call`, and MCP-Connect reads them and appends them to the result as embedded `resource` items (`{"type":"resource","resource":{"uri","mimeType","blob"}}`). This backend already publishes those, so no publisher change is needed.

The switch lives in the server config's env, set by whoever authors that config. Opting in through an integration (`tool_creds` or the resolved integration env) stops working, which is intended: `MCPToolkitService._apply_tool_config_to_mcp_server` drops `CODEMIE_EXPORT_FILES` from user-supplied values before merging them, so a user's own integration cannot switch export on for a server the admin did not opt in. Who can edit that env is still unchecked; EPMCDME-16903 already lists it.

One new bridge request field (17 Nov): `to_request_fields()` sends `conversation_id` when set (it stays `exclude=True` for `model_dump`; `request_uuid` is not sent). MCP-Connect scopes export to `u-<user_id>/c-<conversation_id>` from the `user_id` and `conversation_id` this backend sends. Deploy MCP-Connect !314 first: an older MCP-Connect rejects the unknown field with 400. A `tools/call` without `user_id` exports nothing, so `user_id` must stay in those fields. The `codemie_export_files` argument appears only after the backend's toolkit cache expires (up to 600 s). The MCP-Connect spec is `docs/superpowers/tasks/2026-11-03-epmcdme-14735-capture-generated-files/spec.md` in `codemie-mcp-connect-service`, committed on branch `EPMCDME-14735_capture-generated-files` (see the MR).

## Follow-up: architecture review of 2026-11-13
Items from the review across MCP-Connect !314, this MR and codemie-ui !1945.

- **Export switch** (blocking): see the paragraph above. `CODEMIE_EXPORT_FILES` is dropped from `tool_creds` and from the resolved integration env.
- **Release order** (blocking): this MR must be deployed before any server is opted in on MCP-Connect. Chips and the `sandbox:` links need it; `/bridge` alone does not.
- **Per-result cap**: one MCP result stores at most 20 files and 50 MiB (`MAX_ARTIFACTS_PER_RESULT`, `MAX_ARTIFACT_BYTES_PER_RESULT` in `mcp/toolkit.py`, the same limits as MCP-Connect's export). Later items become a reason text. Only items that store something count. Retention of stored files (artifacts are immutable and the user cannot delete them) has no owner yet.
- **One turn id**: `PublishContext.for_turn(user_id, conversation_id, request_uuid)` is the only place a turn id is chosen: the request uuid, else a fresh hex id. The MCP toolkit, the filesystem toolkit (and so the code executor), the enterprise plugin adapter and the workspace script run use it.
- **One link prefix**: `SANDBOX_FILE_PREFIX` lives in `file_service/sandbox_links.py`; `agent_workspace_service.py` and the publisher import it. `conversation/export_utils.py` still keeps its own copy.
- **`file_names` on the assistant message**: when a turn is saved, `ConversationService.upsert_chat_history` asks `AgentWorkspaceService.turn_artifact_tokens` for the files under `artifacts/<request_uuid>/` in the user's workspace and stores their encoded tokens as the assistant message's `file_names` (`ChatTurnData.generated_file_names`). The user message's `file_names` stay the uploads. A failed lookup is logged and the turn is saved without them. Because three readers treated every message's `file_names` as uploads, they now read user messages only: `build_unique_file_objects` (LLM input files), `_get_conversation_uploaded_file_urls` (workspace virtual uploads) and `GET /conversations/{id}/files`. Share grants (`collect_message_file_tokens`) already read `file_names`, so recipients get a grant for these files with no change.
  - Not done: a workflow turn has no request uuid on its tool calls, so its files stay scattered (EPMCDME-16432). The in-progress turn is saved with whatever is published so far.
  - **UI requirements** (landed in codemie-ui !1945, ce3058d87; the UI skips workflow turns): (1) map the assistant item's `fileNames` in `chatHelpers.ts` (`fileNames: userItem.fileNames` is the only mapping today) onto a new field, since `ChatMessage.fileNames` already means the uploads; (2) render one `File` chip (download, no preview) per token under the answer with `resolveSharedFileToken` for shared chats, skipping a token whose `sandbox:` link is already rendered inline in the text, so a file is not shown twice; (3) the live turn: `file_names` exist only after the turn is saved, so decided 2026-11-13: the UI re-reads the last turn once the stream closes (no backend change; the final streamed chunk does not carry them); (4) keep the markdown chip path for older messages; (5) a message with `fileNames` but no text must still render.
- **`isError` results**: a `resource_link` or `resource` item in an `isError` result has no text, and joining the item texts raised `TypeError`, which replaced the server's error message with a generic one (pre-existing on `main`). Items without text are now skipped.
- **Immutability guards**: `artifacts/` is guarded at seven call sites in `AgentWorkspaceService` (`_refuse_artifact` x4, `_skip_artifact` x3), and the publisher bypasses them through `publish_artifact_file`. Not moved to one write path: the guards differ on purpose (four raise, three skip silently during sync and registration), so one guard would change behaviour. A test covers each site.
- **Publish lock**: tracked in EPMCDME-17263 (claim the path by insert; the `(workspace_id, path)` unique constraint already exists).

---

## Run history and superseded decisions

| Run | Original dir (removed) | Scope | Status |
|---|---|---|---|
| 1 | `2026-09-14-epmcdme-14735-downloadable-mcp-artifacts` | MCP non-image resources → `sandbox:` link | Superseded by run 4 |
| 2 | `2026-09-18-epmcdme-14735-filesystem-toolkit-artifacts` | FileSystem toolkit: shared store helper, `write_file` binary and link, attach tool, size cap | Partly superseded (runs 3 and 4; attach removed 2026-11-03) |
| 3 | `2026-10-02-epmcdme-14735-artifact-auth-gaps` | Rebase on EPMCDME-12708: owner required, no shared namespace, thought-borne share tokens | Mostly superseded by run 4 |
| 4 | `2026-10-22-backend-artifact-publisher` | One publisher into the conversation workspace, spec-shaped MCP parsing, bounded Rule E, download name | **Current** |

**Run 1 (2026-09-14)** added `_save_artifact_to_storage` / `_convert_resource_to_url` for the flat `{type:resource, data}` shape, stored under the shared `MCP_ARTIFACTS_SUBDIR`, and rendered `[Download](sandbox:…)` / `[File could not be saved]`. It accepted URL-holder access to the shared namespace as matching image behavior.
→ Superseded. The flat shape is not deliverable (run 4). Both functions are removed (run 4). `MCP_ARTIFACTS_SUBDIR` was deleted (run 3). The shared namespace is never written (runs 3 and 4). Failure text now names the file and the reason (run 4).

**Run 2 (2026-09-18)** extracted the store-and-mint helper `store_artifact` into `codemie_tools/base/artifact_storage.py`, which three callers migrated to with byte-identical output. It also: fixed `WriteFileTool`'s binary path and appended a truncation-safe link; added `AttachFileTool` with path checks via `CodeExecutorTool._validate_export_paths`; capped size at `FILES_STORAGE_MAX_UPLOAD_SIZE`; used `owner=user_id`; and required `{uuid4()}_{basename}` names so `_strip_uuid_prefix` gives the real filename. AC 2's "recorded size" was dropped during review.
→ Still valid: the size cap (now in the publisher, checked before read or decode), the binary-safe `write_file`, `file_repository=None` degrading without raising (now: no publisher → reason).
→ Superseded: `store_artifact` → `publisher.publish` / thin `publish_artifact` (run 4). The `write_file` link and truncation-safe delivery were removed, and `write_file` no longer publishes (run 4, D3). The path check moved to the shared `get_relative_path` (run 4, B7), which drops the system-directory and symlink checks. Per-user `<uuid>_<name>` storage remains only as the no-conversation fallback. The workspace path `artifacts/<turn>/<name>` is the default (run 4).

**Run 3 (2026-10-02)** rebased onto EPMCDME-12708. It made `store_artifact` fail closed on an empty owner (`ValueError`), deleted `MCP_ARTIFACTS_SUBDIR`, made `FileExportService._collect_single_file` refuse an empty `user_id`, and extended `collect_message_file_tokens` to scan `thoughts[].message` (recursively through `children`, not `input_text`) so thought-borne screenshots reach share recipients. It recorded an open hole: Rule E grants any token in history regardless of the blob owner.
→ Still valid: no write without an owner (now "no user → reason, nothing stored"), and `MCP_ARTIFACTS_SUBDIR` stays deleted.
→ Superseded: thought scanning was removed (run 4, D5). The open Rule E hole is **closed** by the ownership bound (run 4, section 4). `store_artifact` is gone. Run 3's non-goals "no change to `files.py`" and "plugin path left untouched" were reversed by run 4 (download name; enterprise adapter publishes).

**After run 4 (commits b439c21c8a, 4f5dfbedf4, 2026-10-23)**: registration skips blobs the user may not open, the enterprise contract test runs against the real plugin path (xfail on EPMCDME-16353), and the Sonar complexity findings in the publisher and workspace service were fixed.

**2026-11-03 (code review meeting)**: AC #8 dropped. `AttachFileTool`, `ATTACH_FILE_TOOL` and their tests are removed. The `write_file`, read-binary and refusal messages no longer mention attach. The FileSystem admin toolkit is back to 7 tools (8 with the code executor). This supersedes run 2's attach tool and run 4's attach producer.

**2026-11-12 (MR review)**: `is_base64` and the rest of `write_file`'s binary path are removed with their tests (review note 2513254); `src/codemie_tools/data_management/file_system/tools.py` and `tools_vars.py` match `main`. An `http(s)` `resource_link` becomes a plain markdown link instead of "not delivered" (`_web_link` in `mcp/toolkit.py`).
