# EPMCDME-16411 unit failures vs merge base b92bcfd9d

Verdict: **no new regression from the branch's commits.** All 284 tracked failing/erroring node IDs either fail on the base too, fail on the base once the main tree's uncommitted TMP config is applied, or are order-dependent under xdist and pass on both base and branch in a controlled subset run.

## Method
- Branch full suite: `poetry run pytest tests/ -rfE -p no:cacheprovider` (pytest.ini adds `--import-mode=importlib -n 3`, `pythonpath = src`), giving `branch-full.local.log`: 248 failed, 39 errors, 285 unique node IDs (`branch-ids.local.txt`). Same counts as the gate run.
- Base: a detached worktree of b92bcfd9d at `C:/wt16411` (11 chars, vs the repo path `C:\Users\Yauheni_Hil\repos\codemie-dev\codemie`, 46 chars). It reused the main poetry venv python, with the venv `Scripts` on PATH so `ruff` is found, `PYTHONPATH=<worktree>/src` and `--rootdir/-c` set to the worktree. `codemie` is not installed in the venv, so imports resolved to the worktree src, which the traceback paths confirm.
- The parametrized IDs contain spaces, `\n` and quotes, so the run used the **54 files** that hold the failures rather than the individual IDs, and results were compared per node ID. The identical subset was also run on the branch (`branch-subset.local.log`) as a control.
- Logs: `base-run.local.log` (227 failed / 38 errors), `branch-subset.local.log` (241 failed / 38 errors), `base-with-local-config.local.log`.
- The worktree was removed afterwards. The main working tree was not modified.

## Classification (284 IDs, excluding the untracked repro file)
| Class | Count | Notes |
|---|---|---|
| (a) also fails on base | 263 | `a.local.txt`: WinError 206, `/bin/bash` hook tests, mcp_auth bridges, migrations UnicodeDecode, etc. |
| (a') fails on base only with the main tree's uncommitted config | 14 | `diff14.local.txt`, see below |
| order-dependent, passes on base **and** on branch in the subset run | 7 | `orderdep.local.txt` |
| (b) new regression | **0** | |
| (c) not collected on base | 0 | none of the branch's new test files fail |
| untracked local file | 1 | `tests/codemie/rest_api/a2a/test_tmp_repro_12417.py::test_case2_heavy_run_over_streaming_hits_idle_timeout`, absent in the worktree |

### The 14 config-caused failures
They fail on the branch subset and pass on the clean base. The working tree carries uncommitted `config/customer/customer-config.yaml` (`features:projectModelOverride: enabled: true  # TMP EPMCDME-16411 repro`) and extra `repro-*` models in `config/llms/llm-dial-config.yaml`. With those two files copied into the base worktree, **all 14 fail on base too** (14 failed / 226 passed). Traceback heads:
- `test_assistant.py::TestAssistantMcpGovernance::*` (3): `ModelNotWhitelistedException: ('gpt-4o', 'demo')` from `model_availability_service.py:188` via `routers/assistant/management.py:248`
- `test_toolkit_service.py::TestInitializeToolOcr*` (3): `ModelNotWhitelistedException` from `toolkit_service.py:872`
- `test_assistant_service_build_agent_deleted_datasource.py` (2): `assistant_service.py:565 build_agent` calls `enforce_allowed_model` and then `get_project_models`, raising `InvalidRequestError` (session.get on a mocked project). It touches **assistant_service** through the override path but not the branch's changed modules.
- `test_project_service.py` (5) + `test_project_service_cli_analytics.py` (1): `ExtendedHTTPException` at `project_service.py:744`

None of these tracebacks go through assistant_factory, assistant_engine_builder or exception_formatting.

### Order-dependent (7)
These pass on the base and on the branch in the same 54-file subset run. They fail only in the full-suite xdist run, through sys.modules or import-isolation leakage:
`test_job_lock::test_lock_key_is_stable_across_processes`, `test_authorizer::...do_not_pull_in_the_kubernetes_stack[authorizer|handler]`, `test_tool_call_protocol::...does_not_import_the_sandbox_stack`, `test_discovery_probe_bridge::...does_not_import_discovery_probe_facade_at_module_load`, `test_config_validation::test_credential_gate_is_bounded_at_match_time`, and `test_logout_token_cleanup_mock_tms::test_logout_removes_only_the_users_rows_and_pod_fallback`. The last one is skipped in both subset runs because `codemie_enterprise` is not installed; in the full run another test stubs that module.

## Action
Revert the TMP config edits before the final gate run to clear the 14 config-caused failures. The rest are pre-existing Windows/environment failures.
