# Fix 2 CHECKPOINT: golden vectors for repository normalisation

## Items

1. Done. `src/codemie/service/usage_attribution/framework_sessions.py` module docstring: the four line-number citations (`processes.ts:420-530`, `processes.ts:541-563`, `paths.ts:41-43, 61-64, 396-424`, `metrics-post-processor.ts:95-135`) now name file path + function only. No behaviour change. Remaining line citations in this repo are historical task artifacts (`task-3.3a-report.md`, `code-review.local.diff`, `lens-blind.local.md`); left untouched.
2. Done. `tests/codemie/service/usage_attribution/fixtures/repository_normalization_vectors.json`: object with `_comment` and `vectors` (list of `{input, expected, source}`), 124 vectors.
   - `source: cli_test` (68): input and expected taken from the CLI test `src/utils/__tests__/processes-git.test.ts` (all of its `detectGitRemoteRepo` cases; undefined -> `null`). A run of the CLI confirmed every one of them.
   - `source: cli_run` (56): forms the CLI tests do not cover, including all existing cases of `test_framework_sessions.py` not already in the CLI test, plus extras (credentials with `@` in password, `git@host`, `ssh://git@host`, relative paths, empty string, non-ASCII, valid and invalid UTF-8 escapes, `.git.git`, double slashes, Azure `v3` edge cases, Bitbucket ssh `scm`, multiple `scm` segments, `file:///`). Expected is the real CLI output.
   - How the CLI was run: `parseGitRemoteUrl` is not exported, so `detectGitRemoteRepo` from the current CLI source `src/utils/processes.ts` was bundled with esbuild into the scratchpad (CLI checkout not modified, `git status` there unchanged by me) and executed with node v24 against a fake `git` on PATH that prints the input as `git remote get-url origin`.
   - Forms the Python port cannot parse (CLI `undefined`) are listed with `expected: null` (10 from the CLI tests, plus `git@host`, `ssh://git@host`, empty string).
   - Working-directory fallback (`extractRepository`) is not in the fixture; it stays covered by `test_normalize_repository_from_cwd`.
3. Done. `test_normalize_repository_matches_cli_golden_vectors` in `test_framework_sessions.py` (parametrised over the fixture, calls `normalize_repository(input, "")`). Optional per-vector `"xfail": "<reason>"` key makes it a strict xfail. Disagreements between the port and the CLI: **none** (all 124 vectors pass, no xfail used; the port was not changed).
4. Done. `_comment` states it is a copy of CLI behaviour and must be regenerated when the CLI rule changes.

## Test that can fail

`test_normalize_repository_matches_cli_golden_vectors`: the `assert normalize_repository(remote, "") == expected` fails for any vector if the port's rule diverges from the CLI (e.g. dropping the `scm/` strip, the `~` strip, or the credential bail-out changes the result for the corresponding vector). Not run red by reverting (not required).

## Runs

- `make ruff`: all checks passed, 3159 files unchanged.
- `poetry run pytest tests/codemie/service/usage_attribution`: 575 passed (before: 451, the 124 new are the golden vectors). Full `tests/` run not done (directive asks only for the usage_attribution suite).

## Not done

none
