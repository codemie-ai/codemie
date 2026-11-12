# Testing Patterns

## Test Location

Place tests beside the behavior area under `tests/codemie/`, `tests/codemie_tools/`, `tests/unit/`, or `tests/enterprise/`.

| Avoid | Prefer |
|---|---|
| A broad test file unrelated to the changed package | Match the nearest existing test directory |
| Creating a new top-level `tests/` subtree for a new area | Mirror `src/` under an existing root (e.g. `tests/codemie/<package path>/`) and reuse its fixtures |
| Testing external services live by default | Mock provider boundaries unless integration is required |

Evidence: tests are organized under package-specific directories in `tests/`; pytest dependencies are declared at `pyproject.toml:157`.

## Running Tests

Run the narrowest relevant scope unless the user asks for all tests or full verification.

| Avoid | Prefer |
|---|---|
| Reporting the suite passed after a partial run | State the exact command and scope |
| Running tests when user policy says not requested | Report tests skipped by policy |

Evidence: Makefile test target runs all tests at `Makefile:27`.

## Seam Tests for Policy Helpers

When a helper encapsulates a decision (default vs custom, normalized vs rejected), unit-testing it in isolation is not enough — each callsite needs a test observing what reaches the outer boundary (SDK call, emitted request, constructed object) for each branch. Otherwise a callsite guard duplicating the same check stays silent.

| Avoid | Prefer |
|---|---|
| Only unit-testing the helper in isolation | One callsite test per branch, asserting the boundary value |
| `if x == default: skip; else: forward(x)` when the callee already handles `x == default` | Forward unconditionally; seam test proves the boundary call stays correct |

Red flag: deleting a callsite guard leaves tests green → the guard was dead code and no seam test existed to catch it.

## Config-Derived User Flags

`User.is_admin` is recomputed from config when a `User` is built, so the value a test passes is not the value it gets. `pytest.ini` sets `ENV=local`, which makes every `User` an admin; with user management off, admin comes from `ADMIN_USER_ID` and roles. A test that depends on `is_admin` must pin the mode itself, or it passes or fails depending on what an earlier test left in `config`: a non-local `ENV` with `ENABLE_USER_MANAGEMENT=True` keeps the flag passed in (as the user row does in production); a test of the user-management-off mode also pins `ADMIN_USER_ID` and `ADMIN_ROLE_NAME`.

| Avoid | Prefer |
|---|---|
| Trusting `User(is_admin=True)` or `User(is_admin=False)` as built | `monkeypatch.setattr` on `ENV` and `ENABLE_USER_MANAGEMENT` (plus `ADMIN_USER_ID` / `ADMIN_ROLE_NAME` when user management is off) in a fixture of the test that relies on the flag |
| Assigning `config.ENV = ...` directly in a test | `monkeypatch.setattr`, which restores the value for the next test |

Evidence: `resolve_is_admin` at `src/codemie/rest_api/security/user.py:142`; `ENV=local` at `pytest.ini:7`; pinned fixture at `tests/enterprise/litellm/test_cli_attribution_chain.py:80`.
