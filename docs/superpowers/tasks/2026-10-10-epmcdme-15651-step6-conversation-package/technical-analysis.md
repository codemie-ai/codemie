# Technical Research

**Task**: conversation router package split
**Generated**: 2026-10-10
**Research path**: codegraph

---

## 1. Original Context

Implement delivery step 6 of Jira EPMCDME-15651 ("Split oversized REST API router modules into smaller per-concern modules") on branch EPMCDME-15651_split-router-modules (HEAD 94732a6202). Split src/codemie/rest_api/routers/conversation.py (1,243 lines) into the same-named package src/codemie/rest_api/routers/conversation/. Move-only: handler bodies, decorators, signatures and dependencies stay byte-identical; no API change. The package __init__.py builds one combined `router` (same prefix/tags/dependencies as today) from bare `APIRouter()` sub-routers and exports only `router` (`__all__ = ["router"]`), no re-exports. Route modules are per concern (target <=500 lines each); private support modules have a leading underscore. src importers (main.py, routers/ide.py and any others) and tests that import or patch `codemie.rest_api.routers.conversation.*` must be retargeted to the defining submodule (known test files: tests/codemie/rest_api/routers/test_conversation.py, test_conversation_pagination.py, test_conversation_analysis.py, test_conversation_update_router_sites.py, test_assistant_refresh_flow.py, tests/codemie/service/test_upsert_conversation_with_history.py; also check codemie-enterprise and sibling repos read-only). Guard tests in tests/codemie/rest_api/router_guards/ (byte-exact app.openapi() snapshot, route-precedence baseline, hermetic app probe) must stay byte-identical; classes defined in the router file could rename OpenAPI components if moved, and a class that stays defined in the router module would rename its component when it moves between modules, so list every class and decide where it lives. Logic moves into services are NOT in this step (step 7). Auth/access-check code moves here, so the MR will need /sanity.
Ticket body and user-resolved criteria: docs/superpowers/tasks/2026-10-08-epmcdme-15651-split-router-modules/{ticket.md,spec.md}. Lessons and proven method from steps 2-5 (read the step 4 and step 5 sections of local/router-split/HANDOFF.md and the step 5 technical-analysis for the expected shape): docs/superpowers/tasks/2026-10-09-epmcdme-15651-step5-projects-package/technical-analysis.md. Research needs: route inventory (count, paths, methods, decorator-level dependencies, router prefix/tags, any streaming/SSE endpoints), proposed module grouping with line counts and include order that keeps app.routes order unchanged (route order inside the block must stay strict if at all possible), shared helpers/constants/classes and which module each belongs in, every src importer and every test import/patch of routers.conversation (count per test file and per target name), name collisions that could change OpenAPI component names, route-precedence overlaps and whether any overlapping pair spans proposed modules, auth/access-check sites, pre-existing gaps to preserve (do not fix), risk indicators.

---

## 2. Codebase Findings

Tools: Read, Glob, codegraph; no grep, nothing run. The router and the 5 test files were read in full; counts are hand tallies (plus or minus 3); tree-wide absence is unproven.

### Existing Implementations

`src/codemie/rest_api/routers/conversation.py`: 1,243 lines (ticket's 1,187 is stale), 29 routes, 40 functions (29 handlers, 11 helpers), **0 classes**, 8 module-level assigns. **No `from __future__ import annotations`** (steps 4-5 files had it). `logger` is the shared `codemie.configs.logger` (`getLogger("codemie")`): no logger rename. Header L1-13 has curly quotes. `router = APIRouter(tags=["Conversation"], prefix="/v1", dependencies=[Depends(authenticate)])` L81-85; decorators carry full `/conversations...` paths, none sets `tags` or `operation_id`; R6 `response_class=StreamingResponse`, R15/R16 `dependencies=[Depends(admin_access_only)]`, R17 `responses=`, R28 `summary=`, R29 `dependencies=[Depends(authenticate)]`; 26 handlers also take `user: User = Depends(authenticate)` (not R15, R16, R29).

Routes by contiguous source run (BASE lines, inner helpers included; paths after `/v1`; `cid`=`{conversation_id}`, `hi`=`{history_index}`; [R]/[W]/[D] = `Ability(user).can` READ/WRITE/DELETE):
- L116-356 (+ L88-99): R1 GET /conversations/search; R2 GET /conversations/new; R3 GET /conversations/cid [R]; R4 GET .../cid/files [R]
- L359-464: R5 POST .../cid/abort [W]; R6 GET .../cid/stream [R], NDJSON
- L467-530: R7 DELETE /conversations; R8 GET /assistant-folders; R9 DELETE /assistant-folders/{assistant_id}; R10 DELETE /conversations/cid [D]
- L533-597: R11 DELETE .../cid/history/hi [W]; R12 DELETE .../cid/history [W]; R13 PUT .../cid/history/hi [W]
- L600-663: R14 POST .../cid/finish [W, 403]; R15 POST /admin/conversations/finish-bulk; R16 GET /admin/conversations (admin_access_only)
- L666-773: R17 PUT .../cid/history [W if exists]; R18 GET .../cid/history/hi/{message_index}/export [W]
- L776-888: R19 PUT /conversations/cid [W]; R20 GET /conversations; R21 POST /conversations (`create_conversation`)
- L891-999: R22 GET /conversations/folders/list; R23 PUT .../folders/move; R24 DELETE and R25 PUT /conversations/folder/{folder:path}; R26 POST /conversations/folder
- L1002-1154: R27 GET .../cid/export [R via helper]
- L1157-1243: R28 PUT .../cid/messages/hi/output [R]; R29 POST /speech-recognition (UploadFile)

Every helper sits inside its run, except `_use_global_models_for_conversation` L88-99 (R2, R3) and the constants L102-113. Only `CONVERSATION_NOT_FOUND_MSG` L107 (R5, R6, R18) is shared across candidate modules. `conversation_monitoring_service` L102 is used by nothing (`routers/user.py` L48 builds its own). `CONVERSATION_NOT_FOUND_MESSAGE/_HELP` come from `routers/feedback.py`. Function-local imports stay local; R5's local `GenerationManager` shadows the module-level one that R6 uses.

### Architecture and Layers Affected

REST router layer: the router file, two `src` import sites, five test files. Services (`ConversationService`, `GenerationManager`, `MessageExporter`) and models stay put.

### Integration Points

- `main.py`: L85 `conversation,` in the multi-line `from codemie.rest_api.routers import (...)`; L1041 `app.include_router(conversation.router)`, unconditional, after `background_tasks.router` L1040, before `conversation_analysis.router` L1042.
- `routers/ide.py` L32 `from codemie.rest_api.routers.conversation import create_conversation`, called directly at L88 (`create_conversation(request, user)`). Only other src importer found (`routers/__init__.py` is empty). `codemie-enterprise`: codegraph returned nothing for the module (name-based, unproven); `codemie-mcp-connect-service` is not indexed; `codemie-ui`, `codemie-public-skills` not searched.
- Streaming: no SSE. R6 is NDJSON (sync generator, 60 s queue timeout); R18, R27 return file `StreamingResponse`s.
- OpenAPI: no class is defined in the file, so no component key can gain a `codemie__rest_api__routers__conversation__` prefix; its models live in `rest_api/models` or `core/models`. R29's multipart component is expected as `Body_recognize_speech_v1_speech_recognition_post` (named from function and path; not located in the baseline).
- Route precedence: `route_precedence.json` L97-131 holds 7 conversation pairs; my hand derivation gives the same 7: GET R1, R2 before R3; DELETE R11, R12 before R24; PUT R13, R17 before R25; PUT R25 before R28. `app.routes` block offsets were not measured.

### Patterns and Conventions

Inherited from steps 2-5: bare `APIRouter()` per route module; combined router in `__init__` with `include_router` and `__all__ = ["router"]` (analytics `__init__` is the precedent for a router-level `Depends(authenticate)`); annotation and default names imported at module level of the handler's module; private modules never import route modules; underscore names imported in `src/` only from private modules. Scratch tools in `local/router-split/step5/` (gitignored) hard-code `projects` names, module ranges, a future-import check in `size.sh` and a package-prefix predicate.

Access-check sites: 14 `Ability(user).can` routes (marked above), helper `_validate_conversation_access_and_get_assistants` (R27), `admin_access_only` on R15/R16, `authenticate` at router and handler level, `raise_access_denied` (401) and `raise_forbidden` (403, R14 only). The other 15 routes (R1, R2, R7-R9, R15, R16, R20-R26, R29) have no per-resource check; scoping is by `user.id` or inside `ConversationService`.

Pre-existing, to preserve: R28 mutates workflow state with READ only; R18 export needs WRITE while R27 needs READ; R14 answers 403 where the rest answer 401; in R4, R10-R13 `Ability` runs before `if not conversation` (`Conversation.get_by_id` documents a `KeyError`, so those branches look dead); R17 lets any caller create a conversation under a client-chosen id (WRITE check only if it exists); R19 docstring says "Create"; R21 rebuilds `AssistantDetails` inline; `conversation_monitoring_service` unused; R5 swallows DB errors when marking INTERRUPTED; `{folder:path}` shadows a folder named `history`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

No guide cites `routers/conversation.py` (read: project-structure, endpoint-conventions, quality-gates; others unopened).

### Architectural Decisions

Ticket target `conversation/`: crud, history, streaming, folders, messages (29 routes, matches). Spec criteria 1-8 as in the step 5 analysis.

### Derived Conventions

Patch the module whose code resolves the name; `<Name>.<attr>` keeps its tail; imports take the defining submodule; stale-patch detector, FLIP and byte-identical AST proof as in steps 4-5.

---

## 4. Testing Landscape

### Existing Coverage

| File | Lines | Patch strings | Imports / attribute uses |
|---|---|---|---|
| routers/test_conversation.py | 865 | 38 | alias `conversation_router`; `.authenticate` x2, `.admin_finish_conversations_bulk` x1, `.list_admin_conversations` x3, `.router.routes` x1 |
| routers/test_conversation_pagination.py | 864 | 66 | alias `conversation_router`; `.authenticate` x1 |
| routers/test_assistant_refresh_flow.py | 435 | 9 | `CONVERSATION_ABORTED_SUCCESS_MSG` |
| routers/test_conversation_update_router_sites.py | 127 | 2 | `abort_conversation_generation` |
| service/test_upsert_conversation_with_history.py | 198 | 2 | `upsert_conversation_history` |
| routers/test_conversation_analysis.py | 36 | 0 | none; its 2 literals name the sibling `routers.conversation_analysis` |

Totals: 117 patch strings, 5 imports, 8 attribute uses (ticket's 79 is stale). 109 are `<Class>.<attr>`; 8 are whole-name (`Ability` x5, `MessageExporter` x2, `GenerationManager` x1). By target: `Ability.can` 29, `Conversation.find_by_id` 28, `Assistant.get_by_ids` 21, `ConversationService.get_conversation_history_slice` 7, `Conversation.get_by_id` 5, `Conversation.search_by_name_and_user` 4, `ConversationFolder.search_by_name_and_user` 4, `Conversation.get_all_conversations_admin` 3, `Conversation.get_user_conversations` 2, `ConversationService.get_user_conversations_paginated` 2, one each `ConversationService.{finish_conversations_bulk, update_conversation, upsert_conversation_with_history}` and `ConversationFolder.get_all_by_fields`. By handler: R27 48; R1 8 + R3 24; R14-R16 11; R5-R6 11; R18 5; R20 4; R19 3; R17 2; R22 1.

### Testing Framework and Patterns

pytest, `pytest.mark.asyncio`, `@patch`, module-level `TestClient(app)`, `app.dependency_overrides[authenticate]`; some tests call handlers as plain functions; `test_admin_conversation_routes_use_admin_access_only` reads `conversation_router.router.routes[*].dependant.dependencies`. Guards: `poetry run pytest tests/codemie/rest_api/router_guards -n 0 --show-capture=no -q`; the app probe patches no router module.

### Coverage Gaps

No handler test in the known files for 15 routes: R4, R7-R13, R21, R23-R26, R28, R29 (R10 only via the model cascade test). `patch("...conversation.GenerationManager")` in the sites test is inert at BASE (R5 imports the class locally). Other tests may patch the module (13 of 113 files in `tests/codemie/rest_api/routers` were not listed).

---

## 5. Configuration and Environment

### Environment Variables

None read by the router (no `config` import). Hard-coded 60.0 s stream queue timeout; limits from `codemie.service.constants`; header names from `codemie.core.constants` (R17).

### Configuration Files

`pyproject.toml`: ruff E,F,B,N,C4,G,T20,RSE,SIM,C,W,PERF,ISC, line length 120, mccabe 16; no per-file ignore matches a conversation module name. Baselines regenerate only via `regenerate_baselines`, never from a failing run.

### Feature Flags and Deployment Concerns

Router included unconditionally. No pipeline config: the MR needs `/sanity` and a `## Test harness` block. Untracked `opencode.json.lock`, `skills-lock.json` are never staged.

---

## 6. Risk Indicators

- Prefix trap: `routers/conversation_analysis.py` and `test_conversation_analysis.py` share the letters `routers.conversation`; detectors, FLIP, LEFTOVER and the route-dump predicate must match `== pkg` or `startswith(pkg + ".")`.
- 109 of 117 patch strings are `<Class>.<attr>`: a mis-aimed literal passes silently when a sibling module binds the class (FLIP in steps 4-5: 12 of 55, 144 of 314). Only the 8 whole-name patches are module-sensitive; `GenerationManager` can only go to the module binding it (R6's) and stays inert.
- `conversation_router.authenticate` (3 sites) resolves after the split only because `__init__` must import `authenticate`; the 4 handler attribute uses and 3 from-imports fail loudly.
- 15 routes have no handler test; only COMPARE (c) and the guards prove their dependencies.
- `/sanity` scope: 14 `Ability` routes, the access helper, `admin_access_only` x2, the 401/403 split, router-level plus 26 handler-level `authenticate`; carry the pre-existing gaps (Section 2) into the MR as non-goals, notably R28 READ-on-write and R18 WRITE-on-export.
- Unverified: other importers and tests (no grep), `app.routes` block offsets, the `Body_recognize_speech...` key.
- Speculative: grouping S (strict order, include order = source order, so the block and all 7 pairs are unchanged by construction): `retrieval` R1-R4 (about 310 lines), `streaming` R5-R6 (150), `deletion` R7-R10 (110), `history` R11-R18 (290), `crud` R19-R21 (165), `folders` R22-R26 (150), `messages` R27-R29 (300), `_common` (`CONVERSATION_NOT_FOUND_MSG`, unused monitoring instance; 35), `__init__` (50); plus or minus 40, cap not binding. Cohesion cost: assistant-folder R8-R9 sit in `deletion`, `history` holds finish R14 and admin R15-R16, `messages` holds export R27 (optional 8th module). 5 of 7 pairs span modules (R11-R13, R17 before R24/R25; R25 before R28); 2 stay in `retrieval`.
- Speculative: grouping B (ticket names; crud R1-R4, R7, R10, R14, R19-R21; history R11-R13, R17, R18; folders R8-R9, R22-R26; messages R27-R29; streaming; admin R15-R16), include order crud, streaming, history, admin, folders, messages, keeps the 7 pairs (needs history < folders < messages) and per-path method order but moves 17 of 29 positions (step 3 precedent), so the order criterion would need restating.
- Speculative: under S the 117 patch strings split `messages` 48, `retrieval` 32, `history` 18, `streaming` 11, `crud` 7, `folders` 1, `deletion` 0; `abort_conversation_generation`, `CONVERSATION_ABORTED_SUCCESS_MSG` and the 5 `Ability` whole-name patches go to `streaming`; `upsert_conversation_history` and the 4 admin handler uses to `history`; `ide.py`'s `create_conversation` to `crud`.

---

## 7. Summary for Complexity Assessment

A 1,243-line router (29 routes, 40 functions, no class, no env reads) with one importer besides `main.py` (`ide.py`). Router layer only: no class means no OpenAPI component rename, the 7 overlapping pairs hold if the include order follows source order, and the shared logger plus absent future import remove two step 4-5 chores. Roughly seven route modules plus a tiny `_common` and `__init__` are likely (speculative). The `conversation_analysis` prefix is a new trap.

Test surface is 5 files, 117 patch strings, 5 imports and 8 attribute uses (hand-counted), mostly `<Class>.<attr>` patches that are easy to mis-aim silently; 15 routes have no handler test. Access-check code moves (14 `Ability` routes, 2 admin-gated routes), so `/sanity` applies. Fifth use of the step 2-5 method with tooling on disk; step 5 scored M 16/36 for 14 routes and 313 strings.

Key risks: strict order forces a cohesion trade-off (S vs B), silent-pass patches, an unverified importer scan, and pre-existing authorization oddities to preserve and call out, not fix.

---

## 8. External References

All resolved and read. `ticket.md`: item 6 target `conversation/`: crud, history, streaming, folders, messages; "conversation 79 (5)" patch refs (stale); `search` and `new` precede `{conversation_id}`; AC 5 no underscore imports across router modules; AC 8 `/sanity`. `spec.md` (step 0): 500-line cap binds, snapshot identical, precedence baseline = computed pairs, export only `router` with `ide.py` re-pointed to submodules, tests import from submodule paths, move-only. Step 5 `technical-analysis.md` and `HANDOFF.md` (steps 4-5): patch rules (whole-name patches bind per using module, `<Name>.<attr>` keeps its tail), the 4-module strict-order precedent, FLIP and ASTMOVE proofs, `/sanity` plus `## Test harness` block, scratch tools in `local/router-split/step5/`.
