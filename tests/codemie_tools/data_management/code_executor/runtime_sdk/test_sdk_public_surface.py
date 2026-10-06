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

"""A snapshot of what scripts stored in skills and files depend on: the SDK's public names, signatures and codes.

A change here breaks scripts that already exist, so it must be deliberate: names and codes are only added, never
renamed or removed, and a script must treat an error code it does not know as a failure.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
from types import ModuleType

import pytest

from codemie_tools.data_management.code_executor.sdk_reference import REFERENCE_PATH

SDK_PATH: Path = REFERENCE_PATH.parent / "codemie_runtime_sdk.py"

#: Every public name of the SDK module (not starting with an underscore, not an imported module).
PUBLIC_NAMES: frozenset[str] = frozenset(
    {
        "BRIDGE_DIR_NAME",
        "CALL_TIMEOUT_SECONDS",
        "CODE_BAD_ARGUMENTS",
        "CODE_BAD_REQUEST",
        "CODE_DEADLINE_EXCEEDED",
        "CODE_ERROR",
        "CODE_INTERNAL_ERROR",
        "CODE_NO_CONTEXT",
        "CODE_PAYLOAD_TOO_LARGE",
        "CODE_TIMEOUT",
        "CODE_TOOL_BLOCKED",
        "CODE_TOOL_FAILED",
        "CODE_TOOL_UNAVAILABLE",
        "CODE_UNAVAILABLE",
        "CODE_UNKNOWN_OP",
        "ERROR_CODES",
        "FILE_SUFFIX",
        "MAX_BATCH_CALLS",
        "MAX_PAYLOAD_BYTES",
        "POLL_INTERVAL_SECONDS",
        "PROTOCOL_VERSION",
        "REQ_PREFIX",
        "RESP_PREFIX",
        "TOOL_CALL_OP",
        "ToolCallError",
        "UNAVAILABLE_MARKER_NAME",
        "call",
        "call_tool",
        "call_tools",
    }
)

#: The wire value of each error code. A code is only ever added.
CODE_VALUES: dict[str, str] = {
    "CODE_NO_CONTEXT": "no_context",
    "CODE_TOOL_BLOCKED": "tool_blocked",
    "CODE_TOOL_UNAVAILABLE": "tool_unavailable",
    "CODE_BAD_ARGUMENTS": "bad_arguments",
    "CODE_TOOL_FAILED": "tool_failed",
    "CODE_UNKNOWN_OP": "unknown_op",
    "CODE_BAD_REQUEST": "bad_request",
    "CODE_PAYLOAD_TOO_LARGE": "payload_too_large",
    "CODE_INTERNAL_ERROR": "internal_error",
    "CODE_UNAVAILABLE": "unavailable",
    "CODE_TIMEOUT": "timeout",
    "CODE_ERROR": "error",
    "CODE_DEADLINE_EXCEEDED": "deadline_exceeded",
}

SIGNATURES: dict[str, str] = {
    "call": "(op: str, payload: dict[str, object], timeout: float | None = None) -> object",
    "call_tool": "(name: str, args: dict[str, object] | None = None, *, timeout: float | None = None) -> object",
    "call_tools": "(calls: list[dict[str, object]], *, timeout: float | None = None) -> list[object]",
}


@pytest.fixture(scope="module")
def sdk() -> ModuleType:
    spec = importlib.util.spec_from_file_location("codemie_runtime_sdk_surface", SDK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_public_names_are_the_reviewed_ones(sdk: ModuleType) -> None:
    actual = {name for name, value in vars(sdk).items() if not name.startswith("_") and not inspect.ismodule(value)}

    assert actual == PUBLIC_NAMES, (
        "The SDK's public surface changed. Scripts that exist depend on these names: add only, never rename or remove. "
        f"added: {sorted(actual - PUBLIC_NAMES)}, removed: {sorted(PUBLIC_NAMES - actual)}"
    )


@pytest.mark.parametrize("name", sorted(SIGNATURES))
def test_the_public_functions_keep_their_signatures(sdk: ModuleType, name: str) -> None:
    assert str(inspect.signature(getattr(sdk, name))) == SIGNATURES[name]


def test_the_error_class_keeps_its_shape(sdk: ModuleType) -> None:
    error = sdk.ToolCallError("message", "timeout")

    assert issubclass(sdk.ToolCallError, Exception)
    assert (str(error), error.code) == ("message", "timeout")
    assert sdk.ToolCallError("message").code == "error", "the code defaults to the generic one"


def test_the_error_codes_keep_their_wire_values_and_the_set_is_exactly_these(sdk: ModuleType) -> None:
    assert {name: getattr(sdk, name) for name in CODE_VALUES} == CODE_VALUES
    assert frozenset(CODE_VALUES.values()) == sdk.ERROR_CODES


def test_the_protocol_constants_keep_their_values(sdk: ModuleType) -> None:
    assert sdk.PROTOCOL_VERSION == 1
    assert sdk.TOOL_CALL_OP == "tool.call"
    assert sdk.BRIDGE_DIR_NAME == ".codemie_bridge"
    assert (sdk.REQ_PREFIX, sdk.RESP_PREFIX, sdk.FILE_SUFFIX) == ("req.", "resp.", ".json")
    assert sdk.MAX_BATCH_CALLS == 32
    assert sdk.MAX_PAYLOAD_BYTES == 256 * 1024
    assert sdk.CALL_TIMEOUT_SECONDS == 100.0
