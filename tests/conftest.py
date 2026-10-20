# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""
Global test configuration.

This file is automatically loaded by pytest before running any tests.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from dotenv import load_dotenv

# Stub optional native packages that may not be available in all environments
# (e.g. tree_sitter_languages has no Python 3.13 wheel on Windows).
# This must run before any codemie imports so the stubs are in place at import time.
for _missing_pkg in ("tree_sitter_languages", "python_calamine", "switchyard", "switchyard.libsy"):
    try:
        __import__(_missing_pkg)
    except ImportError:
        sys.modules[_missing_pkg] = MagicMock()

# Load test env vars at module level so they are set before any codemie module
# is imported and Config() is instantiated (pydantic-settings reads env at init time).
load_dotenv(Path(__file__).parent / ".env.test", override=True)


_ENTERPRISE_TESTS_DIR = Path(__file__).parent / "enterprise"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--skip-enterprise",
        action="store_true",
        default=False,
        help="Skip tests marked @pytest.mark.enterprise, including everything under tests/enterprise/ "
        "(for local runs without the codemie_enterprise optional extra installed).",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # Every test under tests/enterprise/ needs the real package, whether or not it says so itself.
    for item in items:
        if Path(item.fspath).is_relative_to(_ENTERPRISE_TESTS_DIR):
            item.add_marker(pytest.mark.enterprise)

    if not config.getoption("--skip-enterprise"):
        return
    skip_enterprise = pytest.mark.skip(reason="--skip-enterprise: codemie_enterprise is not installed")
    for item in items:
        if "enterprise" in item.keywords:
            item.add_marker(skip_enterprise)


@pytest.fixture(autouse=True)
def isolate_run_reporting_state():
    """Start every test without reporting sinks and without a request LLM context, and put both back afterwards.

    Importing the application (`codemie.rest_api.main`, which some tests do) registers the metric sinks for the rest of
    the process, and a test that sets the request LLM context without resetting it leaves a billing project behind.
    Either would change what a later, unrelated test sees: an extra metric log line in a log assertion, a project that
    is not the one the test passed in. A test that needs a sink adds it itself.
    """
    from codemie.core.dependecies import litellm_context
    from codemie_tools.base.execution_timing import sandbox_run_sinks, tool_execution_sinks

    saved = (tool_execution_sinks.snapshot(), sandbox_run_sinks.snapshot())
    tool_execution_sinks.clear()
    sandbox_run_sinks.clear()
    token = litellm_context.set(None)
    try:
        yield
    finally:
        litellm_context.reset(token)
        tool_execution_sinks.restore(saved[0])
        sandbox_run_sinks.restore(saved[1])


@pytest.fixture(scope="session", autouse=True)
def mock_database_engine():
    """
    Mock database engine to prevent actual database connections during tests.

    This fixture is automatically applied to all tests (autouse=True) and runs once
    per test session (scope="session"). It ensures that no actual database connections
    are made during test execution, even when modules are imported.
    """
    with patch("codemie.clients.postgres.PostgresClient.get_engine") as mock_get_engine:
        # Return a mock engine that won't try to connect to a real database
        mock_engine = MagicMock()
        mock_engine.__enter__ = MagicMock(return_value=mock_engine)
        mock_engine.__exit__ = MagicMock(return_value=False)
        mock_get_engine.return_value = mock_engine
        yield mock_engine
