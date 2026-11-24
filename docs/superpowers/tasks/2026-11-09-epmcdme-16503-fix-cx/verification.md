# EPMCDME-16503 correction evidence

## Source identity

- Original linker head: `b13d5b2086bbdb3d8a13f9e7d8142208cce7a1ba`.
- Engine base: `fb45cbfa668646b10f299809c3e44a7b2b003ccf`.
- Rebased linker head: `57f1d6348aeef27e41024470504d27d50c3c9505`.
- Remote heads were checked with `git ls-remote origin refs/heads/epmcdme-16499 refs/heads/epmcdme-16503`: the engine remote was `fb45cbfa668646b10f299809c3e44a7b2b003ccf`; the linker remote was `b13d5b2086bbdb3d8a13f9e7d8142208cce7a1ba`. This check preceded publication.
- At QA completion, the correction was in the working tree on `epmcdme-16503`. The user then requested a commit and push after the full-gate failures were reported.
- Command: `git switch epmcdme-16503 && git rebase epmcdme-16499`. All 22 commits rebased without a conflict.

## Acceptance-test map

Test files are under `tests/codemie/workflows/validation/`.

| Condition | Test evidence |
|---|---|
| A / AC-40: no crash, exact key and value, writing on/off | `test_contract_effects.py::test_ac40_full_reset_plus_output_key_matches_the_real_reducer` |
| A: disabled-writing reset precedence | `test_contract_engine_parity.py::test_output_writing_disabled_preserves_values_except_full_reset` |
| A / AC-22, AC-43: required shared save harness and complete corpus | `test_contract_corpus.py::test_the_16499_harness_and_this_loader_see_the_same_corpus`; `test_ac22_ac43_every_template_saves_through_the_harness_path_with_the_linker_on` |
| A: measured warning ceiling | `test_contract_corpus.py::test_ac43_warnings_stay_within_the_recorded_ceiling`; `test_ac43_the_ceiling_file_is_consistent_and_names_every_template` |
| B / AC-S1: macro, with, tuple-loop, and global outputs | `test_contract_jinja.py::test_valid_local_templates_have_no_context_requirements_and_render`; `test_all_runtime_environment_globals_are_available_without_context` |
| B: free and dotted references, nested scopes, shadowing, assignment order | `test_contract_jinja.py::test_scope_occurrences_match_real_rendering`; `test_free_name_presence_and_dotted_missing_fallback` |
| B: macro reads at each call and compiler-level declaration errors | `test_contract_jinja.py::test_macro_context_is_checked_at_known_calls`; `test_a_macro_called_before_assignment_reads_the_supplied_context`; `test_full_linker_accepts_a_macro_called_after_its_context_binding`; `test_invalid_jinja_declarations_use_the_runtime_raw_template_fallback` |
| B: literals, malformed templates, and excluded expressions | `test_contract_jinja.py::test_parse_failure_keeps_raw_template_and_adds_no_requirement`; `test_excluded_expressions_remain_excluded`; `test_static_analysis_never_calls_template_functions` |
| B / AC-S1, AC-S3: both template callers | `test_contract_jinja.py::test_agent_and_tool_template_callers_use_the_same_analysis` |
| B / AC-18, AC-19: real free names still warn/block; valid templates do not | `test_contract_jinja.py::test_full_linker_does_not_report_valid_local_templates`; `test_full_linker_still_warns_or_blocks_a_missing_supported_name` |
| C: state/tool binding ownership, literal/template/implicit bindings | `test_contract_locations.py::test_tool_bindings_are_used_when_state_bindings_are_empty`; `test_nonempty_state_bindings_replace_tool_bindings_and_select_the_state_owner`; `test_implicit_or_unbound_mandatory_parameters_use_the_selected_parent` |
| C: exact numeric, Unicode, punctuation, and long keys; warnings/strict; serialization/copies | `test_contract_locations.py::test_exact_binding_keys_survive_policy_serialization_and_copy`; `test_unknown_location_is_explicit_and_location_model_rejects_ambiguous_types` |
| C: task, input schema, transform index/input key, condition/switch/iterator | `test_contract_locations.py::test_agent_task_and_declared_input_schema_have_state_locations`; `test_transform_inputs_have_custom_node_locations_and_integer_mapping_indices`; `test_transition_requirements_keep_state_owner_and_exact_tokens` |
| C: synthetic output, coverage, convergence, transition and graph findings | `test_contract_locations.py::test_output_contract_coverage_and_convergence_findings_have_source_locations`; `test_transform_schema_note_uses_custom_owner_without_changing_diagnostic_path`; `test_dangling_transitions_have_locations_created_with_the_edge`; `test_graph_failure_has_explicit_root_location` |
| C: strict upstream form conversion and message fallback | `test_contract_locations.py::test_strict_consumer_form_finding_keeps_requirement_location_and_consumer`; `test_render_fallback_keeps_exact_location` |
| D: dictionary/list/scalar outputs, append/reset, result unwrapping | `test_contract_engine_parity.py::test_output_shapes_reset_and_append_match_real_finalization`; `test_tool_result_unwrapping_keeps_its_concrete_type_and_abstract_uncertainty` |
| D: iterator filter, nested scope, first/subsequent node, task overrides, finish and convergence | `test_contract_engine_parity.py::test_real_iteration_dispatch_matches_abstract_whitelist_and_nested_scope`; `test_only_first_iteration_node_spreads_the_item_into_context`; `test_real_iteration_task_override_and_finish_use_the_produced_or_original_task`; `test_real_branch_convergence_retains_the_fields_the_simulator_guarantees` |
| D: transform mappings, schema coercion, required fields, error strategies | `test_contract_engine_parity.py::test_transform_mapping_values_types_and_real_write_path`; `test_transform_schema_coercion_matches_certain_types`; `test_transform_required_field_and_invalid_coercion_follow_real_error_strategy` |
| D: unsupported type declarations and default fallback uncertainty | `test_contract_engine_parity.py::test_transform_does_not_claim_a_type_that_its_runtime_schema_does_not_enforce`; `test_transform_default_output_bypasses_the_own_schema_coercion` |
| D: supervisor and summarization overrides | `test_contract_engine_parity.py::test_summarization_override_does_not_apply_ordinary_context_flags`; `test_supervisor_uses_base_context_writes_but_not_its_declared_output_fields` |
| Spec §6.4: 64 KiB and depth 32 | `test_input_contract_check.py::test_check_input_contract_allows_depth_32_and_ignores_brackets_in_strings`; `test_check_input_contract_rejects_text_over_64kb_without_parsing`; `test_check_input_contract_counts_bytes_not_characters` |

## Environment

`poetry install -E enterprise` first failed because the sandbox could not connect to package sources. The network-enabled attempt installed locked public dependencies but could not authenticate to the enterprise registry. A local wheel was inspected and rejected because its hash did not match `poetry.lock`.

`python3 /tmp/epmcdme-16503-install-enterprise.py` then obtained an access token from the configured Google Cloud account and passed it to Poetry through the child environment. It completed successfully. No token was printed or saved to a project file. The installed package is `codemie-enterprise==2.3.47`; Jinja is `3.1.6`. The lock file and dependency declarations are unchanged.

## Initial focused checks

```bash
poetry run pytest tests/codemie/workflows/validation/test_contract_effects.py tests/codemie/workflows/validation/test_contract_corpus.py -q -rs
```

Result: 147 passed, no skips. This run removed crash tolerance and harness gates before the additional comparison tests.

```bash
poetry run pytest tests/codemie/workflows/validation/test_contract_locations.py tests/codemie/workflows/validation/test_contract_derive.py tests/codemie/workflows/validation/test_contract_policy.py tests/codemie/workflows/validation/test_contract_simulate.py tests/codemie/workflows/validation/test_contract_models_report.py -q -rs
```

Result: 430 passed, no skips. Log: `/tmp/16503-locations-tests.log`.

```bash
poetry run pytest tests/codemie/workflows/validation/test_contract_jinja.py tests/codemie/workflows/validation/test_contract_derive.py tests/codemie/workflows/validation/test_contract_linker.py -q -rs
```

Result: 273 passed, 4 third-party warnings, no skips. Log: `/tmp/16503-jinja-tests-final.log`.

```bash
poetry run pytest tests/codemie/workflows/validation/test_contract_effects.py tests/codemie/workflows/validation/test_contract_engine_parity.py tests/codemie/workflows/validation/test_contract_corpus.py -q -rs
```

Result: 226 passed, 3 third-party warnings, no skips. Log: `/tmp/16503-engine-final.log`.

```bash
poetry run python /tmp/16503_measure_ceiling.py
git diff --check
```

Result: 64 templates, 1 total warning, no changed per-template count, and no ceiling violation. Both required harness tests ran. The existing ceiling is retained because the measured counts are unchanged.

## Additional linker corrections from concrete tests

Seven transform type cases and one default-fallback case exposed false type guarantees before the correction. The linker now accounts for the types that the runtime actually coerces, Python Boolean values in numeric checks, and the default-output path that bypasses own-schema coercion. Unsupported declarations do not invent a guarantee. No runtime engine source changed, and no new engine defect was found in these cases.

## Final checks

The SDLC Factory full review completed with `decision: approve`, high confidence, and no remaining findings. The blind, edge-case, acceptance, and verification-gap reviews ran. Triage checked the stable source and documentation after the corrections. The standalone standards review did not run because the supplied review scope was the uncommitted working-tree diff; the repository checks run separately through QA gates.

Review artifacts: `code-review-final.local.json`, `code-review-brief.local.md`, and `documentation-review.local.diff`. The final reviewed source diff includes the new files and has SHA-256 `fbd1b2d5b37b6eb2dc68352f717a5de5a4543f8752e3c6f8d22c72120b93c5f5` (`code-review-check.local.diff`).

```bash
make ruff
make build
make license-check
make gitleaks
poetry run pytest tests/codemie/workflows tests/codemie/core/workflow_models -q -rs
```

Ruff passed without changing files. The package build passed. The license check passed for 3,153 files, with no missing headers. The secret scan passed with no leaks after Docker socket access was allowed.

The workflow/model suite passed: 2,576 passed, 29 skipped, 6 warnings in 43.80 seconds. The skip reasons are 28 existing workflow-generator tests with `WORKFLOW_GENERATION_ENABLED=False` and one duplicate JSON-pointer test covered by `TC_TNC_003`. Both required 16499 harness tests ran.

`make test` uses `PYTEST_ADDOPTS=-rs` to report skip reasons. No enterprise or dependency exclusion is set. Its first sandboxed run finished with 32,037 passed, 958 skipped, 5 expected failures, and 2 failures. Both failures were URL-content tests whose DNS lookup reached the private/loopback guard. Some live checks also skipped because the sandbox blocked localhost.

```bash
poetry run pytest tests/codemie_tools/file_analysis/email/test_email_analysis_tool.py -q -rs -k 'test_fetch_url_content_returns_bytes_on_success or test_fetch_url_content_raises_on_404'
```

Both URL tests passed outside the sandbox. No guard or test source changed. The complete `make test` rerun used network access. Live PostgreSQL and live-stack opt-in tests retained their documented prerequisites.

The network-enabled `make test` run completed with 32,399 passed, 556 skipped, 5 expected failures, and 42 failures. The URL tests passed. All 42 failed test IDs also failed on the unchanged engine base and on the changed-tree diagnostic run. The full gate remains **BLOCKED**; the base comparison does not make it a pass.

The base diagnostic run had 54 failures and 32 passes. The matching changed-tree run had 52 failures and 34 passes. Both used the same `.venv`, local services, `.env` file, test settings, and three pytest workers. `.env.local` was absent in both. The affected test/source/config files had equal bytes. Extra live SQL failures differed between runs, so those extra results are recorded as unstable. Exact commands, test IDs, and errors are in `gate-baseline-comparison.local.json`.

| Full-run failure group | Count | Evidence |
|---|---:|---|
| CLI Analytics calls missing a required `scope` argument | 28 | Same test IDs and call errors on the base. |
| CLI Analytics SQL result and count errors | 10 | Same test IDs fail on the base; extra live SQL results vary between runs. |
| Live marketplace index count | 1 | 3 published items compared with 170 indexed items; also fails on the base. |
| Marketplace HTTP 404 responses | 3 | The running backend has marketplace management disabled and does not mount these routes; also fails on the base. |

No unrelated source or shared service configuration was changed.

The missing LaTeX tool was supplied in an isolated Nix shell. Both PDF render tests then passed: 2 passed, 6 warnings in 15.40 seconds. No project dependency or test source changed. The final corpus measurement, after all review fixes, was `{"templates":64,"total":1,"ceiling_total":1,"changed_templates":{},"violations":[]}`.

```bash
nix shell --impure --expr 'let pkgs = (builtins.getFlake "nixpkgs").legacyPackages.${builtins.currentSystem}; in pkgs.texlive.combine { inherit (pkgs.texlive) scheme-small collection-fontsrecommended fvextra footnotehyper upquote; }' --command poetry run pytest tests/codemie/rest_api/routers/test_conversation_pagination.py -q -rs -k test_export_pdf_renders_real_pdf_document
```

The 556 full-run skips comprise 382 missing opt-in PostgreSQL URLs, 96 deprecated LiteLLM tests, 48 disabled workflow-generator tests, 9 deliberate datasource cases, 5 manual PostgreSQL tests, 5 opt-in live-stack tests, 2 disposable-PostgreSQL cases, 2 authentication-refactor cases, 2 PDF cases later passed with LaTeX, and 5 single cases. The single cases cover an already-created migration enum, a conversation-creation database requirement, duplicate JSON-pointer coverage, GitLab DiffNote configuration, and an existing Kata endpoint response-model defect. `gate-skips.local.json` records every test location and exact reason. No enterprise exclusion, dependency exclusion, or new skip gate was used.

`gate-run.json` is the full SDLC gate result. `gate-supplemental.local.json` records environment checks, the first sandboxed attempt, DNS/PDF checks, and the final corpus measurement. Logs remain at `/tmp/16503-gate-*.log`.

Conditional checks: live CLI Analytics SQL was outside the changed area; coverage was not requested; the live API harness and remote compliance checks need an MR/publication run. Commit-specific checks had no staged changes or commit message to check. `make verify` was not repeated because its component gates ran separately. These checks are marked skipped or not applicable, not passed.

After the checks, the source diff still had SHA-256 `fbd1b2d5b37b6eb2dc68352f717a5de5a4543f8752e3c6f8d22c72120b93c5f5`, equal to the reviewed source. `git diff --check` passed. This record describes verification before publication. The SDLC record remains at the QA stage because the full gate is blocked; the commit and push do not change that result.

`make check-alembic` passed. `make sonar-local` ran and explicitly skipped because `SONAR_TOKEN` is not configured; it is not a pass. The current pipeline has an Alembic check despite the guide's statement that no pipeline exists; QA includes the actual check.

## Review corrections

The SDLC Factory review found a singleton type-list case that the initial transform tests did not cover. Six concrete cases were added. Five failed before the correction: `['string']`, `['integer']`, `['number']`, and `['boolean']` do not trigger scalar coercion at runtime. The linker now retains that raw-schema distinction. The full effects/engine/corpus command above then passed 232 tests, with 3 third-party warnings and no skips. Logs: `/tmp/16503-review-types-before.log` and `/tmp/16503-review-types-after.log`.

The review also found that the successful scalar conversion test could pass if the linker lost a known numeric type. It now asserts the exact type and `types_certain` before comparing the concrete result.

```bash
poetry run pytest tests/codemie/workflows/validation/test_contract_engine_parity.py -q -k schema_coercion_matches_certain_types
```

Result: 4 passed, 3 third-party warnings. Log: `/tmp/16503-review-type-certainty.log`.

The review input uses the SDLC diff helper, which excludes task artifacts. `documentation-review.local.diff` supplies the task-spec/plan changes, new correction documents, and the exact v6.0-to-v6.1 delta for review.

The review found that a macro can read a binding assigned after its definition but before its call. Known local macro calls now use the assignment state at the call. The tests include calls before and after assignment, repeated calls, simple aliases, nested macros, and a genuine free name. Dynamic invocation remains conservative. Static AST checks also reject duplicate blocks, illegal loop assignments, and duplicate macro parameters without compiling or evaluating template expressions.

The Jinja/derive/linker command above passed 286 tests after these fixes, with 4 third-party warnings and no skips. Log: `/tmp/16503-jinja-review-fix.log`.

## Handoff to 16504

Use `ContractRecord.location` to resolve source lines against the original client YAML. Remove the duplicate binding-owner decision in 16504. Do not expose internal tokens as a new public issue field. Keep the consumer state separate from the configuration owner. Combined API/UI checks require the updated 16504 consumer and 16505 frontend; this 16503 change supplies the producer model and tests.
