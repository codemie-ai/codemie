# EPMCDME-16435: STLC Factory label in CLI Analytics Implementation Plan

**Goal:** Sessions started by STLC Factory skills get the label "STLC Factory (QA factory)" instead of "Pure chat".

**Approach:** Framework labelling is config-driven (`classify_delivery_framework` reads ordered `{regex, label}` rules), so this is one new YAML rule plus keeping the no-config default regex aligned. No classifier, API, UI or migration change.

Commit per task using the repository's existing convention.

---

### Task 1: STLC rule in customer config

**Files:**
- Modify: `config/customer/customer-config.yaml:127-130` (`deliveryFramework.rules`)
- Test: `tests/codemie/service/analytics/test_delivery_framework.py` (append; these tests read the real yaml)

Insert one rule between the `^sdlc-factory:` and `^superpowers:` entries, leaving every other rule and its order untouched:

```yaml
          - regex: "^(stlc-factory:|stlc-feature)"
            label: "STLC Factory (QA factory)"
```

Keep the regex free of `(?`, `\d`, `\w`, `\s`, `$`, `\b` so `framework_regex()` (`usage_attribution/handler.py:145-160`) includes it.

Tests to append (plain functions, same style as the file):
- the four names `stlc-factory:exploratory`, `stlc-feature`, `stlc-factory:stlc-foundation`, `stlc-factory:suite-health` each classify as the new label (parametrize);
- `["sdlc-factory:x", "stlc-factory:exploratory"]` -> "CodeMie AI Factory";
- `["stlc-factory:exploratory", "superpowers:brainstorming"]` -> new label;
- `get_framework_labels()` (import from `codemie.service.analytics.delivery_framework`) contains the new label and still ends with "Pure chat".

Existing tests must pass unchanged.

- [ ] **Test-first: yes — the four-name parametrized test and the label-list test fail on the current yaml (return "Pure chat" / label absent), pass after the rule is added.**

---

### Task 2: Align the no-config default regex

**Files:**
- Modify: `src/codemie/service/usage_attribution/constants.py:80` (`DEFAULT_FRAMEWORK_SKILL_REGEX`)
- Test: `tests/codemie/service/usage_attribution/test_handler.py` (add next to `test_framework_regex_from_cli_analytics_rules`, ~line 366-373)

Add `stlc-factory:|stlc-feature` to the alternation, placed right after `sdlc-factory:` to mirror the yaml order. Keep the leading `(sdlc-factory:` so the existing `startswith("(sdlc-factory:")` assertion still holds.

Tests to add:
- `re.fullmatch(DEFAULT_FRAMEWORK_SKILL_REGEX, name)` matches `stlc-factory:exploratory` and `stlc-feature`, and still matches `sdlc-factory:x`.
- `h.framework_regex()` with the real yaml (no patching) fullmatches `stlc-factory:suite-health` and `stlc-feature`.

- [ ] **Test-first: yes — the default-regex test fails on the current constant (no stlc alternative), passes after the edit.**

---

## Negative-constraint pass

- No `SESSION_SOURCE_DETECTORS` list or classifier algorithm/signal change: Tasks 1-2 touch only YAML, a constant and tests.
- No API model, router or UI change: none planned.
- No change to other frameworks' labels, regexes or order: Task 1 inserts one entry only.
- Not enabling `features:cliAnalytics`: `enabled: false` is left alone.
- No backfill, no deployed ConfigMap change (out of repo): none planned.
- No extra tests beyond the spec's list: Tasks 1-2 cover exactly that list.

negative-constraints: honored by Tasks 1-2, no task violates them.
