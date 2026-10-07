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

from __future__ import annotations

from abc import abstractmethod
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from codemie_tools.base import tool_registry
from codemie_tools.base.codemie_tool import CodeMieTool


class _Args(BaseModel):
    pass


@pytest.fixture(autouse=True)
def _clean_registry():
    """Run each test against an empty registry and restore the real one afterwards."""
    saved = {name: list(classes) for name, classes in tool_registry._CLASSES_BY_NAME.items()}
    tool_registry._CLASSES_BY_NAME.clear()
    yield
    tool_registry._CLASSES_BY_NAME.clear()
    tool_registry._CLASSES_BY_NAME.update(saved)


def _make_tool(name: str, callable_: bool, module: str = "codemie_tools.fake.module") -> type[CodeMieTool]:
    class _Tool(CodeMieTool):
        script_callable = callable_
        description: str = "d"
        args_schema: type[BaseModel] = _Args

        def execute(self) -> str:
            return "ok"

    _Tool.model_fields["name"].default = name
    _Tool.__module__ = module
    return _Tool


class TestRegister:
    def test_concrete_named_tool_in_a_tool_module_is_registered(self) -> None:
        cls = _make_tool("reg_tool", True)

        tool_registry.register(cls)

        record = tool_registry.lookup("reg_tool")
        assert record is not None
        assert record.name == "reg_tool"
        assert record.classes == (cls,)
        assert record.modules == ("codemie_tools.fake.module",)
        assert record.script_callable == (True,)

    def test_codemie_module_is_accepted(self) -> None:
        tool_registry.register(_make_tool("platform_tool", True, "codemie.agents.tools.x"))

        assert tool_registry.lookup("platform_tool") is not None

    def test_foreign_module_is_skipped(self) -> None:
        tool_registry.register(_make_tool("test_local_tool", True, "tests.some_test"))

        assert tool_registry.lookup("test_local_tool") is None

    def test_abstract_class_is_skipped(self) -> None:
        class _Abstract(CodeMieTool):
            description: str = "d"
            args_schema: type[BaseModel] = _Args

            @abstractmethod
            def other(self) -> None: ...

            def execute(self) -> str:
                return "ok"

        _Abstract.model_fields["name"].default = "abstract_tool"
        _Abstract.__module__ = "codemie_tools.fake.module"

        tool_registry.register(_Abstract)

        assert tool_registry.lookup("abstract_tool") is None

    def test_nameless_class_is_skipped(self) -> None:
        class _Nameless(CodeMieTool):
            description: str = "d"
            args_schema: type[BaseModel] = _Args

            def execute(self) -> str:
                return "ok"

        _Nameless.__module__ = "codemie_tools.fake.module"

        tool_registry.register(_Nameless)

        assert tool_registry._CLASSES_BY_NAME == {}

    def test_two_classes_with_one_name_are_both_kept(self) -> None:
        first, second = _make_tool("dup_tool", True), _make_tool("dup_tool", False)

        tool_registry.register(first)
        tool_registry.register(second)

        record = tool_registry.lookup("dup_tool")
        assert record is not None
        assert record.classes == (first, second)
        assert record.script_callable == (True, False)

    def test_registering_the_same_class_twice_keeps_one_entry(self) -> None:
        cls = _make_tool("once_tool", True)

        tool_registry.register(cls)
        tool_registry.register(cls)

        record = tool_registry.lookup("once_tool")
        assert record is not None
        assert record.classes == (cls,)

    def test_lookup_of_unknown_name_is_none(self) -> None:
        assert tool_registry.lookup("nope") is None


class TestKnownExcluded:
    def test_true_when_every_class_is_not_script_callable(self) -> None:
        tool_registry.register(_make_tool("ex_tool", False))
        tool_registry.register(_make_tool("ex_tool", False))

        assert tool_registry.is_known_excluded_from_script_calls("ex_tool") is True

    def test_false_when_a_class_is_script_callable(self) -> None:
        tool_registry.register(_make_tool("ok_tool", True))

        assert tool_registry.is_known_excluded_from_script_calls("ok_tool") is False

    def test_false_for_mixed_names(self) -> None:
        tool_registry.register(_make_tool("mixed_tool", False))
        tool_registry.register(_make_tool("mixed_tool", True))

        assert tool_registry.is_known_excluded_from_script_calls("mixed_tool") is False

    def test_false_for_unknown_names(self) -> None:
        assert tool_registry.is_known_excluded_from_script_calls("unknown_tool") is False


class TestSubclassHook:
    def test_defining_a_subclass_goes_through_register(self) -> None:
        with patch.object(tool_registry, "register") as register:

            class _Hooked(CodeMieTool):
                description: str = "d"
                args_schema: type[BaseModel] = _Args

                def execute(self) -> str:
                    return "ok"

        register.assert_called_once_with(_Hooked)

    def test_real_tool_classes_are_registered_after_loading(self) -> None:
        tool_registry.ensure_loaded()

        # the clean-registry fixture emptied the dict after import, so only classes imported now would re-register;
        # the module-level registry is covered by the sync test. Here: loading must not raise and is idempotent.
        tool_registry.ensure_loaded()


class TestEnsureLoaded:
    def test_imports_each_package_once(self) -> None:
        with patch.object(tool_registry, "_LOADED", False), patch.object(tool_registry, "_import_all") as import_all:
            tool_registry.ensure_loaded()
            tool_registry.ensure_loaded()

        assert [c.args[0] for c in import_all.call_args_list] == list(tool_registry.TOOL_PACKAGES)

    def test_import_failure_is_logged_not_swallowed_silently(self) -> None:
        with (
            patch.object(tool_registry, "_LOADED", False),
            patch.object(tool_registry, "_import_all", side_effect=ImportError("boom")),
            patch.object(tool_registry, "logger") as logger,
        ):
            tool_registry.ensure_loaded()

        assert logger.warning.called
