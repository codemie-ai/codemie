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

"""The build policy of one agent build: the resolved policy, the stored toolkit lookup and builtin path keys."""

from __future__ import annotations

from types import SimpleNamespace

from codemie.service.assistant.assistant_engine_builder import (
    LangGraphAssistantBuilder,
)
from codemie.service.subagents.builtin_subagents import BuiltinSubagent


def test_one_parent_never_gets_two_builtins_of_one_kind():
    builtin = BuiltinSubagent.GENERAL_PURPOSE_SUBAGENT
    assistant = SimpleNamespace(
        enabled_builtin_subagents=[builtin, builtin.value, builtin],
        skill_ids=[],
        _runtime_disable_skill_builtin_subagents=False,
    )

    assert LangGraphAssistantBuilder._merge_skill_builtin_subagents(assistant) == [builtin]
