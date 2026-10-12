# EPMCDME-13877 — Fix inverted semantics of CodeExecutor security_threshold parameter

**Issue Type:** Bug
**Status:** In Progress
**Labels:** AI-Generated, AI/Run, codemie_core
**Components:** None
**Related:** EPMCDME-13695 (Code Executor filesystem guard investigation)

## Summary

Fix the inverted semantics of the `CodeExecutorConfig.security_threshold` parameter so that the
configured threshold directly represents policy strictness.

## Description

The `CodeExecutorConfig.security_threshold` parameter uses `SecurityIssueSeverity` values: `SAFE`,
`LOW`, `MEDIUM`, and `HIGH`. Its current enforcement behavior is inverted relative to its name and
expected user intuition.

The current check in `src/codemie_tools/data_management/code_executor/security_policies.py` is:

```python
SAFE < threshold <= pattern.severity
```

This means a violation is blocked when the threshold is above `SAFE` and less than or equal to the
pattern severity. As a result:

- `threshold=LOW` enforces the strictest policy by blocking `MEDIUM` and `HIGH` severity patterns.
- `threshold=HIGH` is the most permissive setting because it only blocks `HIGH` severity patterns
  and allows `LOW` and `MEDIUM`.

This behavior is backwards from what users intuitively expect. `LOW` should mean lenient or
permissive policy enforcement, while `HIGH` should mean strict or restrictive policy enforcement.

The issue was discovered while investigating EPMCDME-13695, related to the Code Executor filesystem
guard. In that investigation, `default_security_policies.yaml` blocked `pathlib` at `MEDIUM`
severity, and with the production threshold set to `LOW`, `pathlib` was blocked. This contradicts
the YAML comment:

> LOW (1): Allows read operations like requests.get()

That comment implies `LOW` should be permissive.

The fix should invert the semantics so the threshold parameter directly represents policy strictness:

- `threshold=LOW` means permissive and blocks less.
- `threshold=HIGH` means strict and blocks more.

The reporter is concerned that the inverted condition may indicate a broader issue in policy
behavior. Therefore, the implementation team must review the policy evaluation logic end to end,
confirm that policy rules work as intended for every severity level, and update the documented
requirements to reflect the validated behavior.

Before changing default behavior, confirm that no downstream configurations or deployments rely on
the current inverted behavior.

## Preconditions

- Code Executor security policies are configured through `CodeExecutorConfig.security_threshold`.
- Security issue severities are represented by `SecurityIssueSeverity`: `SAFE`, `LOW`, `MEDIUM`, `HIGH`.
- Security policy enforcement is implemented in
  `src/codemie_tools/data_management/code_executor/security_policies.py`.
- Default security policy comments are maintained in `default_security_policies.yaml`.
- The `CODE_EXECUTOR_SECURITY_THRESHOLD` environment variable documentation is maintained in `models.py`.

## Scenarios of Use

1. A developer configures `CodeExecutorConfig.security_threshold=LOW`.
2. Code Executor evaluates a code pattern with `MEDIUM` severity, such as the `pathlib` policy
   referenced during the filesystem guard investigation.
3. The security policy engine applies threshold enforcement.
4. The behavior matches intuitive policy strictness: `LOW` is permissive and does not unexpectedly
   block `MEDIUM` patterns when a lenient policy is intended; `HIGH` is strict and blocks more patterns.
5. The implementation team validates policy evaluation across all supported severity levels and
   policy patterns, rather than validating only the inverted comparison.
6. Requirements, documentation, and comments describe the validated policy behavior consistently.
7. Existing tests validate the corrected semantics and do not assert the current inverted behavior.
8. Existing downstream configurations and deployments are reviewed before changing the default.

## Affected Areas

- `src/codemie_tools/data_management/code_executor/security_policies.py`
- `default_security_policies.yaml`
- `models.py`
- `CODE_EXECUTOR_SECURITY_THRESHOLD` environment variable documentation
- Code Executor security policy tests
- Code Executor default policy behavior
- Downstream Code Executor configurations and deployments
- Related investigation context: EPMCDME-13695

## Acceptance Criteria

- The comparison logic in `src/codemie_tools/data_management/code_executor/security_policies.py` is
  reviewed and updated so `security_threshold` directly represents policy strictness.
- `threshold=LOW` behaves as the permissive/lenient setting and blocks fewer policy patterns than
  stricter thresholds.
- `threshold=HIGH` behaves as the strict/restrictive setting and blocks more policy patterns.
- The complete policy evaluation flow is reviewed to confirm that all policies are evaluated and
  enforced according to the intended severity semantics.
- Test coverage verifies the behavior of each supported severity level (`SAFE`, `LOW`, `MEDIUM`,
  `HIGH`) against applicable policy patterns, and identifies any policy that does not behave as specified.
- Requirements for policy severity and threshold behavior are updated to document the validated
  logic, including the expected enforcement outcome for each threshold level.
- The `default_security_policies.yaml` comments are updated to match the corrected threshold semantics.
- The `CODE_EXECUTOR_SECURITY_THRESHOLD` environment variable documentation in `models.py` is checked
  and updated if needed.
- Tests that assert the current inverted behavior are updated to assert the corrected behavior.
- Regression coverage confirms the intended ordering of `LOW`, `MEDIUM`, and `HIGH` strictness.
- The scenario from the EPMCDME-13695 investigation is validated: `pathlib` at `MEDIUM` severity is
  not blocked unexpectedly by a permissive `LOW` threshold.
- Existing downstream configurations and deployments are reviewed before changing the default.
- Any required migration or release note is documented if current deployments rely on the previous
  inverted behavior.
