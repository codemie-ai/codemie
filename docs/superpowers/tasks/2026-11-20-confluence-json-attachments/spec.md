# Spec: agent-generated files as Confluence attachments (EPMCDME-16431)

## Problem
`GenericConfluenceTool` can attach files, but only those in `config.input_files`, which hold user uploads (`confluence/tools.py:165-171, 216-265`; `toolkit_service.py:918-927`). Files an agent generates (stored in the conversation workspace under `artifacts/<turn_id>/`, `ArtifactPublisherService`) never reach it. An attachment POST with no files goes out as a plain JSON request with no error, so the agent can imply a file was attached when it was not (technical-analysis.md section 2).

## Approach
The Confluence tool gets a constructor dependency, in the style of `CodeExecutorTool` (`code_executor_tool.py:217-232`; Protocol precedent `codemie_tools/base/artifact_publishing.py:63-82`). It reads generated files at call time, because `input_files` is filled before the turn starts and `generated_file_names` is saved only when the turn ends (`conversation_service.py:525-537`).

Contract of the dependency:
- It is a read-only Protocol in `codemie_tools/base` with two operations. One lists generated files (bare name, path, size, content checksum) without bytes. The other reads the bytes of one named file. The tool never imports `codemie.service`.
- It is an adapter over `AgentWorkspaceService`. The plan names the wrapped methods. It does not reuse `get_workspace_input_files`, which loads the bytes of every workspace file.
- The conversation id and user are bound when `ToolkitService` builds the tool, from the conversation the platform already holds. No params field, file name or id from the model can change them. A name from another conversation is simply "not found".
- Size is checked before reading, against `FILES_STORAGE_MAX_UPLOAD_SIZE`.

Name matching is one unit independent of Confluence (candidates of source, bare name and bytes), so the other-tools follow-up can reuse it. The attachment path no longer uses the resolve and filter of `FileToolMixin`. The class still inherits the mixin, which is untouched.

Ruled out: filling `input_files` with generated files (misses current-turn files, changes the pinned user-upload collector in `core/utils.py`); changing `FileToolMixin` (13 consumers, no tests).

## Behavior
1. The upload path is taken only for POST or PUT to `.../child/attachment`. GET, DELETE and other URLs containing `/attachment` go through the normal request unchanged.
2. A generated file is one the agent produced in the current conversation, including earlier messages. The scope is fixed as described in the Approach.
   Widened after manual testing: a generated file is any file in the conversation's workspace, at any path and depth (not only `artifacts/<turn>/<name>`), except reserved script-runtime paths. The model may name it by exact workspace path or bare name; a bare name shared by different files is refused with the candidate paths listed. Copies of one export (same name, checksum and size, for example the code executor's root file plus its `artifacts/<turn>/` copy) count as one file; the exact-path copy, else the shortest path, is attached. The attachment is named by the file's basename. An upload that is also synced into the workspace counts as the upload only.
3. The model names a file by its exact bare file name. Matching is strict, with no all-files fallback. The attachment keeps that name and the file's bytes. Any file type is accepted.
4. Candidates are generated files plus user uploads (`input_files`). If a name matches both an upload and a generated file, the tool refuses and the error lists both candidates.
5. If the workspace holds several different generated files with the same bare name (for example `artifacts/<turn1>/r.json` and `artifacts/<turn5>/r.json`), the name is ambiguous and the error lists their full paths; the model retries with a path. The earlier "newest wins" rule was dropped on purpose: workspace rewrites overwrite in place and artifact copies are plain paths.
6. A name that matches nothing returns a "not found" error that lists the available file names.
7. An attachment POST/PUT with no file named, or with no files available, returns an explicit error and is never sent as a plain request.
8. Any failure (including an empty file or a failed upload) is a plain error that names the failed file and never implies success. A failure on one file in a multi-file call raises an error naming that file.
9. When generated files are not available (no conversation, for example a workflow tool node or per-user files), the tool returns the error "generated files are not available here". Uploaded files still work there.
10. A same-name attachment on a page adds a new version. This must be verified against the installed atlassian library.
11. The tool description (`tools_vars.py`) and `ConfluenceInput.params` tell the model that generated files are attachable by exact file name.

## Acceptance criteria
- AC1: An agent with Confluence permissions and a conversation can attach a file it generated, in this turn or an earlier one of the same conversation, to the intended page (for example JSON).
- AC2: The attachment keeps the generated file's name and content. Page text does not count.
- AC3: Not found, name clash, upload failure and unavailable generated files each give a clear message that does not imply success. A test shows a name from another conversation returns not found.
- AC4: Text POST/PUT and GET behavior is unchanged.
- AC5: Uploaded files can still be attached. A name that matches nothing is an error and no longer attaches all files.
- AC6: GET and DELETE on attachment URLs are not treated as uploads.

## Non-goals
- No change to `FileToolMixin` or its all-files fallback and empty-file drop.
- No change to the user-upload collector in `core/utils.py`.
- No fix for Jira, XWiki, Azure DevOps or SharePoint. They need a follow-up ticket, not yet created, owner to be set.
- No generated-file support in workflow tool nodes or for per-user files outside a conversation. They need a separate follow-up ticket, not yet created.
- No extension restriction, no cross-conversation access, no path-based file naming.
- No separate attached/not-attached report for partial uploads, no dedicated empty-file message, no extra script-bridge design (it shares `_perform` and its behavior).
- No UI, SDK or REST API change.

## Decisions
- Constructor dependency for generated-file access over filling `input_files` or a workspace lookup inside the tool: how the file reaches the tool.
- Read-only Protocol adapter over `AgentWorkspaceService` with no bytes in listing, over reusing `get_workspace_input_files`: what the dependency looks like.
- Conversation and user bound at tool construction over any model-supplied scope: how other conversations are excluded.
- Fix inside the Confluence tool only over fixing the shared mixin: where strict matching lives.
- Matching in one Confluence-independent unit over code inside the Confluence class: reuse by the follow-up.
- Strict matching with a "not found" error over an all-files fallback: what happens when no file matches.
- Upload path only for POST/PUT to `.../child/attachment` over any URL containing `/attachment`: which requests are uploads.
- Generated file means one from the current conversation, including earlier messages, over other conversations or workspaces: what counts as generated.
- Bare file-name matching over workspace-path matching: how the model names a file.
- Same-name attachment adds a new version, pending library verification: what happens on a name collision on the page.
- A plain error naming the failed file over a separate attached/not-attached report: how failures are reported.
- Empty files use the standard failure message over a dedicated message: how empty files are reported.
- Name clash between an uploaded and a generated file is refused with both candidates listed, over letting either win — user
- Any generated file type is accepted over JSON only — user
- Newest generated file wins when several share a name (AR-2) — dropped on purpose: workspace rewrites overwrite in place and artifact copies are plain paths, so different same-name files are ambiguous and listed by full path; identical copies (same name, checksum, size) count as one
- Workflow tool nodes and per-user files are out of scope, with a "generated files are not available here" error, over supporting them now — user, answers AR-4
- Follow-ups for other tools and for workflows are out of scope.

## Open risks
- Atlassian `attach_content` same-name versioning, request timeout, and the URL used to update an attachment (a PUT to `.../attachment/{id}/data` would not match rule 1) are unverified. The plan settles them and adds tests.
- Whether `ToolkitService` has the conversation id at build time is unverified (`toolkit_service.py:866-891`).
- The two follow-up tickets do not exist yet.
- The attachment path has no existing tests, so it needs a new test base first.
