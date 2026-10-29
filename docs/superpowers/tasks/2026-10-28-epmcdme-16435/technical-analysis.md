# Technical Research

**Task**: cli analytics sessions source detector framework
**Generated**: 2026-10-28
**Research path**: filesystem

---

## 1. Original Context

Jira EPMCDME-16435 (Bug) — "[CLI Analytics][Sessions] Sessions started via STLC Factory skills are labeled 'Pure chat' instead of a factory". The verbatim ticket body (description, steps, actual vs expected, acceptance criteria) is in /Users/vponkratina/work/ai/codemie/docs/superpowers/tasks/2026-10-28-epmcdme-16435/ticket.md — read it in full and treat it as the task_description. Summary: sessions whose first prompt is an STLC Factory skill (/stlc-factory:exploratory, /stlc-feature, /stlc-factory:stlc-foundation, /stlc-factory:suite-health) must get a STLC Factory label in the CLI Analytics Framework column and session-details header; SDLC Factory ("CodeMie AI Factory"), Superpowers and "Pure chat" labelling must stay unchanged; other session data unchanged. Note: a Jira comment hints the fix is a detector entry plus constant in the ordered SESSION_SOURCE_DETECTORS list, copying the sdlc-factory pattern — verify in the code whether this already exists on the current branch, how the sdlc-factory/superpowers detectors match (prefix/regex, skill-name forms such as the bare `/stlc-feature` without a plugin prefix), where the label text is mapped for the API/UI, and which tests cover the detectors.

(Ticket body: acceptance criteria are 4 skills labelled with an STLC Factory label in the table and the details header; SDLC/Superpowers/Pure chat unchanged; cost/tokens/duration/turns/tool calls/skills unchanged. Expected label text in the ticket: "STLC Factory (QA factory)".)

---

## 2. Codebase Findings

### Existing Implementations
- `SESSION_SOURCE_DETECTORS` does NOT exist anywhere in `src` or `tests` (grep of whole repo; also no `session_source`). The Jira hint does not match this codebase. Detection is a config-driven regex rule list, not a code list.
- `/Users/vponkratina/work/ai/codemie/src/codemie/service/analytics/delivery_framework.py` — `classify_delivery_framework(skill_names)`: loads `features:cliAnalytics.deliveryFramework` via `customer_config.get_feature_setting`; compiles `rules[].regex`/`label`; first rule whose regex `.match()`es (anchored at start) ANY skill name wins; otherwise `fallbackLabel` (default "Pure chat"). Also `get_framework_labels()` = rule labels in order + fallback (feeds the filter options).
- `/Users/vponkratina/work/ai/codemie/config/customer/customer-config.yaml:125-137` — the only place the rules and label texts live in the repo: `^sdlc-factory:` -> "CodeMie AI Factory"; `^superpowers:` -> "Superpowers"; `^bmad-` -> "BMAD"; `^opsx:` -> "OpenSpec"; `^speckit\.` -> "Spec Kit"; `fallbackLabel: "Pure chat"`. No `stlc` string appears anywhere in `src`, `config`, `tests`, `deploy-templates`. Note `features:cliAnalytics` is `enabled: false` in this file.
- `/Users/vponkratina/work/ai/codemie/src/codemie/service/usage_attribution/constants.py:80` — `DEFAULT_FRAMEWORK_SKILL_REGEX = "(sdlc-factory:|superpowers:|bmad-|opsx:|speckit\\.).*"` (Lucene regexp, used only when config has no usable rules) and `handler.py:145-160` `framework_regex()` which derives a Lucene regexp from the same config rules (strips leading `^`; skips rules containing `(?`, `\d`, `\w`, `\s`, `$`, `\b`). Used by the usage-attribution ES queries (`queries.py:399`) and `handler.py:884-943` (`classify_delivery_framework` for person recent sessions). A new config rule is picked up automatically; the hard-coded default is not.

### Architecture and Layers Affected
- Config layer: `customer-config.yaml` (repo default; deployed instances mount a ConfigMap `codemie-customer-config` at `/app/config/customer`, see `deploy-templates/values.yaml:650-660`, so the repo yaml only changes the default/dev config; deployed Preview/prod config is outside this repo).
- Service layer: `delivery_framework.py` (no code change needed for a new regex rule).
- Read paths using the classifier: `handlers/cli_analytics_handler.py:698-737` (sessions list, skills from `reader.get_skill_names_by_session` -> `session_skills`) and `:797,894-895` (session details header); `cli_analytics_jobs.py:101` (`runtime.set_classifier`) -> `repository/cli_analytics/postgres/session_derived.py` writes `session_dims.delivery_framework` (skill names = keys of `session_dims.skills` when a summary exists, else `session_skills` plus `skills` keys). Router `rest_api/routers/cli_analytics.py:598-601` `GET frameworks` returns `get_framework_labels()`. Model field `rest_api/models/cli_analytics.py:227` `delivery_framework: str | None`.
- Label text is a free string passed through to API/UI; no enum or mapping in the backend (the sibling UI repo `/Users/vponkratina/work/ai/codemie-ui/src` has no "CodeMie AI Factory" or "stlc" string either).

### Integration Points
- Skill names come from `agent.skill.dispatch` hook events (`skill_name`, `command_name`, `command_source`), documented in `.ai-run/guides/integration/cli-analytics-events.md:359-384`; the sample shows plugin skills as `<plugin>:<skill>` (e.g. `sdlc-factory:sdlc-standard`). The `skill_name` form for a bare typed `/stlc-feature` (Claude Code allows omitting the plugin prefix) is not documented in the repo; the ticket's `Skills = 1` for suite-health only confirms a skill row exists.
- Usage attribution (ES) uses the same rules via `framework_regex()`.

### Patterns and Conventions
- Rules are ordered; first match wins (positional). Regexes are anchored at skill-name start with `^`; keep them Lucene-portable if they should feed `framework_regex()`.
- Labels per rule are plain strings; the fallback label doubles as a filter option.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/cli-analytics-events.md` — `agent.skill.dispatch` contract and skill name examples.
- `.ai-run/guides/integration/cli-analytics-storage.md:153,157,296,348` — `delivery_framework` is rollup-derived (recomputed each refresh), NULL after a `--now` backfill until the refresher runs; "the classifier over the skill names".
- `.ai-run/guides/development/configuration-patterns.md` — config conventions (not read in detail).

### Architectural Decisions
- None recorded specific to delivery-framework rules beyond the code comment "To support additional signal types in future ... introduce a SessionSignals dataclass" in `classify_delivery_framework`.

### Derived Conventions
- A new framework = one new rule entry in `customer-config.yaml` plus a unit test in `test_delivery_framework.py`.

---

## 4. Testing Landscape

### Existing Coverage
- `/Users/vponkratina/work/ai/codemie/tests/codemie/service/analytics/test_delivery_framework.py` — 9 tests against `classify_delivery_framework`: sdlc beats superpowers, superpowers beats bmad, bmad, opsx, speckit, bmad beats openspec, empty list -> "Pure chat", unknown -> "Pure chat", `sdlc-factory:anything`. These run against the real `config/customer/customer-config.yaml` (default path from `CUSTOMER_CONFIG_DIR`), so editing the yaml is exercised by them.
- `tests/codemie/service/usage_attribution/test_handler.py:360-373` — `framework_regex()` with custom rules (hard-coded rule strings, not the yaml).
- `tests/codemie/repository/cli_analytics/postgres/test_session_derived.py`, `test_rollups.py`, `test_runtime.py`, `tests/codemie/service/analytics/test_cli_analytics_jobs.py` — classifier wiring with stubs.
- `tests/codemie/rest_api/routers/test_usage_attribution.py:330` sets `deliveryFramework={"rules": []}`.

### Testing Framework and Patterns
- pytest; plain functions, no fixtures needed for `test_delivery_framework.py`.

### Coverage Gaps
- No test for `get_framework_labels()`, for the `frameworks` endpoint label list, nor for the default `DEFAULT_FRAMEWORK_SKILL_REGEX` content; nothing covers any `stlc` skill name or a bare (unprefixed) skill name.

---

## 5. Configuration and Environment

### Environment Variables
- `CUSTOMER_CONFIG_DIR` (via `config.CUSTOMER_CONFIG_DIR`) locates `customer-config.yaml` (`src/codemie/configs/customer_config.py:76`).

### Configuration Files
- `config/customer/customer-config.yaml` (`features:cliAnalytics.settings.deliveryFramework`); `deploy-templates/values.yaml:650-660` mounts a deployment-specific ConfigMap `codemie-customer-config` over `/app/config/customer`.

### Feature Flags and Deployment Concerns
- `features:cliAnalytics.enabled` is `false` in the repo yaml (enabled per environment). Deployed environments take rules from their own ConfigMap, outside this repo.

---

## 6. Risk Indicators

- The ticket/Jira hint (a `SESSION_SOURCE_DETECTORS` list with constants) does not match the codebase; the mechanism is config rules in YAML. Downstream stages must not plan against a non-existent list.
- Speculative: the bare `/stlc-feature` may arrive with `skill_name` `stlc-feature` (no prefix) rather than `stlc-factory:stlc-feature`; a rule anchored only on `^stlc-factory:` would then miss it. A regex like `^(stlc-factory:|stlc-feature)` covers both but cannot be verified from the repo (event contract does not specify it; the STLC package on gitbud is not reachable locally).
- Speculative: the label text is not fixed by the ticket beyond "STLC Factory (QA factory)"; the final string needs a decision.
- Speculative: rule order matters (first match wins); a session that also used an `sdlc-factory:` skill would already be labelled by an earlier rule. Insert position should be decided.
- Speculative: the hard-coded `DEFAULT_FRAMEWORK_SKILL_REGEX` in usage_attribution lacks the new prefix; it is only used when config has no rules, but would diverge.
- Speculative: Preview/prod use a ConfigMap, so changing the repo yaml alone does not relabel deployed environments; previously stored `session_dims.delivery_framework = 'Pure chat'` rows are rewritten only when the refresher recomputes them (per storage guide: rollup-derived, recomputed each time; list/detail endpoints classify at read time).
- Lucene portability: rules containing alternation groups are fine, but `(?`, `\d`, `\w`, `\s`, `$`, `\b` are dropped from `framework_regex()`.

---

## 7. Summary for Complexity Assessment

The change surface is small: framework labelling is driven by an ordered list of `{regex, label}` rules in `config/customer/customer-config.yaml` (`features:cliAnalytics.deliveryFramework`), evaluated by `classify_delivery_framework` (first match wins, `re.match` against each activated skill name). The `SESSION_SOURCE_DETECTORS` list named in the Jira comment does not exist in this repo; no `stlc` reference exists anywhere. The label string is passed through unchanged to the sessions list, session details header and framework filter, so no API/model/UI mapping needs to be found.

Layers touched: customer config (YAML), optionally `usage_attribution/constants.py` default regex, and `tests/.../test_delivery_framework.py` (which already reads the real yaml, so rule tests are straightforward). No schema or migration is involved. Technical novelty is low (copy of the sdlc-factory rule).

Main uncertainties: the exact skill_name forms emitted for the four skills (especially bare `stlc-feature`), the final label text and rule position, and that deployed environments source the rules from a ConfigMap outside this repo. Test coverage of the classifier is decent for existing frameworks; there is no coverage of `get_framework_labels` or bare-name skills.

---

## 8. External References

None named by the task as source-of-truth paths other than `ticket.md`, which was read in full (resolved). The ticket links the STLC Factory package at https://gitbud.epam.com/epm-cdme/codemie-public-skills/-/tree/feat/stlc-factory/ai-packages/stlc-factory as the factory under test; it is a remote URL, not reachable from the filesystem, and no local checkout of it was found (searched for a directory named `stlc-factory`). Skill names there were therefore not verified; the four names are taken from the ticket: `/stlc-factory:exploratory`, `/stlc-feature`, `/stlc-factory:stlc-foundation`, `/stlc-factory:suite-health`. Expected label per the ticket: "STLC Factory (QA factory)".
