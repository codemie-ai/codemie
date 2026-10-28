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

"""
This module provides base agent abstraction.
"""

import re
from time import time
from typing import Any

from pydantic import BaseModel

from codemie.chains import StreamedGenerationResult
from codemie.configs import config
from codemie.configs.logger import logger

from codemie.core.error_constants import BUDGET_MESSAGE_KEY, CURRENT_COST_KEY, MAX_BUDGET_KEY, ErrorCategory, ErrorCode
from codemie.core.errors import ErrorResponse, InternalError
from codemie.core.thread import ThreadedGenerator
from codemie.enterprise.litellm.proxy_router import handle_agent_exception
from codemie.service.agent_workspace_service import AgentWorkspaceService


SANDBOX_FILE_RE = re.compile(r"sandbox:/v1/files/[^\s)\]>\"']+")


class AbstractAgent:
    def extended_error(self, error_response: ErrorResponse, exception: Exception, hide_raw: bool = False) -> str:
        """Maintain original full error text for internal errors.

        With hide_raw=True the raw exception text is never returned; the sanitized error message is used instead.
        """
        err = error_response.get_error()
        error_code = err.error_code
        details = err.details or {}

        if error_response.category == ErrorCategory.INTERNAL:
            if hide_raw or not isinstance(err, InternalError):
                return err.message
            exception_type = details.get("type", "Unknown")
            error_message = details.get("message", "")
            return f"AI Agent run failed with error: {exception_type}: {error_message}"

        if error_code in (ErrorCode.LITE_LLM_BUDGET_EXCEEDED_ERROR, ErrorCode.AGENT_BUDGET_EXCEEDED):
            return self._budget_error_message(err, details, exception, hide_raw)

        if error_code == ErrorCode.LITE_LLM_BAD_REQUEST_ERROR and details.get("schema_validation_context"):
            return f"{err.message}\n{details['schema_validation_context']}"

        if error_code == ErrorCode.AGENT_TOKEN_LIMIT:
            return err.message

        return err.message if hide_raw else str(exception)

    @staticmethod
    def _budget_error_message(err, details: dict, exception: Exception, hide_raw: bool) -> str:
        if CURRENT_COST_KEY in details and MAX_BUDGET_KEY in details:
            context = (
                f"Budget has been exceeded: Your current spending: {details[CURRENT_COST_KEY]}, "
                f"available budget: {details[MAX_BUDGET_KEY]}"
            )
        elif details.get(BUDGET_MESSAGE_KEY):
            context = details[BUDGET_MESSAGE_KEY]
        else:
            return err.message if hide_raw else str(exception)
        return f"{err.message}\n{context}"

    def send_error_response(
        self,
        thread_generator: ThreadedGenerator,
        thread_context: dict | None,
        exception: Exception,
        execution_start: float,
        chunks_collector: list,
    ) -> None:
        """Send a single stream chunk with error details and mark the stream as finished.

        When HIDE_AGENT_STREAMING_EXCEPTIONS=True: friendly message in generated,
        structured payload in error_details. When False: legacy text in generated,
        error_details=None.
        """
        time_elapsed: float = time() - execution_start
        error_response: ErrorResponse = handle_agent_exception(exception)

        if config.HIDE_AGENT_STREAMING_EXCEPTIONS:
            generated: str = error_response.get_error().message
            error_details = (error_response.get_error().details or {}).get("error")
        else:
            text = self.extended_error(error_response, exception)
            chunks_collector.append(text)
            generated = "".join(chunks_collector)
            error_details = None

        thread_generator.send(
            StreamedGenerationResult(
                generated=generated,
                generated_chunk="",
                last=True,
                time_elapsed=time_elapsed,
                debug={},
                context=thread_context,
                execution_error=error_response.get_error().error_code.value,
                error_details=error_details,
            ).model_dump_json()
        )


class WorkspaceAwareAgent(AbstractAgent):
    """Base agent with shared generated-workspace file persistence helpers.

    Callers pass conversation and workspace context explicitly.
    """

    def _persist_generated_workspace_files(
        self,
        response: Any,
        conversation_id: str | None,
        user: Any,
        request_file_names: list[str] | None = None,
    ) -> None:
        if not conversation_id:
            return

        generated_file_urls = self._extract_generated_file_urls(response)
        if not generated_file_urls:
            return

        try:
            AgentWorkspaceService().register_generated_files(
                conversation_id=conversation_id,
                generated_file_urls=generated_file_urls,
                user=user,
                request_file_urls=request_file_names or [],
            )
        except Exception as exc:
            logger.warning(f"Failed to register generated files in workspace for conversation {conversation_id}: {exc}")

    @classmethod
    def _extract_generated_file_urls(cls, response: Any) -> list[str]:
        urls = dict.fromkeys(cls._extract_sandbox_urls(response))
        return list(urls)

    @classmethod
    def _extract_sandbox_urls(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return SANDBOX_FILE_RE.findall(value)

        if isinstance(value, BaseModel):
            return cls._extract_sandbox_urls(value.model_dump(mode="json"))

        if isinstance(value, dict):
            urls: list[str] = []
            for item in value.values():
                urls.extend(cls._extract_sandbox_urls(item))
            return urls

        if isinstance(value, (list, tuple, set)):
            urls: list[str] = []
            for item in value:
                urls.extend(cls._extract_sandbox_urls(item))
            return urls

        content = getattr(value, "content", None)
        if content is not None:
            return cls._extract_sandbox_urls(content)

        return []
