# Service Testing Patterns

## Service Isolation

Test service orchestration with repositories/providers mocked or controlled unless the behavior is explicitly integration-level.

| Avoid | Prefer |
|---|---|
| Hitting live cloud or SaaS services in unit tests | Mock provider/toolkit boundaries |
| Testing router and repository details through a service test | Focus on service decisions and calls |

Evidence: service modules live under `src/codemie/service/`; repository tests are separated under `tests/codemie/repository/`.

## Async Services

Use async test support for async service paths.

| Avoid | Prefer |
|---|---|
| Calling async methods without awaiting | Mark async tests through pytest async support |
| Hiding event-loop errors with sync wrappers | Test the async boundary directly |

Evidence: `pytest-asyncio` is a dev dependency at `pyproject.toml:159`.
