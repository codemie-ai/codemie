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

"""Abstract base class for observability providers.

All observability backends (Langfuse, Phoenix, etc.) implement this interface
so that core business logic remains provider-agnostic.
"""

from __future__ import annotations

import contextlib
from abc import ABC, abstractmethod
from typing import Any, Literal

ObservationLevel = Literal["DEFAULT", "DEBUG", "WARNING", "ERROR"]
"""Observation severity, mirrored 1:1 from Langfuse's ObservationLevel enum.

Phoenix maps DEFAULT -> OTEL Status OK, ERROR -> OTEL Status ERROR; DEBUG and
WARNING are recorded as the ``observation.level`` span attribute only."""


class ObservabilityProvider(ABC):
    """Contract for all observability backend implementations.

    Implementations must be safe to call at any time — before initialization,
    after shutdown, or when the backend is unavailable. All methods must degrade
    gracefully (return None/False/{}) rather than raising.
    """

    @abstractmethod
    def initialize(self) -> None:
        """Initialize the provider (connect, register handlers). Called once at app startup."""

    @abstractmethod
    def shutdown(self) -> None:
        """Flush pending data and release resources. Called at app shutdown."""

    @abstractmethod
    def is_enabled(self) -> bool:
        """Return True if this provider is active and configured."""

    @abstractmethod
    def get_callback_handler(self) -> Any | None:
        """Return a LangChain-compatible CallbackHandler, or None if not applicable.

        Returns None for auto-instrumentation providers (e.g., Phoenix) that use global
        OTEL instrumentation instead of per-invocation callbacks.
        """

    @abstractmethod
    def create_workflow_trace_context(
        self,
        execution_id: str,
        workflow_id: str | None,
        workflow_name: str,
        user_id: str | None,
        session_id: str | None = None,
        tags: list[str] | None = None,
    ) -> Any | None:
        """Open a parent trace for a workflow execution. Returns an opaque context object."""

    @abstractmethod
    def get_workflow_trace_context(self, execution_id: str) -> Any | None:
        """Retrieve the active trace context for a workflow execution."""

    @abstractmethod
    def clear_workflow_trace_context(self, execution_id: str) -> bool:
        """Clean up trace context after workflow completion. Must be called in finally blocks."""

    @abstractmethod
    def build_agent_metadata(
        self,
        agent_name: str,
        conversation_id: str,
        llm_model: str,
        username: str | None = None,
        tags: list[str] | None = None,
        trace_context: Any | None = None,
    ) -> dict:
        """Build run-config metadata dict for LangChain agent invocation."""

    @abstractmethod
    def should_trace_request(self, request_metadata: dict | None) -> bool:
        """Determine if the current request should be traced.

        Checks provider availability, global config, and per-request overrides
        (e.g., observability_traces_enabled in request metadata).
        """

    @abstractmethod
    def make_observe_decorator(self) -> Any:
        """Return a decorator factory equivalent to Langfuse's @observe(name=...).

        The returned callable must support the signature:
            observe(name: str | None = None) -> decorator

        When the provider is disabled, must return a no-op decorator factory.
        """

    def get_trace_context(
        self,
        trace_name: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        tags: list[str] | None = None,
    ):
        """Return a context manager that sets trace attributes for the current invocation.

        Default implementation returns nullcontext() — a no-op. Provider implementations
        (e.g., Langfuse) override this to return propagate_attributes() or equivalent.

        Must never raise; degrade silently to nullcontext() on any failure.
        """
        return contextlib.nullcontext()

    def update_current_observation(
        self,
        *,
        name: str | None = None,
        input: Any | None = None,
        output: Any | None = None,
        metadata: dict[str, Any] | None = None,
        level: ObservationLevel | None = None,
        status_message: str | None = None,
    ) -> None:
        """Update attributes on the currently active observation/span.

        Provider-agnostic primitive used by callers that are inside an active
        ``@observe``-decorated frame. Maps to ``update_current_observation`` on
        Langfuse and to OTEL span attribute/status updates on Phoenix.

        Default implementation is a no-op — safe to call when no provider is active.
        Must never raise; degrade silently on any failure.
        """

    def update_current_trace(
        self,
        *,
        name: str | None = None,
        input: Any | None = None,
        output: Any | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Update attributes on the currently active trace (root).

        Provider-agnostic primitive. Maps to ``update_current_trace`` on Langfuse;
        on Phoenix where there is no separate trace-root concept, falls back to
        annotating the current OTEL span.

        Default implementation is a no-op. Must never raise.
        """

    def update_current_generation(
        self,
        *,
        model: str | None = None,
        model_parameters: dict[str, Any] | None = None,
        input: Any | None = None,
        output: Any | None = None,
        usage_details: dict[str, int | float] | None = None,
        cost_details: dict[str, float] | None = None,
        metadata: dict[str, Any] | None = None,
        level: ObservationLevel | None = None,
        status_message: str | None = None,
    ) -> None:
        """Record model-call details on the active observation, for calls made outside LangChain.

        Use inside a frame opened with ``observe(name=..., as_type="generation")`` to trace
        direct SDK calls (speech-to-text, for example) that no callback handler sees.
        ``usage_details`` keys follow Langfuse naming (``input``, ``output``, ``total``, or a
        custom unit such as ``audio_seconds``). ``cost_details`` holds an explicit cost in USD
        (``input``, ``output``, ``total``) for calls the backend cannot price from usage alone.

        Default implementation is a no-op. Must never raise.
        """
