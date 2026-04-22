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

"""OpenTelemetry tracing utilities.

Single import point for all span creation across the codebase.  When OTEL is
disabled (no TracerProvider configured) every call returns a no-op span with
zero overhead — no guard clauses are needed in callers.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

from opentelemetry import context, trace
from opentelemetry.context import Context
from opentelemetry.context import Token
from opentelemetry.trace import SpanKind, StatusCode

# Module-level tracer. Safe to create at import time even before configure_opentelemetry()
# is called — OTel returns a no-op tracer until a real TracerProvider is set.
tracer = trace.get_tracer("codemie")

# Attributes can be a static dict or a callable that receives the wrapped function's
# arguments and returns a dict — useful for extracting per-call values from `self`.
_AttributesArg = dict[str, Any] | Callable[..., dict[str, Any]] | None


def traced(span_name: str | None = None, attributes: _AttributesArg = None):
    """Decorator for sync and async functions that wraps the body in a child span.

    Records any exception automatically and sets ERROR status before re-raising.

    ``attributes`` can be a static dict or a callable that is called with the same
    arguments as the wrapped function and must return a dict — handy for capturing
    instance attributes without putting tracing code inside the method body.

    Usage::

        @traced("assistant.process")
        async def process(self, ...):
            ...

        @traced("guardrail.check", {"guardrail.type": "content"})
        def check(self, ...):
            ...

        @traced("agent.generate", lambda self, *a, **kw: {
            "codemie.agent_name": self.agent_name,
            "codemie.model": self.llm_model,
        })
        def generate(self, ...):
            ...
    """

    def decorator(func):
        name = span_name or func.__qualname__

        def _resolve_attrs(*args, **kwargs) -> dict[str, Any]:
            if callable(attributes):
                return attributes(*args, **kwargs)
            return attributes or {}

        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                with tracer.start_as_current_span(name, attributes=_resolve_attrs(*args, **kwargs)) as span:
                    try:
                        return await func(*args, **kwargs)
                    except Exception as exc:
                        span.record_exception(exc)
                        span.set_status(StatusCode.ERROR, str(exc))
                        raise

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            with tracer.start_as_current_span(name, attributes=_resolve_attrs(*args, **kwargs)) as span:
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(StatusCode.ERROR, str(exc))
                    raise

        return sync_wrapper

    return decorator


@contextmanager
def span(
    name: str,
    attributes: dict[str, Any] | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
) -> Generator[trace.Span, None, None]:
    """Context manager that creates a child span.

    Usage::

        with span("elasticsearch.search", {"db.elasticsearch.index": idx}):
            ...
    """
    with tracer.start_as_current_span(name, kind=kind, attributes=attributes or {}) as s:
        yield s


@contextmanager
def propagated_span(
    ctx: Context,
    name: str,
    attributes: dict[str, Any] | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
) -> Generator[trace.Span, None, None]:
    """Start a child span inside a thread that doesn't inherit contextvars.

    Attaches the OTel context captured on the HTTP handler thread, starts a child span,
    records any unhandled exception, then detaches on exit — regardless of exception.

    Use this wherever a ``threading.Thread`` needs to be a child of the originating
    HTTP request span (background agents, workflow execution, datasource processing).

    Usage::

        # In __init__ (HTTP handler thread):
        self._otel_context = context.get_current()

        # In the background thread:
        with propagated_span(self._otel_context, "agent.stream", {
            "codemie.agent_name": self.agent_name,
        }):
            ...
    """
    token = context.attach(ctx)
    try:
        with tracer.start_as_current_span(name, kind=kind, attributes=attributes or {}) as s:
            try:
                yield s
            except Exception as exc:
                if s.is_recording():
                    s.record_exception(exc)
                    s.set_status(StatusCode.ERROR, str(exc))
                raise
    finally:
        context.detach(token)


def set_span_attribute(key: str, value: Any) -> None:
    """Set an attribute on the current active span. Silent no-op if none is active."""
    current = trace.get_current_span()
    if current.is_recording():
        current.set_attribute(key, value)


def record_exception_on_span(exc: Exception) -> None:
    """Record an exception on the current span and mark it as ERROR."""
    current = trace.get_current_span()
    if current.is_recording():
        current.record_exception(exc)
        current.set_status(StatusCode.ERROR, str(exc))


def get_otel_context_for_thread() -> Context:
    """Capture the current OTel context to propagate into a ThreadPoolExecutor thread.

    ``asyncio.to_thread()`` copies ``contextvars`` automatically (Python 3.12+),
    so this is only needed when using ``executor.submit()`` directly.

    Usage::

        ctx = get_otel_context_for_thread()
        future = executor.submit(_run_in_thread, ctx, arg)

        def _run_in_thread(ctx, arg):
            token = attach_otel_context(ctx)
            try:
                ...
            finally:
                detach_otel_context(token)
    """
    return context.get_current()


def attach_otel_context(ctx: Context) -> Token:
    """Attach a previously captured OTel context inside a thread."""
    return context.attach(ctx)


def detach_otel_context(token: Token) -> None:
    """Detach a context that was attached with ``attach_otel_context``."""
    context.detach(token)
