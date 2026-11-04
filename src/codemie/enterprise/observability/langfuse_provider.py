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

"""Langfuse observability provider.

Thin delegation wrapper around the existing enterprise/langfuse/ adapter layer.
Contains no logic changes — all business logic remains in enterprise/langfuse/.
"""

from __future__ import annotations

from typing import Any

from codemie.configs import logger

from .base import ObservabilityProvider, ObservationLevel


def _drop_none(values: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``values`` without keys whose value is ``None``."""
    return {k: v for k, v in values.items() if v is not None}


def _safe_call(fn: Any, kwargs: dict[str, Any], action: str) -> None:
    """Invoke ``fn(**kwargs)`` if there is anything to send; log on failure."""
    if not kwargs:
        return
    try:
        fn(**kwargs)
    except Exception as e:
        logger.warning(f"Failed to {action}: {e}")


def _client_or_none() -> Any | None:
    """Return the active Langfuse client, or ``None`` when Langfuse is disabled."""
    from codemie.enterprise.langfuse import get_langfuse_client_or_none

    return get_langfuse_client_or_none()


class LangfuseObservabilityProvider(ObservabilityProvider):
    """Delegates all calls to the existing enterprise/langfuse/ adapter layer.

    This is a pure forwarding adapter — no logic lives here. All behavior
    is unchanged from the pre-abstraction implementation.
    """

    def __init__(self) -> None:
        self._service: Any = None

    def initialize(self) -> None:
        """Initialize the Langfuse service and register it as the global singleton."""
        from codemie.enterprise.langfuse.dependencies import (
            initialize_langfuse_from_config,
            set_global_langfuse_service,
        )

        self._service = initialize_langfuse_from_config()
        set_global_langfuse_service(self._service)
        self._register_control_flow_exceptions()

    @staticmethod
    def _register_control_flow_exceptions() -> None:
        """Mark stream-cancellation exceptions as DEFAULT level in Langfuse traces.

        ``stream.close()`` on a LangChain / LangGraph stream injects ``GeneratorExit``
        into the chain, which fires ``on_chain_error`` and — by default — marks the
        observation level as ERROR. This causes both legitimate user disconnects and
        request-hedging fast-path wins to surface as red traces in the UI.

        Langfuse's callback handler already has an extensible escape hatch for this
        (``CONTROL_FLOW_EXCEPTION_TYPES``); we register ``GeneratorExit`` so that
        any planned cancellation is reported at DEFAULT level instead of ERROR.
        Tags / status_message added by callers (e.g. ``mark_trace_cancelled_by_hedging``)
        still differentiate hedging cancellation from other cancellations.
        """
        try:
            from langfuse.langchain.CallbackHandler import CONTROL_FLOW_EXCEPTION_TYPES

            CONTROL_FLOW_EXCEPTION_TYPES.add(GeneratorExit)
        except Exception as e:
            logger.warning(f"Could not register GeneratorExit as Langfuse control-flow exception: {e}")

    def shutdown(self) -> None:
        """Flush pending traces, release resources, and clear the global singleton."""
        from codemie.enterprise.langfuse.dependencies import set_global_langfuse_service

        if self._service is not None:
            self._service.shutdown()
            logger.info("LangFuse service shutdown complete")
        set_global_langfuse_service(None)
        self._service = None

    def is_enabled(self) -> bool:
        from codemie.enterprise.langfuse import is_langfuse_enabled

        return is_langfuse_enabled()

    def get_callback_handler(self) -> Any | None:
        from codemie.enterprise.langfuse import get_langfuse_callback_handler

        return get_langfuse_callback_handler()

    def create_workflow_trace_context(
        self,
        execution_id: str,
        workflow_id: str | None,
        workflow_name: str,
        user_id: str | None,
        session_id: str | None = None,
        tags: list[str] | None = None,
    ) -> Any | None:
        from codemie.enterprise.langfuse import create_workflow_trace_context

        return create_workflow_trace_context(
            execution_id=execution_id,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            user_id=user_id,
            session_id=session_id,
            tags=tags,
        )

    def get_workflow_trace_context(self, execution_id: str) -> Any | None:
        from codemie.enterprise.langfuse import get_workflow_trace_context

        return get_workflow_trace_context(execution_id)

    def clear_workflow_trace_context(self, execution_id: str) -> bool:
        from codemie.enterprise.langfuse import clear_workflow_trace_context

        return clear_workflow_trace_context(execution_id)

    def build_agent_metadata(
        self,
        agent_name: str,
        conversation_id: str,
        llm_model: str,
        username: str | None = None,
        tags: list[str] | None = None,
        trace_context: Any | None = None,
    ) -> dict:
        from codemie.enterprise.langfuse import build_agent_metadata_with_workflow_context

        return build_agent_metadata_with_workflow_context(
            agent_name=agent_name,
            conversation_id=conversation_id,
            llm_model=llm_model,
            username=username,
            tags=tags,
            trace_context=trace_context,
        )

    def should_trace_request(self, request_metadata: dict | None) -> bool:
        """Determine if tracing should be enabled for this request.

        Order of evaluation: a per-request metadata override (if set) wins over
        the global ``is_langfuse_enabled()`` gate. Also requires that the
        callback handler is available (service initialized) — otherwise tracing
        would silently no-op even if requested.
        """
        from codemie.enterprise.langfuse import get_langfuse_callback_handler, is_langfuse_enabled

        if not is_langfuse_enabled():
            return False

        # Handler must be available (service initialized); otherwise tracing is a no-op
        if get_langfuse_callback_handler() is None:
            return False

        # Per-request override: check both Langfuse-specific and generic keys
        trace_setting = None
        if request_metadata:
            trace_setting = request_metadata.get("langfuse_traces_enabled")
            if trace_setting is None:
                trace_setting = request_metadata.get("observability_traces_enabled")

        if trace_setting is not None:
            logger.info(f"Per-request trace override: {trace_setting}")
            if isinstance(trace_setting, str):
                return trace_setting.strip().lower() == "true"
            if isinstance(trace_setting, bool):
                return trace_setting
            logger.warning("Unsupported type for traces_enabled metadata key; defaulting to False.")
            return False

        # is_langfuse_enabled() already gated on config.LANGFUSE_TRACES.
        return True

    def get_trace_context(
        self,
        trace_name: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        tags: list[str] | None = None,
    ):
        """
        Return a context manager that sets trace attributes for the current invocation.

        Delegates to get_langfuse_trace_context(); returns nullcontext() if Langfuse
        is disabled.
        """
        from codemie.enterprise.langfuse.dependencies import get_langfuse_trace_context

        return get_langfuse_trace_context(
            trace_name=trace_name,
            user_id=user_id,
            session_id=session_id,
            tags=tags,
        )

    def make_observe_decorator(self) -> Any:
        """Return the existing Langfuse @observe decorator from loader.py."""
        from codemie.enterprise.loader import observe

        return observe

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
        """Update the active observation (span) on the v4 Langfuse SDK."""
        client = _client_or_none()
        if client is None:
            return
        _safe_call(
            client.update_current_span,
            _drop_none(
                {
                    "name": name,
                    "input": input,
                    "output": output,
                    "metadata": metadata,
                    "level": level,
                    "status_message": status_message,
                }
            ),
            "update current Langfuse observation",
        )

    def update_current_generation(
        self,
        *,
        model: str | None = None,
        model_parameters: dict[str, Any] | None = None,
        input: Any | None = None,
        output: Any | None = None,
        usage_details: dict[str, int | float] | None = None,
        metadata: dict[str, Any] | None = None,
        level: ObservationLevel | None = None,
        status_message: str | None = None,
    ) -> None:
        """Update the active generation observation on the v4 Langfuse SDK."""
        client = _client_or_none()
        if client is None:
            return
        _safe_call(
            client.update_current_generation,
            _drop_none(
                {
                    "model": model,
                    "model_parameters": model_parameters,
                    "input": input,
                    "output": output,
                    "usage_details": usage_details,
                    "metadata": metadata,
                    "level": level,
                    "status_message": status_message,
                }
            ),
            "update current Langfuse generation",
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
        """Update the active trace on the v4 Langfuse SDK.

        Input/output go via ``set_current_trace_io``; tags, metadata, user_id,
        and session_id are not exposed as a public method, so they are written
        directly to the active OTEL span using Langfuse's documented attribute names.
        """
        client = _client_or_none()
        if client is None:
            return
        _safe_call(
            client.set_current_trace_io,
            _drop_none({"input": input, "output": output}),
            "set Langfuse trace I/O",
        )
        self._set_trace_tags_metadata_via_otel(
            name=name, tags=tags, metadata=metadata, user_id=user_id, session_id=session_id
        )

    @staticmethod
    def _set_trace_tags_metadata_via_otel(
        *,
        name: str | None,
        tags: list[str] | None,
        metadata: dict[str, Any] | None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Write name / tags / metadata / user / session as OTEL attributes on the
        active span using Langfuse's documented attribute keys (matches
        ``propagate_attributes``).

        The ``user.id`` and ``session.id`` attribute names match
        ``LangfuseOtelSpanAttributes.TRACE_USER_ID`` /
        ``LangfuseOtelSpanAttributes.TRACE_SESSION_ID``.
        """
        if name is None and tags is None and metadata is None and user_id is None and session_id is None:
            return
        try:
            import json as _json

            from opentelemetry import trace as _otel_trace

            span = _otel_trace.get_current_span()
            if not span.is_recording():
                return
            if name is not None:
                # Langfuse maps `langfuse.trace.name` to the trace's display name.
                span.set_attribute("langfuse.trace.name", name)
            if tags is not None:
                span.set_attribute("langfuse.trace.tags", list(tags))
            if user_id is not None:
                span.set_attribute("user.id", user_id)
            if session_id is not None:
                span.set_attribute("session.id", session_id)
            if metadata is not None:
                for k, v in metadata.items():
                    attr_key = f"langfuse.trace.metadata.{k}"
                    if isinstance(v, (str, int, float, bool)):
                        span.set_attribute(attr_key, v)
                    else:
                        span.set_attribute(attr_key, _json.dumps(v, default=str))
        except Exception as e:
            logger.warning(f"Failed to set Langfuse trace name/tags/metadata via OTEL: {e}")
