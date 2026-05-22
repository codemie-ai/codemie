# Code Quality

## Python Style

Use the repo's Ruff configuration rather than local preference. The project config sets line length, selected lint rules, and per-file ignores in `pyproject.toml:177`.

| Avoid | Prefer |
|---|---|
| Formatting by hand | Run the Ruff gate from `.ai-run/guides/quality-gates.md` |
| Introducing a second formatter | Keep formatting aligned with `tool.ruff` |

## Types And Imports

Keep modern Python type hints and first-party imports aligned with package names.

| Avoid | Prefer |
|---|---|
| Untyped public functions in new code | Explicit parameter and return types |
| Treating generated or external code like normal source | Respect configured ignores for `src/external/*` and generated plugin code |

Evidence: project packages are `codemie` and `codemie_tools` at `pyproject.toml:16`; Ruff first-party imports are configured at `pyproject.toml:209`.

## License Headers

Source files use the Apache 2.0 header checker.

| Avoid | Prefer |
|---|---|
| Adding new source files without headers | Run `make license-check` or `make license-fix` as appropriate |
| Editing generated/external files just to satisfy style | Check the owning guide or existing ignore first |

Evidence: Makefile license targets are defined at `Makefile:41`.
