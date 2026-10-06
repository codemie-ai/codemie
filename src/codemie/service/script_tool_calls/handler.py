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


"""The ``tool.call`` handler: lets a workspace script call one platform tool and get its structured result.

Order of a call: parse the payload, authorize the tool, validate the arguments against the tool's
``args_schema``, wait for a place in the process-wide gate, run the tool through ``run_for_script`` with call logging
suppressed, build the envelope. Argument values and result content never appear in error messages or log lines.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, ValidationError

from codemie.configs import logger
from codemie.configs.script_call_log_guard import suppress_call_logging
from codemie.core.utils import sanitize_string
from codemie.service.script_tool_calls.authorizer import authorize_tool_call
from codemie.service.script_tool_calls.concurrency import ToolRunGate, process_gate
from codemie.service.script_tool_calls.context import ScriptRunContext
from codemie_tools.base.script_result import JsonValue, ScriptCallable, ScriptResult, ScriptResultTooLarge
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import (
    CODE_BAD_ARGUMENTS,
    CODE_PAYLOAD_TOO_LARGE,
    CODE_TOOL_FAILED,
    CODE_TOOL_UNAVAILABLE,
    MAX_PAYLOAD_BYTES,
    TOOL_CALL_OP,
)
from codemie_tools.data_management.code_executor.tool_call_protocol import ToolCallHandler, ToolCallRefused

_MAX_CAUSE_CHARS: int = 300
_PAYLOAD_FIELDS: frozenset[str] = frozenset({"name", "args"})
# ASCII whitespace (tab, newline, vertical tab, form feed, carriage return, space) and control
# characters, plus DEL. No `\s`: it already matches several codepoints below 0x20, which would
# duplicate part of that range inside the character class.
_WHITESPACE_OR_CONTROL = re.compile(r"[\x00-\x20\x7f]+")


def build_tool_call_handlers(
    context: ScriptRunContext,
    *,
    gate: ToolRunGate | None = None,
    max_payload_bytes: int = MAX_PAYLOAD_BYTES,
) -> Mapping[str, ToolCallHandler]:
    """Handlers for one script run: ``tool.call`` bound to the run's context.

    ``gate`` bounds the tool runs of the whole process (default: the process-wide gate); ``max_payload_bytes`` is the
    size cap of an answer, which lets an oversized result be refused before it is parsed.
    """
    run_gate = gate if gate is not None else process_gate()

    def handle_tool_call(payload: Mapping[str, object]) -> dict[str, JsonValue]:
        return _handle_tool_call(context, payload, run_gate, max_payload_bytes)

    return {TOOL_CALL_OP: handle_tool_call}


def _handle_tool_call(
    context: ScriptRunContext, payload: Mapping[str, object], gate: ToolRunGate, max_payload_bytes: int
) -> dict[str, JsonValue]:
    name, args = _parse_payload(payload)
    tool = authorize_tool_call(context, name)
    if not isinstance(tool, ScriptCallable):
        # No structured-result path exists for other tools, and _run/invoke would bypass the script contract.
        raise ToolCallRefused(
            CODE_TOOL_UNAVAILABLE,
            f"Tool '{name}' is not available to this script run; use a tool from your own tool list.",
        )
    kwargs = _validated_kwargs(tool, args)
    try:
        with gate.hold(), suppress_call_logging():
            outcome = tool.run_for_script(kwargs, max_result_bytes=max_payload_bytes)
    except ToolCallRefused:
        raise
    except ScriptResultTooLarge:
        raise ToolCallRefused(
            CODE_PAYLOAD_TOO_LARGE, f"The result of tool '{tool.name}' is over the {max_payload_bytes} byte limit."
        ) from None
    except Exception as exc:  # noqa: BLE001 - any tool failure becomes a coded error for the script
        logger.warning(
            "Script tool call failed: tool=%s code=%s exception=%s", tool.name, CODE_TOOL_FAILED, type(exc).__name__
        )
        raise ToolCallRefused(CODE_TOOL_FAILED, _failure_message(tool.name, exc)) from None
    return _envelope(outcome)


def _kind(value: object) -> str:
    return type(value).__name__


def _parse_payload(payload: Mapping[str, object]) -> tuple[str, Mapping[str, object]]:
    unknown = sorted(str(key) for key in payload if key not in _PAYLOAD_FIELDS)
    if unknown:
        # A setting of a call (story 3 adds one) is accepted only once the backend knows it; until then a script that
        # sends one is told, not ignored.
        raise ToolCallRefused(CODE_BAD_ARGUMENTS, f"Unknown field in the call: {', '.join(unknown)}.")
    name = payload.get("name")
    if not isinstance(name, str) or not name:
        raise ToolCallRefused(CODE_BAD_ARGUMENTS, f"Field 'name' must be a non-empty string, got {_kind(name)}.")
    args = payload.get("args", {})
    if not isinstance(args, Mapping):
        raise ToolCallRefused(CODE_BAD_ARGUMENTS, f"Field 'args' must be an object, got {_kind(args)}.")
    return name, args


def _validated_kwargs(tool: ScriptCallable, args: Mapping[str, object]) -> dict[str, object]:
    """Validate against the tool's pydantic ``args_schema``; return only the fields the script provided.

    An argument name the schema does not know is refused, never dropped: a misspelled filter would
    otherwise run the tool with defaults and report success.
    """
    schema = tool.args_schema
    if schema is None:
        if args:
            names = ", ".join(sorted(str(name) for name in args))
            raise ToolCallRefused(CODE_BAD_ARGUMENTS, f"Tool '{tool.name}' takes no arguments; got: {names}.")
        return {}
    if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
        raise ToolCallRefused(
            CODE_TOOL_UNAVAILABLE, f"Tool '{tool.name}' has no argument schema the script path can use."
        )
    try:
        validated = _strict_schema(schema).model_validate(dict(args))
    except ValidationError as exc:
        problems = "; ".join(f"{_location(error['loc'])}: {error['type']}" for error in exc.errors())
        raise ToolCallRefused(CODE_BAD_ARGUMENTS, f"Invalid arguments for tool '{tool.name}': {problems}.") from None
    return {field: getattr(validated, field) for field in validated.model_fields_set}


@lru_cache(maxsize=256)
def _strict_schema(schema: type[BaseModel]) -> type[BaseModel]:
    """The tool's schema with unknown keys rejected (``extra="forbid"``), unless it already allows extras."""
    if schema.model_config.get("extra") == "allow":
        return schema

    class _Strict(schema):  # type: ignore[valid-type,misc]
        model_config = ConfigDict(extra="forbid")

    _Strict.__name__ = schema.__name__
    return _Strict


def _location(loc: tuple[int | str, ...]) -> str:
    return ".".join(str(part) for part in loc) or "(root)"


def _failure_message(tool_name: str, exc: Exception) -> str:
    cause = _WHITESPACE_OR_CONTROL.sub(" ", sanitize_string(str(exc))).strip()[:_MAX_CAUSE_CHARS].strip()
    base = f"Tool '{tool_name}' failed ({type(exc).__name__})"
    return f"{base}: {cause}" if cause else f"{base}."


def _envelope(outcome: ScriptResult) -> dict[str, JsonValue]:
    envelope: dict[str, JsonValue] = {"result": outcome.result}
    if outcome.http_status is not None:
        envelope["http"] = {"status": outcome.http_status, "reason": outcome.http_reason}
    return envelope
