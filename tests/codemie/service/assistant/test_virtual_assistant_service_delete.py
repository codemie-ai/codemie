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

from __future__ import annotations

from collections.abc import Iterator
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from codemie.service.assistant import VirtualAssistantService
from codemie.service.assistant.virtual_assistant_service import VirtualAssistant

EXECUTION_ID = "exec-1"


def _assistant(execution_id: str) -> VirtualAssistant:
    mock = MagicMock()
    mock.execution_id = execution_id
    return cast(VirtualAssistant, mock)


class _RacyAssistants(dict[str, VirtualAssistant]):
    """Dict that concurrently drops another key the first time a key is read."""

    def __init__(self, data: dict[str, VirtualAssistant]) -> None:
        super().__init__(data)
        self._raced = False

    def _race(self, key: str) -> None:
        if self._raced:
            return
        self._raced = True
        for other in [k for k in list(self.keys()) if k != key]:
            dict.pop(self, other, None)

    def __getitem__(self, key: str) -> VirtualAssistant:
        self._race(key)
        return super().__getitem__(key)

    def get(self, key: str, default: VirtualAssistant | None = None) -> VirtualAssistant | None:  # type: ignore[override]
        self._race(key)
        return super().get(key, default)


@pytest.fixture
def racy_assistants() -> Iterator[_RacyAssistants]:
    racy = _RacyAssistants(
        {
            "a": _assistant(EXECUTION_ID),
            "b": _assistant(EXECUTION_ID),
            "c": _assistant("other"),
        }
    )
    with patch.object(VirtualAssistantService, "assistants", racy):
        yield racy


def test_delete_by_execution_id_tolerates_concurrent_removal(racy_assistants: _RacyAssistants) -> None:
    VirtualAssistantService.delete_by_execution_id(EXECUTION_ID)

    assert all(a.execution_id != EXECUTION_ID for a in racy_assistants.values())


def test_delete_by_execution_id_removes_only_matching() -> None:
    plain = {"a": _assistant(EXECUTION_ID), "b": _assistant("other")}
    with patch.object(VirtualAssistantService, "assistants", plain):
        VirtualAssistantService.delete_by_execution_id(EXECUTION_ID)

    assert list(plain) == ["b"]
