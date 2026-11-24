# EPMCDME-16504 correction evidence

## Scope and source

The user requested the backend correction plan and confirmed that a different agent owns the UI. No UI file was changed. The consumer trace is in `consumer-research.local.md`.

- Completed linker base: `b778b451e603f1f65e02b2eeb9e6a7ef112dea9f`.
- Original branch commit: `5b96bf5ce1b18bb211d03c5e1b3c20f7c2e7444c`.
- Rebased branch commit: `b6e7b4c8b0f81d35d29c74181cbc749f9c9e49db`.
- Rebase command: `git rebase --onto epmcdme-16503 b13d5b2086 epmcdme-16504`.
- Conflicts were resolved by retaining typed locations together with the API field and strict-source metadata. At verification time, the correction remained in the working tree and no correction commit or push had been made.

`git ls-remote origin refs/heads/epmcdme-16499 refs/heads/epmcdme-16503 refs/heads/epmcdme-16504` confirmed the engine at `fb45cbfa668646b10f299809c3e44a7b2b003ccf`, the completed linker at the base above, and the remote API at the original head. MR 4725 is still draft, unapproved, and based on that old API head. All 11 review threads remain unresolved. The extra resolvable note is the existing strict-source implementation reply, not a new finding. No MR message was posted or resolved.

## Focused checks

Before the final review fixes, the command below passed **1,837 tests**, with **3 existing workflow-generation skips** and 7 warnings:

```bash
poetry run pytest tests/codemie/workflows/validation tests/codemie/service/workflow/test_save_validation.py tests/codemie/rest_api/routers/test_workflow.py tests/codemie/configs/test_env_example.py tests/codemie/service/assistant/test_credential_validator.py tests/codemie/service/assistant/test_assistant_integration_validator.py tests/codemie/service/assistant/test_assistant_integration_validator_refactored.py tests/codemie/service/tools/test_tool_metadata_service.py -q -rs -n0
```

Log: `/tmp/16504-final-focused.log`. The three skips are existing generation tests with `WORKFLOW_GENERATION_ENABLED=False`. No enterprise exclusion is set. The installed enterprise version is `2.3.47`, equal to `poetry.lock`.

`poetry run pytest tests/codemie/rest_api/router_guards -q -rs` passed **48 tests**. The OpenAPI probe retained all **500 paths**. Its only differences were the intended HTTP 503 descriptions on create, update, new validation, and saved validation. `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines` generated those changes. The helper also regenerated unrelated route-overlap entries; that file was restored to its previous content. Existing overlap warnings remain visible.

After all review fixes, the final focused command passed **1,789 tests**, with **3 existing skips** and **5 expected failures**:

```bash
poetry run pytest tests/codemie/workflows/validation tests/codemie/service/workflow/test_save_validation.py tests/codemie/rest_api/routers/test_workflow.py tests/codemie/rest_api/routers/test_export_roundtrip.py -q -rs -n0
```

Log: `/tmp/16504-review-final-evidence.log`. `review-fix-evidence.local.md` maps each finding to the correction and tests. This command adds export-roundtrip coverage; the earlier command also checked the credential/integration helper suites. The final broad rerun covers those helpers again.

## Source metadata measurements

`poetry run python /tmp/run_count_16504.py` measured clean, warning, and strict requests, each with unchanged and normalized source, and two requests per case. There were six `yaml.safe_load` calls per request, seven safe-loader node parses with unchanged text, and eight with normalized text. There were no standalone `yaml.compose` calls on these paths.

These totals are measurements, not permanent assertions. The regression tests require one metadata build per distinct source text within a request and no reuse across requests. `parse-counts.local.json` contains the measurements.

## Live API checks

The real application ran from this checkout on ports 18081 and 18082 with local header authentication. Startup background services were disabled with `--lifespan off`; the checks used the existing local PostgreSQL and Elasticsearch services. The tests did not exercise production authentication or application startup.

```bash
ENV=local IDP_PROVIDER=local ENABLE_USER_MANAGEMENT=false WORKFLOW_CONTRACT_LINKING_ENABLED=true LITELLM_LOCAL_MODEL_COST_MAP=True poetry run uvicorn codemie.rest_api.main:app --host 127.0.0.1 --port 18081 --lifespan off
poetry run python /tmp/16504-live-check.py
ENV=local IDP_PROVIDER=local ENABLE_USER_MANAGEMENT=false POSTGRES_PORT=1 WORKFLOW_CONTRACT_LINKING_ENABLED=true LITELLM_LOCAL_MODEL_COST_MAP=True poetry run uvicorn codemie.rest_api.main:app --host 127.0.0.1 --port 18082 --lifespan off
poetry run python /tmp/16504-live-check.py http://127.0.0.1:18082 database
ENV=local IDP_PROVIDER=local ENABLE_USER_MANAGEMENT=false WORKFLOW_CONTRACT_LINKING_ENABLED=false LITELLM_LOCAL_MODEL_COST_MAP=True poetry run uvicorn codemie.rest_api.main:app --host 127.0.0.1 --port 18082 --lifespan off
poetry run python /tmp/16504-live-check.py http://127.0.0.1:18082 unlinked
```

All **35 final requests passed** after the review fixes:

- 26 linked requests: clean/warning validation, create and update, equal paired findings/coverage, original YAML line, strict rejection through all four routes, unchanged stored workflow after rejection, invalid YAML, top-level, YAML-only, and conflicting-input autonomous refusal across all four routes, deletion, and confirmed 404 after deletion.
- 8 database requests: all four routes in both formats returned a fixed safe 503 when their isolated API used an unavailable database port. The shared database was not stopped or changed.
- 1 unlinked request: contract warnings and coverage were absent.

All temporary workflows were deleted. The two API processes were stopped; `lsof -nP -iTCP:18081 -iTCP:18082 -sTCP:LISTEN` returned no listeners. The existing development service was not stopped. Durable response evidence is in `live-linked.local.json`, `live-database.local.json`, and `live-unlinked.local.json`. The script is `live-check.local.py`. Final summaries are `/tmp/16504-live-final-linked.log`, `/tmp/16504-live-final-database.log`, and `/tmp/16504-live-final-unlinked.log`.

The live script initially expected 201 for create and a flat error object. Those fixture assumptions were corrected to the existing 200 status and nested error envelope. Each created workflow was removed, including those from the initial attempts. These were verification-script corrections, not product fixes. The later conflicting-mode case found an actual update refusal bypass. Both saved routes now check explicit request mode after access and before preparation, then check the parsed mode before guardrails. The final 35-request run passed and removed its temporary workflow.

## Delivery gates

```bash
make ruff
make build
make license-check
make check-alembic
make sonar-local
make gitleaks
```

Ruff passed with 3,485 files unchanged. Package build passed. License check passed for 3,162 files, with no missing headers. Alembic check passed. Sonar explicitly skipped because `SONAR_TOKEN` is not configured; this is not a pass.

The full secret scan reported eight `generic-api-key` matches in the pre-existing 16503 local gate logs. Every match is a synthetic `test-epmcdme-*` session ID inside a `KeyError`, defined in tracked test fixtures. No source match was reported. No old evidence or scan exclusion was changed. A separate scan of the 16504 review diff passed with no leaks:

```bash
docker run --rm -i -v "$PWD:/workspace" ghcr.io/gitleaks/gitleaks:v8.30.1 stdin --no-banner --redact --config=/workspace/.gitleaks.toml < docs/superpowers/tasks/2026-11-09-epmcdme-16504-fix-cx/code-review.local.diff
```

The required sanity harness was attempted against the local API:

```bash
uvx codemie-test-harness --api-domain http://127.0.0.1:18081 run sanity-api -n 2 --reruns 2 --results-dir /tmp/16504-harness-results
```

It stopped before tests because the harness requires `/v1/local-auth/login`, which is not enabled in this local header-auth configuration. No configured harness credentials were available. Log: `/tmp/16504-harness.log`. This is blocked, not passed. Direct API verification above does not replace the complete harness.

The full test gate uses the locked enterprise package and the LaTeX tools required by PDF tests:

```bash
nix shell --impure --expr 'let pkgs = (builtins.getFlake "nixpkgs").legacyPackages.${builtins.currentSystem}; in pkgs.texlive.combine { inherit (pkgs.texlive) scheme-small collection-fontsrecommended fvextra footnotehyper upquote; }' --command env PYTEST_ADDOPTS='-rs --junitxml=/tmp/16504-full.xml' make test
```

The first full run was interrupted after a worker waited on an external GitLab request. It reported **52 failed, 32,358 passed, 554 skipped, 4 expected failures**, and 668 warnings in 639.71 seconds. This is an incomplete, failed gate.

The failures consist of 42 node IDs present in the recorded 16503 baseline failure comparison, eight stale export-roundtrip patch targets, one test fixture error already corrected before the focused run above, and the interrupted GitLab worker. The eight export tests were corrected without changing their assertions; the export file and related suites then reported 335 passed, 3 existing skips, and 5 expected failures. `full-gate-classification.local.json` records each node ID and its group.

A separate bounded diagnostic ran `poetry run pytest tests/codemie_tools/git/test_gitlab_budget_attribution.py::test_gitlab_toolkit_extracts_model_name_from_string -q -n0 -o faulthandler_timeout=20`. The stack shows a real GitLab authentication request in `handle_retry_on_status`. The diagnostic process group was interrupted after 45 seconds. No test completed. The test and toolkit files have no changes from the completed linker base. Log: `/tmp/16504-gitlab-diagnostic.log`.

The final rerun command was:

```bash
nix shell --impure --expr 'let pkgs = (builtins.getFlake "nixpkgs").legacyPackages.${builtins.currentSystem}; in pkgs.texlive.combine { inherit (pkgs.texlive) scheme-small collection-fontsrecommended fvextra footnotehyper upquote; }' --command env 'PYTEST_ADDOPTS=-rs --junitxml=docs/superpowers/tasks/2026-11-09-epmcdme-16504-fix-cx/qa-final-broad.local.xml --deselect=tests/codemie_tools/git/test_gitlab_budget_attribution.py::test_gitlab_toolkit_extracts_model_name_from_string --deselect=tests/codemie_tools/git/test_gitlab_budget_attribution.py::test_gitlab_toolkit_extracts_model_name_from_llm_instance --deselect=tests/codemie_tools/git/test_gitlab_budget_attribution.py::test_gitlab_toolkit_handles_llm_with_deployment_name' make test
```

The final broad rerun reported **42 failed, 32,754 passed, 554 skipped, 5 expected failures**, and 658 warnings in 171.48 seconds. All 42 failed node IDs match the recorded baseline failure set; no new failure ID remains. The three unchanged GitLab toolkit tests were explicitly deselected because they call real external authentication. Thus the result is a limited failed gate, not a full pass. `qa-final-classification.local.json` records the comparison; `qa-final-broad.local.log` contains the output.

Final Ruff, build, and license checks passed. The final source/test diff secret scan passed. The full-tree secret scan, Sonar, and harness limits above remain. Independent-review results are recorded below. A failed or blocked gate is not treated as passed.

## Independent review and final state

The full initial review used blind, edge-case, acceptance, and verification-gap lenses, followed by triage. It confirmed five findings. All were fixed, together with the related saved/update mode conflict found by the live test. The single check review returned **approve**, high confidence, and **zero findings**. The verdict is `code-review-check.local.json`; the evidence mapping is `code-review-fix-evidence.local.json`.

The standards audit was unavailable for this working-tree review scope and remains unverified. Code review approval does not mean that all QA gates passed. `gate-run.json` reports `passed: false`, with the full-tree secret scan as the first blocked gate. The full test gate also remains failed; Sonar and the full API harness remain unverified. The workflow cursor remains at validation, not complete.

`git diff --check` passed on the final changes. No temporary verification API is listening on ports 18081 or 18082. No UI file was changed. At verification time, no correction commit, push, MR message, or thread resolution had been made.

## MR evidence map

The test names below are in `tests/codemie/workflows/validation/` unless a different path is given. This table is a local reply draft. It is not a posted MR reply or reviewer agreement.

| Review note | Current evidence |
|---|---|
| 2515764 | `test_contract_parity.py::test_database_errors_are_safe_at_every_route_stage` and `test_database_errors_from_provider_boundaries_reach_safe_route_mapper`; eight live 503 cases. |
| 2515765 | `test_autonomous_update_refusal_precedes_guardrails_and_shared_validation`; `test_real_model_normalization_preserves_findings_and_original_lines_on_all_routes`. |
| 2515766 | `test_new_validation_checks_real_project_membership_without_write_permission`; saved write checks remain active. |
| 2515767 | `test_non_global_rejection_gives_identical_errors_warnings_and_coverage_on_every_entry`; `test_rejected_routes_keep_nonempty_warnings_and_truncation`. |
| 2516574 and 2516688 | `test_strict_source_survives_every_route_and_recoverable_formatter_failure`, including workflow/state strict, label-free form findings, graph failures, coverage, and settings failures. Shared v6.1 and task spec updated. |
| 2516689 | `test_json_response_preserves_semantic_names_without_html_entity_encoding`; `consumer-research.local.md`. The user assigned UI fixes/tests to the separate 16505 agent. |
| 2516690 | `test_contract_policy.py` checks C0/C1, U+2028/U+2029 removal and preservation of ordinary Unicode and `<>&`. |
| 2516691 | `test_contract_issues.py::test_final_fallback_is_independent_serializable_and_keeps_classification`; `test_final_issue_fallback_keeps_all_four_rejections_structured`. |
| 2516692 | `test_autonomous_update_refusal_precedes_guardrails_and_shared_validation`, with no guardrail, shared-validation, or persistence call. |
| 2516693 | `test_new_validation_refuses_autonomous_mode_found_only_in_yaml`; live YAML-only refusal. |

`test_contract_locations.py` drives real derive/report/format flow for state/tool ownership, empty bindings, custom nodes, transitions, numeric strings, Unicode, literal dots/brackets, and true list indices. The save-service tests cover metadata reuse and request isolation. Publication needs a correction commit, current gate evidence, and reviewer agreement before any thread can be resolved.
