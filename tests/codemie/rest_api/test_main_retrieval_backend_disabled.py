# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Regression test for the two module-scope RETRIEVAL_BACKEND=none conditionals in main.py.

``codemie.rest_api.main`` is already imported once per test session (by other test modules)
under the default ``RETRIEVAL_BACKEND=elasticsearch``, so the module-level guards at
``main.py:894`` (``StateImportService().import_indexes()``) and ``main.py:902``
(``app.include_router(index.router)``) have already executed with that value by the time any
in-process test runs. Re-evaluating them with ``RETRIEVAL_BACKEND=none`` therefore requires a
genuinely fresh import -- and ``main.py`` performs non-idempotent, process-global side effects at
import time (notably Prometheus collector registration), so reloading it in the current
interpreter risks "Duplicated timeseries in CollectorRegistry" failures and cross-test pollution.

A subprocess gives a truly fresh interpreter for the RETRIEVAL_BACKEND=none import, at the cost of
re-doing the handful of import-time preconditions tests/conftest.py normally provides (stubbing
optional native deps that may be absent, and mocking the Postgres engine so import doesn't try a
real DB connection).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_DIR = str(_REPO_ROOT / "src")

_PROBE_SCRIPT = """
import sys
from unittest.mock import MagicMock, patch

# Mirror tests/conftest.py: stub optional native deps that may not be installed,
# before any codemie import triggers them transitively.
for _missing_pkg in ("tree_sitter_languages", "python_calamine"):
    try:
        __import__(_missing_pkg)
    except ImportError:
        sys.modules[_missing_pkg] = MagicMock()

mock_engine = MagicMock()
mock_engine.__enter__ = MagicMock(return_value=mock_engine)
mock_engine.__exit__ = MagicMock(return_value=False)

with patch("codemie.clients.postgres.PostgresClient.get_engine", return_value=mock_engine):
    with patch(
        "codemie.rest_api.utils.state_import.StateImportService.import_indexes"
    ) as mock_import_indexes:
        import codemie.rest_api.main as main
        from codemie.rest_api.routers import index as index_router_module

    assert mock_import_indexes.call_count == 0, (
        f"StateImportService.import_indexes() ran {mock_import_indexes.call_count} time(s) "
        "with RETRIEVAL_BACKEND=none"
    )

index_route_paths = {route.path for route in index_router_module.router.routes}
app_route_paths = {route.path for route in main.app.routes}
leaked = index_route_paths & app_route_paths
assert not leaked, f"index router routes registered on app despite RETRIEVAL_BACKEND=none: {leaked}"

print("PROBE_OK")
"""


def test_main_skips_index_import_and_router_when_retrieval_backend_none():
    """With RETRIEVAL_BACKEND=none, main.py must neither call import_indexes() nor register index.router."""
    env = dict(os.environ)
    env["RETRIEVAL_BACKEND"] = "none"
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (_SRC_DIR, existing_pythonpath) if p)

    result = subprocess.run(
        [sys.executable, "-c", _PROBE_SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )

    assert result.returncode == 0, (
        f"probe subprocess failed (RETRIEVAL_BACKEND=none)\n" f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "PROBE_OK" in result.stdout
