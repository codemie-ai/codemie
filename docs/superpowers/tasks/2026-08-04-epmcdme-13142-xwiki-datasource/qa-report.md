# QA Gate Report — 2026-08-04-epmcdme-13142-xwiki-datasource

**Branch**: `EPMCDME-13142_xwiki-datasource`
**Runner**: guide-first (`.ai-run/guides/quality-gates.md`), Makefile targets
**Merge base**: `origin/main`
**Started**: 2026-08-05T04:55:00Z
**Status**: BLOCKED — two gates fail, both for reasons outside this change (see below)

## Gates

| Gate | Status | Command | Notes |
|---|---|---|---|
| Lint And Format | **PASS** | `make ruff` | 2254 files unchanged by format; `ruff check --fix` and `ruff check` both clean |
| Build | **PASS** | `make build` | Built `codemie-0.8.0.tar.gz` and `codemie-0.8.0-py3-none-any.whl` |
| License Headers | **PASS** | `make license-check` | 2011 files checked, 0 missing headers |
| Secret Scan | **FAIL** | `make gitleaks` | 1 leak: `/workspace/.env` only. See below |
| Tests | **FAIL** | `make test` | 42 collection errors from the incomplete local venv. See below |
| Coverage | SKIPPED | `make coverage` | Not requested (guide: "Skip if the user did not request coverage") |
| Static Analysis | SKIPPED | `make sonar-local` | No Sonar token/config available (guide allows this skip) |
| Full Verification | N/A | `make verify` | Composite of the four gates above, all run individually. Deliberately not re-run: it silently skips the license gate on macOS because the Makefile has no `.PHONY` and the `license` target resolves to the `LICENSE` file |
| Test Harness | SKIPPED | `make test-harness` | Prerequisite missing: `~/.codemie/test-harness.json` does not exist. **Required before opening the MR** — the `auto_epm-cdme_vcs` bot fails checks 3.1/3.2 without a `## Test harness` section |

## Failure detail

### Secret Scan — `make gitleaks`

```
Fingerprint: /workspace/.env:generic-api-key:1
leaks found: 1
```

The single finding is `/workspace/.env`, which is:

- **gitignored** — `.gitignore:10` matches `.env`, confirmed with `git check-ignore -v`
- **not in this change** — `git diff --name-only origin/main...HEAD` does not contain `.env`
- **a local developer file** — a clean CI checkout has no `.env`, so this gate passes there

`make gitleaks` runs `gitleaks dir` over the whole working tree, which includes gitignored files, so it reports this on any developer machine with a populated `.env`. Nothing in the reviewed diff triggers a finding.

**Action taken during this run**: `code-review.diff` and `code-review-check.diff` were generated under the task directory for the review lenses and have been **deleted**. They are the artifact class that leaked an API key on EPMCDME-12313 and must never be committed. `find docs/superpowers -name '*.diff'` confirms none remain in this task's directory.

### Tests — `make test`

```
Interrupted: 42 errors during collection
1 skipped, 10 warnings, 42 errors in 64.64s
```

**All 42 are collection errors, not test failures, and every one of them has the same single cause.**
Verified by re-running the collection with line tracebacks:

```
$ poetry run pytest tests/ --collect-only -q --tb=line | grep -E "^E "
     42  E   ModuleNotFoundError: No module named 'zephyr'
```

`zephyr` is a **declared project dependency** — `zephyr-python-api = "^0.1.0"` at `pyproject.toml:140`. It is simply not installed in this developer's local Poetry virtualenv. Because `codemie.service.settings.settings_tester` imports the Zephyr tools, and a large part of the REST and enterprise test tree transitively imports settings, one missing package takes out 42 modules.

Confirmed directly:

```
$ PYTHONPATH=src poetry run python -c "import codemie.service.settings.settings_tester"
ModuleNotFoundError: No module named 'zephyr'
```

**Only 1 of the 42 files belongs to this change.** The other 41 are pre-existing and untouched by this branch — most importantly `tests/codemie/service/settings/test_settings_tester.py`, which this change does not modify and which fails identically.

<details>
<summary>All 42 files (single cause: <code>No module named 'zephyr'</code>) — 1 new, 41 pre-existing</summary>

New in this change:

```
tests/codemie/service/settings/test_xwiki_settings.py
```

Pre-existing, unmodified by this branch:

```
tests/codemie/rest_api/routers/test_ai_kata.py
tests/codemie/rest_api/routers/test_assistant_categories.py
tests/codemie/rest_api/routers/test_assistant_mapping.py
tests/codemie/rest_api/routers/test_assistant_marketplace.py
tests/codemie/rest_api/routers/test_assistant_mcp_tools.py
tests/codemie/rest_api/routers/test_assistant_plugin_tools.py
tests/codemie/rest_api/routers/test_assistant_prompt_variable_mapping.py
tests/codemie/rest_api/routers/test_assistant_reactions.py
tests/codemie/rest_api/routers/test_assistant_sort_params.py
tests/codemie/rest_api/routers/test_assistant_users.py
tests/codemie/rest_api/routers/test_background_router.py
tests/codemie/rest_api/routers/test_conversation.py
tests/codemie/rest_api/routers/test_conversation_pagination.py
tests/codemie/rest_api/routers/test_custom_node.py
tests/codemie/rest_api/routers/test_customer_config_router.py
tests/codemie/rest_api/routers/test_files.py
tests/codemie/rest_api/routers/test_index.py
tests/codemie/rest_api/routers/test_llm_models.py
tests/codemie/rest_api/routers/test_mcp_managed.py
tests/codemie/rest_api/routers/test_provider.py
tests/codemie/rest_api/routers/test_resume_workflow_execution.py
tests/codemie/rest_api/routers/test_share.py
tests/codemie/rest_api/routers/test_system_prompt_validation.py
tests/codemie/rest_api/routers/test_tool.py
tests/codemie/rest_api/routers/test_user_kata_progress.py
tests/codemie/rest_api/routers/test_user_settings.py
tests/codemie/rest_api/routers/test_user_settings_crud.py
tests/codemie/rest_api/routers/test_workflow.py
tests/codemie/rest_api/routers/test_workflow_execution_transitions.py
tests/codemie/rest_api/routers/test_workflow_executions.py
tests/codemie/rest_api/routers/test_workflow_executions_marketplace.py
tests/codemie/rest_api/routers/test_workflow_marketplace_router.py
tests/codemie/rest_api/test_mcp_auth_required_handler.py
tests/codemie/service/settings/test_settings_tester.py
tests/codemie_tools/qa/test_qa_toolkit.py
tests/codemie_tools/qa/zephyr/test_generic_tool.py
tests/enterprise/mcp_auth/test_client_metadata_bridge.py
tests/enterprise/mcp_auth/test_mcp_auth_status_bridge.py
tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py
tests/enterprise/mcp_auth/test_saml_initiate_bridge.py
tests/enterprise/mcp_auth/test_saml_metadata_bridge.py
```

</details>

The local venv cannot be repaired with `poetry install`: it resolves against a private GCP Artifact Registry this checkout has no access to, and `tree_sitter_languages` does not build on Python 3.13.

**Counter-evidence — the same suites with full dependencies**, in a throwaway container built from the project image:

```
$ docker run --rm -v <repo>:/repo -w /repo codemie-dev-codemie \
    sh -c '/venv/bin/python -m pytest tests/codemie/datasource/ tests/codemie/service/index/ \
           tests/codemie/rest_api/test_xwiki_index_endpoints.py tests/codemie/triggers/test_xwiki_reindex.py -q'
1438 passed, 8 skipped
```

and the settings suite that fails to collect locally:

```
$ docker run --rm -v <repo>:/repo -w /repo codemie-dev-codemie \
    sh -c '/venv/bin/python -m pytest tests/codemie/service/settings/ -q'
196 passed, 41 warnings in 31.14s
```

That run collects and passes both `test_xwiki_settings.py` (new) and `test_settings_tester.py` (pre-existing) — the two files whose local collection failure is quoted above.

Every test in this change passes with the dependencies actually installed, including the 61 xWiki loader/processor tests. CI, which installs from the lockfile, is unaffected by this local gap.

## Live end-to-end verification

Beyond the mechanical gates, the feature was exercised against a real xWiki instance:

- Migration `0f4f8b95eaaa` applied, downgraded and re-applied against the real Postgres; `index_info.xwiki` is `jsonb`.
- Backend boots with the new YAML block (`XWikiDatasourceConfig instantiated: ...` in the logs).
- Health check: `KB` → 8 pages (6 own + 2 from the descendant space), `KB.Onboarding` → 2, blank space → `field_error: space`, wrong base URL → `ConnectionException` with `field_error: url`.
- Indexing completed **7/7, 7 chunks, 0 failed, 1 skipped** (the deliberately empty fixture page).
- Elasticsearch holds all 7 documents with all nine metadata keys, including `KB.Onboarding` (the page) and `KB.Onboarding` (the space home) under **distinct** `source` URLs.
- Semantic search returns the nested `Onboarding/Checklist` page for an English onboarding question and the `%20`-encoded `Vacation Policy` page for a Ukrainian-language question.
- Re-run after the code-review fixes: still 7/7, 7 chunks, `error=false`.

## Drift signal

**no** — the implementation matches the spec's names and signatures. The one recorded deviation (the loader implements its own percent-encoding path builders instead of reusing `build_spaces_path`) is documented in the spec, the plan and the code.

## Outstanding before the MR — `make test-harness`

**This gate has NOT been run and is not covered by anything above.** qa-gates and the test harness are
different gates: nothing in this report substitutes for it.

- **Status**: not run. Prerequisite `~/.codemie/test-harness.json` does not exist on this machine.
- **Command**: `make test-harness` → `uvx codemie-test-harness --sanity-api`
- **Other prerequisites**: docker stack up (`docker compose up -d`), superadmin fixtures, and the
  `ENV=local` Bearer-hijack patch (see the `codemie-test-harness-local-setup` setup guide).
- **Why it blocks the MR**: the `auto_epm-cdme_vcs` compliance bot fails checks 3.1 and 3.2 unless the
  MR description contains a `## Test harness` section holding the **copy-pasted terminal summary** of
  the run. Screenshots are not accepted.

Run it and paste its summary into the MR description before requesting review.

## Out-of-scope finding

`git ls-files 'docs/superpowers/**/*.diff'` returns **42 tracked `.diff` files** from previous tickets, and `.gitignore` does not cover that pattern. This is the exact artifact class that leaked an API key on EPMCDME-12313. Gitleaks currently reports no secret in them, so there is no active exposure — but the pattern is unguarded against a recurrence. Out of scope for this ticket; worth a `.gitignore` rule (`docs/superpowers/**/*.diff`) as a separate change.
