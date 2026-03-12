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
Base class for assistant validation nodes.
"""

from langchain_core.language_models import BaseLanguageModel
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from codemie.configs.logger import logger
from codemie.core.dependecies import get_llm_by_credentials
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User
from codemie.service.monitoring.base_monitoring_service import send_log_metric
from codemie.service.monitoring.metrics_constants import (
    MARKETPLACE_ASSISTANT_VALIDATION_LLM_INVOKE_METRIC,
    MetricsAttributes,
)


class BaseValidationNode:
    """Base class for assistant validation nodes that need LLM access.

    This is a simplified base class separate from BaseNode because:
    1. Validation workflow doesn't use callbacks, workflow_execution_service, or thought_queue
    2. Validation is synchronous and completes quickly (<10 seconds)
    3. We only need LLM access and basic error handling
    """

    # LLM Configuration for Validation
    DEFAULT_TEMPERATURE = 0.0  # Deterministic for consistency
    DEFAULT_STREAMING = False  # No streaming needed for validation

    def __init__(self, llm_model: str, request_id: str | None = None):
        """Initialize validation node with LLM access.

        Args:
            llm_model: LLM model name (e.g., "gpt-4o-mini")
            request_id: Optional request ID for tracking
        """
        self.llm_model = llm_model
        self.request_id = request_id
        self._llm: BaseLanguageModel | None = None

    @property
    def llm(self) -> BaseLanguageModel:
        """Lazy-load LLM instance on first access with validation-specific config.

        Configuration:
        - temperature=0.0: Deterministic responses for consistent validation
        - streaming=False: No streaming needed (synchronous validation)

        Returns:
            BaseLanguageModel: LLM instance ready for use

        Raises:
            RuntimeError: If LLM initialization fails
        """
        if self._llm is None:
            try:
                self._llm = get_llm_by_credentials(
                    llm_model=self.llm_model,
                    temperature=self.DEFAULT_TEMPERATURE,
                    streaming=self.DEFAULT_STREAMING,
                    request_id=self.request_id,
                )
                logger.debug(
                    f"Initialized LLM for validation: {self.llm_model} "
                    f"(temp={self.DEFAULT_TEMPERATURE}, streaming={self.DEFAULT_STREAMING})"
                )
            except Exception as e:
                logger.error(f"Failed to initialize LLM: {e}", exc_info=True)
                raise RuntimeError(f"Cannot initialize LLM model {self.llm_model}: {e}")
        return self._llm

    @retry(
        stop=stop_after_attempt(3),  # 3 attempts total (initial + 2 retries)
        wait=wait_exponential(multiplier=1, min=1, max=4),  # 1s, 2s, 4s
        retry=retry_if_exception(lambda e: True),  # Retry on any exception
        reraise=True,
    )
    def _invoke_llm(self, prompt: str, output_model: type[BaseModel]) -> BaseModel:
        """Invoke LLM with structured output (with automatic retry via decorator).

        Args:
            prompt: The prompt to send to LLM
            output_model: Pydantic model for structured output

        Returns:
            BaseModel: Parsed structured output
        """
        return self.llm.with_structured_output(output_model).invoke(prompt)  # pyright: ignore

    def invoke_llm_with_retry[F: BaseModel](
        self, prompt: str, output_model: type[F], user: User | None = None, max_llm_retries: int = 2
    ) -> F:
        """Invoke LLM with automatic retry on transient failures.

        Args:
            prompt: The prompt to send to LLM
            output_model: Pydantic model for structured output
            user: User object for metrics tracking (optional)
            max_llm_retries: Maximum number of retry attempts for LLM failures

        Returns:
            BaseModel: Parsed structured output

        Raises:
            ExtendedHTTPException: If LLM call fails after retries
        """
        try:
            result = self._invoke_llm(prompt, output_model)  # pyright: ignore

            # Send success metric
            send_log_metric(
                name=MARKETPLACE_ASSISTANT_VALIDATION_LLM_INVOKE_METRIC,
                attributes={
                    MetricsAttributes.LLM_MODEL: self.llm_model,
                    MetricsAttributes.USER_ID: user.id if user else "-",
                    MetricsAttributes.USER_NAME: user.name if user else "-",
                    MetricsAttributes.USER_EMAIL: user.username if user else "-",
                    MetricsAttributes.STATUS: "success",
                },
            )

            return result
        except Exception as e:
            logger.error(f"LLM call failed after {max_llm_retries + 1} attempts: {e}", exc_info=True)

            # Send failure metric
            send_log_metric(
                name=MARKETPLACE_ASSISTANT_VALIDATION_LLM_INVOKE_METRIC,
                attributes={
                    MetricsAttributes.LLM_MODEL: self.llm_model,
                    MetricsAttributes.USER_ID: user.id if user else "-",
                    MetricsAttributes.USER_NAME: user.name if user else "-",
                    MetricsAttributes.USER_EMAIL: user.username if user else "-",
                    MetricsAttributes.STATUS: "failed",
                    MetricsAttributes.ERROR: str(e)[:500],  # Limit error message length
                },
            )

            raise ExtendedHTTPException(
                code=500,
                message="LLM validation service unavailable",
                details=f"Failed to get response from LLM after {max_llm_retries + 1} attempts: {str(e)}",
                help="Please try again later. If the issue persists, contact support.",
            )
