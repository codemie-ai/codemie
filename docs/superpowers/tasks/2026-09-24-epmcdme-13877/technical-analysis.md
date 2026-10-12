# Technical Research

**Task**: code-executor security-policies severity threshold (EPMCDME-13877)
**Generated**: 2026-09-24
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-13877 (Bug) — Fix inverted semantics of CodeExecutor security_threshold parameter.

The full ticket body is at docs/superpowers/tasks/2026-09-24-epmcdme-13877/ticket.md — READ IT FIRST.

Short form: `CodeExecutorConfig.security_threshold` uses `SecurityIssueSeverity` (SAFE, LOW, MEDIUM,
HIGH). The enforcement check in src/codemie_tools/data_management/code_executor/security_policies.py
is currently `SAFE < threshold <= pattern.severity`, which makes threshold=LOW the STRICTEST setting
(blocks MEDIUM and HIGH patterns) and threshold=HIGH the most PERMISSIVE (blocks only HIGH). That is
inverted from user intuition and from the comments in default_security_policies.yaml. The fix must
invert the semantics so LOW = permissive/blocks less and HIGH = strict/blocks more. The reporter
also wants the whole policy-evaluation flow reviewed end to end, all severity levels validated,
docs/comments/env-var documentation corrected, tests that assert the inverted behavior updated, and
downstream configs/deployments reviewed before changing the default.

(Full ticket text, including Preconditions, Scenarios of Use, Affected Areas, and Acceptance
Criteria, is on disk at `docs/superpowers/tasks/2026-09-24-epmcdme-13877/ticket.md` and was read in
full during research — see Section 8.)

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie_tools/data_management/code_executor/security_policies.py`
  - `check_security_policy(policy, code)` (lines 276-341) — the regex-layer enforcement point. Line
    295: `threshold = policy.severity_threshold or SecurityIssueSeverity.SAFE`. Line 338 is the exact
    condition named in the ticket: `if SecurityIssueSeverity.SAFE < threshold <= pattern_obj.severity: return False, violations`. Algebraically this is `threshold > SAFE AND pattern.severity >= threshold` — i.e. "block when the pattern's severity is at or above the configured threshold", with an added guard that threshold=SAFE never blocks.
  - `get_restricted_module_names(severity_threshold, yaml_config_path)` (lines 229-273) — a second,
    independent call site with the *same-direction* semantics: line 270,
    `restricted_names = [module.name for module in policy.restricted_modules if module.severity >= severity_threshold]`. Its own docstring (lines 239-244) documents the current (ticket-inverted) behavior explicitly: "LOW (1): returns modules with LOW, MEDIUM, and HIGH severity" / "HIGH (3): returns only HIGH severity modules". This function's output feeds the tool's `blocked_modules_str` description text shown in `code_executor_tool.py`, so its docstring and behavior both need to change in lockstep with `check_security_policy`.
  - `get_codemie_security_policy(severity_threshold, yaml_config_path)` (lines 150-226) — loads YAML,
    then sets `policy.severity_threshold = severity_threshold` (line 223). Its docstring (lines
    171-177) repeats the same SAFE/LOW/MEDIUM/HIGH semantics text found in `models.py` and the YAML
    header comment.
  - `_SecurityPolicyLoader` — YAML parsing only; not directly involved in the enforcement direction,
    but explicitly logs a warning (lines 70-74) if `severity_threshold` is present in the YAML,
    because threshold is intentionally supplied only via `CodeExecutorConfig`/env var, never the
    policy file itself.

- `src/codemie_tools/data_management/code_executor/ast_security_checker.py`
  - `CodeExecutorSecurityChecker._add_violation` (lines 113-125): `if severity >= self.threshold:` —
    a **third** call site with the same-direction semantics as `check_security_policy`, expressed
    without the `SAFE <` guard. All violations this checker can produce (`__import__`, `exec`/
    `eval`/`compile`, `__builtins__` access, `getattr(__builtins__, …)`) are hard-coded to
    `SecurityIssueSeverity.HIGH` (lines 58, 66, 84, 104). Because `HIGH` is the maximum enum value,
    `severity >= self.threshold` is `True` for *every* valid threshold (SAFE..HIGH) — the AST layer
    currently blocks these bypass techniques unconditionally, regardless of the configured
    threshold. This is a distinct, notable finding: the AST layer's threshold parameter is
    effectively a no-op today because it is never fed anything below HIGH severity.
  - `check_code_with_ast(code, severity_threshold)` (lines 128-168) is the public entry, called from
    `check_security_policy` (security_policies.py line 298) as "Layer 1" before the regex layer runs.

- `src/codemie_tools/data_management/code_executor/models.py`
  - `CodeExecutorConfig.security_threshold` field (lines 183-192): `default=SecurityIssueSeverity.LOW`, with an inline description repeating the SAFE/LOW/MEDIUM/HIGH semantics text ("LOW (1): allows read operations like requests.get()." etc.) — this is the "documented requirements" the ticket names as needing correction.
  - `validate_security_threshold` field_validator (lines 302-335) — parses str/int/enum into
    `SecurityIssueSeverity`; purely a type-coercion validator, no enforcement-direction logic to fix.
  - `from_env()` classmethod (lines 337-414): `security_threshold=os.getenv("CODE_EXECUTOR_SECURITY_THRESHOLD", "LOW")` (line 404) — default `"LOW"` when the env var is unset. The `from_env()` docstring (line 365) lists the env var but not its semantics.

- `src/codemie_tools/data_management/code_executor/code_executor_tool.py`
  - Lines 251-257: `CodeExecutorTool.__init__` calls `get_codemie_security_policy(severity_threshold=self.config.security_threshold, ...)` and `get_restricted_module_names(severity_threshold=self.config.security_threshold, ...)`, feeding the tool-description text (`blocked_modules_str`). This is the primary consumer that wires `CodeExecutorConfig.security_threshold` into the two enforcement-adjacent call sites above.

- `src/codemie_tools/data_management/code_executor/sandbox_guard.py` and `filesystem_policy.py` — do
  **not** read `security_threshold` or `SecurityIssueSeverity` at all. They implement a separate,
  independent runtime defense-in-depth layer (monkeypatched `os`/`shutil`/`sqlite3`/`builtins` +
  `sys.addaudithook`) that enforces workspace-root path boundaries at execution time, unrelated to
  the pre-execution severity-threshold policy check. `filesystem_policy.py` line 17 states explicitly: "SECURITY BOUNDARY CLASSIFICATION: this module is DEFENSE-IN-DEPTH ONLY." No changes to these two files are implied by this ticket's scope.

- **External library convention** — `venv/Lib/site-packages/llm_sandbox/security.py`:
  - `SecurityIssueSeverity(IntEnum)`: `SAFE=0, LOW=1, MEDIUM=2, HIGH=3` (lines 9-15) — a plain
    ordered `IntEnum`, so `<`, `<=`, `>=` comparisons between severities/thresholds work as integer
    comparisons.
  - `SecurityPolicy.severity_threshold` field docstring (line 49): "The minimum severity level at
    which security issues will be blocked." This is the upstream library's own documented
    convention — i.e. "block when `pattern.severity >= threshold`" — and it is exactly the
    convention CodeMie's three call sites currently implement (in three different syntactic forms).
    The ticket is asking CodeMie's own `security_threshold` config field to mean something different
    from — and inverted relative to — the upstream `SecurityPolicy.severity_threshold` field it is
    assigned into. Any fix needs to either invert the value before assignment into
    `SecurityPolicy.severity_threshold` (keeping the library's own field semantics intact) or
    otherwise reconcile the two meanings; this is a design decision for the spec/plan stage, not
    resolved here.

### Architecture and Layers Affected

- **Configuration layer**: `CodeExecutorConfig` (Pydantic model in `models.py`) — field default,
  field description, `from_env()` docstring.
- **Policy/domain layer**: `security_policies.py` (regex-pattern enforcement, module-name filtering,
  YAML loading) and `ast_security_checker.py` (AST-based bypass detection) — both implement
  threshold-vs-severity comparisons that must move in the same direction together.
- **Tool/integration layer**: `code_executor_tool.py` — wires config into both policy functions and
  surfaces `get_restricted_module_names` output into the tool's LLM-facing description string; no
  enforcement-direction logic of its own, but its behavior (which modules appear as "blocked" in the
  description) will change when the two functions it calls change.
- **Data/policy-definition layer**: `default_security_policies.yaml` — pattern/module severity
  assignments and the header comment block (lines 8-12) describing SAFE/LOW/MEDIUM/HIGH semantics.
- **Documentation layer**: `code_executor/README.md` (environment-variable reference table) and the
  various docstrings above.
- Runtime filesystem guard (`filesystem_policy.py`, `sandbox_guard.py`) is a separate, unaffected
  layer (see above).

### Integration Points

- `CodeExecutorConfig.security_threshold` → `CodeExecutorTool.__init__` → `get_codemie_security_policy()` → `SecurityPolicy.severity_threshold` (upstream `llm_sandbox` model) → `check_security_policy()` (reads `policy.severity_threshold`) → `check_code_with_ast()` (AST layer, "Layer 1") and inline regex loop (Layer 2).
- `CodeExecutorConfig.security_threshold` → `CodeExecutorTool.__init__` → `get_restricted_module_names()` → tool description string (`blocked_modules_str`), a second, parallel consumer of the same config value.
- `CODE_EXECUTOR_SECURITY_THRESHOLD` env var → `CodeExecutorConfig.from_env()` → same chain as above.
- No other module in `src/codemie_tools/data_management/code_executor/` imports `security_threshold`
  or `SecurityIssueSeverity` (confirmed via repo-wide grep — only `models.py`, `security_policies.py`,
  `code_executor_tool.py`, and `ast_security_checker.py` reference these names in `src/`).

### Patterns and Conventions

- Pydantic v2 `field_validator(..., mode="before")` is the established pattern for config
  normalization in `models.py` (used for `execution_mode`, `sandbox_mode`, `runtime_class_name`,
  `security_threshold`) — confirmed by a prior technical-analysis document
  (`docs/superpowers/tasks/2026-08-21-epmcdme-13733-container-runtime-none/technical-analysis.md`).
- YAML-declared severities are parsed through `_SecurityPolicyLoader._parse_severity`, which accepts
  string, int, or enum forms — any fix that changes comparison direction should not need to touch
  this parsing path, only the comparison that consumes the parsed values.
- `SecurityIssueSeverity` is a plain `IntEnum` from the third-party `llm_sandbox` package (not
  owned by this repo) — its ordering (SAFE < LOW < MEDIUM < HIGH) is fixed by that library and is
  not itself in scope to change.

---

## 3. Documentation Findings

### Guides and Architecture Docs

No `.ai-run/guides/` file covers Code Executor security-threshold semantics specifically. The
closest general guide, `.ai-run/guides/development/security-patterns.md`, was checked and contains
no reference to `security_threshold`, `SecurityIssueSeverity`, or Code Executor.

`src/codemie_tools/data_management/code_executor/README.md` documents
`CODE_EXECUTOR_SECURITY_THRESHOLD` (default `LOW`) in its environment-variable reference table
(lines 33, 45) and in three usage examples (Local Development, Production, Docker Compose, lines
158, 170, 188), but — unlike `models.py` and the YAML header — it does **not** currently spell out
per-level semantics (no "SAFE blocks nothing / LOW allows reads / …" text), so it carries lower
direct-inversion risk but should still be checked for consistency once the semantics change.

### Architectural Decisions

- Git history shows the `pathlib` scenario cited in the ticket was already independently remediated:
  commit `83da83c4e` ("EPMCDME-13695: Allow pathlib in security policy for workspace scripts")
  removed `pathlib` from `restricted_modules` entirely, replacing it with the comment now on lines
  40-41 of the YAML ("pathlib is allowed — runtime fs guard enforces workspace boundaries; blocking
  the import breaks legitimate presentation and data scripts."). `pathlib` is **not** currently
  present anywhere in `default_security_policies.yaml`'s `restricted_modules` or `patterns` lists.
  The general inverted-threshold bug described in the ticket still applies to the other
  MEDIUM-severity entries that remain: `glob`, `tempfile`, `requests`, `inspect`, `importlib`
  (modules) and the `requests.(get|post|put|delete)` pattern.
- `filesystem_policy.py` (lines 15-32) carries an explicit inline "SECURITY BOUNDARY CLASSIFICATION"
  comment establishing that the runtime guard is defense-in-depth only and not a substitute for the
  severity-threshold policy check — relevant context for scoping this ticket away from that module.
- `security_policies.py` lines 68-74 record a deliberate decision that `severity_threshold` must
  never be set via the YAML file itself, only via `CodeExecutorConfig`/env var — a constraint any
  fix must continue to respect.

### Derived Conventions

- The three enforcement call sites (`check_security_policy`, `get_restricted_module_names`,
  `CodeExecutorSecurityChecker._add_violation`) each express the same underlying "block if
  severity >= threshold" relationship in a different syntactic form. A correct fix needs to locate
  and change all three consistently — grepping for the literal string from the ticket
  (`SAFE < threshold <= pattern.severity`) alone would only find one of the three.
- Severity/threshold semantics are currently documented redundantly in at least four places that
  must stay in sync: `models.py` field description, `models.py` `from_env()` docstring (env var list
  only, no semantics), `default_security_policies.yaml` header comment, and
  `get_codemie_security_policy` / `get_restricted_module_names` docstrings in `security_policies.py`.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie_tools/data_management/code_executor/test_security_config.py`:
  - `TestSecurityThresholdConfig` — config parsing/env-var loading only (SAFE/LOW/MEDIUM/HIGH round
    trip, case-insensitivity, invalid-value rejection). No assertions about blocking direction; not
    expected to need semantic changes.
  - `TestSecurityPolicyThreshold` — asserts `get_codemie_security_policy(...).severity_threshold`
    equals the input threshold (pass-through only, not enforcement direction).
  - `TestSecurityThresholdIntegration.test_tool_uses_config_threshold` /
    `test_tool_default_threshold_is_low` — assert the tool wires config threshold through to
    `security_policy.severity_threshold`; pass-through only.
  - `TestOsModulePolicy` and `TestPipModulePolicy` — call `check_security_policy` with
    `severity_threshold=SecurityIssueSeverity.LOW` and assert `os.system`/`os.remove`/`os.rmdir`/
    `os.unlink`/`os.environ[...]`/`pip` import are blocked. All of these patterns/modules are
    **HIGH** severity in the YAML, so — under either the current or the ticket's desired inverted
    semantics — a HIGH-severity pattern is blocked at LOW threshold in the current (strict-at-LOW)
    behavior, and would need to remain blocked at LOW under a naive "swap LOW/HIGH" fix only if LOW
    is still defined to block HIGH-severity patterns (the ticket's own example, `threshold=LOW`
    blocking `pathlib`/MEDIUM, implies LOW should still block HIGH severity, just not MEDIUM). These
    tests likely do not need behavioral changes, but should be re-run/re-verified once the
    comparison changes, since they are the closest existing regression net for the HIGH-severity
    path.
  - `TestRequestsSecurityPatterns` — asserts `requests` module/pattern severity is MEDIUM; does not
    assert blocking behavior at any threshold.

- `tests/codemie_tools/data_management/code_executor/test_ast_security_checker.py`:
  - `TestSeverityThreshold.test_high_threshold_blocks_high_severity`,
    `test_low_threshold_blocks_high_severity`, `test_safe_threshold_allows_everything` (lines
    117-141) directly exercise `check_code_with_ast`'s threshold filtering. The test names
    themselves ("LOW threshold blocks HIGH severity") describe the current (ticket-named-inverted)
    behavior at the boundary case; `test_safe_threshold_allows_everything`'s body (lines 133-141)
    only asserts `isinstance(is_safe, bool)` — i.e. it does not actually assert SAFE permits the
    code, despite its name and docstring claiming so. This is a pre-existing weak/no-op assertion,
    independent of the ticket but relevant to "test coverage gaps."

- `tests/codemie_tools/data_management/code_executor/test_sandbox_guard.py` and
  `test_filesystem_policy.py` — cover the runtime filesystem guard only; contain no references to
  `security_threshold` or `SecurityIssueSeverity` (confirmed via grep). Not part of the threshold
  enforcement flow.

- `tests/codemie_tools/data_management/code_executor/test_models.py`:
  `TestSecurityThresholdValidator` (lines 181-254) — pure field-validator tests (string/int/enum
  coercion, invalid values). No enforcement-direction assertions.

### Testing Framework and Patterns

- `pytest` (see `pytest.ini`: `testpaths = tests`, `pythonpath = src`, `-n 2` parallel execution via
  `pytest-xdist`, `env` plugin for fixed test env vars `ENV=local`, etc.).
- `unittest.TestCase`-style test classes mixed with plain pytest test classes are both present in
  this test directory (`test_security_config.py` uses `unittest.TestCase`; `test_ast_security_checker.py` uses plain classes) — no single mandated style in this file group.
- `unittest.mock.patch` / `patch.dict(os.environ, ...)` is the standard pattern for env-var-driven
  config tests here.
- No fixtures/factories specific to security-policy severity are defined; each test builds policies
  directly via `get_codemie_security_policy(severity_threshold=...)`.

### Coverage Gaps

- **No existing test exercises a MEDIUM-severity pattern/module under different thresholds** (e.g.
  `glob`, `tempfile`, `requests`, `inspect`, `importlib`, or the `requests.(get|post|put|delete)`
  regex pattern) to confirm it is blocked/allowed correctly at LOW vs MEDIUM vs HIGH. This is exactly
  the gap the EPMCDME-13695 `pathlib`-at-MEDIUM scenario exposed, and `pathlib` itself is no longer
  in the policy to serve as that regression case — a new test target (e.g. `glob` or `requests`,
  both still MEDIUM in the YAML) would be needed to cover the acceptance criterion "the scenario …
  is validated."
- No test asserts the *relative ordering* of strictness across all four threshold levels for the
  same pattern (e.g. "a MEDIUM-severity pattern is blocked at MEDIUM and HIGH but not at LOW or
  SAFE") — the acceptance criteria explicitly ask for "regression coverage confirms the intended
  ordering of LOW, MEDIUM, HIGH strictness," which has no current analogue.
- `get_restricted_module_names` has tests only for pass-through equality
  (`policy.severity_threshold == input`), not for which module names are actually returned at each
  threshold level — the function's own docstring gives concrete examples (lines 253-257) that are
  not backed by tests.
- `check_code_with_ast`'s `test_safe_threshold_allows_everything` does not actually assert SAFE
  permits code (see above) — a real gap independent of this ticket.

---

## 5. Configuration and Environment

### Environment Variables

- `CODE_EXECUTOR_SECURITY_THRESHOLD` — read in `CodeExecutorConfig.from_env()`
  (`src/codemie_tools/data_management/code_executor/models.py` line 404) with fallback default
  `"LOW"` when unset. Accepted values: `SAFE`/`LOW`/`MEDIUM`/`HIGH` (case-insensitive) or integer
  0-3, validated by `validate_security_threshold`.
- `CODE_EXECUTOR_YAML_POLICY_PATH` — optional path to a custom policy YAML, read in `from_env()` and
  passed through to `get_codemie_security_policy`; not itself part of the threshold-direction bug but
  is the mechanism by which a deployment could supply a non-default policy file.

### Configuration Files

- `src/codemie_tools/data_management/code_executor/default_security_policies.yaml` — the default
  policy: header comment (lines 8-12) states the same SAFE/LOW/MEDIUM/HIGH semantics text targeted
  by the ticket; `restricted_modules` (17 entries) and `patterns` (8 entries) each carry a
  `severity:` field consumed by the enforcement code. Confirmed current severities: HIGH —
  `subprocess`, `sys`, `shutil`, `socket`, `urllib`, `httpx`, `eval`, `exec`, `compile`, `threading`,
  `multiprocessing`, `pip`; MEDIUM — `glob`, `tempfile`, `requests`, `inspect`, `importlib`; and in
  `patterns`, HIGH — `os.system`, `subprocess.(run|call|Popen|check_output)`,
  `os.(remove|rmdir|unlink)`, `socket.socket(`, `eval(`, `exec(`, `os.environ[`, `__builtins__`;
  MEDIUM — `requests.(get|post|put|delete)`.
- `.env`, `.env.example`, `tests/.env.test` — checked; none set `CODE_EXECUTOR_SECURITY_THRESHOLD`
  (grep returned no matches in any of the three).
- `deploy-templates/values.yaml` — checked (this is the repo's only deployment manifest containing
  `CODE_EXECUTOR_*` references); it references `CODE_EXECUTOR_RUNTIME_CLASS_NAME`,
  `CODE_EXECUTOR_TOLERATIONS`, and `CODE_EXECUTOR_NAMESPACE` in comments/examples, but does **not**
  set `CODE_EXECUTOR_SECURITY_THRESHOLD` anywhere.

**Current default value of `security_threshold` and every place it is set:**
1. `CodeExecutorConfig.security_threshold` Pydantic field default: `SecurityIssueSeverity.LOW`
   (`models.py` line 184).
2. `CodeExecutorConfig.from_env()` fallback when `CODE_EXECUTOR_SECURITY_THRESHOLD` is unset: the
   string `"LOW"` (`models.py` line 404).
3. No `.env`/`.env.example`/`tests/.env.test` file, and no `deploy-templates/values.yaml` entry, sets
   this env var — a repo-wide grep for `CODE_EXECUTOR_SECURITY_THRESHOLD` across `*.yml`/`*.yaml`/
   `*.env*`/`Dockerfile*` (excluding `venv/`) returned no matches outside `models.py`, the tests, the
   README, and this ticket's own docs. This means the effective default everywhere in this repo is
   `LOW`, sourced purely from the two in-code defaults above — there is no discovered downstream
   deployment config that pins a different value and would be silently affected by a default-value
   change.

### Feature Flags and Deployment Concerns

No feature flag gates security-threshold enforcement. No Dockerfile or CI/CD reference to
`CODE_EXECUTOR_SECURITY_THRESHOLD` was found. Because no deployment manifest in this repository
overrides the default, the ticket's "downstream configurations and deployments" review (Acceptance
Criteria) resolves, within this repository's own tracked files, to: no override exists here: any
risk is confined to deployments outside this repo (e.g. a customer/ops Helm values file or secret
store not tracked in this repository) that may have set `CODE_EXECUTOR_SECURITY_THRESHOLD=LOW`
under the current strict-at-LOW behavior and would become more permissive after an unannounced
semantic flip at the same default value.

---

## 6. Risk Indicators

- Speculative: three independent call sites implement the enforcement direction in three different
  syntactic forms (`SAFE < threshold <= pattern.severity` in `security_policies.py`,
  `module.severity >= severity_threshold` in `get_restricted_module_names`, and
  `severity >= self.threshold` in `ast_security_checker.py`) — a fix that only edits the literal
  expression named in the ticket would miss the other two, leaving the policy evaluation
  inconsistent across the AST layer, regex layer, and module-name-listing helper.
- Speculative: the AST layer's threshold check is presently a no-op in practice, because every
  violation it can produce is hard-coded to `SecurityIssueSeverity.HIGH` and `HIGH` is the enum
  maximum — any inversion of the comparison direction must be checked against this layer
  specifically, since a naive swap could either (a) leave it a no-op (blocks always, at every
  threshold including SAFE-adjacent settings) or (b) accidentally make SAFE/LOW stop blocking these
  bypass techniques, which would be a regression in a different direction than the one the ticket is
  fixing.
- Speculative: the upstream `llm_sandbox.security.SecurityPolicy.severity_threshold` field is
  documented by that (third-party, unowned) library as "the minimum severity level at which security
  issues will be blocked" — i.e. the *library's own* convention matches CodeMie's current
  (ticket-named-inverted) behavior. Any fix that inverts CodeMie's `CodeExecutorConfig.security_threshold` meaning without also translating the value before it is assigned into `SecurityPolicy.severity_threshold` (`security_policies.py` line 223) risks either double-inverting or only fixing one of the two "threshold" concepts in play.
- The `pathlib` scenario named in the ticket as validation criteria is already independently
  resolved (pathlib was removed from `restricted_modules` in commit `83da83c4e`, prior to this
  ticket) — the acceptance criterion "pathlib at MEDIUM severity is not blocked unexpectedly" cannot
  be demonstrated against `pathlib` itself under the current YAML; a different still-MEDIUM entry
  (`glob`, `tempfile`, `requests`, `inspect`, or `importlib`) would be needed to exercise the same
  class of regression.
- No existing test exercises a MEDIUM-severity pattern across multiple threshold levels (see Section
  4, Coverage Gaps) — this is the exact class of gap that let the current inversion ship
  undetected, and the acceptance criteria explicitly ask for this ordering to be regression-tested
  going forward.
- Speculative: because at least four locations restate the SAFE/LOW/MEDIUM/HIGH semantics text nearly
  verbatim (`models.py` field description, `default_security_policies.yaml` header comment,
  `get_codemie_security_policy` docstring, `get_restricted_module_names` docstring), any fix must
  update all four in the same direction or the documentation will contradict itself again in a
  different place than the one the ticket originally found.
- No downstream deployment manifest, `.env` file, or Dockerfile in this repository currently pins
  `CODE_EXECUTOR_SECURITY_THRESHOLD`, so the "review downstream configs before changing the default"
  requirement is, within this repository's tracked files, already satisfied with a clean result;
  residual risk is limited to untracked/external deployments this research cannot see.
- `test_ast_security_checker.py::TestSeverityThreshold::test_safe_threshold_allows_everything` has a
  body that only asserts `isinstance(is_safe, bool)` (i.e., it does not actually verify permissive
  behavior) — a pre-existing weak assertion that will not catch a regression in either direction
  unless it is strengthened as part of this fix.

---

## 7. Summary for Complexity Assessment

The change is scoped to a single, well-bounded domain (Code Executor security-threshold semantics)
but touches three independent enforcement call sites that must move in the same direction:
`check_security_policy` and `get_restricted_module_names` in `security_policies.py`, and
`CodeExecutorSecurityChecker._add_violation` in `ast_security_checker.py`. It also touches four
places carrying near-duplicate documentation of the same semantics (`models.py` field description,
`default_security_policies.yaml` header comment, and the two docstrings in `security_policies.py`),
plus `code_executor_tool.py` as a consumer whose tool-description text output will change. The
runtime filesystem guard (`filesystem_policy.py`, `sandbox_guard.py`) is architecturally separate and
out of scope. No database, API, or migration surface is involved — this is a pure domain/policy-layer
and config-documentation change plus supporting yaml comments.

The primary technical novelty/risk is not size but correctness: the upstream third-party
`llm_sandbox.SecurityPolicy.severity_threshold` field already documents the convention CodeMie's code
currently implements ("block when severity >= threshold"), so inverting CodeMie's own config
semantics requires either translating the value at the assignment boundary (`security_policies.py`
line 223) or accepting a semantic mismatch between CodeMie's `security_threshold` and the library's
`severity_threshold` field it flows into — a decision the plan/spec stage must make explicitly, not
something discoverable purely from existing code. A second correctness risk is the AST layer, whose
threshold check is currently a no-op in practice (all its violations are hard-coded HIGH severity)
and could silently change behavior under a naive fix.

Test coverage posture is a meaningful gap: no existing test exercises a MEDIUM-severity
pattern/module across multiple threshold levels, which is precisely the scenario (originally
`pathlib`, now already independently fixed by removing `pathlib` from the policy) that exposed this
bug. New regression tests will need a different still-MEDIUM entry (e.g. `glob` or `requests`) to
cover the acceptance criteria's ordering requirement. Downstream-deployment risk is low: no
`.env`/`.env.example`/`values.yaml`/Dockerfile in this repository currently overrides
`CODE_EXECUTOR_SECURITY_THRESHOLD`, so the default (`LOW`, set in two places in `models.py`) is the
only value in effect anywhere in this codebase's tracked configuration.

---

## 8. External References

The task named one in-repo source of truth: `docs/superpowers/tasks/2026-09-24-epmcdme-13877/ticket.md`. This resolved and was read in full at the start of research (Step 0); its content is
reproduced in Section 1 and used throughout Sections 2-6 (the exact current comparison, the
`pathlib`/EPMCDME-13695 scenario, and the full Acceptance Criteria list). No path or URL outside this
repository was named by the task.
