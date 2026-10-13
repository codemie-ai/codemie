# EPMCDME-15651, step 6: conversation router package

## Purpose

Replace `routers/conversation.py` (1,243 lines, 29 routes R1-R29, prefix `/v1`, router-level `authenticate`, 40 functions, no classes) with a package, no API change. Move-only, reviewable with `git diff --color-moved`. Rules follow steps 0, 4 and 5.

## Scope

`routers/conversation/` replaces the file. Bodies, decorators, docstrings, local imports and constants move byte-identical, in source order within each module; no `from __future__ import annotations` is added (the source has none); every file copies the source's 13-line header. Cap 500 lines. Old lines in parentheses, estimates in brackets.

Route modules in include order. Strict order needs each to hold one contiguous source range, so include order is source order:

- `retrieval` R1-R4 (88-99, 116-356) [310]
- `streaming` R5-R6 (359-464) [150]
- `deletion` R7-R10 (467-530) [110]
- `history` R11-R18 (533-773) [290]
- `crud` R19-R21 (776-888) [165]
- `folders` R22-R26 (891-999) [150]
- `messages` R27-R29 (1002-1243) [300]

Each helper and constant moves with the one module that reads it. `_common` [35] holds the exceptions: `CONVERSATION_NOT_FOUND_MSG` (107; read by R5, R6, R18) and the unused `conversation_monitoring_service` (102), kept as dead code.

Why: the ticket's five names would move 17 of 29 positions; contiguity forces seven modules. Accepted cohesion costs: R8-R9 in `deletion`, R14-R16 in `history`, R27 in `messages`. A module over 500 lines is cut at a route boundary; a cut that would reorder a route: stop and ask.

## Constraints

- `__init__.py`: `router = APIRouter(tags=["Conversation"], prefix="/v1", dependencies=[Depends(authenticate)])` as at 81-85, seven `include_router` calls in the order above, `__all__ = ["router"]`, and a comment that the order is load-bearing (5 of the 7 precedence pairs span modules). Route modules use a bare `APIRouter()`.
- In `src/`, underscore names are imported only from private modules; `_common` imports no route module; shared names are imported by name.
- The source imports the shared `codemie` logger (26); each module that logs repeats that import, so nothing is renamed (unlike steps 2-5). `streaming` keeps the module-level `GenerationManager` (R6) and R5's local import (378).
- `ide.py:32` imports `create_conversation` from `conversation.crud`; `main.py` is unchanged.
- Measured with `routers\.conversation\b`: 117 patch strings, 5 imports, 8 alias attribute uses, 5 test files.
- Prefix trap: `routers/conversation_analysis.py` and `test_conversation_analysis.py` share the letters `routers.conversation`. Every detector, flip check, leftover scan and route-dump predicate matches whole dotted components, `== pkg` or `startswith(pkg + ".")`; regexes use `routers\.conversation\b`. Both sibling files stay byte-identical.

## Test retarget rules

Patch the module whose code resolves the name on the exercised path. Whole-name patches bind per using module: `Ability` (5) and `GenerationManager` (1, inert at BASE, stays inert) to `streaming`; `MessageExporter` (2) to the module of the handler called. The 109 `<Class>.<attr>` patches keep their tail and go to the handler's module; expected split `messages` 48, `retrieval` 32, `history` 18, `streaming` 11, `crud` 7, `folders` 1. Dotted patches share the class, so a mis-aimed one passes silently; the flip check is the net.

Imports take the defining submodule (tests may import underscore names). Alias uses, which an AST scan must find: `conversation_router.authenticate` (3) becomes a direct import from `codemie.rest_api.security.authentication` (same object; the package keeps exporting only `router`); `admin_finish_conversations_bulk` (1) and `list_admin_conversations` (3) use a `history` alias; `router.routes` (1) stays.

## Route order and OpenAPI

A one-off dump script in the plan (not committed; gitignored `local/router-split/step6/`; BASE dump on the pre-move HEAD) checks the sequence.

The 7 precedence pairs (`route_precedence.json` 97-131): GET `search`, `new` before `{conversation_id}` (inside `retrieval`); DELETE and PUT `history`, `history/{hi}` before `folder/{folder:path}` (`history` before `folders`); PUT `folder/{folder:path}` before `messages/{hi}/output` (`folders` before `messages`).

`openapi.json` and `route_precedence.json` stay byte-identical, nothing regenerated; no class is defined here, so no component key moves. If a guard fails: stop, report the diff, ask; never run `regenerate_baselines`.

## Acceptance criteria

1. `conversation.py` is gone; the package holds `__init__.py`, 7 route modules and `_common`, each at most 500 lines (`wc -l`) with the full header; check its last line, `# limitations under the License.` (the license check misses a truncated header, as in step 4).
2. Dump before and after: (a) 29 rows (methods, path, name, status code, tags, dependency call names) equal element-wise, in order, ignoring the module field; (b) one contiguous block at the same `app.routes` offsets with the same neighbors, rest unchanged; (c) `router.prefix == "/v1"`, `.tags == ["Conversation"]`, `.dependencies == [authenticate]`, R15 and R16 read `authenticate` then `admin_access_only`; (d) no endpoint in `__init__`; (e) control: the predicate finds 29 at BASE and none from `conversation_analysis`.
3. `git diff --color-moved` shows only moved blocks, imports, headers, `APIRouter()`, `include_router`, `__all__` and logger imports. An AST check finds every top-level def, async def and assignment except `router` (47: 40 defs, 7 assignments) byte-identical, decorators included, in exactly one new module.
4. No `routers.conversation.<name>` in `src/` or `tests/` unless `<name>` is `router` or a real submodule, and no alias attribute use but `router`; each target module reads its `<name>` in a function body. Flip check: each literal pointed once at a sibling module binding the name; silent passes go in the handoff.
5. No `src/` module imports an underscore name from a module defining a `router`; nothing under `service/` or `rest_api/handlers/` imports `routers/conversation`.
6. Each of the 9 modules imports alone in a fresh interpreter; the guarded app builds hermetically; guards pass, baselines unchanged.
7. `make ruff` passes. Tests pass in the 5 edited files, the untouched `test_conversation_analysis.py`, `tests/codemie/rest_api/routers`, `tests/unit/routers` and `router_guards`.
8. The handoff says the eventual MR must ask a reviewer for `/sanity` and carry a `## Test harness` block. Auth code that moves: router-level `authenticate`; 14 `Ability(user).can` routes (R3-R6, R10-R14, R17-R19, R27 via its access helper, R28) and that helper; `admin_access_only` (R15, R16); `raise_access_denied` 401 vs `raise_forbidden` 403 (R14); 26 handler-level and 1 decorator-level `Depends(authenticate)`.

## Non-goals

- Moving logic into `service/` (step 7); renaming; changing paths, tags, operationIds, response models or dependencies; deleting dead code.
- Fixing pre-existing behavior, carried as MR notes: R28 changes workflow state on READ; R18 needs WRITE where R27 needs READ; R14 answers 403, the rest 401; R4 and R10-R13 check `Ability` before `if not conversation` (dead branch); R17 lets any caller create a conversation under a client-chosen id; R19's docstring says "Create"; R21 rebuilds `AssistantDetails` inline; R5 swallows DB errors marking INTERRUPTED; `{folder:path}` shadows a folder named `history`.
- New tests, `main.py`, guard code, baselines; commits or MR beyond the flow's.

## Open risks

- 15 routes have no handler test (R4, R7-R13, R21, R23-R26, R28, R29): AC 2, 3 and 6 are their net.
- AC 4 is necessary, not sufficient: the flip check only lists silent passes.
- Line counts are estimates; the cap does not bind.
