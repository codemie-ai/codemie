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

"""A failing probe must fail every consuming test fast, from one spawn, with the child's stderr."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from unittest import mock

import pytest

from tests.codemie.rest_api.router_guards import guarded_app

_CHILD_STDERR = "ImportError: stalled while importing main"


@pytest.fixture(autouse=True)
def isolated_probe_cache() -> Iterator[None]:
    guarded_app._probe_outcome.cache_clear()
    yield
    guarded_app._probe_outcome.cache_clear()


@pytest.mark.parametrize(
    "probe_result",
    [
        pytest.param({"return_value": subprocess.CompletedProcess([], 3, "", _CHILD_STDERR)}, id="non-zero-exit"),
        pytest.param(
            {"side_effect": subprocess.TimeoutExpired([], 180, stderr=_CHILD_STDERR.encode())},
            id="timeout-stderr-as-bytes",
        ),
        pytest.param(
            {"side_effect": subprocess.TimeoutExpired([], 180, stderr=_CHILD_STDERR)},
            id="timeout-stderr-as-text",
        ),
    ],
)
def test_failed_probe_is_spawned_once_and_every_call_reports_the_child_stderr(probe_result):
    spawn = mock.Mock(**probe_result)

    with mock.patch.object(guarded_app.subprocess, "run", spawn):
        for _ in range(3):
            with pytest.raises(RuntimeError, match=_CHILD_STDERR):
                guarded_app.load_guarded_app_dump()

    assert spawn.call_count == 1


def test_timeout_without_captured_stderr_still_raises_runtime_error():
    spawn = mock.Mock(side_effect=subprocess.TimeoutExpired([], 180))

    with mock.patch.object(guarded_app.subprocess, "run", spawn):
        with pytest.raises(RuntimeError, match="timed out"):
            guarded_app.load_guarded_app_dump()
