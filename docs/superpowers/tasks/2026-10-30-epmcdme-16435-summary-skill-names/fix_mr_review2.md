# Fix 2: golden vectors for repository normalisation (review item 11)

Worktree: /Users/vponkratina/work/ai/codemie/.claude/worktrees/EPMCDME-16435-stlc-summary-names. Work only there. No git add/commit. English only.
Write `fix_mr_review2.CHECKPOINT.md` next to this file.

`src/codemie/service/usage_attribution/framework_sessions.py` `normalize_repository` ports the CLI `parseGitRemoteUrl` (CLI checkout, read-only: /Users/vponkratina/work/ai/codemie-code, find it by function name `parseGitRemoteUrl` in src/ and its tests under __tests__/). Docstrings cite line numbers of another repository, which go stale.

1. Replace line-number citations in framework_sessions.py with function names (file path + function name), nothing else changed in behaviour.
2. Add a golden-vector fixture `tests/codemie/service/usage_attribution/fixtures/repository_normalization_vectors.json`: list of {"input": ..., "expected": ...} taken from the CLI's own test cases for parseGitRemoteUrl / the repository value it reports (plus the existing cases in test_framework_sessions.py). Where the CLI has no test for a form, compute the expected value by actually running the CLI function with node (`npx tsx` or the compiled dist in the CLI checkout; do not modify that checkout) and say which forms came from the CLI test vs a run. A form the Python port cannot parse must be listed with expected null (undercount, never an error).
3. Add one parametrised test in test_framework_sessions.py loading the fixture; if a vector disagrees with the Python port, do NOT change the port silently: record it in the CHECKPOINT and leave that vector marked xfail with the reason.
4. In the fixture header comment field (e.g. "_comment") say it is a copy of CLI behaviour and must be regenerated when the CLI rule changes.
Run `make ruff` and `pytest tests/codemie/service/usage_attribution`.
