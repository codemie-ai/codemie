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

import json
import re
from collections.abc import Mapping
from functools import lru_cache

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, ValidationError

from codemie.configs import logger
from codemie.configs.script_call_log_guard import suppress_call_logging
from codemie.core.utils import sanitize_string
from codemie.service.script_tool_calls.authorizer import authorize_tool_call
from codemie.service.script_tool_calls.concurrency import GateTimeout, ToolRunGate, process_gate
from codemie.core.constants import TOOL_TYPE, ToolType
from codemie.service.script_tool_calls.context import ScriptRunContext
from codemie.service.script_tool_calls.observer import ScriptRunObserver
from codemie.service.script_tool_calls.token_count import count_tokens
from codemie_tools.data_management.workspace.tools_vars import EXECUTE_WORKSPACE_SCRIPT_TOOL
from codemie_tools.base.script_result import JsonValue, ScriptCallable, ScriptResult, ScriptResultTooLarge
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import (
    CODE_BAD_ARGUMENTS,
    CODE_PAYLOAD_TOO_LARGE,
    CODE_TOOL_FAILED,
    CODE_TOOL_UNAVAILABLE,
    MAX_PAYLOAD_BYTES,
    TOOL_CALL_OP,
)
from codemie_tools.data_management.code_executor.tool_call_protocol import (
    ToolCallHandler,
    ToolCallRefused,
    current_call_seq,
)

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
    observer: ScriptRunObserver | None = None,
) -> Mapping[str, ToolCallHandler]:
    """Handlers for one script run: ``tool.call`` bound to the run's context.

    ``gate`` bounds the tool runs of the whole process (default: the process-wide gate); ``max_payload_bytes`` is the
    size cap of an answer, which lets an oversized result be refused before it is parsed. ``observer`` learns the
    resolved tool of each authorized call (under the call's sequence number).
    """
    run_gate = gate if gate is not None else process_gate()

    def handle_tool_call(payload: Mapping[str, object]) -> dict[str, JsonValue]:
        try:
            return _handle_tool_call(context, payload, run_gate, max_payload_bytes, observer)
        except ToolCallRefused as refused:
            _record_error_class(observer, refused.cause_class)
            raise

    return {TOOL_CALL_OP: handle_tool_call}


def _handle_tool_call(
    context: ScriptRunContext,
    payload: Mapping[str, object],
    gate: ToolRunGate,
    max_payload_bytes: int,
    observer: ScriptRunObserver | None = None,
) -> dict[str, JsonValue]:
    name, args = _parse_payload(payload)
    try:
        tool = authorize_tool_call(context, name)
    except ToolCallRefused:
        _record_refused_detail(observer, context, name)
        raise
    _record_detail(observer, tool)
    if not isinstance(tool, ScriptCallable):
        # No structured-result path exists for other tools, and _run/invoke would bypass the script contract.
        raise ToolCallRefused(
            CODE_TOOL_UNAVAILABLE,
            f"Tool '{name}' is not available to this script run; use a tool from your own tool list.",
        )
    kwargs = _validated_kwargs(tool, args)
    try:
        with gate.hold() as ticket, suppress_call_logging():
            if observer is not None:
                observer.record_gate(ticket.waited, ticket.wait_seconds)
            outcome = tool.run_for_script(kwargs, max_result_bytes=max_payload_bytes)
    except GateTimeout as timeout:
        if observer is not None:
            observer.record_gate_timeout(getattr(timeout, "wait_seconds", 0.0))
        raise
    except ToolCallRefused:
        raise
    except ScriptResultTooLarge:
        raise ToolCallRefused(
            CODE_PAYLOAD_TOO_LARGE,
            f"The result of tool '{tool.name}' is over the {max_payload_bytes} byte limit.",
            ScriptResultTooLarge.__name__,
        ) from None
    except Exception as exc:  # noqa: BLE001 - any tool failure becomes a coded error for the script
        logger.warning(
            "Script tool call failed: tool=%s code=%s exception=%s", tool.name, CODE_TOOL_FAILED, type(exc).__name__
        )
        raise ToolCallRefused(CODE_TOOL_FAILED, _failure_message(tool.name, exc), type(exc).__name__) from None
    envelope = _envelope(outcome)
    _record_tokens(observer, context, name, args, envelope)
    return envelope


def _record_tokens(
    observer: ScriptRunObserver | None,
    context: ScriptRunContext,
    name: str,
    args: Mapping[str, object],
    envelope: Mapping[str, JsonValue],
) -> None:
    """Count the tokens of a successful call's request and answer, for the savings estimate. Never raises."""
    seq = current_call_seq()
    if observer is None or seq is None:
        return
    try:
        request = json.dumps({"name": name, "args": args}, ensure_ascii=False, default=str)
        answer = json.dumps(envelope, ensure_ascii=False, default=str)
        observer.record_tokens(seq, count_tokens(request, context.llm_model), count_tokens(answer, context.llm_model))
    except Exception as exc:  # noqa: BLE001 - a metric must never fail the call
        logger.warning(f"Script call token count failed: {type(exc).__name__}")


def _record_error_class(observer: ScriptRunObserver | None, cause_class: str | None) -> None:
    """Remember the class of the exception behind a refusal, so the error metric names it like a direct call's does."""
    seq = current_call_seq()
    if observer is not None and seq is not None and cause_class:
        observer.record_error_class(seq, cause_class)


def _record_detail(observer: ScriptRunObserver | None, tool: BaseTool) -> None:
    """Tell the observer which tool this call resolved to (the name of the tool, never the script's raw text)."""
    seq = current_call_seq()
    if observer is None or seq is None:
        return
    observer.record_detail(seq, tool.name, (tool.metadata or {}).get(TOOL_TYPE, ToolType.INTERNAL))


def _record_refused_detail(observer: ScriptRunObserver | None, context: ScriptRunContext, name: str) -> None:
    """A refused call to a tool that exists (blocked or excluded) still counts under that tool. A name that does not
    resolve stays unknown: the script's raw text must never become a metric label."""
    seq = current_call_seq()
    if observer is None or seq is None:
        return
    try:
        tool = context.scope.resolve(name)
    except ToolCallRefused:
        return
    if tool is not None:
        _record_detail(observer, tool)
    elif name == EXECUTE_WORKSPACE_SCRIPT_TOOL.name:
        observer.record_detail(seq, name, ToolType.INTERNAL)


def _kind(value: object) -> str:
    return type(value).__name__


def _parse_payload(payload: Mapping[str, object]) -> tuple[str, Mapping[str, object]]:
    unknown = sorted(str(key) for key in payload if key not in _PAYLOAD_FIELDS)
    if unknown:
        # The 'alias' field is deferred to the future Integration Control for Scripts story. Unknown fields
        # are refused, not silently dropped, so scripts know their input was unexpected.
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
        raise ToolCallRefused(
            CODE_BAD_ARGUMENTS, f"Invalid arguments for tool '{tool.name}': {problems}.", type(exc).__name__
        ) from None
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
