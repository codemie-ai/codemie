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

import json
import traceback
from abc import abstractmethod
from collections.abc import Mapping
from time import time
from typing import Any, ClassVar, Optional, Union

from langchain_core.tools import BaseTool
from langchain_core.tools.base import ToolException
from pydantic import BaseModel

from codemie.configs import config
from codemie.configs.logger import logger
from codemie_tools.base.errors import TruncatedOutputError
from codemie_tools.base.models import ToolOutputFormat
from codemie_tools.base.script_result import JsonValue, ScriptResult, ScriptResultSource
from codemie_tools.base.utils import get_encoding, sanitize_string, humanize_error


def _to_json_safe(value: object) -> JsonValue:
    """Recursive JSON-safe copy: dict keys and values that are not JSON types go through `str`."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, dict):
        return {str(key): _to_json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_json_safe(item) for item in value]
    return str(value)


def _parse_json_container(text: str) -> JsonValue:
    """Parse a string that looks like a JSON object/array; any other string is returned unchanged."""
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            parsed: JsonValue = json.loads(stripped)
        except ValueError:
            return text
        return parsed
    return text


def to_script_result(output: object, max_bytes: int | None = None) -> ScriptResult:
    """Convert a raw tool output to the structured result handed to a workspace script."""
    if isinstance(output, ScriptResultSource):
        return output.to_script_result(max_bytes)
    return ScriptResult(result=_to_script_value(output))


def _to_script_value(output: object) -> JsonValue:
    if isinstance(output, BaseModel):
        return output.model_dump(mode="json")
    if isinstance(output, (dict, list)):
        return _to_json_safe(output)
    if output is None or isinstance(output, (bool, int, float)):
        return output
    if isinstance(output, str):
        return _parse_json_container(output)
    return str(output)


class CodeMieTool(BaseTool):
    base_name: Optional[str] = None
    handle_tool_error: bool = True
    tokens_size_limit: int = config.TOOL_TOKENS_SIZE_LIMIT
    throw_truncated_error: bool = False
    truncate_message: str = "Tool output is truncated."
    base_llm_model_name: str = "gpt-4.1-mini"
    output_format: ToolOutputFormat = ToolOutputFormat.TEXT

    _SAFE_HTTP_METHODS: ClassVar[frozenset[str]] = frozenset({"GET", "HEAD", "OPTIONS"})

    # Opt-in for workspace scripts: a tool is callable through the bridge only when its class sets this to True.
    script_callable: ClassVar[bool] = False

    @staticmethod
    def _http_method_is_safe(args: dict, method_key: str = "method") -> bool:
        """Return True when the HTTP method in args is a safe (read-only) method."""
        method = (args.get(method_key) or "").strip().upper()
        return method in CodeMieTool._SAFE_HTTP_METHODS

    def is_safe(self, args: dict) -> bool:
        """Return True if this tool call produces no side effects (safe to auto-approve).

        Default: False (conservative — unknown tools are treated as mutating).
        Override in subclasses with well-known semantics.
        For generic REST tools, inspect args to detect the HTTP method.
        """
        return False

    def _parse_input(self, tool_input: Union[str, dict], tool_call_id: Optional[str]) -> Union[str, dict[str, Any]]:
        """Override _parse_input to catch all exceptions and raise ToolException.

        Args:
            tool_input: The input to the tool.
            tool_call_id: The id of the tool call.

        Returns:
            The parsed input.

        Raises:
            ToolException: If any exception occurs during input parsing.
        """
        try:
            return super()._parse_input(tool_input, tool_call_id)
        except Exception as e:
            error_message = f"Error parsing tool input in {self.name}: {str(e)}"
            logger.error(f"{error_message}.\nTool input: {tool_input}\nTool call id: {tool_call_id}")
            raise ToolException(error_message) from e

    def _run(self, *args, **kwargs):
        start = time()
        try:
            # Validate configuration before executing
            self._validate_config()
            result = self.execute(*args, **kwargs)
            output, _ = self._limit_output_content(result)
            return self._post_process_output_content(output, *args, **kwargs)
        except Exception as ex:
            duration = time() - start
            stacktrace = sanitize_string(traceback.format_exc())
            error_message = (
                f"Error calling tool: {self.name} with: \n"
                f"Arguments: {kwargs}. \n"
                f"The root cause is: '{sanitize_string(str(ex))}'"
            )
            logger.error(f"{error_message}. Error stacktrace: {stacktrace} duration={duration:.2f}s")
            raise ToolException(error_message) from ex

    def _execute_for_script(self, *args: Any, **kwargs: Any) -> Any:
        """The raw output of a run for a workspace script. By default the tool's own ``execute``; a tool whose
        script run differs from its model run (for example an HTTP tool that returns a non-2xx status as data)
        overrides this and shares the rest with ``execute``."""
        return self.execute(*args, **kwargs)

    def execute_structured(self, *args: Any, **kwargs: Any) -> ScriptResult:
        """Script-path twin of `execute()`: raw output as a structured result, no token limiting or truncation.

        Exceptions propagate unchanged.
        """
        return to_script_result(self._execute_for_script(*args, **kwargs))

    def run_for_script(self, arguments: Mapping[str, object], *, max_result_bytes: int | None = None) -> ScriptResult:
        """Run the tool for a workspace script: validate config, run with ``arguments``, convert the output.

        ``max_result_bytes`` is the size cap of the answer; an output that knows its size is refused before it is
        converted (see :class:`ScriptResultTooLarge`).
        """
        self._validate_config()
        return to_script_result(self._execute_for_script(**arguments), max_result_bytes)

    @abstractmethod
    def execute(self, *args, **kwargs) -> Any:
        pass

    def healthcheck(self):
        try:
            self._healthcheck()
        except Exception as e:
            return False, humanize_error(e)
        return True, ""

    def _healthcheck(self):
        raise NotImplementedError(f"Healthcheck method is not implemented for {self.name}")

    def _validate_config(self) -> None:
        """
        Validate configuration fields marked as required at runtime.

        This method checks all fields in self.config that have
        json_schema_extra={'required_at_runtime': True} and raises
        ValueError if any are empty.

        Tools can override this method for custom validation logic.

        Raises:
            ValueError: If required fields are missing or empty
        """
        if not hasattr(self, 'config') or not self.config:
            return  # No config to validate

        missing_fields = self._get_missing_required_fields()

        if missing_fields:
            raise ValueError(
                f"Tool config is not set. Please provide {', '.join(missing_fields)} before using the tool."
            )

    def _get_missing_required_fields(self):
        """
        Get list of required fields that are missing or empty.

        Returns:
            List of missing field names in user-friendly format
        """
        missing = []

        # Access model_fields from the class, not the instance (Pydantic v2.11+)
        for field_name, field_info in self.config.__class__.model_fields.items():
            # Check if field is marked as required at runtime
            json_schema_extra = field_info.json_schema_extra or {}
            if json_schema_extra.get('required_at_runtime'):
                field_value = getattr(self.config, field_name, None)

                # Check if empty (None, empty string, empty list, etc.)
                if not field_value:
                    # Use Pydantic's title if available, otherwise format field name
                    display_name = field_info.title or field_name.replace('_', ' ')
                    missing.append(display_name)

        return missing

    def calculate_tokens_count(self, output: Any) -> int:
        encoding = get_encoding(self.base_llm_model_name)

        tokens = encoding.encode(str(output))
        return len(tokens)

    def _limit_output_content(self, output: Any) -> Any:
        """
        Limit the size of the output based on token constraints.

        Args:
            output (Any): The content to be processed and potentially truncated.

        Returns:
            Tuple[Any, int]: The (possibly truncated) output and the token count.

        Raises:
            TruncatedOutputError: If the output exceeds the token size limit and throwing errors is enabled.
        """
        encoding = get_encoding(self.base_llm_model_name)

        tokens = encoding.encode(str(output))
        token_count = len(tokens)

        logger.info(f"{self.name}: Tokens size of potential response: {token_count}")

        if token_count <= self.tokens_size_limit:
            return output, token_count

        # Output exceeds token limit: calculate truncation details
        truncate_ratio = self.tokens_size_limit / token_count
        truncated_data = encoding.decode(tokens[: self.tokens_size_limit])
        truncated_output = (
            f"{self.truncate_message} Ratio limit/used_tokens: {truncate_ratio}. Tool output: {truncated_data}"
        )
        error_message = (
            f"{self.name} output is too long: {token_count} tokens. "
            f"Ratio limit/used_tokens: {truncate_ratio} for output tokens {self.tokens_size_limit}"
        )

        logger.error(error_message)

        if self.throw_truncated_error:
            raise TruncatedOutputError(truncated_output)

        return truncated_output, token_count

    def _post_process_output_content(self, mcp_tool_output: Any, *args, **kwargs) -> Any:
        """Convert tool output to a string suitable for LLM consumption.

        Non-string outputs (dicts, lists) are JSON-serialized to prevent them from
        being misinterpreted as LLM message content blocks by LangChain/LangGraph.
        For example, a GitHub API response ``{"type": "file", ...}`` would otherwise
        be treated as an OpenAI content block with ``type="file"``, which Azure
        OpenAI does not support.
        """
        if isinstance(mcp_tool_output, str):
            return mcp_tool_output
        try:
            return json.dumps(mcp_tool_output, ensure_ascii=False)
        except Exception:
            return str(mcp_tool_output)
