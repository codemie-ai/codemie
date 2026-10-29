# CHECKPOINT: fix_stlc_feature_prefix (EPMCDME-16435)

## Files changed (tests only, no source/YAML change)
1. `tests/codemie/service/analytics/test_delivery_framework.py`: new parametrized
   `test_stlc_feature_is_an_open_prefix` (`stlc-feature`, `stlc-feature-x`) asserting
   `classify_delivery_framework` returns "STLC Factory (QA factory)"; comment says the open prefix is deliberate. Done.
2. `tests/codemie/service/usage_attribution/test_handler.py`: extended the existing
   `test_default_framework_regex_matches_stlc_and_sdlc` params with `stlc-feature-x` (`stlc-feature` was already
   there), both now `re.fullmatch` `DEFAULT_FRAMEWORK_SKILL_REGEX`. Done.
3. Ran both files: 57 passed. `ruff check`: all checks passed. `ruff format --check`: 2 files already formatted.

## Can the tests fail
- New delivery test: would fail on `stlc-feature-x` if the rule were anchored with `$` or an exact-name match
  (classification would fall through to another label).
- Handler test: the `stlc-feature-x` case fails if `DEFAULT_FRAMEWORK_SKILL_REGEX` lost the open `stlc-feature` prefix.

## Not done
none
