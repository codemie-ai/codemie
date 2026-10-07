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


"""The registry's static "not script-callable" answer must agree with the instance check used at call time."""

from __future__ import annotations

import pytest

from codemie.service.script_tool_calls.exclusions import is_excluded_from_script_calls
from codemie_tools.base import tool_registry


def _not_callable_classes() -> list[tuple[str, type]]:
    tool_registry.ensure_loaded()
    return [
        (name, cls)
        for name, classes in sorted(tool_registry._CLASSES_BY_NAME.items())
        for cls in classes
        if not getattr(cls, "script_callable", False)
    ]


def test_registry_is_populated_after_loading() -> None:
    tool_registry.ensure_loaded()

    assert tool_registry.is_known_excluded_from_script_calls("code_executor")


@pytest.mark.parametrize(
    ("name", "cls"), _not_callable_classes(), ids=lambda v: v if isinstance(v, str) else v.__qualname__
)
def test_registry_exclusion_matches_the_instance_check(name: str, cls: type) -> None:
    instance = cls.model_construct()

    assert is_excluded_from_script_calls(instance), f"{name} ({cls.__module__}.{cls.__qualname__})"
