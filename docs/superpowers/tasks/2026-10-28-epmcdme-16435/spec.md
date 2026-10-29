# EPMCDME-16435: STLC Factory label in CLI Analytics

## Problem

Sessions started by STLC Factory skills (`/stlc-factory:exploratory`, `/stlc-feature`,
`/stlc-factory:stlc-foundation`, `/stlc-factory:suite-health`) show "Pure chat" in the Sessions
table Framework column and in the session-details header. SDLC Factory ("CodeMie AI Factory"),
Superpowers and other frameworks are labelled correctly.

## Root cause

Framework labelling is config-driven, not a code list. `classify_delivery_framework`
(`src/codemie/service/analytics/delivery_framework.py`) reads ordered `{regex, label}` rules from
`features:cliAnalytics.deliveryFramework` in `config/customer/customer-config.yaml:125-137`. The
first rule whose regex `re.match`es any activated skill name wins, otherwise `fallbackLabel`. No
rule covers STLC skills. (The `SESSION_SOURCE_DETECTORS` list named in the Jira comment does not
exist in this repo.)

## Approach

Add one rule to the `deliveryFramework.rules` list in `customer-config.yaml`:

- Regex: `^(stlc-factory:|stlc-feature)`. It covers the plugin-prefixed form and the bare
  `/stlc-feature` form, since the `skill_name` emitted for a bare typed command is not documented
  in the repo. The regex stays Lucene-portable (no `(?`, `\d`, `\w`, `\s`, `$`, `\b`), so
  `framework_regex()` (`usage_attribution/handler.py:145-160`) picks it up automatically.
- Label: `STLC Factory (QA factory)`, the text given in the ticket's expected column.
- Position: after `^sdlc-factory:` and before `^superpowers:`. Existing relative order of all other
  rules is unchanged.

Keep `DEFAULT_FRAMEWORK_SKILL_REGEX` (`usage_attribution/constants.py:80`) aligned by adding the
same alternatives, so the no-config fallback does not diverge from the yaml.

No classifier code, API model, UI or migration change. The label is passed through as a string to
the sessions list, session details and the `frameworks` filter options.

## Acceptance criteria

1. Skill names `stlc-factory:exploratory`, `stlc-feature`, `stlc-factory:stlc-foundation` and
   `stlc-factory:suite-health` each classify as `STLC Factory (QA factory)`.
2. The label appears in the Sessions table Framework column, the session-details header and
   `get_framework_labels()` (and so the frameworks filter), with no endpoint changes.
3. `sdlc-factory:*`, `superpowers:*`, `bmad-*`, `opsx:*`, `speckit.*` still classify as before; an
   empty or unrelated skill list still gives "Pure chat". Existing tests in
   `tests/codemie/service/analytics/test_delivery_framework.py` pass unchanged.
4. A session with both an `sdlc-factory:` and an `stlc-factory:` skill is labelled
   "CodeMie AI Factory" (rule order); one with `stlc-factory:` and `superpowers:` is labelled
   STLC.
5. Cost, tokens, duration, turns, tool calls and skills data are untouched.
6. Unit tests added to `test_delivery_framework.py` for the four names, the ordering cases above and
   `get_framework_labels()` containing the new label; a test for `framework_regex()`/default regex
   matching `stlc-factory:` and `stlc-feature`.

## Non-goals

- No new `SESSION_SOURCE_DETECTORS` list or any change to the classifier algorithm or signal types.
- No change to API models, routers or the UI repo.
- No change to the config of deployed environments: Preview and prod mount the
  `codemie-customer-config` ConfigMap (`deploy-templates/values.yaml:650-660`), which is outside
  this repo and must get the same rule separately.
- No backfill job; stored `session_dims.delivery_framework` values are recomputed by the existing
  refresher, and list/detail endpoints classify at read time.
- No change to other frameworks' labels, regexes or order, and no enabling of
  `features:cliAnalytics`.
- No new tests of the other repo paths beyond those listed above.

## Risks

- The real `skill_name` for bare `/stlc-feature` is unverified; the regex covers both plausible
  forms, but any other form would stay "Pure chat".
- Deployed environments need the same rule in their ConfigMap to see the fix.
