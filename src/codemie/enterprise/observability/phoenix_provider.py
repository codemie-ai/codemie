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

"""Arize Phoenix observability provider.

Implements the ObservabilityProvider ABC and delegates all Phoenix-specific
logic to the codemie_enterprise.phoenix support modules (service, context
manager, helpers, observe).

Available when OBSERVABILITY_PROVIDER=phoenix and codemie_enterprise[phoenix]
is installed.
"""

from __future__ import annotations

import contextlib
import json
from typing import TYPE_CHECKING, Any

from codemie.configs import config, logger

from .base import ObservabilityProvider, ObservationLevel

if TYPE_CHECKING:
    from codemie_enterprise.phoenix.models import PhoenixTraceContext
    from codemie_enterprise.phoenix.service import PhoenixService


_USAGE_ATTRIBUTES = {
    "input": "llm.token_count.prompt",
    "output": "llm.token_count.completion",
    "total": "llm.token_count.total",
}

_COST_ATTRIBUTES = {
    "input": "llm.cost.prompt",
    "output": "llm.cost.completion",
    "total": "llm.cost.total",
}


def _current_recording_span() -> Any | None:
    """Return the active OTEL span if it is recording, else ``None``."""
    from opentelemetry import trace as otel_trace

    span = otel_trace.get_current_span()
    return span if span.is_recording() else None


def _set_span_name(span: Any, name: str) -> None:
    """Best-effort rename of an active span; never raises."""
    with contextlib.suppress(Exception):
        span.update_name(name)


def _set_json_attr(span: Any, key: str, value: Any) -> None:
    """Set a span attribute whose value is JSON-encoded (``default=str`` fallback)."""
    span.set_attribute(key, json.dumps(value, default=str))


def _set_metadata_attrs(span: Any, prefix: str, metadata: dict[str, Any]) -> None:
    """Flatten ``metadata`` into ``{prefix}.metadata.{key}`` string attributes."""
    for key, value in metadata.items():
        span.set_attribute(f"{prefix}.metadata.{key}", str(value))


class PhoenixObservabilityProvider(ObservabilityProvider):
    """Arize Phoenix implementation of the ObservabilityProvider contract.

    Thin delegation wrapper — all Phoenix-specific logic lives in
    codemie_enterprise.phoenix (service, context_manager, helpers, observe).
    This class owns lifecycle, wires dependencies, and satisfies the ABC.
    """

    def __init__(self) -> None:
        from codemie_enterprise.phoenix.config import PhoenixConfig

        self._config: PhoenixConfig = PhoenixConfig(
            enabled=True,
            host=config.PHOENIX_HOST,
            project_name=config.PHOENIX_PROJECT_NAME,
            api_key=config.PHOENIX_API_KEY,
            batch_span_processor=config.PHOENIX_BATCH_SPAN_PROCESSOR,
        )
        self._service: PhoenixService | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def initialize(self) -> None:
        """Register OTEL tracer provider and install LangChain instrumentation."""
        from codemie_enterprise.phoenix.service import PhoenixService

        self._service = PhoenixService(self._config)
        self._service.initialize()

    def shutdown(self) -> None:
        """Flush pending spans and shut down the OTEL tracer provider."""
        if self._service is not None:
            self._service.shutdown()
            logger.info("Phoenix service shutdown complete")
        self._service = None

    # ── State ──────────────────────────────────────────────────────────────────

    def is_enabled(self) -> bool:
        """True if Phoenix was configured and initialized successfully."""
        return self._config.enabled and self._service is not None and self._service.tracer_provider is not None

    # ── Tracing surface ────────────────────────────────────────────────────────

    def get_callback_handler(self) -> None:
        """Always returns None — Phoenix uses OTEL auto-instrumentation, not callbacks."""
        return None

    def create_workflow_trace_context(
        self,
        execution_id: str,
        workflow_id: str | None,
        workflow_name: str,
        user_id: str | None,
        session_id: str | None = None,
        tags: list[str] | None = None,
    ) -> PhoenixTraceContext | None:
        """Open a root OTEL span for a workflow and attach it to the OTEL context."""
        if not self.is_enabled():
            return None
        from codemie_enterprise.phoenix.context_manager import PhoenixContextManager

        return PhoenixContextManager.create_workflow_trace_context(
            execution_id=execution_id,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            user_id=user_id,
            session_id=session_id,
            tags=tags,
            tracer_provider=self._service.tracer_provider if self._service else None,
        )

    def get_workflow_trace_context(self, execution_id: str) -> PhoenixTraceContext | None:
        """Return the active PhoenixTraceContext for the given execution_id."""
        from codemie_enterprise.phoenix.context_manager import PhoenixContextManager

        return PhoenixContextManager.get_current_trace_context(execution_id)

    def clear_workflow_trace_context(self, execution_id: str) -> bool:
        """End the workflow span and detach OTEL context. Must be called in finally blocks."""
        from codemie_enterprise.phoenix.context_manager import PhoenixContextManager

        return PhoenixContextManager.clear_trace_context(execution_id)

    def build_agent_metadata(
        self,
        agent_name: str,
        conversation_id: str,
        llm_model: str,
        username: str | None = None,
        tags: list[str] | None = None,
        trace_context: Any | None = None,
    ) -> dict[str, Any]:
        """Build metadata dict for a LangChain agent run config."""
        from codemie_enterprise.phoenix.helpers import build_agent_metadata as _build
        from codemie_enterprise.phoenix.models import PhoenixTraceContext

        resolved_context = trace_context if isinstance(trace_context, PhoenixTraceContext) else None
        return _build(
            agent_name=agent_name,
            conversation_id=conversation_id,
            llm_model=llm_model,
            username=username,
            tags=tags,
            trace_context=resolved_context,
        )

    def should_trace_request(self, request_metadata: dict[str, Any] | None) -> bool:
        """Return True if this request should be traced.

        Checks provider availability, then optional per-request override via
        "observability_traces_enabled" metadata key.
        """
        if not self.is_enabled():
            return False
        if request_metadata is None:
            return True
        override = request_metadata.get("observability_traces_enabled")
        if override is not None:
            return bool(override)
        return True

    def make_observe_decorator(self) -> Any:
        """Return an OTEL span-based @observe decorator factory."""
        from codemie_enterprise.phoenix.observe import make_observe

        return make_observe(
            is_enabled=self.is_enabled,
            tracer_provider=self._service.tracer_provider if self._service else None,
        )

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
        """Annotate the currently recording OTEL span with attributes / status.

        Phoenix has no separate observation/trace distinction — the active OTEL
        span is updated. ``level`` maps to OTEL ``Status``: ``DEFAULT`` clears
        any prior error state to OK; ``ERROR`` records ERROR with the message.
        """
        if not self.is_enabled():
            return
        try:
            from opentelemetry.trace.status import Status, StatusCode

            span = _current_recording_span()
            if span is None:
                return
            if name is not None:
                _set_span_name(span, name)
            if input is not None:
                _set_json_attr(span, "observation.input", input)
            if output is not None:
                _set_json_attr(span, "observation.output", output)
            if metadata:
                _set_metadata_attrs(span, "observation", metadata)
            if level == "DEFAULT":
                span.set_status(Status(StatusCode.OK, status_message or ""))
            elif level == "ERROR":
                span.set_status(Status(StatusCode.ERROR, status_message or ""))
            if level is not None:
                span.set_attribute("observation.level", level)
            if status_message is not None:
                span.set_attribute("observation.status_message", status_message)
        except Exception as e:
            logger.warning("Failed to update current Phoenix observation: %s", e, exc_info=True)

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
        """Annotate the active OTEL span as an LLM call using OpenInference attribute names."""
        if not self.is_enabled():
            return
        try:
            span = _current_recording_span()
            if span is None:
                return
            span.set_attribute("openinference.span.kind", "LLM")
            if model is not None:
                span.set_attribute("llm.model_name", model)
            if model_parameters:
                _set_json_attr(span, "llm.invocation_parameters", model_parameters)
            if usage_details:
                for key, value in usage_details.items():
                    span.set_attribute(_USAGE_ATTRIBUTES.get(key, f"llm.usage.{key}"), value)
            if cost_details:
                for key, value in cost_details.items():
                    span.set_attribute(_COST_ATTRIBUTES.get(key, f"llm.cost.{key}"), value)
        except Exception as e:
            logger.warning("Failed to update current Phoenix generation: %s", e, exc_info=True)
        self.update_current_observation(
            input=input, output=output, metadata=metadata, level=level, status_message=status_message
        )

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
        """Annotate the active OTEL span with trace-level fields.

        OTEL has no parent-trace concept distinct from the active span, so
        ``input``, ``output``, ``tags``, and ``metadata`` are added as span
        attributes.
        """
        if not self.is_enabled():
            return
        try:
            span = _current_recording_span()
            if span is None:
                return
            if name is not None:
                _set_span_name(span, name)
            if input is not None:
                _set_json_attr(span, "trace.input", input)
            if output is not None:
                _set_json_attr(span, "trace.output", output)
            if tags:
                _set_json_attr(span, "trace.tags", list(tags))
            if user_id is not None:
                span.set_attribute("user.id", user_id)
            if session_id is not None:
                span.set_attribute("session.id", session_id)
            if metadata:
                _set_metadata_attrs(span, "trace", metadata)
        except Exception as e:
            logger.warning("Failed to update current Phoenix trace: %s", e, exc_info=True)
