# EPMCDME-15651 Step 6: Conversation Router Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace `routers/conversation.py` (1,243 lines, 29 routes R1-R29, prefix `/v1`, router-level `authenticate`, 47 top-level names besides `router`, no classes) with the package `routers/conversation/` (7 route modules, private `_common`, `__init__`; each at most 500 lines). Retarget `ide.py` and 117 patch strings, 5 imports, 8 alias uses in 5 test files. No API change.

**Architecture:** Move-only. Task 1 `git mv`s the file to `conversation/__init__.py` unchanged; Task 2 extracts `_common`; Tasks 3-9 extract route modules tail first (messages, folders, crud, history, deletion, streaming, retrieval), each adding its `include_router` line at the bottom of `__init__`, above the earlier ones. Inline routes register before the includes and each extracted block is the tail of the inline run, so `app.routes` equals BASE order after every commit. Commit per task using the repository's existing convention (`EPMCDME-15651: Capital description`).

**Spec:** `docs/superpowers/tasks/2026-10-10-epmcdme-15651-step6-conversation-package/spec.md`. `BASE` = `94732a6202`.

## Global Constraints

- Bodies, decorators, docstrings, local imports and constants move byte-identical, in source order, by script (never retyped); only imports, headers, `APIRouter()`, includes and `__all__` change. Each new `.py` starts with the verbatim 13-line header (curly quotes; last line `# limitations under the License.`) and has NO `from __future__ import annotations` (the source has none), so every annotation and default name is module-level in its handler's module.
- `__init__` keeps the existing `router = APIRouter(...)` statement; route modules use a bare `APIRouter()`; `__all__ = ["router"]`, no re-exports. Route modules never import `__init__` or each other; `_common` imports no route module. A module that reads `logger` repeats `from codemie.configs import logger` (no rename).
- Prefix trap: `routers/conversation_analysis.py` and `test_conversation_analysis.py` share the letters `routers.conversation`. Every predicate matches whole dotted components (`== pkg` or `startswith(pkg + ".")`, regexes `routers[./]conversation\b`); both files stay byte-identical.
- No edits to `main.py`, guard code or baselines; never `regenerate_baselines`; if GUARDS fails on `openapi.json` or `route_precedence.json`: stop, report the diff, ask. Pre-existing gaps (spec Non-goals) stay; no logic into `service/`; no new tests. `make ruff`, whole-suite gates, sonar, browser checks and Jira/MR work belong to Stage 7 and later.
- Stage only the task's files by path (never `git add -A`, `.gitignore`, the task dir, `opencode.json.lock`, `skills-lock.json`, anything under `local/`); no push, no MR.
- Before Task 3 query context7 for FastAPI `include_router` (prefix, tags, dependencies merge); `COMPARE` proves it.

## Module map

BASE lines. `CONVERSATION_NOT_FOUND_MSG` (R5, R6, R18) is the only symbol read by more than one module.

| Module | Routes | BASE lines | Symbols |
|---|---|---|---|
| `_common` | none | 102, 107 | `conversation_monitoring_service` (unused, kept), `CONVERSATION_NOT_FOUND_MSG` (2) |
| `retrieval` | R1-R4 | 88-99, 116-356 | 5 helpers, 4 handlers (9) |
| `streaming` | R5-R6 | 108-109, 359-464 | 2 constants, 4 stream helpers, 2 handlers (8) |
| `deletion` | R7-R10 | 467-530 | 4 handlers |
| `history` | R11-R18 | 533-773 | 8 handlers |
| `crud` | R19-R21 | 776-888 | 3 handlers |
| `folders` | R22-R26 | 891-999 | 5 handlers |
| `messages` | R27-R29 | 104-105, 110-113, 1002-1243 | 3 export constants, 2 helpers, 3 handlers (8) |

Final `__init__.py` (header kept; no other code):

```python
from fastapi import APIRouter, Depends

from codemie.rest_api.routers.conversation import crud, deletion, folders, history, messages, retrieval, streaming
from codemie.rest_api.security.authentication import authenticate

router = APIRouter(...)  # the existing statement, unchanged
# Include order is source order and load-bearing: 5 of the 7 route-precedence pairs span modules.
router.include_router(retrieval.router)  # then streaming, deletion, history, crud, folders, messages
__all__ = ["router"]
```

## Tools and checks

`WORK=local/router-split/step6` (gitignored, never staged); `PKG=src/codemie/rest_api/routers/conversation`; Python as `poetry run python`.

- Origin: copy the top-level `*.py`, `*.sh`, `README.md` of `local/router-split/step5/` (the README describes each tool; never copy `*.json`, `BASE`, `base_*`, `patch-map.txt`, `flip-report.txt`, `baseline.md`) and adapt to package `conversation`, BASE file `$WORK/base_conversation.py`, only `router` exempt (`logger` is imported here, not defined): `_lib.sh`; `at.sh`; `size.sh` (header = `base_conversation.py` 1-13, no `from __future__`); `modulemap.py` (ranges above; `--check`: the 47 symbols once each, routes 4/2/4/8/3/5/3, symbols 9/8/4/8/3/5/8, `_common` 2); `moved.sh`, `leftover.sh`, `import.sh`, `move.sh`, `move_symbols.py`, `patchuse.py`, `flip.py` (the 5 edited test files), `astmove.py` (47 nodes).
- `AT` = pytest on `tests/codemie/rest_api/routers/{test_conversation,test_conversation_pagination,test_assistant_refresh_flow,test_conversation_update_router_sites}.py`, `tests/codemie/service/test_upsert_conversation_with_history.py` and the untouched `test_conversation_analysis.py`. `GUARDS` = `tests/codemie/rest_api/router_guards`. Both `-n 0 --show-capture=no -q`.
- `PATCHUSE` gains an `ast` scan of attribute uses of any package alias (`import ... as X`, `from ...routers import conversation`) that fails on any attribute but `router` (7 hits at BASE). `LEFTOVER` = `rg -nP "routers[./]conversation\.(?!(?:router|retrieval|streaming|deletion|history|crud|folders|messages|_common)\b)\w+|routers\.conversation import (?!router\b)" src tests .ai-run`, empty at the end. `FLIP` and `ASTMOVE` as in the step 5 README.
- `ROUTES <out>` / `COMPARE [--final] <after.json>` against `routes_before.json`; the dump adds `analysis_routes` (the `conversation_analysis` control). (a) 29 rows (methods, path, name, status_code, tags, deps) equal in order, `module` ignored; (b) same `pos` offsets, one contiguous block, same neighbors, other routes equal; (c) prefix `/v1`, tags `['Conversation']`, dependencies `['authenticate']`, R15 and R16 deps exactly `['authenticate', 'admin_access_only']`; (d) `--final`: no endpoint in `__init__`; (e) control: 29 rows at BASE, `analysis_routes` non-empty, equal before and after, none counted as conversation. The `__init__` remainder after Tasks 3-9 reads 26, 21, 18, 10, 6, 4, 0.

**Patch rules** (spec "Test retarget rules" win): retarget a string only when the code resolving the name on the exercised path moved in this task. The 109 `<Class>.<attr>` patches keep their tail and go to the handler's module; `Ability` (5) and `GenerationManager` (1, inert at BASE, stays inert) go to `streaming`, `MessageExporter` (2) to the module of the handler called. Imports take the defining submodule. Append each `file:line: old -> new` to `$WORK/patch-map.txt`; final totals: messages 48, retrieval 32, history 18, streaming 11, crud 7, folders 1, deletion 0. A test red for a missing second patch: stop and ask.

**Procedure (Tasks 2-9):** (1) Retarget the task's tests, run `AT` (`IMPORT <module>` when untested): red with ModuleNotFoundError or AttributeError. (2) Create the module: `head -n 13 $WORK/base_conversation.py`, a copy of the `__init__` import block, `router = APIRouter()` (route modules), then `move.sh <module> $(poetry run python $WORK/modulemap.py <module>)`; add the import and `include_router` line to `__init__`; prune with `ruff check --select F401 --fix --unsafe-fixes` on both files and add names from `--select F821`. (3) `MOVED`, `PATCHUSE`. (4) `IMPORT`, `SIZE` (each module at most 500 lines), `ruff check` and `ruff format --check` on touched files, `AT`, `ROUTES` + `COMPARE` (remainder as above), `GUARDS` with `git status` showing no baseline change; `git add` the task's files and confirm `git diff --cached --color-moved=dimmed-zebra` shows only moved blocks, imports, headers, `APIRouter()`, includes, logger imports. (5) Commit.

---

### Task 1: Tools, before-dump, package skeleton

**Files:** `git mv src/codemie/rest_api/routers/conversation.py src/codemie/rest_api/routers/conversation/__init__.py` (mkdir first); scratch tools in `$WORK`, uncommitted.

Test-first: no — rename only; proof: `git diff --cached -M --stat` shows a 100% rename, `AT`, `GUARDS`, `COMPARE` stay green.

- [ ] Setup: `git check-ignore $WORK`; `git rev-parse HEAD > $WORK/BASE` (`git diff --exit-code 94732a6202 HEAD -- src tests` empty); `git show 94732a6202:src/codemie/rest_api/routers/conversation.py > $WORK/base_conversation.py`; `git grep -nP 'routers\.conversation\b' 94732a6202 -- tests > $WORK/base_test_refs.txt` (expect 122 lines in 5 files: 117 patch strings, 5 imports; plus 8 `conversation_router.` lines).
- [ ] Tools: copy-and-adapt; self-check `modulemap.py --check` (a shortened range must fail), after the `git mv` `move.sh --dry-run` per module. Trap controls: a scratch literal `codemie.rest_api.routers.conversation_analysis.X` and the two sibling files give no hit from `MOVED`, `PATCHUSE`, `LEFTOVER`, `FLIP --dry-run` or the ROUTES predicate; the alias scan reports the 7 BASE hits.
- [ ] Baseline: `ROUTES $WORK/routes_before.json` before the move (29 rows, control non-empty), keep `.orig.json` and its sha256; record `AT` and `GUARDS` pass counts in `$WORK/baseline.md`.
- [ ] `git mv`, then `AT`, `GUARDS`, `ROUTES` + `COMPARE` (all 29 endpoints in `__init__`); commit the rename only.

### Task 2: `_common`

**Files:** Create `conversation/_common.py`; modify `__init__.py` (import `CONVERSATION_NOT_FOUND_MSG` from `._common`). No test edits.

Test-first: yes — `IMPORT ..._common` fails with ModuleNotFoundError; nothing patches its two symbols; after: `AT` green.

- [ ] Procedure without `router` or include; R5, R6, R18 stay inline and read the constant through `__init__`.

### Task 3: `messages` (R27-R29)

**Files:** Create `messages.py`; modify `__init__.py` (first `include_router` line); tests: R27-R29 literals (48).

Test-first: yes — retargeted strings fail with ModuleNotFoundError; R28, R29 have no handler test, so `IMPORT`, `COMPARE` (a)-(c), `GUARDS` are their proof; after: remainder 26.

- [ ] Procedure.

### Task 4: `folders` (R22-R26)

**Files:** Create `folders.py`; modify `__init__.py`; tests: `test_conversation.py` (one `ConversationFolder.get_all_by_fields` literal).

Test-first: yes — as Task 3; R23-R26 have no handler test; after: remainder 21.

- [ ] Procedure.

### Task 5: `crud` (R19-R21)

**Files:** Create `crud.py`; modify `__init__.py`, `src/codemie/rest_api/routers/ide.py:32` (import `create_conversation` from `conversation.crud`); tests: R19 (3) and R20 (4) literals.

Test-first: yes — `IMPORT codemie.rest_api.routers.ide` and the retargeted strings fail with ModuleNotFoundError; R21 has no handler test (`ide.py` calls it); after: remainder 18.

- [ ] Procedure; edit `ide.py` first (red), then extract.

### Task 6: `history` (R11-R18)

**Files:** Create `history.py`; modify `__init__.py`; tests: R14-R18 literals (18), import `upsert_conversation_history` (`test_upsert_conversation_with_history.py`), `test_conversation.py` aliases `admin_finish_conversations_bulk` (1) and `list_admin_conversations` (3) through a new `history` alias (the `conversation_router` alias stays for `.router.routes`).

Test-first: yes — as Task 3; R11-R13 have no handler test; after: remainder 10.

- [ ] Procedure; R18 reads `CONVERSATION_NOT_FOUND_MSG` from `_common`.

### Task 7: `deletion` (R7-R10)

**Files:** Create `deletion.py`; modify `__init__.py`. No test edits.

Test-first: yes — `IMPORT ...deletion` fails with ModuleNotFoundError; R7-R9 have no handler test, `COMPARE` (a)-(c) and `GUARDS` are their proof; after: remainder 6.

- [ ] Procedure.

### Task 8: `streaming` (R5-R6)

**Files:** Create `streaming.py`; modify `__init__.py`; tests: R5-R6 literals (11), imports `abort_conversation_generation` (`test_conversation_update_router_sites.py`) and `CONVERSATION_ABORTED_SUCCESS_MSG` (`test_assistant_refresh_flow.py`).

Test-first: yes — as Task 3; after: remainder 4.

- [ ] Procedure; reads `CONVERSATION_NOT_FOUND_MSG` from `_common`.

### Task 9: `retrieval` and the final `__init__`

**Files:** Create `retrieval.py`; reduce `__init__.py` to the final shape (delete unused imports by hand, add `__all__`); tests: R1 (8) and R3 (24) literals; `conversation_router.authenticate` (3) becomes a direct import from `codemie.rest_api.security.authentication` (`test_conversation_pagination.py` drops its unused alias import).

Test-first: yes — the alias scan lists the 3 `.authenticate` uses and the retargeted strings fail with ModuleNotFoundError; after: `LEFTOVER` empty, `rg -n '^(def|class|async def|[A-Za-z_]+ *=)' $PKG/__init__.py` shows only `router`, `__all__`, `COMPARE --final` green.

- [ ] Procedure; `retrieval` is the first include.

### Task 10: Final proof and handoff

**Files:** none; nothing to commit (a working-tree diff is a bug).

Test-first: no — proof only, no code change.

- [ ] **Step 1: Proof (AC 1-6).** `conversation.py` gone; 9 `.py` files; `SIZE` OK, `rg -L 'limitations under the License' $PKG` empty; `ROUTES` + `COMPARE --final` (a)-(e); `ASTMOVE` with a negative control (one body byte changed in a scratch copy of a module must fail); `git diff --color-moved=dimmed-zebra 94732a6202 -- src` shows only moved blocks, imports, headers, `APIRouter()`, includes, `__all__`; `LEFTOVER` empty, `PATCHUSE` OK, every line of `base_test_refs.txt` is on the left of `patch-map.txt` or the kept package-alias import, per-module totals match; `FLIP` run, `flip-report.txt` written. AC 5: `rg -nUP "routers\.conversation\.(?:retrieval|streaming|deletion|history|crud|folders|messages) import (?:[^\n(]*|\([^)]*)\b_[a-z]\w*" src`, `rg -n "routers\.conversation\b" $PKG/_common.py` and `rg "routers\.conversation\b" src/codemie/service src/codemie/rest_api/handlers` all empty; `rg -n 'routers/conversation\.py' . -g '!docs/superpowers' -g '!local'` empty; read-only `rg 'rest_api\.routers\.conversation\b'` over `codemie-enterprise`, `codemie-mcp-connect-service`, `codemie-ui`, `codemie-public-skills` has no hit (a hit: stop and ask); `rg -c '^class '` over the package totals 0. `IMPORT` for the package and all 8 submodules. `git diff --exit-code 94732a6202 -- src/codemie/rest_api/main.py src/codemie/rest_api/routers/conversation_analysis.py tests/codemie/rest_api/router_guards tests/codemie/rest_api/routers/test_conversation_analysis.py` is empty.
- [ ] **Step 2: Suites.** `GUARDS`; `AT`; `poetry run pytest tests/codemie/rest_api/routers tests/unit/routers -n 0 --show-capture=no -q`.
- [ ] **Step 3: Handoff and cleanup.** In `local/router-split/HANDOFF.md` (gitignored, never staged) record step 6 done, `patch-map.txt`, the scripts, the before/after `ROUTES` + `COMPARE` output, the `PASS` and `NO-SIBLING` rows of `flip-report.txt` (the inert `GenerationManager` included), any fallback cut, the spec's non-goal MR notes, and that the eventual MR must ask a reviewer to post `/sanity` and carry a `## Test harness` block, listing the moved auth code of spec AC 8. Delete scratch artifacts (`routes_*.json`, `base_*`, `__pycache__`, `<task dir>/test.local.log`); keep scripts, `README.md`, `patch-map.txt`, `flip-report.txt`, `baseline.md`; `git status --porcelain` lists only the two lock files (plus the planning docs if uncommitted).

## Negative-constraint pass

negative-constraints: stated (spec Constraints, Non-goals, Open risks; caller notes).

- No route reordered or moved across modules (tail-first keeps order): Tasks 3-9 `COMPARE` (a)-(b).
- No `from __future__`, logger rename or re-export; `__init__` holds only `router`, includes, `__all__`: `SIZE`, Task 9 shape check, `LEFTOVER`, `ASTMOVE`.
- No underscore import across modules; nothing under `service/` or `rest_api/handlers/` imports the router: Task 10 `rg` checks.
- No changed router-level setting, path, tag, operationId, response model or dependency; bare `APIRouter()` in route modules: `COMPARE` (c) per task; `GUARDS` byte-exact snapshot.
- No behavior change, rename, logic into `service/` (step 7), dead-code deletion (`conversation_monitoring_service` kept) or fix of the listed pre-existing gaps: color-moved review per task; `ASTMOVE`.
- Both `conversation_analysis` files untouched and never matched by a tool: Task 1 controls, `COMPARE` (e), Task 10 `git diff --exit-code`.
- No `main.py`, guard-code or baseline change, no `regenerate_baselines`, no new tests: Task 10 `git diff --exit-code`; `GUARDS` per task.
- No `.gitignore` or planning-doc edit in a task commit, no staged scratch or lock files, no whole-suite gates, `make ruff`, sonar, browser or Jira/MR work, no push: staging rule; none planned.
