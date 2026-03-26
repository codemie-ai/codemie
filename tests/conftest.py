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

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from dotenv import load_dotenv

# Load test env vars at module level so they are set before any codemie module
# is imported and Config() is instantiated (pydantic-settings reads env at init time).
load_dotenv(Path(__file__).parent / ".env.test", override=True)


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
