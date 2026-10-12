# Fix inverted CodeExecutor `security_threshold` semantics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the inverted LOW/HIGH enforcement semantics of `CodeExecutorConfig.security_threshold` across all three enforcement call sites via a single boundary-translation helper, without rewriting any existing comparison or the upstream `llm_sandbox` convention.

**Architecture:** Add one translation helper in `security_policies.py` mapping CodeMie's user-facing `security_threshold` to the internal enforcement cutoff the existing `severity >= cutoff` comparisons already implement, and call it at the two points a raw config value enters enforcement (`get_codemie_security_policy`, `get_restricted_module_names`). Make the AST layer's already-unconditional bypass blocking explicit instead of vestigially threshold-gated. Flip both in-code `LOW` defaults in `models.py` to `HIGH` to preserve today's effective enforcement level, and correct the semantics text everywhere it is documented.

**Tech Stack:** Python, Pydantic v2, pytest/unittest, `llm_sandbox` (third-party `SecurityIssueSeverity` IntEnum).

**Spec:** docs/superpowers/tasks/2026-09-24-epmcdme-13877/spec.md

## Global Constraints

- Boundary translation only — `LOW->HIGH`, `MEDIUM->MEDIUM`, `HIGH->LOW`, `SAFE->SAFE` — applied at exactly two call sites (`get_codemie_security_policy`, `get_restricted_module_names`); the three existing comparison operators are never rewritten.
- The AST layer's hardcoded-HIGH bypass detections stay unconditionally blocking at every threshold — no behavior change, only clarity (drop the vestigial `severity >= self.threshold` branch).
- Both `LOW` defaults in `models.py` become `HIGH` (field default + `from_env()` fallback), plus a migration/release note.
- No change to `SecurityIssueSeverity`'s ordering, `filesystem_policy.py`/`sandbox_guard.py`, `_SecurityPolicyLoader`'s YAML rejection of `severity_threshold`, or `check_code_with_ast`'s public signature.
- No `pathlib` reintroduction, no feature flag or dual-mode toggle.
- Commit per task using the repository's existing convention.

---

### Task 1: Boundary-translation helper

**Files:**
- Modify: `src/codemie_tools/data_management/code_executor/security_policies.py` — add a new function immediately before `get_codemie_security_policy` (~line 148).
- Test: `tests/codemie_tools/data_management/code_executor/test_security_config.py` — add `TestSecurityThresholdTranslation`.

**Interfaces:**
- Produces: `translate_security_threshold(security_threshold: SecurityIssueSeverity) -> SecurityIssueSeverity`, module-level in `security_policies.py`.

**Test-first: yes — mapping assertions fail with an ImportError until the function exists.**

- [ ] **Step 1: Write the failing test**

```python
class TestSecurityThresholdTranslation(unittest.TestCase):
    """Boundary translation from user-facing strictness to internal enforcement cutoff."""

    def test_translation_mapping(self):
        from codemie_tools.data_management.code_executor.security_policies import (
            translate_security_threshold,
        )
        assert translate_security_threshold(SecurityIssueSeverity.SAFE) == SecurityIssueSeverity.SAFE
        assert translate_security_threshold(SecurityIssueSeverity.LOW) == SecurityIssueSeverity.HIGH
        assert translate_security_threshold(SecurityIssueSeverity.MEDIUM) == SecurityIssueSeverity.MEDIUM
        assert translate_security_threshold(SecurityIssueSeverity.HIGH) == SecurityIssueSeverity.LOW
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/codemie_tools/data_management/code_executor/test_security_config.py::TestSecurityThresholdTranslation -v`
Expected: FAIL (ImportError — `translate_security_threshold` does not exist).

- [ ] **Step 3: Implement the helper**

```python
def translate_security_threshold(security_threshold: SecurityIssueSeverity) -> SecurityIssueSeverity:
    """Translate CodeMie's user-facing security_threshold (LOW=permissive .. HIGH=strict) into
    the internal enforcement cutoff the existing severity >= cutoff comparisons already consume.
    The upstream llm_sandbox.SecurityPolicy.severity_threshold field, and every comparison that
    reads it, keep the opposite ("block when severity >= threshold") convention; this is the
    single point where the two meanings are reconciled, so no comparison operator is rewritten.
    """
    return {
        SecurityIssueSeverity.SAFE: SecurityIssueSeverity.SAFE,
        SecurityIssueSeverity.LOW: SecurityIssueSeverity.HIGH,
        SecurityIssueSeverity.MEDIUM: SecurityIssueSeverity.MEDIUM,
        SecurityIssueSeverity.HIGH: SecurityIssueSeverity.LOW,
    }[security_threshold]
```

- [ ] **Step 4: Run to verify it passes**

Run: same command as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

---

### Task 2: Wire translation into the two enforcement entry points; regression-lock the LOW<MEDIUM<HIGH ordering

**Files:**
- Modify: `security_policies.py:223` (`get_codemie_security_policy`'s `policy.severity_threshold = severity_threshold` assignment) and `security_policies.py:270` (`get_restricted_module_names`'s filter comprehension).
- Test: `tests/codemie_tools/data_management/code_executor/test_security_config.py` — update `TestSecurityPolicyThreshold` (lines 90-103) and add `TestRequestsThresholdOrdering`.

**Interfaces:**
- Consumes: `translate_security_threshold` from Task 1.

**Test-first: yes — `test_low_does_not_block_medium_severity_requests` fails against the current, untranslated code, which blocks MEDIUM severity at LOW threshold.**

- [ ] **Step 1: Write the failing tests** — update the two threshold-specific methods in `TestSecurityPolicyThreshold`:

```python
    def test_policy_with_low_threshold(self):
        """LOW is user-facing permissive; internally it becomes the HIGH cutoff."""
        policy = get_codemie_security_policy(severity_threshold=SecurityIssueSeverity.LOW)
        assert policy.severity_threshold == SecurityIssueSeverity.HIGH

    def test_policy_with_high_threshold(self):
        """HIGH is user-facing strict; internally it becomes the LOW cutoff."""
        policy = get_codemie_security_policy(severity_threshold=SecurityIssueSeverity.HIGH)
        assert policy.severity_threshold == SecurityIssueSeverity.LOW
```

(`test_policy_with_safe_threshold` and `test_policy_with_medium_threshold` keep their existing pass-through assertions unchanged — SAFE and MEDIUM map to themselves.)

Add the ordering regression test near `TestRequestsSecurityPatterns`:

```python
class TestRequestsThresholdOrdering(unittest.TestCase):
    """requests is still MEDIUM severity; substitutes for the retired pathlib scenario
    (pathlib was removed from restricted_modules by a prior, unrelated commit)."""

    def _check(self, threshold):
        from codemie_tools.data_management.code_executor.security_policies import check_security_policy
        policy = get_codemie_security_policy(severity_threshold=threshold)
        return check_security_policy(policy, "import requests\nrequests.get('https://example.com')")

    def test_safe_does_not_block(self):
        assert self._check(SecurityIssueSeverity.SAFE)[0]

    def test_low_does_not_block_medium_severity_requests(self):
        assert self._check(SecurityIssueSeverity.LOW)[0]

    def test_medium_blocks_medium_severity_requests(self):
        assert not self._check(SecurityIssueSeverity.MEDIUM)[0]

    def test_high_blocks_medium_severity_requests(self):
        assert not self._check(SecurityIssueSeverity.HIGH)[0]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/codemie_tools/data_management/code_executor/test_security_config.py -v`
Expected: the four tests above FAIL (current code blocks `requests` at LOW, and pass-through equality no longer holds at LOW/HIGH).

- [ ] **Step 3: Implement the wiring** — in `security_policies.py`, change line 223 to `policy.severity_threshold = translate_security_threshold(severity_threshold)`, and change the filter at line 270 from `module.severity >= severity_threshold` to `module.severity >= translate_security_threshold(severity_threshold)`.

- [ ] **Step 4: Run to verify it passes**

Run: same command as Step 2. Expected: PASS for the whole file, including `TestOsModulePolicy`/`TestPipModulePolicy` (unchanged — HIGH-severity patterns stay blocked at LOW under the new cutoff too).

- [ ] **Step 5: Commit**

---

### Task 3: AST layer — make the always-block invariant explicit

**Files:**
- Modify: `src/codemie_tools/data_management/code_executor/ast_security_checker.py:113-125` (`_add_violation`).
- Test: `tests/codemie_tools/data_management/code_executor/test_ast_security_checker.py:114-141` (`TestSeverityThreshold`).

**Test-first: no** — clarity-only change; every hardcoded-HIGH bypass violation already blocks at every threshold today (HIGH is the enum max), so there is no failing-first assertion available. The tests below are a regression lock added alongside the code change, not a red-then-green cycle.

- [ ] **Step 1: Replace the three existing tests with an explicit invariant check** — remove `test_high_threshold_blocks_high_severity`, `test_low_threshold_blocks_high_severity`, and the weak `test_safe_threshold_allows_everything` (which only asserted `isinstance(is_safe, bool)`), replacing the class body with:

```python
class TestSeverityThreshold:
    """Hardcoded-HIGH bypass detections (__import__, exec/eval/compile, __builtins__ access)
    are intentionally unconditional: they block at every configured threshold."""

    def test_safe_threshold_blocks_bypass_technique(self):
        is_safe, violations = check_code_with_ast('__import__("os")', SecurityIssueSeverity.SAFE)
        assert not is_safe and len(violations) >= 1

    def test_low_threshold_blocks_bypass_technique(self):
        is_safe, violations = check_code_with_ast('__import__("os")', SecurityIssueSeverity.LOW)
        assert not is_safe and len(violations) >= 1

    def test_medium_threshold_blocks_bypass_technique(self):
        is_safe, violations = check_code_with_ast('__import__("os")', SecurityIssueSeverity.MEDIUM)
        assert not is_safe and len(violations) >= 1

    def test_high_threshold_blocks_bypass_technique(self):
        is_safe, violations = check_code_with_ast('__import__("os")', SecurityIssueSeverity.HIGH)
        assert not is_safe and len(violations) >= 1

    def test_safe_threshold_allows_non_bypass_code(self):
        """SAFE must not flag ordinary code that triggers no bypass detection."""
        is_safe, violations = check_code_with_ast("x = 1 + 2\nprint(x)", SecurityIssueSeverity.SAFE)
        assert is_safe and violations == []
```

- [ ] **Step 2: Remove the vestigial branch** — in `ast_security_checker.py`, delete the `if severity >= self.threshold:` guard in `_add_violation` (lines 116-117) so the violation is always recorded, dedenting the body underneath it. Add a comment directly above the method stating these hardcoded-HIGH sandbox-escape vectors are enforced at every threshold by design. `self.threshold` stays stored on the instance (kept for signature/documentation continuity) but is no longer branched on.

- [ ] **Step 3: Run the full AST test file**

Run: `pytest tests/codemie_tools/data_management/code_executor/test_ast_security_checker.py -v`
Expected: PASS, including the untouched `TestDynamicImportDetection`, `TestSafeCode`, etc.

- [ ] **Step 4: Commit**

---

### Task 4: Flip the default from LOW to HIGH; add the migration note

**Files:**
- Modify: `src/codemie_tools/data_management/code_executor/models.py:184` (field default) and `:404` (`from_env()` fallback string).
- Test: `tests/codemie_tools/data_management/code_executor/test_security_config.py` — `TestSecurityThresholdConfig.test_default_security_threshold` (lines 31-34) and `TestSecurityThresholdIntegration.test_tool_default_threshold_is_low` (lines 186-200, renamed to `test_tool_default_threshold_is_high`).
- Modify: this repository's existing changelog/release-notes file at the repo root (use whichever one already exists; if none exists, add the note to the MR description instead of creating a new file).

**Test-first: yes — `test_default_security_threshold` currently asserts `LOW`; updating it to `HIGH` fails until the default changes.**

- [ ] **Step 1: Write the failing tests**

```python
    def test_default_security_threshold(self):
        """Default security threshold is HIGH (strict) after the semantic flip."""
        config = CodeExecutorConfig()
        assert config.security_threshold == SecurityIssueSeverity.HIGH
```

Rename `test_tool_default_threshold_is_low` to `test_tool_default_threshold_is_high` and update its final two assertions (keep the surrounding env-var patching and tool construction unchanged):

```python
        assert tool.config.security_threshold == SecurityIssueSeverity.HIGH
        assert tool.security_policy.severity_threshold == SecurityIssueSeverity.LOW
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/codemie_tools/data_management/code_executor/test_security_config.py -v`
Expected: these two tests FAIL against the current `LOW` default.

- [ ] **Step 3: Change the defaults** — in `models.py`, change the field default at line 184 from `SecurityIssueSeverity.LOW` to `SecurityIssueSeverity.HIGH`, and the `from_env()` fallback at line 404 from `"LOW"` to `"HIGH"`.

- [ ] **Step 4: Run to verify it passes**

Run: same command as Step 2. Expected: PASS.

- [ ] **Step 5: Add the migration/release note** covering: the semantic flip itself (LOW is now permissive, HIGH is now strict); the default change LOW->HIGH and that it preserves today's effective enforcement level bit-for-bit; and an explicit call-out that any deployment pinning `CODE_EXECUTOR_SECURITY_THRESHOLD=LOW` to keep the old strict behavior must change it to `HIGH`, or it will silently become the new permissive setting.

- [ ] **Step 6: Commit**

---

### Task 5: Correct the SAFE/LOW/MEDIUM/HIGH semantics text everywhere it's documented

**Files:**
- Modify: `src/codemie_tools/data_management/code_executor/models.py:185-191` (field description).
- Modify: `src/codemie_tools/data_management/code_executor/default_security_policies.yaml:8-12` (header comment).
- Modify: `src/codemie_tools/data_management/code_executor/security_policies.py:171-177` (`get_codemie_security_policy` docstring) and `:239-257` (`get_restricted_module_names` docstring, including its `Example:` block).
- Modify: `src/codemie_tools/data_management/code_executor/README.md:33,45` (Quick Configuration Reference bullet and env-var table row).

**Test-first: no** — documentation-only; no test exercises docstring or comment text.

- [ ] **Step 1: Replace the semantics text** in every location above with the corrected table: SAFE = disabled, blocks nothing; LOW = permissive, blocks HIGH only; MEDIUM = moderate, blocks MEDIUM + HIGH; HIGH = strict, blocks LOW + MEDIUM + HIGH. In `get_restricted_module_names`'s docstring, also update the two `>>> get_restricted_module_names(...)` examples so the described output matches the corrected per-level meaning (LOW returns only HIGH-severity module names; HIGH returns LOW+MEDIUM+HIGH-severity module names). In `README.md`, update both the `(default: LOW)` bullet (line 33) and the table row's default column (line 45) to `HIGH`.

- [ ] **Step 2: Cross-check** — re-read all five edited locations side by side and confirm they agree with each other and with the corrected table above.

- [ ] **Step 3: Commit**

---

## Negative-constraint pass

- **Operators unchanged**: Tasks 1-2 add and call the translation helper only at the two named entry points; `SAFE < threshold <= pattern.severity` (regex layer), `module.severity >= severity_threshold` (`get_restricted_module_names`), and `severity >= self.threshold` (AST layer) are never edited as expressions. Task 2 changes only which value flows into the first two; Task 3 removes the AST comparison entirely, per the spec's explicit "drop the comparison" option — a removal, not a rewrite.
- **AST layer stays unconditionally blocking, vestigial comparison removed/documented**: Task 3.
- **Default LOW->HIGH in both `models.py` locations, plus migration/release note**: Task 4.
- **No change to `SecurityIssueSeverity` ordering/values**: no task touches the third-party enum.
- **No change to `filesystem_policy.py`/`sandbox_guard.py`**: no task references either file.
- **No change to `_SecurityPolicyLoader`'s YAML-side rejection of `severity_threshold`**: no task touches `_SecurityPolicyLoader`.
- **No reintroduction of `pathlib` into `restricted_modules`**: Task 2's regression test uses `requests`, not `pathlib`; no task edits the YAML's module list.
- **No feature flag / dual-mode toggle**: no task adds one; Task 4 is a direct default change.
- **No change to `check_code_with_ast`'s public signature**: Task 3 only edits `_add_violation`'s body.

negative-constraints: checked above against the spec's Non-goals and inline "not rewritten" / "no behavior change" clauses — none violated by this plan.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/tasks/2026-09-24-epmcdme-13877/plan.md`.
