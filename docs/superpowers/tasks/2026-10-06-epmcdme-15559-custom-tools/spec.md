# EPMCDME-15559 Run a Script from a Workflow Step - Spec (codemie repo)

Scope: backend only. No UI, no models, no migrations. Ticket decisions (except the alias part of decision 6) and architecture spec decisions are not re-opened. Revised after architecture reviews round 1 and 2 (`local/architecture-review/2026-10-06-epmcdme-15559-spec*/report.md`).

## Goal

A workflow tool step can run `execute_workspace_script` against the run's workspace, and files given at run start are visible in that workspace. `integration_alias` is deferred (see Deferred).

## Behavior

1. **Tool-step resolution (confirm, no new code expected).** `ToolNode` passes the execution id as the conversation id. The workspace toolkit builds the script registry with a workflow project through `_workflow_project()` (`src/codemie_tools/data_management/workspace/toolkit.py`). The step output is the whole script-tool JSON (`message`, `output`, `workspace_files`), and `output_key` stores it unchanged. A real script run end to end needs a jobs-mode sandbox and stays a manual real-run check (ticket decisions 1, 2, 7).

2. **Run-file registration helper.** Add a public `AgentWorkspaceService.register_run_files(execution_id, file_urls, user)`. It applies rules (a) and (b) of item 3, then calls `sync_uploaded_files`. `create_workflow_execution` calls it once. Story 15560 and a later resume fix reuse it.
   - Registration is idempotent within one execution id only. A failure after registration (conversation write, execution save, parent update) now removes the workspace and its file rows best-effort (`AgentWorkspaceRepository.delete_by_execution_id`; cleanup errors are logged and never mask the original error). A client retry mints a new execution id, so the first workspace stays orphaned in that case. This is accepted.
   - A database error in registration fails run creation (user decision).
   - A missing blob at run start is a rejection (not a silent zero-byte registration). It is raised as a client error.
   - The full-blob read (checksum) on the create request is accepted and unmeasured. A chat start reads each blob up to three times (router sync, chat branch, run files).

3. **Two rules, not one predicate.**
   (a) Shape: a pure `is_safe_blob_ref(file_object)` in `codemie/service/file_service/`. It rejects empty, absolute, `..`, backslash, bare `.` and control characters. It is imported by the download route (`files.py`, replacing the router-private `_points_outside_owner`) and by `AgentWorkspaceService`. A service never imports from a router.
   (b) Ownership: a file may be registered at run start if and only if the user could download it, under the same rules A-E the download route applies (own id, own `workspace-<id>`, and the other download rules; the share-grant rule E needs a share token that a run-start request does not carry, so it does not apply here and a file taken from a shared conversation is rejected with 400, decided 2026-10-06). Extract those rules to the service layer and reuse them. They are not re-invented, and "foreign owner" is not `owner != user.id`.
   - The router returns 400 for a token that is malformed, unsafe or not downloadable. `sync_uploaded_files` keeps skip-and-log as defence in depth.
   - The router also validates a file-count cap (value in config, default chosen at planning). The existing `_validate_workflow_supports_files_and_raise` checks only the bedrock case, so these checks are new.
   - **Callers of `sync_uploaded_files` (five):** `rest_api/handlers/assistant_handlers.py:274`, `rest_api/routers/workflow_executions.py:263`, `service/conversation_service.py:472`, `service/workflow_service.py:313` and `service/agent_workspace_service.py:138` (`create_workspace` seeding). Shape rule (a) applies inside `sync_uploaded_files`, so chat behaviour changes too: unsafe tokens are skipped there. Rule (b) applies in `register_run_files` and in the router validation. Accept tests protect the other callers.
   - Coverage limit: the checks cover run-start registration only. `register_generated_files` (`sandbox:` URLs from model output), virtual conversation files built from history, and `build_unique_file_objects_list` (`tool_node.py:195`) are follow-ups. The router 400 keeps a bad token out of history.

4. **Ordering in `create_workflow_execution`.** The true current order (`workflow_service.py:236-348`) is: augment input, `_get_or_create_conversation` (saves a new conversation), mint `execution_id`, build the execution, `conversation.update()` with a `workflow_execution_ref`, the chat-branch sync, then `execution_config.save`. Validation lives in the router, not in the service. Reorder so the execution id is minted and `register_run_files` runs before `_get_or_create_conversation` and `conversation.update()`. A registration failure then leaves no conversation message and no dangling `workflow_execution_ref`. This changes the chat path only on failure. Non-chat runs register too, with or without a conversation, when `file_names` is non-empty.

5. **Router sync kept.** The router's own sync (`workflow_executions.py:263`) is redundant for the conversation path (the chat branch already syncs). It is kept in this story and named as a follow-up, together with a test that the conversation workspace is still seeded.

6. **Resume gap.** `resume_workflow_execution` takes `file_names` (`workflow_executions.py:424`). Registration at resume is out of scope. Files given only at resume are invisible to script steps, so a script step on a resumed run fails with "script path missing". A different resuming user may get a separate workspace (unverified). A later fix is one call to `register_run_files`. Proposed line for the ticket's Out of Scope: "Files supplied when a paused run is resumed are not registered in the run's workspace."

7. **Automatic lookup in a tool step.** A script call in a tool step uses the running user's automatic lookup in the workflow's project, not the creator's USER-type integrations. The lookup still reaches PROJECT-type settings of the workflow's project (the last link of the settings chain, no access check). That is existing behaviour and is unchanged here. An access check is a follow-up (to be created, author sign-off needed; no ticket key invented). Webhook and cron runs run as the trigger user (ticket decision 4).

8. **Fix to old code.** `VirtualAssistantService.delete_by_execution_id` (`virtual_assistant_service.py:152`, from the initial commit `afc61fa12`, not story 1) snapshots keys and then runs `del cls.assistants[key]`. That raises `KeyError` when another thread deletes in between, in the workflow's unguarded final cleanup (`workflow.py:1027`). Use `pop(key, None)` over a key snapshot.

9. **Typing.** Strict typing (no `Any`, `X | None` unions) in all new code.

## Tests

Type tags: **new** (fails today) and **characterisation** (passes on unchanged code, guards behaviour).

- AC 1, 3, 6: unit-level seam tests (tool node to a fake workspace script tool, script file persistence in the workspace fake, `ProjectScope` resolving a catalog tool with the running user). The real script run is the manual jobs-mode check.
- AC 2 (new): registration with a conversation, without a conversation, with empty `file_names` (no workspace), database error fails creation, missing blob rejected, router 400 and file-count cap.
- AC 2 order (new): a registration failure leaves no conversation message and no `workflow_execution_ref`.
- AC 4 (characterisation): a missing script path fails the step with a clear message.
- AC 5 (characterisation): a failing script reports the step failed with the script's output.
- AC 7 (characterisation): an assistant step's scope is only its assistant's tools, and a request field never widens it.
- AC 8 (new): a global workflow created by A and run by B, with a script call in a tool step, resolves through B's lookup. Variant: a global workflow tool step with a pinned integration alias on the step itself (`ToolNode._owner_user_id` returns the creator then) asserts which user `ScriptToolRegistry` holds. A webhook or cron run asserts the trigger user. A PROJECT-type settings fixture asserts the documented reach (reference for the follow-up).
- AC 9 (new): `is_safe_blob_ref` reject cases (empty, absolute, `..`, backslash, bare `.`, control chars). Ownership accept cases (own id, own `workspace-<id>`) and reject cases (foreign owner, share-grant file without a token) in `register_run_files` and at the router. The download route uses the same functions.
- AC 10 (characterisation, guard for later stories): `get_current_workflow_id()` is visible in a channel handler thread. The tool-step path does not consume it (decision 7).
- AC 11 (new): the `delete_by_execution_id` regression test forces the interleaving (patch the dict to delete between snapshot and delete, or a deleter thread).

Keep `test_error_code_contract.py` green. Follow the seam-test convention in `.ai-run/guides/testing/testing-patterns.md`.

## Acceptance criteria

1. A tool step runs a script supplied as a run input, and its output (the whole script-tool JSON) reaches later steps. The real-run check is manual.
2. Run-start files are registered by reference under the execution id, also without a conversation, before any conversation write. `input_files` and attachments are unchanged. A database error fails run creation and leaves no conversation message. A missing blob is rejected.
3. Files changed or created by the script persist for later steps.
4. A missing script path fails the step with a clear message.
5. A failing script reports the step failed together with the script's output.
6. In a tool step, a call to any non-excluded catalog tool succeeds with the running user's automatic lookup in the workflow project. An unknown or excluded tool gives a clear error.
7. In an assistant step, only the assistant's own tools are available, and a request field never widens the scope.
8. A global workflow tool step uses the running user's lookup and never the creator's USER-type integrations. PROJECT-type settings of the workflow's project remain reachable (existing behaviour).
9. A file may be registered at run start only if it passes the shape rule and is downloadable by the user. The router returns 400 otherwise, and enforces a file-count cap.
10. The workflow id reaches the channel handler thread (guard test).
11. `delete_by_execution_id` no longer raises `KeyError` under concurrent deletes.

## Deferred

`integration_alias` is deferred out of this story to the future story "Integration Control for Scripts". There is no alias in the payload, SDK, scope or resolver, no unknown-alias error, no alias refusal in `ToolListScope` or `NoScope`, and no `CallSettings` carrier. The call keeps `name` only.

Adding the optional field later is additive on the wire. An old backend answers `bad_arguments` for an unknown field, so it fails closed. In code it touches about eight places in five modules, together with story 13: `_parse_payload` and `authorize_tool_call` (`handler.py`, `authorizer.py`), four `resolve` signatures (`ScriptScope`, `NoScope`, `ToolListScope`, `ProjectScope`), `WorkflowToolResolver`, `resolve_workflow_tool`, and `owner_user_id`, which is not stored on `VirtualAssistant`. Build an empty `CallSettings` carrier earlier only if story 13 is within a quarter.

This needs author sign-off and updates to Jira and the architecture spec. The destination story has no Jira ticket yet. Affected documents:
- Ticket EPMCDME-15559: AC 8, AC 10, decision 6 (alias part), sub-task 4, the Complexity paragraph (scored L 23/36 with the alias) and Architecture notes.
- Architecture spec: §5.1, §5.3, §7.3, §7.5, the story-table row, D19 (it says `call_tools` takes `integration_alias` as built, which the SDK does not), D21, D28, and the v5 change log.
- Workflow-script-step story, analysis and complexity files, and the epic story and analysis.
- `script-integration-control/story.md` lines 18-21 (it says it builds on "story 3 lets a script call with an alias", now false, which enlarges that story).
- Code comment at `handler.py:111` ("story 3 adds one").

Follow-ups, each "to be created, author sign-off needed" (no ticket keys invented): the alias and creator-pinned story, the PROJECT-type access check, removal of the router's redundant sync, resume registration, protection of the other workspace routes (item 3), cleanup of execution workspaces.

## Non-goals

- No `integration_alias`, no `CallSettings` carrier, no call-request value object.
- No creator-pinned integrations and no alias for assistant or chat tools.
- No access check on PROJECT-type settings.
- No new error code and no new authorizer branch.
- No call-event or analytics work (EPMCDME-15566).
- No workflow-attached files (EPMCDME-15560) and no skill scripts.
- No files for child workflows.
- No registration of files at resume.
- No removal of the router's redundant sync.
- No guard on the other workspace routes (generated files, history files, tool-step file loading).
- No UI changes and no documentation page for script authors.
- No moving of `input_files` or attachment consumers to the workspace.
