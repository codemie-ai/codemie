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

from time import time

from codemie.chains import StreamedGenerationResult
from codemie.configs import config

from codemie.core.error_constants import BUDGET_MESSAGE_KEY, CURRENT_COST_KEY, MAX_BUDGET_KEY, ErrorCategory, ErrorCode
from codemie.core.errors import ErrorResponse, InternalError
from codemie.core.thread import ThreadedGenerator
from codemie.enterprise.litellm.proxy_router import handle_agent_exception


class AbstractAgent:
    def extended_error(self, error_response: ErrorResponse, exception: Exception) -> str:
        """Maintain original full error text for internal errors."""
        err = error_response.get_error()
        error_code = err.error_code
        details = err.details or {}

        if error_response.category == ErrorCategory.INTERNAL and isinstance(err, InternalError):
            details = err.details or {}
            exception_type = details.get("type", "Unknown")
            error_message = details.get("message", "")
            return f"AI Agent run failed with error: {exception_type}: {error_message}"

        if error_code in (ErrorCode.LITE_LLM_BUDGET_EXCEEDED_ERROR, ErrorCode.AGENT_BUDGET_EXCEEDED):
            if CURRENT_COST_KEY in details and MAX_BUDGET_KEY in details:
                context = (
                    f"Budget has been exceeded: Your current spending: {details[CURRENT_COST_KEY]}, "
                    f"available budget: {details[MAX_BUDGET_KEY]}"
                )
            elif details.get(BUDGET_MESSAGE_KEY):
                context = details[BUDGET_MESSAGE_KEY]
            else:
                return str(exception)
            return f"{err.message}\n{context}"

        if error_code == ErrorCode.LITE_LLM_BAD_REQUEST_ERROR and details.get("schema_validation_context"):
            return f"{err.message}\n{details['schema_validation_context']}"

        return str(exception)

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
