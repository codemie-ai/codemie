# Fix: pin the open-prefix behaviour of the STLC rule (EPMCDME-16435)

Context: the rule `^(stlc-factory:|stlc-feature)` and `DEFAULT_FRAMEWORK_SKILL_REGEX` in
`src/codemie/service/usage_attribution/constants.py` intentionally match `stlc-feature` as an open prefix
(no `$`: rules containing `$` are dropped from the Lucene `framework_regex()`). Decision: keep the code as is.

Do exactly this, nothing else (no source or YAML change):
1. In `tests/codemie/service/analytics/test_delivery_framework.py`, add one parametrized test asserting
   that `stlc-feature` and `stlc-feature-x` both classify as "STLC Factory (QA factory)" (follow the
   style of the existing STLC tests in that file; a short comment says the open prefix is deliberate).
2. In `tests/codemie/service/usage_attribution/test_handler.py`, extend the existing default-regex test
   (or add one case) so `stlc-feature` and `stlc-feature-x` both match `DEFAULT_FRAMEWORK_SKILL_REGEX`.
3. Run both test files and `ruff check` + `ruff format --check` on the changed files. No git operations.

Write `fix_stlc_feature_prefix.CHECKPOINT.md` next to this file: files changed, test results.
