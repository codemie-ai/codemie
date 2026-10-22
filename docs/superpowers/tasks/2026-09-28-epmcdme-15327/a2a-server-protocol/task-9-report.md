DONE_WITH_CONCERNS

## Commit
26231b80c4521eb627f1ad27a2ad9df75388aef1 — "EPMCDME-15327: Rename A2A discovery route to agent-card.json"

## Changed files
- src/codemie/rest_api/routers/a2a.py — one line: discovery route path
  `/assistants/{assistant_id}/.well-known/agent.json` → `.../agent-card.json`
  (decorator's other args, function body, and every other route in the file
  untouched).
- src/codemie/rest_api/a2a/types.py — `A2ARequestBody` Swagger-example class
  only: `method` default `"tasks/send"` → `"SendMessage"`, and the example
  `parts` entry dropped its `"type": "text"` key. No other class touched.
- tests/codemie/rest_api/routers/test_a2a_router.py — updated the 5 existing
  discovery tests' URL from `.well-known/agent.json` to
  `.well-known/agent-card.json`; added `test_get_agent_card_shape_matches_v1`
  asserting `url` is absent, `supportedInterfaces[0].transport == "JSONRPC"`,
  and `capabilities.extendedAgentCard is True`.

## Tests
RED (before the two production edits, all 6 discovery tests requesting
`agent-card.json` against the still-`agent.json`-registered route):
```
poetry run pytest tests/codemie/rest_api/routers/test_a2a_router.py -v
```
6 failed (five with `assert 404 == <expected>`, one `KeyError` on the
now-404 body) — RED for the expected reason.

GREEN (after the route path and A2ARequestBody edits):
```
poetry run pytest tests/codemie/rest_api/routers/test_a2a_router.py -v
```
EXIT=0 — 6 passed, 4 warnings, 140.89s. Log:
docs/superpowers/tasks/2026-09-29-a2a-server-protocol/test.local.log

Regression (Tasks 1-8 scope):
```
poetry run pytest tests/codemie/rest_api/a2a/test_types.py tests/codemie/rest_api/a2a/test_utils.py tests/codemie/rest_api/a2a/server/test_codemie_task_manager.py -v
```
EXIT=0 — 48 passed (8+11+29), 4 warnings, 135.14s. Log:
docs/superpowers/tasks/2026-09-29-a2a-server-protocol/test-regression.local.log

`git show --stat HEAD` confirms exactly the three files listed above.

## Concerns (environment, not scope)
The local Poetry venv (`codemie-_abB3MgH-py3.13`) was significantly out of
sync with `poetry.lock` — importing `codemie.rest_api.main` (required to
exercise the router via `AsyncClient`/`ASGITransport`) failed first on
`ModuleNotFoundError: No module named 'zephyr'` (declared as
`zephyr-python-api = "^0.1.0"` in pyproject.toml but absent from the venv),
then on `webencodings`. `poetry install --sync` could not fully repair this:
it aborts on several packages needing native compilers unavailable on this
Windows box (`tree-sitter`, `tree-sitter-languages`, `pyroscope-io`,
`pymssql`, `simsimd`) — the same class of problem the repo already works
around for `switchyard`/`python_calamine` in `tests/conftest.py`.

I installed only the two missing pure-Python wheels directly
(`pip install --no-deps zephyr-python-api==0.1.0` and
`pip install --no-deps webencodings==0.5.1`, versions pinned to match
`poetry.lock`) to unblock running this test file; I did not touch
`pyproject.toml`, `poetry.lock`, or attempt to fix the native-build
packages. This is a venv-local, non-reproducible fix — a fresh clone or CI
runner may hit the same two import errors (and possibly more; I did not
audit the full 54-package drift list beyond what this test file's import
chain actually required). Recommend a `poetry install --sync` (or
equivalent) on a machine with the needed build toolchains, or extending
`tests/conftest.py`'s native-package stub list, as a separate housekeeping
item — out of scope for this task.

No other concerns. The two production edits are exactly as scoped; router
dispatch logic and the rest of `routers/a2a.py`/`types.py` were not touched.
