# Testing Patterns

## Test Location

Place tests beside the behavior area under `tests/codemie/`, `tests/codemie_tools/`, `tests/unit/`, or `tests/enterprise/`.

| Avoid | Prefer |
|---|---|
| A broad test file unrelated to the changed package | Match the nearest existing test directory |
| Testing external services live by default | Mock provider boundaries unless integration is required |

Evidence: tests are organized under package-specific directories in `tests/`; pytest dependencies are declared at `pyproject.toml:157`.

## Running Tests

Run the narrowest relevant scope unless the user asks for all tests or full verification.

| Avoid | Prefer |
|---|---|
| Reporting the suite passed after a partial run | State the exact command and scope |
| Running tests when user policy says not requested | Report tests skipped by policy |

Evidence: Makefile test target runs all tests at `Makefile:27`.
