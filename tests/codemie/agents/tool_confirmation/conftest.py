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

"""Fixtures shared by the tool-confirmation tests; the helper classes live in ``support.py``."""

from __future__ import annotations

import pytest

from tests.codemie.agents.tool_confirmation.support import FakeCheckpointService, patched_storage, reset_counters


@pytest.fixture(autouse=True)
def _reset_counters():
    reset_counters()


@pytest.fixture
def service():
    with patched_storage(FakeCheckpointService()) as fake:
        yield fake
