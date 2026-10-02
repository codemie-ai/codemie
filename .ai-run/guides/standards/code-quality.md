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
| `typing.Optional[X]` and bare `list`/`dict` annotations | `X \| None` unions and parameterized generics (`list[str]`, `dict[str, str]`); add `from __future__ import annotations` in new modules |
| Leaving a duck-typed parameter unannotated | Spell out the accepted union (e.g. `bytes \| str \| dict[str, object]`) |
| Treating generated or external code like normal source | Respect configured ignores for `src/external/*` and generated plugin code |

Evidence: project packages are `codemie` and `codemie_tools` at `pyproject.toml:16`; Ruff first-party imports are configured at `pyproject.toml:209`.

## License Headers

Source files use the Apache 2.0 header checker.

| Avoid | Prefer |
|---|---|
| Adding new source files without headers | Run `make license-check` or `make license-fix` as appropriate |
| Editing generated/external files just to satisfy style | Check the owning guide or existing ignore first |

Evidence: Makefile license targets are defined at `Makefile:41`.

## Module Cohesion

Code goes into the module that owns its concern. A new concern does not go into an existing file just because the file is nearby.

| Avoid | Prefer |
|---|---|
| Appending a new concern (its own constants, pure functions and tests) to an existing module | A new module next to the code that uses it, imported by the existing one |
| Adding to a file of about 500 lines or more when the change is not that file's core responsibility | Extract the new piece first, then wire it in |
| Adding code where the piece cannot be named | Name the piece in one noun; if it has a name, it is a module |

Evidence: the workspace script bridge added timeout handling, channel lifecycle and limit clamping to large service and runner modules, and each was later moved into its own module without changing behavior (`workspace_script_bridge.py`, `job_bridge.py`, `tool_calling_limits.py`).
