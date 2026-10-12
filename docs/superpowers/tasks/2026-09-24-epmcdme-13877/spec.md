# Spec: Fix inverted CodeExecutor `security_threshold` semantics (EPMCDME-13877)

## Problem

`CodeExecutorConfig.security_threshold` (`SecurityIssueSeverity`: SAFE, LOW, MEDIUM, HIGH) currently
makes `LOW` the strictest setting and `HIGH` the most permissive — backwards from its name, from user
intuition, and from the YAML comments. The bug is expressed in three independent call sites that must
move together, and the fix must respect a real constraint: the upstream third-party
`llm_sandbox.SecurityPolicy.severity_threshold` field, which CodeMie's value flows into, already
documents the convention CodeMie's current (buggy) code implements ("block when severity >=
threshold"). The fix therefore corrects CodeMie's *user-facing* meaning without breaking that
upstream contract.

## Design

### 1. Boundary translation, not comparison inversion

Add one small, unit-testable helper in `security_policies.py` that maps CodeMie's user-facing
`security_threshold` to the internal enforcement cutoff the three existing call sites already
understand:

- `LOW` -> `HIGH` (internal cutoff)
- `MEDIUM` -> `MEDIUM`
- `HIGH` -> `LOW`
- `SAFE` -> `SAFE` (unchanged: the existing "never block" guard stays intact)

Call this helper at every point the raw config value enters enforcement:

- `get_codemie_security_policy` (security_policies.py ~line 223), before assigning into
  `policy.severity_threshold` — this single point also covers the regex layer
  (`check_security_policy` ~line 295/338, which reads `policy.severity_threshold`) and the AST layer
  call it makes downstream (~line 298), since both consume the already-translated value.
- `get_restricted_module_names` (security_policies.py ~line 270) — this is invoked independently from
  `code_executor_tool.py` with the raw config value, so it needs its own call to the same helper.

The three call sites' own comparison operators (`SAFE < threshold <= pattern.severity`,
`module.severity >= severity_threshold`, `severity >= self.threshold`) are **not** rewritten — they
keep implementing the upstream `severity >= threshold` convention, now against the translated cutoff.
`SecurityPolicy.severity_threshold` continues to carry the upstream (post-translation) meaning, not
CodeMie's user-facing strictness value; document this distinction where the field is assigned.

### 2. Resulting user-facing behavior

| `security_threshold` | Meaning | Blocks |
|---|---|---|
| SAFE | disabled | nothing |
| LOW | permissive | HIGH only |
| MEDIUM | moderate | MEDIUM + HIGH |
| HIGH | strict | LOW + MEDIUM + HIGH |

### 3. AST layer: make the always-block invariant explicit

Every violation `CodeExecutorSecurityChecker._add_violation` (ast_security_checker.py ~line 118) can
produce (`__import__`, `exec`/`eval`/`compile`, `__builtins__` access) is hardcoded
`SecurityIssueSeverity.HIGH`. Because HIGH is the enum maximum, `severity >= self.threshold` is
vacuously true for every threshold value both before and after this fix — these bypass techniques are
already, and remain, unconditionally blocked at SAFE/LOW/MEDIUM/HIGH alike. No behavior change is
required or wanted here (per decision: keep them always-enforced, independent of the strictness knob).

What must change is clarity: replace the vestigial `severity >= self.threshold` comparison with
unconditional violation recording (drop the comparison, or keep `threshold` for signature
compatibility but stop branching on it), with an inline comment stating these hardcoded-HIGH
sandbox-escape vectors are intentionally enforced at every threshold. Add a test asserting this
invariant explicitly across all four threshold levels, so it can no longer be mistaken for a live gate
by a future reader.

### 4. Default value: change LOW -> HIGH

Change both in-code defaults in `models.py`:

- `CodeExecutorConfig.security_threshold` field default (~line 184): `LOW` -> `HIGH`.
- `from_env()` fallback string (~line 404): `"LOW"` -> `"HIGH"`.

Rationale: under the corrected mapping, `HIGH` blocks LOW+MEDIUM+HIGH — exactly what `LOW` blocked
under the old (buggy) semantics. This preserves today's effective production enforcement level
bit-for-bit across the semantic flip, rather than silently loosening it.

Add a migration/release note (CHANGELOG entry or MR description — implementer's choice) covering: the
semantic flip itself; the default change and why; an explicit call-out that any deployment which
pinned `CODE_EXECUTOR_SECURITY_THRESHOLD=LOW` expecting the old strict behavior must change that
value to `HIGH` to preserve prior enforcement, since it will otherwise silently become the new
permissive setting. No deployment manifest, `.env` file, or Dockerfile in this repository currently
sets this variable (confirmed in Stage 1 research), so the residual exposure is external/untracked
deployments only — an accepted, documented risk, not something code can mitigate further.

### 5. Documentation to correct

Update the SAFE/LOW/MEDIUM/HIGH semantics text, consistently with Section 2's table, in: `models.py`
field description and `from_env()` docstring; `default_security_policies.yaml` header comment (lines
8-12); `get_codemie_security_policy` and `get_restricted_module_names` docstrings in
`security_policies.py` (lines 171-177 and 239-257); `code_executor/README.md`'s env var reference
table and usage examples. All four+ locations must agree with each other and with the new default.

### 6. Test plan

- Add a direct unit test for the new translation helper (mapping table verification).
- Update `TestSecurityPolicyThreshold`-style assertions that currently assert raw pass-through
  (`policy.severity_threshold == input`) — post-translation this equality only holds at MEDIUM; assert
  the translated value instead.
- Add regression coverage proving the LOW < MEDIUM < HIGH strictness ordering using `requests`
  (a still-MEDIUM module/pattern in the YAML) exercised at SAFE, LOW, MEDIUM, and HIGH — this is the
  substitute for the retired `pathlib` scenario (pathlib was already removed from
  `restricted_modules` by a prior, unrelated commit) and should live alongside the existing
  `TestRequestsSecurityPatterns` coverage.
- Re-verify (with explicit assertions, not assumptions) that `TestOsModulePolicy`/`TestPipModulePolicy`
  (HIGH-severity patterns) still block at every threshold except SAFE under the new mapping.
- Update `test_ast_security_checker.py::TestSeverityThreshold`: rename/reassert tests whose names
  describe old inverted-sounding semantics (e.g. `test_low_threshold_blocks_high_severity`) to reflect
  the always-blocks invariant from Section 3, and fix `test_safe_threshold_allows_everything`'s
  existing weak `isinstance`-only assertion to actually assert permissive behavior for non-hardcoded
  content.
- Update `TestSecurityThresholdIntegration.test_tool_default_threshold_is_low` for the new HIGH
  default.
- No test may assert the pre-fix inverted behavior after this change lands.

## Non-goals

- No change to `filesystem_policy.py` / `sandbox_guard.py` — separate, self-documented
  defense-in-depth layer that never reads `security_threshold` or `SecurityIssueSeverity`.
- No change to `SecurityIssueSeverity`'s enum ordering or values (owned by third-party `llm_sandbox`).
- No change to `_SecurityPolicyLoader`'s existing rejection of `severity_threshold` inside YAML policy
  files — threshold stays config/env-var-only.
- No reintroduction of `pathlib` into `restricted_modules` — that was resolved by a prior, unrelated
  commit and is out of scope here.
- No feature flag or dual-mode toggle between old and new semantics — this is a direct bugfix with a
  release note, not a staged migration.
- No change to `check_code_with_ast`'s public signature beyond the internal clarity fix in Section 3.

## Acceptance criteria

- `security_threshold` directly represents policy strictness: LOW permissive, HIGH strict.
- All three enforcement call sites (regex layer, `get_restricted_module_names`, AST layer) behave
  consistently with the table in Section 2.
- The AST layer's hardcoded-HIGH bypass detections block unconditionally at every threshold, and the
  code makes this explicit rather than incidental.
- The default `security_threshold` changes from LOW to HIGH in both `models.py` locations, preserving
  today's effective enforcement level across the semantic flip.
- A migration/release note documents the semantic flip and the default change for external
  deployments.
- `models.py`, `default_security_policies.yaml`, `security_policies.py` docstrings, and
  `code_executor/README.md` consistently describe the corrected semantics.
- No test asserts the old inverted behavior; new regression coverage proves the LOW < MEDIUM < HIGH
  ordering using `requests` as the MEDIUM-severity case, substituting for the retired `pathlib`
  scenario.
- Downstream configs/deployments tracked in this repository are confirmed to set no override (already
  verified in Stage 1 research — no code change needed for that confirmation itself).

## Open risks

External/untracked deployments that pinned `CODE_EXECUTOR_SECURITY_THRESHOLD=LOW` outside this
repository will become more permissive after the default/semantic change unless updated to `HIGH`;
mitigated only by the migration/release note, not by code — an accepted risk per explicit decision.
