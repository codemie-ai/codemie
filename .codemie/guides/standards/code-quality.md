# Code Quality Standards

## Quick Summary

Code quality standards for CodeMie using Ruff linting/formatting, comprehensive type hints, complexity limits (≤16), docstring conventions, and Python 3.12+ features.

**Category**: Development/Standards | **Complexity**: Medium | **Prerequisites**: Python 3.12+, Ruff, typing

---

## Naming & Import Conventions

| Element | Convention | Example |
|---------|------------|---------|
| **Packages** | snake_case | `rest_api/`, `workflow_execution/` |
| **Modules** | snake_case | `assistant_service.py`, `workflow_config.py` |
| **Classes** | PascalCase | `AssistantAgent`, `WorkflowExecutionService` |
| **Functions/Methods** | snake_case | `create_assistant()`, `execute_workflow()` |
| **Constants** | UPPER_SNAKE_CASE | `MAX_RETRIES`, `DEFAULT_MODEL` |
| **Tests** | `test_*.py` | `test_assistant_agent.py` |

**Import Order**: Standard library → Third-party → codemie-tools → Local

**Reference**: `src/codemie/rest_api/routers/assistant.py`, `src/codemie/service/assistant/assistant_service.py`

---

## Ruff Configuration

**Config**: `pyproject.toml:88-124` | **line-length**: 120 | **max-complexity**: 16

**Enabled Rules**: E/W (pycodestyle), F (Pyflakes), B (bugbear), N (naming), C4 (comprehensions), SIM (simplify), C (mccabe), PERF (performance)

**Key Ignored**: B008 (func call in defaults), G004 (f-string logging), N818 (Exception naming), PERF401/403 (manual comprehensions)

### Commands

| Task | Command |
|------|---------|
| **Check** | `ruff check .` |
| **Fix** | `ruff check --fix .` |
| **Format** | `ruff format .` |
| **Complexity only** | `ruff check --select C .` |
| **CI check** | `ruff format --check .` |

---

## Type Hints

**Rule**: Type ALL function signatures — both parameters AND return types. Includes private/helper methods. Use modern Python 3.12+ syntax.

### Modern Syntax

| Pattern | Use | Example |
|---------|-----|---------|
| **Union** | `str \| int \| None` | NOT `Union[str, int, None]` |
| **Generics** | `dict[str, Any]` | NOT `Dict[str, Any]` |
| **Annotated** | Metadata for types | `Annotated[Sequence[T], add_messages]` |
| **TypedDict** | State/config structures | See `src/codemie/workflows/models.py:42-48` |
| **Pydantic** | Validated models | See `src/codemie/workflows/models.py:55-72` |
| **Protocol** | Structural subtyping | Duck typing with type safety |
| **Return types** | Required on ALL methods | `def _helper(cls, key: str) -> Select[tuple[Model]]:` |

**Reference**: `src/codemie/workflows/models.py:13-72` for TypedDict, Pydantic, Annotated examples

---

## Complexity Limits

**Max Complexity**: 16 (McCabe C901) | **Check**: `ruff check --select C .`

### Refactoring Techniques

| Technique | When to Use | Example |
|-----------|-------------|---------|
| **Extract Function** | Nested logic | Split deep nesting into helper functions |
| **Early Returns** | Multiple nested ifs | Guard clauses at function start |
| **Extract Method** | Complex conditions | Separate validation/processing |
| **Strategy Pattern** | Type-based logic | Replace conditionals with polymorphism |
| **Table-Driven** | Multiple ifs | Use dict lookup instead of if/elif chains |

**Example**: See how nested ifs (complexity 18) become 3 functions (complexity 4-6 each) through extraction

---

## Docstring Conventions

**Format**: Google Style

**Required**: Public APIs, complex logic (complexity >8), config classes, utilities

**Optional**: Private helpers, simple getters/setters, tests

**Example**: See `.codemie/guides/validate-docs.py:20-48`

---

## Python 3.12+ Features

| Feature | Use Case | Example |
|---------|----------|---------|
| **Union `\|`** | Type unions | `str \| int \| None` |
| **match/case** | Pattern matching | Replace complex if/elif chains |
| **type aliases** | Type shortcuts | `type Point = tuple[float, float]` |
| **Generic aliases** | Type vars | `type Result[T] = Success[T] \| Failure` |
| **Required/NotRequired** | TypedDict | Optional keys in typed dicts |
| **asyncio.TaskGroup** | Concurrent tasks | Structured concurrency |
| **asyncio.timeout** | Async timeouts | Context manager for timeouts |

---


## String Literals and Constants

**Rule**: Define module-level constants (`UPPER_SNAKE_CASE`) for strings used 3+ times

| Use Constant For | Skip For |
|------------------|----------|
| Used 3+ times (field names, error codes) | Used once (function-local) |
| Config values (`MAX_RETRIES = 3`) | - |
| API contracts (status codes, event types) | - |

**Benefits**: Prevents typos, single source of truth, IDE autocomplete, easy refactoring

**Reference**: `src/codemie/service/analytics/handlers/cli_handler.py:16-23`

---

## Anti-Patterns to Avoid

| ❌ Don't | ✅ Do |
|---------|-------|
| Missing return type annotation | `-> ReturnType` required on ALL methods (including `_private` helpers) |
| High complexity (>16) | Extract functions, use early returns |
| `Union[str, int]` | Use `str \| int` (Python 3.10+) |
| `Dict[str, List]` | Use `dict[str, list]` |
| Inconsistent formatting | Run `ruff format .` |
| Duplicate string literals | Define module constants for 3+ uses |
| Mutable defaults `def f(x=[])` | Use `None`, initialize in body |
| Bare `except:` | Catch specific exceptions |

---

## Verification & CI Integration

| Check | Command |
|-------|---------|
| **All quality checks** | `ruff check . && ruff format --check .` |
| **Complexity only** | `ruff check --select C .` |
| **Type issues** | `ruff check --select F821,F401,F841 .` |
| **Line count** | `python .codemie/guides/validate-docs.py --check all --dir <dir>` |

**Pre-commit**: Add ruff to `.pre-commit-config.yaml` (see ruff-pre-commit docs)

---

## Troubleshooting

| Error Code | Issue | Solution |
|------------|-------|----------|
| **C901** | Function too complex | Extract functions, reduce to ≤16 |
| **F821** | Undefined name | Add imports or fix typo |
| **E501** | Line too long | Break at 120 chars, use `()` for continuation |
| **B008** | Function call in defaults | Use `None`, initialize in body |

**Type checking**: `pip install mypy && mypy src/codemie/`

---

## Related Guides

- **Testing**: `.codemie/guides/testing/testing-patterns.md`
- **Error Handling**: `.codemie/guides/development/error-handling.md`
- **Logging**: `.codemie/guides/development/logging-patterns.md`
- **Configuration**: `.codemie/guides/development/configuration-patterns.md`

## References

- **Config**: `pyproject.toml:88-124`
- **Type Examples**: `src/codemie/workflows/models.py:13-72`
- **Docstring Examples**: `.codemie/guides/validate-docs.py:20-48`
- **Constants**: `src/codemie/service/analytics/handlers/cli_handler.py:16-23`
- **Ruff Docs**: https://docs.astral.sh/ruff/
- **Python Typing**: https://docs.python.org/3/library/typing.html
