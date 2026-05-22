# Local Testing

## Test Scope

Run tests only when the user asks for tests or when a requested quality gate includes them.

| Avoid | Prefer |
|---|---|
| Running the entire suite for every small edit without request | Run the narrowest relevant test when tests are requested |
| Reporting unrun tests as passing | State skipped or blocked gates explicitly |

Evidence: Makefile test target runs `tests/` at `Makefile:27`; pytest dependencies are declared at `pyproject.toml:157`.

## Environment Blocks

Some tests depend on optional services, credentials, or enterprise behavior.

| Avoid | Prefer |
|---|---|
| Treating missing credentials as code failure | Report the missing prerequisite |
| Masking import errors from optional packages | Identify whether the dependency is optional/enterprise |

Evidence: enterprise extras are optional at `pyproject.toml:153`.
