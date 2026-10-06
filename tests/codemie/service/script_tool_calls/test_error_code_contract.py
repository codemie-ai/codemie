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

"""Every error code a script can see is a named constant of the SDK, and ``ERROR_CODES`` is exactly those constants."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from codemie.service import script_tool_calls
from codemie_tools.data_management.code_executor import exec_runner, tool_call_channel, tool_call_protocol
from codemie_tools.data_management.code_executor.runtime_sdk import codemie_runtime_sdk

# Constructors that carry an error code, and the position of the code among the positional arguments.
_CODE_ARGUMENT_POSITION: dict[str, int] = {"ToolCallRefused": 0, "ToolCallError": 1, "error_response": 1, "_fail": 2}
_CODE_PATTERN = re.compile(r"^[a-z]+(_[a-z]+)*$")


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _code_arguments(path: Path) -> Iterator[tuple[ast.expr, str]]:
    """``(expression, "file:line")`` for every expression passed as an error code in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name not in _CODE_ARGUMENT_POSITION:
                continue
            position = _CODE_ARGUMENT_POSITION[name]
            if len(node.args) > position:
                yield node.args[position], f"{path.name}:{node.lineno}"
            for keyword in node.keywords:
                if keyword.arg == "code":
                    yield keyword.value, f"{path.name}:{node.lineno}"
        elif isinstance(node, ast.FunctionDef) and node.name == "__init__":
            # a default for a ``code`` parameter, e.g. ``ToolCallError.__init__(self, message, code=CODE_ERROR)``
            arguments = node.args
            defaults = [None] * (len(arguments.args) - len(arguments.defaults)) + list(arguments.defaults)
            for argument, default in zip(arguments.args, defaults, strict=True):
                if argument.arg == "code" and default is not None:
                    yield default, f"{path.name}:{node.lineno}"


def _backend_files() -> list[Path]:
    package_dir = Path(script_tool_calls.__file__).parent
    return [
        *sorted(package_dir.glob("*.py")),
        Path(tool_call_protocol.__file__),
        Path(tool_call_channel.__file__),
        Path(exec_runner.__file__),
    ]


def _sdk_constants() -> dict[str, str]:
    return {
        name: value
        for name, value in vars(codemie_runtime_sdk).items()
        if name.startswith("CODE_") and isinstance(value, str)
    }


def test_the_documented_set_is_exactly_the_named_constants() -> None:
    assert set(_sdk_constants().values()) == codemie_runtime_sdk.ERROR_CODES
    assert len(_sdk_constants()) == len(codemie_runtime_sdk.ERROR_CODES), "two constants must not share a value"


def test_no_string_literal_is_passed_as_an_error_code_in_the_backend() -> None:
    literals = {
        where: ast.unparse(expression)
        for path in _backend_files()
        for expression, where in _code_arguments(path)
        if isinstance(expression, ast.Constant)
    }

    assert literals == {}, "use the named constants of the SDK module"


def test_no_string_literal_is_passed_as_an_error_code_in_the_sdk_either() -> None:
    literals = {
        where: ast.unparse(expression)
        for expression, where in _code_arguments(Path(codemie_runtime_sdk.__file__))
        if isinstance(expression, ast.Constant)
    }

    assert literals == {}


def test_every_code_a_backend_module_passes_is_a_known_sdk_constant() -> None:
    constants = _sdk_constants()
    unknown: dict[str, str] = {}
    for path in _backend_files():
        for expression, where in _code_arguments(path):
            if isinstance(expression, ast.Name) and expression.id.startswith("CODE_"):
                if expression.id not in constants:
                    unknown[where] = expression.id
            elif (
                isinstance(expression, ast.Attribute)
                and expression.attr.startswith("CODE_")
                and expression.attr not in constants
            ):
                unknown[where] = expression.attr

    assert unknown == {}


def test_every_documented_code_is_used_by_the_backend_or_the_sdk() -> None:
    """A code nobody emits must leave the closed set, so the set stays the truth."""
    names_used: set[str] = set()
    for path in [*_backend_files(), Path(codemie_runtime_sdk.__file__)]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                names_used.add(node.id)
            elif isinstance(node, ast.Attribute):
                names_used.add(node.attr)

    unused = sorted(name for name in _sdk_constants() if name not in names_used)

    assert unused == []


def test_the_backend_names_the_codes_the_backend_and_the_sdk_are_known_to_emit() -> None:
    names = {
        expression.id
        for path in [*_backend_files(), Path(codemie_runtime_sdk.__file__)]
        for expression, _ in _code_arguments(path)
        if isinstance(expression, ast.Name)
    }

    assert {
        "CODE_NO_CONTEXT",
        "CODE_TOOL_BLOCKED",
        "CODE_TOOL_UNAVAILABLE",
        "CODE_BAD_ARGUMENTS",
        "CODE_TOOL_FAILED",
        "CODE_UNKNOWN_OP",
        "CODE_BAD_REQUEST",
        "CODE_PAYLOAD_TOO_LARGE",
        "CODE_INTERNAL_ERROR",
        "CODE_UNAVAILABLE",
        "CODE_TIMEOUT",
        "CODE_ERROR",
        "CODE_DEADLINE_EXCEEDED",
    } <= names


def test_the_tool_call_op_is_defined_once() -> None:
    handler_source = (Path(script_tool_calls.__file__).parent / "handler.py").read_text(encoding="utf-8")

    assert "TOOL_CALL_OP: str" not in handler_source
    assert 'TOOL_CALL_OP = "' not in handler_source


@pytest.mark.parametrize("code", sorted(codemie_runtime_sdk.ERROR_CODES))
def test_documented_codes_are_lowercase_snake_case(code: str) -> None:
    assert _CODE_PATTERN.match(code)


def test_the_set_is_immutable() -> None:
    assert isinstance(codemie_runtime_sdk.ERROR_CODES, frozenset)
