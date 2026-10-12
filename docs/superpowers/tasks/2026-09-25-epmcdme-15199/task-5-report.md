DONE
commit: 9902a450
changed: src/codemie/service/customer_config_declarations.py, tests/codemie/service/test_customer_config_declarations.py
test_command: cd /Users/Oleksandr_Cherevach/work/codemie-dev/codemie && poetry run pytest tests/codemie/service/test_customer_config_declarations.py tests/codemie/configs/test_customer_config_override.py > /Users/Oleksandr_Cherevach/work/codemie-dev/codemie/docs/superpowers/tasks/2026-09-25-epmcdme-15199/test.local.log 2>&1; echo "EXIT=$?"; tail -25 .../test.local.log
RED: ImportError: cannot import name 'EXCHANGE_POLLING' from 'codemie.service.customer_config_declarations' (EXIT=1, collection error)
GREEN: 24 passed (declarations file alone), then 30 passed combined with override regression file (EXIT=0)
YAML check: `grep -rl "exchangePolling" --include="*.yaml" --include="*.yml" .` returned no matches — no shipped customer-config YAML enables the flag.
Reused helpers/fixtures: reused `clear_snapshot`/`publish_snapshot` from `codemie.configs.component_resolution` and the `reset_snapshot` autouse fixture pattern, and `CustomerConfig.model_construct(...)` construction style, both mirrored from `tests/codemie/configs/test_customer_config_override.py`. Declaration-shape tests mirrored the existing `SCHEDULERS` tests in the same file (test_schedulers_declaration_registered / _has_enabled_switch / test_by_component_id_finds_schedulers).
Guides applied: `.ai-run/guides/testing/testing-patterns.md` (pytest policy), `.ai-run/guides/standards/code-quality.md` (ruff format/check run on both changed files, no findings).
Concerns: none. No backend reader/consumer of the flag was added, `_validate_minimum_hourly_frequency`/cron untouched, no new dependencies. `.ai-run/sdlc-factory/doctor.json` and `docs/superpowers/tasks/` were left unstaged as instructed.
