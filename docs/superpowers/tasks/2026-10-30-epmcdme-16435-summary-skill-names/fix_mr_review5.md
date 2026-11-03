# Fix 5: Sonar cognitive complexity (EPMCDME-16435, MR !4696)

Worktree: /Users/vponkratina/work/ai/codemie/.claude/worktrees/EPMCDME-16435-stlc-summary-names (HEAD 396219aa3). Work only there. No git add/commit. English only.
Run `make ruff` and `env -u CLI_ANALYTICS_PG_URL poetry run pytest tests/codemie/repository/cli_analytics tests/codemie/service/usage_attribution -q -k "not live"`. Write `fix_mr_review5.CHECKPOINT.md` next to this file.

Sonar reports two code smells (limit 15), no behaviour change wanted:
1. `src/codemie/service/usage_attribution/framework_sessions.py` line ~80, the function reported at 32 (the URL/scp remote parser `_parse_git_remote_url`, a port of the CLI `parseGitRemoteUrl`): split into small named helpers (one per step of the CLI function: scheme/scp detection, host/path split, path cleanup, segment trimming), each below the limit, keeping behaviour byte-for-byte. The golden vectors in tests/codemie/service/usage_attribution/fixtures/repository_normalization_vectors.json (all 150+) are the safety net and must stay green unchanged. Keep function names in the docstring citations (no CLI line numbers).
2. `src/codemie/service/usage_attribution/worker.py` line ~347, `fetch_inputs` at 18: extract cohesive steps into private helpers (module-level in worker.py or in framework_merge.py if it belongs there; worker.py is over ~500 lines so prefer the sibling module for anything new) without changing behaviour, ordering of the DB/ES reads, db_queries timing entries, or the cached-blob paths. Existing worker tests must stay green.

Estimate the complexity of each result yourself (count nesting + branches) and state in the CHECKPOINT that each function and each new helper is <= 15; if a Sonar/radon-like tool is available locally (`poetry run radon cc -s` or `flake8 --max-cognitive-complexity`), use it and report the numbers, else say "estimated by hand".
No other change. Do not touch .codemie/ or doctor.json.
