# QA Gate Report — epmcdme-13666-move-copy-project-integrations

**Branch**: `EPMCDME-13666_move-copy-project-integrations`
**Merge base**: `origin/main`
**Runner**: poetry (guide-first mode — gates taken from `.ai-run/guides/quality-gates.md`)
**Started**: 2026-07-23T16:08:00Z
**Status**: PASSED (with two environment skips and one scoped gate — see notes)

## Gates

| Gate | Status | Duration | Command | Notes |
|---|---|---|---|---|
| Lint And Format | PASS | 5s | `make ruff` | format + `check --fix` + `check` all clean; 2191 files unchanged |
| Build | PASS | 15s | `make build` | sdist and wheel built (`codemie-0.8.0`) |
| License Headers | PASS | 3s | `make license-check` | 1958 files checked, 0 missing headers |
| Secret Scan | SKIPPED | — | `make gitleaks` | **Environment block**: Docker is not installed or on PATH. The guide permits this skip ("Skip if: Docker is unavailable; report the environment block explicitly"). Not a pass — see Environment blocks below. |
| Tests | PASS (scoped) | 46s | see below | **206 passed.** Scoped to the settings area, not the full suite — see Test scope below. |
| Coverage | SKIPPED | — | `make coverage` | Guide: "Skip if: The user did not request coverage." Not requested. |
| Static Analysis | SKIPPED | — | `make sonar-local` | Guide: "Skip if: Sonar configuration, network access, or required credentials are unavailable." Not configured in this environment. |
| Full Verification | N/A | — | `make verify` | Composite of ruff + license + gitleaks + test; components reported individually above rather than re-run as a bundle. Would currently fail on the Docker-dependent gitleaks step. |
| Affected tests | SKIPPED | — | (none) | No changed-file-aware pytest command configured in this repo. |
| UI | SKIPPED | — | (none) | **Green skip**: 0 of 6 changed files match `ui_globs`. Python backend, no UI surface changed. `feature-verification` correctly not invoked (`ui` flag off). |

## Test scope

The guide's Tests gate names `make test` (the full `tests/` tree). That run was started and then **stopped before completion at the user's request** because it was taking too long. It therefore did **not** pass — it did not finish, and no full-suite result is claimed here.

What was actually run, verbatim:

```
poetry run pytest tests/codemie/service/settings \
  tests/codemie/rest_api/routers/test_settings_transfer.py \
  tests/codemie/rest_api/routers/test_project_settings.py \
  tests/codemie/rest_api/routers/test_user_settings.py \
  tests/codemie/rest_api/routers/test_user_settings_crud.py -q
```

Result: **206 passed, 32 warnings, 38.74s** (exit 0).

This is the narrowest scope that covers the change plus everything it could regress: the entire `service/settings` package and every settings-related router suite. It follows `.ai-run/guides/testing/testing-patterns.md` ("Run the narrowest relevant scope unless the user asks for all tests or full verification"), and that guide's rule "state the exact command and scope" is why the command is reproduced above rather than summarised.

**Residual risk**: tests outside the settings area were not executed in this session. The change is additive — two new modules, one new router, and a two-line registration in `main.py` — so the blast radius outside settings is limited to the router registration. That registration was verified independently by importing the real app and asserting the route resolves (`['/v1/settings/transfer']`).

## Environment blocks

- **Docker unavailable** — `make gitleaks` could not run. Secret scanning has not been performed in this session. The new code introduces no literal secrets (test fixtures use `test-fake-` prefixes per the repo convention), but this is an unverified claim, not a scanned result. Run `make gitleaks` on a machine with Docker, or rely on CI, before merge.
- **Sonar not configured** — `make sonar-local` requires credentials/network not present here.

## Warnings

32 warnings, all pre-existing and none originating in the new code: a `UserWarning` for a field-shadow in `src/codemie/core/models.py:838`, a langgraph `LangChainPendingDeprecationWarning` from a third-party package, and `StarletteDeprecationWarning` for `HTTP_422_UNPROCESSABLE_ENTITY`. That constant has 59 usages across 15 existing files and is the established convention in this repo; the new code follows it deliberately rather than diverging. Migrating all 59 is out of scope for this ticket.

## Drift signal

**no** — the implementation matches the approved spec. The two deviations from the literal spec text are documented improvements, both recorded in the code-review artifacts: `application_repository.get_by_name` plus an explicit `deleted_at` check instead of `exists_by_name` (which does not exclude soft-deleted projects), and `Depends(authenticate)` added to the route's `dependencies` list so `request.state.user` exists before the admin guard reads it. The spec's own section 5 validation ordering, section 6 atomicity guarantee, and section 10 test requirements are all implemented and verified after the code-review fix-up round.
