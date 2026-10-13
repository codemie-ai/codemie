# EPMCDME-15651 Step 7: Assistant Helpers to service/assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Move 10 helpers (inline-credential detection; prompt-variable encrypt, restore, mask) from `routers/assistant/{_inline_credentials,management,catalog}.py` into `service/assistant/{inline_credentials,sensitive_prompt_variables}.py`; routers call the public names. No API change.

**Architecture:** Not move-only, so spec AC 2-5 replace the AST proof of steps 2-6: characterization and wiring tests first (green at BASE), one module per task with bodies copied by script, proof last. Commit per task using the repository's existing convention (`EPMCDME-15651: Capital description`); a task's tests and code share its commit.

**Spec:** `docs/superpowers/tasks/2026-10-10-epmcdme-15651-step7-assistant-service/spec.md` (research: `technical-analysis.md`). `BASE` = HEAD before Task 1 (`dc221e653f` or a later planning-only commit); Tasks 1-2 touch tests only.

## Global Constraints

- Rename map (underscore dropped): `_validate_assistant_inline_integrations` into `inline_credentials`; `_get_masked_prompt_variables`, `_mask_sensitive_prompt_variables`, `_encrypt_sensitive_prompt_variables`, `_encrypt_prompt_variables_in_request`, `_restore_existing_encrypted_values` into `sensitive_prompt_variables`. Kept private in `inline_credentials`: `_check_toolkit_credentials`, `_check_object_credential_values`, `_check_mcp_server_credentials`, `_build_validation_result`.
- Bodies (docstrings, comments, function-local `SENSITIVE_VALUE_MASK`/`EncryptionFactory` imports) are copied by `extract.py` from BASE, never retyped; parameters unchanged, return hints allowed. Each new module: verbatim 13-line Apache header (`head -n 13` of `_inline_credentials.py`, last line `# limitations under the License.`), `from __future__ import annotations`, annotation-only imports under `TYPE_CHECKING`, module functions, no class.
- `inline_credentials` imports `get_masked_prompt_variables`, never the reverse; nothing under `service/` imports `routers/`; no re-export or shim.
- Call sites keep their positions: `management.py:121` and the `if request.prompt_variables:` guards at `:276`/`:281` (encrypt, then restore, both before the `try` at `:284`).
- Untouched: `service/assistant_service.py:933` (decrypt), `_shared.py`, `_ask.py`, `_sub_assistants.py`, `main.py`, `tests/codemie/rest_api/router_guards`; never `regenerate_baselines`.
- Stage only the task's files by path (never `git add -A`, `opencode.json.lock`, `skills-lock.json`, the task dir, anything under `local/`); no push, no MR. `make ruff`, the whole test run and `router_guards` are Stage 7's; on a failure there: stop, report, ask.

## Tools and checks

`WORK=local/router-split/step7` (gitignored, never staged; `git check-ignore $WORK`), Python as `poetry run python`.

- `extract.py <dest> <name>...` slices functions from `$WORK/base_*.py` (`git show BASE:<path>` of the three router files) by `ast` line range and applies the rename map on word boundaries. `bodycheck.py` compares `ast.dump` of each of the 10 bodies, docstring included, BASE against now under the map; `--negative` alters one constant in a scratch copy and must exit non-zero. `flip.py` is adapted from `local/router-split/step6/flip.py`. Task outputs, never committed.
- `AT` = `pytest -n 0 --show-capture=no -q` on `tests/codemie/service/assistant/test_sensitive_prompt_variables.py` and in `tests/codemie/rest_api/routers/`: `test_inline_credentials.py` (from Task 5 under `tests/codemie/service/assistant/`), `test_assistant_prompt_variable_wiring.py`, `test_assistant_marketplace.py`, `test_assistant.py`.
- `IMPORT <module>` = `poetry run python -I -c "import importlib; importlib.import_module('<module>')"` (a cycle: stop, ask). `LINT` = `ruff check` and `ruff format --check` on the task's touched files. `DIFFCHK` = `git diff --cached -U0` of the task's test files shows only import lines and renamed identifiers.

## Review Focus

- Inert patches (4 `marketplace` literals, the masker literal, `MagicMock` assistants) pass when mis-aimed: Task 6 flip.
- Restore before encrypt double-encrypts: Task 2 update test. Factory resolved only after the empty-list return (cloud constructors do network work): Task 1.
- Pre-existing quirks are pinned and named, not fixed.

---

### Task 1: Characterization tests, green at BASE

**Files:** Create `tests/codemie/service/assistant/test_sensitive_prompt_variables.py`; modify `tests/codemie/rest_api/routers/test_inline_credentials.py` (append). Setup, uncommitted: `$WORK`, `BASE`, `base_*.py`, `extract.py`, `bodycheck.py`.

Test-first: yes — a pure relocation cannot go RED, so the first check is every new test green against the unmoved helpers. Then one working-tree mutation per helper (`!=` to `==` in both encrypt twins, `==` to `!=` in restore, drop the mask assignment): the matching test goes red; `git checkout --` the file.

- [ ] New file imports the five names from `management` (3), `catalog` (`_mask_sensitive_prompt_variables`) and `_inline_credentials` (`_get_masked_prompt_variables`); `EncryptionFactory` is patched at `codemie.service.encryption.encryption_factory.EncryptionFactory` (defining module; this literal never changes). Variables are `PromptVariable` (`rest_api/models/assistant.py:275-284`), the request twin takes an `AssistantRequest`.
  - Encrypt, both variants parametrized: only sensitive, non-empty, non-mask values are encrypted, once each; empty or `None` list returns with the factory `assert_not_called`; a provider error propagates as the same exception.
  - Restore: mask swapped for the stored value by key; either list empty returns; stored empty value leaves the mask. Quirks: stored value copied although the stored variable is not sensitive; unmatched mask stays literal.
  - Mask: empty returns `[]`; model input gives dicts, sensitive non-empty defaults equal `"********"`, others unchanged, model not mutated; `UserDict` takes the `dict(var)` branch (copy). Quirk: plain dict input is mutated in place. In-place variant: changes only sensitive `default_value`, early return on empty.
- [ ] In `test_inline_credentials.py`, through `_validate_assistant_inline_integrations`: `mcp_environment_vars` (server needs `settings.credential_values`; the mock's `mcp_environment_vars` attribute is never read), `mcp_auth_token`, `mcp_inline_config_env` (`env_vars == list(config.env)`), each asserting `credential_type`, `mcp_server`, `toolkit == "MCP"`; `prompt_variables` masked for sensitive entries and absent when set to `[]` (an unset `MagicMock` attribute is truthy).
- [ ] `AT` green; commit; `git rev-parse HEAD > $WORK/T1`.

### Task 2: Handler wiring tests, green at BASE

**Files:** Create `tests/codemie/rest_api/routers/test_assistant_prompt_variable_wiring.py`.

Test-first: yes — green at BASE (the wiring exists); no moved name appears in the file, so it stays byte-identical. Spot-check: swap the calls at `management.py:277`/`:282` in the working tree, the update test goes red, `git checkout --`.

- [ ] Fake service (`encrypt = lambda v: f"enc({v})"`) behind the `EncryptionFactory` literal. Create: `create_assistant` with the patches of `TestCreateAssistantSlug._patches`/`_run_create` (`test_assistant.py:667-709`); the saved assistant holds `enc(s3cret)` for the sensitive variable, plain for the other. Update: stored `enc(s3cret)`; request sends the mask plus one new sensitive value; the request reaching `AssistantRepository().update` holds `enc(s3cret)` (not `enc(enc(s3cret))`) and the new value encrypted once. Detail: `get_assistant_by_id` with the patch set of `_enrichment_patches` (`test_assistant.py:1060-1085`) minus the masker patch plus `catalog.get_assistant_by_id_or_raise`; body shows `"********"`, no ciphertext.
- [ ] `AT` green; commit; `git rev-parse HEAD > $WORK/T2`.

### Task 3: `sensitive_prompt_variables` (encrypt, restore) and `management` callers

**Files:** Create `src/codemie/service/assistant/sensitive_prompt_variables.py`; modify `routers/assistant/management.py` (delete `:386-449`, import the public names, rename calls `:121`, `:277`, `:282`); modify `test_sensitive_prompt_variables.py` (3 imports and identifiers).

Test-first: yes — retarget the 3 imports first: ModuleNotFoundError; after: Task 1 and 2 tests green, `bodycheck.py` OK for 3 of 10, `DIFFCHK` clean.

- [ ] Produces `encrypt_sensitive_prompt_variables(assistant: Assistant) -> None`, `encrypt_prompt_variables_in_request(request: AssistantRequest) -> None`, `restore_existing_encrypted_values(request: AssistantRequest, existing_assistant: Assistant) -> None` via `extract.py`. `IMPORT`, `LINT` (F401 in `management.py`), `AT`; commit.

### Task 4: Mask pair and `catalog` rewire

**Files:** Modify `sensitive_prompt_variables.py` (append `get_masked_prompt_variables(prompt_variables: list) -> list` from `_inline_credentials.py:44-76`, `mask_sensitive_prompt_variables(assistant: Assistant) -> None` from `catalog.py:429-444`); `_inline_credentials.py` (delete `:44-76`, import the public name for `:39`); `catalog.py` (import `:37-39` becomes `mask_sensitive_prompt_variables`, delete `:429-444`, call `:231`); `test_sensitive_prompt_variables.py` (2 imports and identifiers); `test_assistant.py:1068` to `codemie.rest_api.routers.assistant.catalog.mask_sensitive_prompt_variables`.

Test-first: yes — the retargeted literal and imports fail (AttributeError, ImportError) before the move; after: `AT` green, `bodycheck.py` OK for 5 of 10.

- [ ] As Task 3: `extract.py`, `IMPORT`, `LINT`, `AT`, `DIFFCHK`; commit.

### Task 5: `inline_credentials`, `marketplace` callers, test move

**Files:** Create `src/codemie/service/assistant/inline_credentials.py` (the 5 functions of `_inline_credentials.py:19-208`); `git rm` `routers/assistant/_inline_credentials.py`; modify `marketplace.py` (import `:36-38`, calls `:126`, `:137`); `git mv tests/codemie/rest_api/routers/test_inline_credentials.py tests/codemie/service/assistant/` (import lines and the one renamed identifier only); `test_assistant_marketplace.py:129,189,264,319` to `codemie.rest_api.routers.assistant.marketplace.validate_assistant_inline_integrations`.

Test-first: yes — the moved test's changed imports fail with ModuleNotFoundError before the module exists; after: moved file and `test_assistant_marketplace.py` green, `bodycheck.py` OK for 10 of 10, `git diff --cached -M` shows a rename, `DIFFCHK` clean.

- [ ] Produces `validate_assistant_inline_integrations(assistant: Assistant) -> dict`; create via `extract.py` before `git rm`. `IMPORT` for the module and `codemie.service.assistant`, `LINT`, `AT`; commit.

### Task 6: Proof and handoff

**Files:** none; nothing to commit (a working-tree diff is a bug).

Test-first: no — proof only, no code change.

- [ ] AC 1: `_inline_credentials.py` gone; `sed -n 13p` of both modules is the header's last line; `rg -nw` for each of the 6 renamed names over `src tests` is empty; the 4 kept names hit only `service/assistant/inline_credentials.py` and its moved test (`AssistantIntegrationValidator._build_validation_result` excluded). AC 2: `bodycheck.py` 10 of 10, `--negative` fails.
- [ ] AC 3: against `$WORK/T1` the two characterization files differ only in import lines and renamed identifiers, `EncryptionFactory` literal count equal; `git diff --exit-code $(cat $WORK/T2) -- tests/codemie/rest_api/routers/test_assistant_prompt_variable_wiring.py` is empty.
- [ ] AC 5 flip: point each of the 5 retargeted literals once at the defining service module (the only other module binding the name), run the enclosing test, restore in `finally`; `PASS` is inert (handoff), `FAIL` is caught; clean tree before, `git diff --exit-code -- tests` after.
- [ ] AC 6-8: `rg -nUP "routers\.[\w.]+ import (?:[^\n(]*|\([^)]*)\b_[a-z]\w*" src` lists only `_shared`, `_ask`, `_sub_assistants` imports; routers import only public names from the two new modules, which mention no `routers`; `service/` and `rest_api/handlers/` still hit only `routers.utils` (3 and 1); `IMPORT` both modules; `git diff --exit-code BASE -- src/codemie/rest_api/main.py tests/codemie/rest_api/router_guards`.
- [ ] Handoff in `local/router-split/HANDOFF.md` (never staged): step 7 done, name map and test move, flip `PASS` list, pinned quirks, spec non-goal MR notes, MR must ask a reviewer to post `/sanity` and carry a `## Test harness` block, moved security code (encrypt x2, restore, mask x2, publish-time gate `validate_assistant_inline_integrations`; decrypt did not move), Stage 7 owns `make ruff`, the full run and guards. Delete `base_*`, `__pycache__`; `git status --porcelain` lists only the lock files (plus planning docs if uncommitted).

## Negative-constraint pass

negative-constraints: stated (spec Behavior, Non-goals, AC 5-8).

- No body edit, API, route, tag, operationId or schema change: `extract.py`, `bodycheck.py`, Task 6 `git diff --exit-code`.
- Encrypt/restore order, call positions and lazy factory unchanged: Constraints, Tasks 1-2.
- No fix of pre-existing behavior (quirks pinned only), no twin merge, no sharing with `assistant_prompt_variable_mapping_service` or `inline_credentials_collector.py`, no class, injection, caching, typed exception or logging: Task 1; module functions only.
- Decrypt, `_shared.py`, `_ask.py`, `_sub_assistants.py`, other `management.py` helpers untouched; no re-export, no `service/` import of `routers/`: Task 6 AC 1, 6.
- No `regenerate_baselines`, `codemie-ui`/`codemie-enterprise` edit, whole-suite, ruff-wide or guard task, browser check, push, MR or staged lock/scratch file: Constraints; none planned.
