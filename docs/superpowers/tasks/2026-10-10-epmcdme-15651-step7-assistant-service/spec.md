# EPMCDME-15651, step 7 (assistant): domain helpers into service/assistant

## Purpose

Move the inline-credential detection and the prompt-variable encrypt, restore and mask helpers from `routers/assistant/` into `service/assistant/`, no API change. Not move-only: handlers call the new modules and patch targets change, so the AST proof of steps 2-6 does not apply (criteria 2-5 replace it). Same branch; no MR until the ticket is done, and that MR must ask a reviewer to post `/sanity` and carry a `## Test harness` block.

## Scope

10 functions, bodies unchanged (function-local imports of `SENSITIVE_VALUE_MASK` and `EncryptionFactory` stay local). Callers: `marketplace.py:126,137`, `catalog.py:231`, `management.py:121,277,282`; no other in this repo or the sibling repos.

| Old | New |
|---|---|
| `_inline_credentials.py:19-208`: `_validate_assistant_inline_integrations`, `_check_toolkit_credentials`, `_check_object_credential_values`, `_check_mcp_server_credentials`, `_build_validation_result` | `service/assistant/inline_credentials.py`: public `validate_assistant_inline_integrations`; the rest keep private names. Old file deleted, no re-export. |
| `_inline_credentials.py:44` `_get_masked_prompt_variables`; `catalog.py:429-444` `_mask_sensitive_prompt_variables`; `management.py:386-449` `_encrypt_sensitive_prompt_variables`, `_encrypt_prompt_variables_in_request`, `_restore_existing_encrypted_values` | `service/assistant/sensitive_prompt_variables.py`: the same five names without the underscore |

`inline_credentials` imports `get_masked_prompt_variables` from `sensitive_prompt_variables`, never the reverse. Module functions, no class (they were stateless functions). Handlers import the public names and keep their call positions. New modules use `from __future__ import annotations`; signatures may gain hints, bodies are not edited.

## Behavior that must not change

- Encrypt resolves `EncryptionFactory().get_current_encryption_service()` per call, only after the empty-list return; skips non-sensitive, empty and mask-equal values; provider errors propagate unwrapped.
- `update_assistant` encrypts, then restores, both before its `try`; create encrypts outside its `try`. The reverse order would double-encrypt a restored value.
- Restore swaps a sensitive value equal to `SENSITIVE_VALUE_MASK` for the stored one, matched by key. Mask replaces a non-empty sensitive default with the mask.

## Acceptance criteria

1. `_inline_credentials.py` is gone; both modules carry the full 13-line Apache header (check its last line). The 6 renamed names (`_validate_assistant_inline_integrations` and the five sensitive-variable helpers) are gone: a word-boundary grep for each whole identifier finds none in `src/` or `tests/`. The 4 kept private names appear only in `service/assistant/inline_credentials.py` and its moved test. The unrelated `AssistantIntegrationValidator._build_validation_result` is excluded from both checks.
2. Body check: a throwaway script (gitignored `local/router-split/step7/`) compares `ast.dump` of each body, docstring included, at BASE and now, under a declared underscore-to-public rename map. All 10 match.
3. Characterization tests are written first and run green against the unmoved helpers; after the move they change only in import lines and renamed identifiers, while assertions, fixtures and the `EncryptionFactory`/mask patch literals (they target the defining modules) stay byte-identical (diff-checkable). They cover every branch of encrypt (both variants, including the empty list with the factory never resolved, and error propagation), restore and mask (including dict and model input, input not mutated, the in-place variant); the credential types `mcp_environment_vars`, `mcp_auth_token`, `mcp_inline_config_env`; and masked `prompt_variables` in the validation result. Pre-existing quirks are pinned and named as such.
4. Handler tests prove the wiring: create stores ciphertext; update with a masked sentinel keeps the stored ciphertext encrypted once; the by-id detail route returns the mask with the masker unpatched.
5. `test_inline_credentials.py` moves to `tests/codemie/service/assistant/` (`git mv`; import lines and the renamed identifier only). The 4 `marketplace._validate_assistant_inline_integrations` literals and `test_assistant.py:1068` retarget to the names the router modules now read. Flip check: point each literal once at a sibling module that binds the name but does not read it in the function under test; tests that still pass go in the handoff as inert.
6. No `src/` module imports an underscore name from a module that defines a `router`; underscore imports from the private `_shared`, `_ask` and `_sub_assistants` modules stay. Routers import only public names from the two new service modules. `service/` and `rest_api/handlers/` import from `routers/` only `routers.utils` (3 and 1 hits at BASE); the new modules import none.
7. Each new module imports alone in a fresh interpreter (`python -I`); `import codemie.service.assistant` works; the guarded app builds hermetically.
8. `router_guards` pass with `openapi.json` and `route_precedence.json` unchanged (`git diff` empty); `regenerate_baselines` never run. On a failure: stop, report, ask.
9. `make ruff` passes (F401 after the removals, complexity 16, license). Tests pass in `tests/codemie/rest_api/routers`, `tests/unit/routers`, `tests/codemie/service/assistant`, `router_guards`.
10. The handoff records the name map, the inert list and the `/sanity` and `## Test harness` requirement. Security code that moves: encrypt, restore, mask, the publish-time credential gate. Decrypt does not move.

## Non-goals

- Fixing pre-existing behavior, carried as MR notes: restore copies the stored value without checking it is sensitive (`management.py:446-449`); an unmatched mask is stored literally; the `hasattr`/`dict(var)` fallbacks in masking.
- Merging the two encrypt twins; sharing code with `assistant_prompt_variable_mapping_service` (own mask `"*" * 10`) or `inline_credentials_collector.py`.
- Moving decrypt (`service/assistant_service.py:933`), `_shared.py`, `_ask.py`, `_sub_assistants.py` or other `management.py` helpers; injecting or caching `EncryptionFactory`; typed exceptions, logging, classes.
- Any path, tag, operationId, schema or route-order change; `main.py`; guard code; `codemie-ui`, `codemie-enterprise`; other packages; commits or MR beyond the flow's.

## Open risks

- The new tests are written from reading the code: they pin what it does, not what it should do. Running them at BASE first is the only check.
- Inert patches (4 marketplace literals, `MagicMock` assistants) can pass silently; the flip check lists them but cannot prove the mocks are asserted.
- The body check cannot see a wrong import or call site; handler tests and the flip check cover that.
