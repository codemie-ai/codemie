# API Testing Patterns

## Router Tests

Test API behavior through the router/client boundary when request validation, auth, or response shape matters.

| Avoid | Prefer |
|---|---|
| Testing only service methods for endpoint response contracts | API tests for status codes and response bodies |
| Repeating auth setup in every test | Reuse existing fixtures/helpers near API tests |
| Patching a name on the router package, or where it is defined, when the endpoint module bound it with `from ... import` | Patch the module that looks the name up: the endpoint submodule, or `_common` only for names `_common` itself reads; `patch.object` on a class or singleton survives module moves |

Evidence: router tests live under `tests/codemie/rest_api/routers/` (a few under `tests/unit/routers/`); package router imported at `tests/codemie/rest_api/routers/test_projects_router.py:40`; submodule patch at `tests/codemie/rest_api/routers/test_assistant.py:62`; `_common` patch at `tests/codemie/rest_api/routers/test_projects_router.py:80`; `patch.object` on a repository class at `tests/codemie/rest_api/routers/test_projects_spending_wiring.py:90`.

## Error Cases

Include negative cases for validation and authorization changes.

| Avoid | Prefer |
|---|---|
| Only testing happy paths for protected routes | Test unauthenticated/unauthorized behavior |
| Asserting raw exception text | Assert structured error response fields |

Evidence: app-level error response shape is defined at `src/codemie/rest_api/main.py:1192`.

## Router Contract Guards

`tests/codemie/rest_api/router_guards/` builds `main.py` in a subprocess under a fixed env profile and compares the result with committed baselines; the guards run under `make test`.

| Guard | Fails when | Baseline |
|---|---|---|
| `test_openapi_matches_baseline` | Any OpenAPI difference: path, operationId, tag, request or response schema, component | `baselines/openapi.json` |
| `test_baseline_order_is_preserved` | A recorded overlapping route pair swaps order or loses a route; new overlapping pairs only warn | `baselines/route_precedence.json` |

| Avoid | Prefer |
|---|---|
| Regenerating baselines to make a failing guard pass | Fix the code if the contract change was unintended; regenerate only after an intended, reviewed change and review the baseline diff in the MR |
| Assuming a new flag-gated router is guarded | Add its flag to `PROFILE_ENV`, which switches on every conditional router except MCP auth |

Regenerate: `poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines`

Evidence: `tests/codemie/rest_api/router_guards/test_openapi_snapshot.py:31`; `tests/codemie/rest_api/router_guards/test_route_precedence.py:100`; profile at `tests/codemie/rest_api/router_guards/guarded_app.py:52`; command at `tests/codemie/rest_api/router_guards/regenerate_baselines.py:20`.
