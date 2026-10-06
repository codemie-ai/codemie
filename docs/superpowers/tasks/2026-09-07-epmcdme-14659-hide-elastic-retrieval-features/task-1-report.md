status: DONE

commit: 30db8906a
changed_files: src/codemie/configs/config.py, tests/codemie/configs/test_config.py
test_command: poetry run pytest tests/codemie/configs/test_config.py -k retrieval_available -v

Added `RETRIEVAL_BACKEND: str = "elasticsearch"` to the `Config` class (adjacent to the `ELASTIC_*` block, `src/codemie/configs/config.py`) and a module-level `retrieval_available(cfg) -> bool` helper placed just above `HealthCheckFilter`, below the `Config` class body and above the `config = Config()` singleton instantiation — importable as `from codemie.configs.config import retrieval_available`. Note: the plan referenced a `Settings` class, but the actual class in this codebase is named `Config`; followed current source per project convention.

Followed TDD: added `test_retrieval_available_true_for_elasticsearch` and `test_retrieval_available_false_for_none` to the existing `tests/codemie/configs/test_config.py` (using a `SimpleNamespace` stand-in for `cfg`, matching the plan's requirement of a bare object rather than the full `Config` class), confirmed RED (ImportError: cannot import name 'retrieval_available'), implemented the minimal code, and confirmed GREEN (2 passed). Also ran the full `tests/codemie/configs/test_config.py` (14 passed) and `tests/codemie/configs/test_env_example.py` (4 passed) to confirm no regressions from the new config field.
