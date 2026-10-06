# Code review — 2026-09-07-epmcdme-14659-hide-elastic-retrieval-features (2026-09-07)

**approve** · confidence: high · 6/6 prior findings resolved · 0 unresolved
Coverage: targeted verifier ✓  (6/6 blocking findings graded)

## Verification summary

- CR-001 (major) resolved — `src/codemie/configs/config.py:970` `finalize_settings` now rejects any `RETRIEVAL_BACKEND` outside `{elasticsearch, none}`.
- CR-002 (major) resolved — `src/codemie/configs/config.py:1053` `retrieval_available(cfg: Config)` now typed.
- CR-003 (major) resolved — `tests/codemie/rest_api/test_main_retrieval_backend_disabled.py` exercises both `main.py:894`/`:902` module-scope conditionals via a fresh subprocess import with `RETRIEVAL_BACKEND=none`.
- CR-004 (critical) resolved — `src/codemie/service/tools/tool_execution_service.py:312` `_get_context_tools` now raises `ToolException` for KB/CODE context when retrieval is unavailable, mirroring `ToolkitService.add_context_tools`; three new tests cover it.
- CR-005 (major) resolved (not_applicable, verified) — `src/codemie_tools/code/toolkit.py`'s `CODEBASE_TOOLS` entry contains only the credentialed Sonar tool, unrelated to retrieval; AC29 does not require hiding it.
- CR-006 (major) resolved (accepted risk, verified) — `spec.md` §6 documents the unguarded `src/codemie/triggers/actors/datasource.py` trigger actor as a legitimate, pre-approved residual risk (unreachable while degraded mode blocks datasource creation).

## Checked and clean

commit-format ✓ · security ✓ · code-quality carried forward as `partial` from the prior round's audit (its cited gap, the untyped `retrieval_available` parameter, is CR-002 above, now resolved) — `standards_review` is not re-audited on a check round.
