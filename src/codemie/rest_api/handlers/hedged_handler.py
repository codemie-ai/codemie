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

from __future__ import annotations

import functools
import json
import queue
import threading
import uuid
from dataclasses import dataclass
from time import time
from types import SimpleNamespace
from typing import Any

from fastapi import Request
from starlette.responses import StreamingResponse

from codemie.chains.base import StreamedGenerationResult, Thought, ThoughtAuthorType
from codemie.configs import logger
from codemie.core.dependecies import set_disable_prompt_cache
from codemie.core.errors import ErrorDetailLevel
from codemie.core.models import AssistantChatRequest, BaseModelResponse
from codemie.core.otel_tracing import attach_otel_context, detach_otel_context, get_otel_context_for_thread
from codemie.core.thread import HedgingCancellationReason, ThreadedGenerator
from codemie.enterprise.observability import get_observability_provider
from codemie.rest_api.handlers.assistant_handlers import (
    NDJSON_MEDIA_TYPE,
    ChatHistoryData,
    StandardAssistantHandler,
)
from codemie.rest_api.utils.request_utils import extract_custom_headers
from codemie.service.assistant_service import AssistantService
from codemie.service.monitoring.hedging_monitoring_service import HedgingMetricPayload, HedgingMonitoringService
from codemie.service.tools.dynamic_value_utils import process_string
from codemie.service.tools.hedging_tool_service import HedgingToolService
from codemie.configs.logger import copy_logging_context, restore_logging_context


def _lazy_observe(**kwargs):
    """Decorator factory that resolves the observability provider lazily on each call.

    Avoids binding the provider at module import time, which could capture a no-op
    provider when hedged_handler is imported before the observability stack initializes.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **wkwargs):
            return get_observability_provider().make_observe_decorator()(**kwargs)(fn)(*args, **wkwargs)

        return wrapper

    return decorator


_FAST_PATH_EMPTY_OUTPUT = "[Request Hedging]: MISS"
_FAST_PATH_ERROR_OUTPUT = "[Request Hedging]: ERROR"


class _FastPathOutcome:
    """Outcome of a single fast-path attempt, surfaced as a trace tag."""

    HIT = "hit"
    MISS = "miss"
    ERROR = "error"


@dataclass(frozen=True)
class FastPathResult:
    """Everything one fast-path attempt produces.

    Bundling the data-to-serve with its telemetry (latency, classified outcome, error type) lets
    ``_run_fast_path`` express its full result through a single return value — no out-parameters or
    queue side-channels. Mirrors the ``subprocess.run() -> CompletedProcess`` idiom.
    """

    result: str | None  # raw result string to (maybe) serve; None when empty/error
    outcome: str  # one of _FastPathOutcome
    latency_ms: int
    error_type: str | None = None


class HedgedAssistantHandler(StandardAssistantHandler):
    """
    Handler that races a fast-path tool invocation against the full LangGraphAgent pipeline
    in parallel.

    Both paths start simultaneously. If the fast-path tool returns a non-empty result within
    the configured timeout, the agent is cancelled (via ThreadedGenerator.close(), which both
    AIToolsAgent and LangGraphAgent honour by checking is_closed() at each chunk boundary) and
    the fast-path result is returned immediately. Otherwise the agent response is streamed
    normally — with zero added latency because it has been running concurrently all along.

    The fast path supports two tool types (configured via hedging_config):
      - Internal CodeMieHedgeTool subclasses (cfg.tool)
      - External DSP/Provider tools called via HTTP (cfg.provider_tool)

    Both paths support generic input_mapping: Jinja2 template strings resolved at runtime
    from {{query}}, {{user.*}}, {{headers.*}}, {{metadata.*}}, and {{conversation_id}}.
    """

    def _tool_display_name(self) -> str:
        cfg = self.assistant.hedging_config
        if not cfg:
            return "N/A"
        if cfg.tool:
            return cfg.tool.name
        if cfg.provider_tool:
            p = cfg.provider_tool
            return f"{p.provider_name}/{p.toolkit_name}/{p.tool_name}"
        return "N/A"

    def _datasource_name(self) -> str | None:
        """Return the named datasource backing the fast path, or None.

        Only provider-tool fast paths can be backed by a named datasource; internal
        CodeMieHedgeTool fast paths have none.
        """
        cfg = self.assistant.hedging_config
        if cfg and cfg.provider_tool:
            return cfg.provider_tool.datasource_name
        return None

    def _trace_user_id(self) -> str | None:
        """Return the username for trace user attribution (matches agent traces)."""
        user = getattr(self, "user", None)
        return user.username if user and getattr(user, "username", None) else None

    def _invoke_internal_tool(
        self,
        cfg,
        template_context: dict,
    ) -> tuple[str | None, Any, float]:
        """Invoke a CodeMieHedgeTool and return (result, obs_output, invoke_elapsed)."""
        try:
            fast_tool = HedgingToolService.instantiate(cfg)
        except Exception as e:
            logger.warning(
                f"[HEDGED][fast-path] Failed to instantiate tool {cfg.tool.name!r}: {e} "
                f"assistant_id={self.assistant.id}"
            )
            return None, None, 0.0
        if fast_tool is None:
            return None, None, 0.0
        query = process_string(cfg.input_mapping.get("query", "{{query}}"), template_context)
        metadata = {k: process_string(v, template_context) for k, v in cfg.input_mapping.items() if k != "query"}
        invoke_t0 = time()
        raw = fast_tool.invoke({"query": query, "metadata": metadata})
        invoke_elapsed = time() - invoke_t0
        logger.debug(
            f"[HEDGED][fast-path] tool={cfg.tool.name!r} elapsed={invoke_elapsed:.3f}s "
            f"hit={raw is not None} assistant_id={self.assistant.id}"
        )
        return (raw if raw else None), (raw if raw else None), invoke_elapsed

    def _invoke_provider_tool_path(
        self,
        cfg,
        template_context: dict,
        request_uuid: str,
    ) -> tuple[str | None, Any, float]:
        """Invoke an external provider tool and return (result, obs_output, invoke_elapsed)."""
        tool_name = self._tool_display_name()
        invoke_t0 = time()
        hedge_result = HedgingToolService.invoke_provider_tool(
            cfg=cfg,
            template_context=template_context,
            user=self.user,
            project_id=self.assistant.project,
            request_uuid=request_uuid,
        )
        invoke_elapsed = time() - invoke_t0
        logger.info(
            f"[HEDGED][fast-path] provider_tool={tool_name!r} elapsed={invoke_elapsed:.3f}s "
            f"empty={hedge_result.empty} assistant_id={self.assistant.id}"
        )
        return hedge_result.model_dump_json(), (hedge_result.data if not hedge_result.empty else None), invoke_elapsed

    @_lazy_observe(name="hedging.fast_path", capture_input=False, capture_output=False)
    def _run_fast_path(
        self,
        request: AssistantChatRequest,
        request_uuid: str,
        request_headers: dict,
    ) -> FastPathResult:
        """Execute one fast-path attempt and return a :class:`FastPathResult` describing it.

        The single return value carries both the data to (maybe) serve and the attempt's
        telemetry — no out-parameters or queues. Delivering the result to the racing coordinator
        is the caller's concern (see ``_handle_stream._fast_path_with_winner``).
        """
        cfg = self.assistant.hedging_config
        t0 = time()
        tool_name = self._tool_display_name()
        datasource_name = self._datasource_name()
        logger.debug(f"[HEDGE-INIT] Firing hedged fast-path: tool={tool_name} assistant_id={self.assistant.id}")
        result: str | None = None
        invoke_elapsed: float = 0.0
        obs_input: Any = request.text
        obs_output: Any = None
        error: BaseException | None = None
        outcome: str = _FastPathOutcome.MISS
        try:
            template_context = HedgingToolService.build_template_context(request, self.user, request_headers)
            if cfg.tool is not None:
                result, obs_output, invoke_elapsed = self._invoke_internal_tool(cfg, template_context)
            elif cfg.provider_tool is not None:
                result, obs_output, invoke_elapsed = self._invoke_provider_tool_path(
                    cfg, template_context, request_uuid
                )
        except Exception as e:
            error = e
            logger.error(
                f"[HEDGED][fast-path] ERROR exception={type(e).__name__}: {e} "
                f"assistant_id={self.assistant.id} elapsed={time() - t0:.3f}s"
            )
        finally:
            if error is not None:
                outcome = _FastPathOutcome.ERROR
            elif obs_output is not None:
                outcome = _FastPathOutcome.HIT
            else:
                outcome = _FastPathOutcome.MISS
            self._emit_fast_path_trace(
                request_uuid=request_uuid,
                tool_name=tool_name,
                datasource_name=datasource_name,
                obs_input=obs_input,
                obs_output=obs_output,
                outcome=outcome,
                error=error,
                invoke_elapsed=invoke_elapsed,
                user_id=self._trace_user_id(),
                session_id=request.conversation_id,
            )
        return FastPathResult(
            result=result,
            outcome=outcome,
            latency_ms=int(invoke_elapsed * 1000),
            error_type=type(error).__name__ if error is not None else None,
        )

    def _emit_fast_path_trace(
        self,
        *,
        request_uuid: str,
        tool_name: str,
        datasource_name: str | None = None,
        obs_input: Any,
        obs_output: Any,
        outcome: str,
        error: BaseException | None,
        invoke_elapsed: float,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Populate the active fast-path span/trace with input, output, and metadata.

        Provider-agnostic: routes through the active ``ObservabilityProvider`` so
        Langfuse and Phoenix both receive the fast-path attempt's payload. The
        ``outcome`` discriminates the three terminal states:

        * ``hit``   — tool returned a non-empty result; agent stream will be cancelled.
        * ``empty`` — tool returned no data; agent path will continue and respond.
        * ``error`` — tool raised; agent path will continue and respond.

        For ``empty`` and ``error`` we emit a predefined sentinel output so the
        trace is not blank in the UI; ``error`` additionally records the exception
        type/message in metadata and bumps the observation level to WARNING.

        ``tool_name`` is also used to rename the active trace/span so it appears
        in the UI under the tool's name instead of the generic ``hedging.fast_path``.
        """
        try:
            provider = get_observability_provider()
            output_value: Any
            if outcome == _FastPathOutcome.HIT and obs_output is not None:
                output_value = obs_output
            elif outcome == _FastPathOutcome.ERROR:
                output_value = _FAST_PATH_ERROR_OUTPUT
            else:
                output_value = _FAST_PATH_EMPTY_OUTPUT
            metadata: dict[str, Any] = {
                "tool": tool_name,
                "elapsed_ms": int(invoke_elapsed * 1000),
                "outcome": outcome,
                "assistant_id": str(self.assistant.id),
                "request_uuid": request_uuid,
            }
            if datasource_name:
                metadata["datasource"] = datasource_name
            if error is not None:
                metadata["error_type"] = type(error).__name__
            level = "WARNING" if outcome == _FastPathOutcome.ERROR else "DEFAULT"
            status_message = f"fast_path_error:{type(error).__name__}" if error is not None else f"fast_path_{outcome}"
            provider.update_current_observation(
                name=tool_name,
                input=obs_input,
                output=output_value,
                metadata=metadata,
                level=level,
                status_message=status_message,
            )
            trace_metadata: dict[str, Any] = {
                "request_uuid": request_uuid,
                "tool": tool_name,
                "outcome": outcome,
            }
            if datasource_name:
                trace_metadata["datasource"] = datasource_name
            provider.update_current_trace(
                name=tool_name,
                input=obs_input,
                output=output_value,
                tags=["hedging_role:fast_path", f"hedging_outcome:{outcome}"],
                metadata=trace_metadata,
                user_id=user_id,
                session_id=session_id,
            )
        except Exception as e:
            logger.warning(f"[HEDGED][fast-path] failed to emit observability trace: {e}")

    @staticmethod
    def _parse_fast_path_result(result: str | None) -> str | None:
        """Return the data string from a fast-path JSON result, or None if empty/invalid."""
        try:
            parsed = json.loads(result) if result else {"empty": True}
        except json.JSONDecodeError:
            logger.warning(f"[HEDGED] fast-path result is not valid JSON: {result[:100]!r}")
            parsed = {"empty": True}
        data = parsed.get("data") if not parsed.get("empty", True) else None
        return str(data) if data is not None else None

    def _stream_agent_path(
        self,
        agent,
        agent_queue: ThreadedGenerator,
        execution_start: float,
        request: AssistantChatRequest,
        include_tool_errors: bool,
        error_detail_level: ErrorDetailLevel,
    ):
        response = StreamedGenerationResult(generated="")
        chunk_count = 0
        try:
            while True:
                try:
                    value = agent_queue.get(timeout=300)
                except queue.Empty:
                    logger.error(f"[HEDGED][coordinator] agent queue timed out assistant_id={self.assistant.id}")
                    break
                if value is not StopIteration:
                    chunk_count += 1
                    gen = json.loads(value, object_hook=lambda d: SimpleNamespace(**d))
                    if gen.generated is not None:
                        response = gen
                    yield value
                    agent_queue.queue.task_done()
                else:
                    agent_queue.queue.task_done()
                    logger.debug(
                        f"[HEDGED][coordinator] agent stream done chunks={chunk_count} "
                        f"elapsed={time() - execution_start:.3f}s assistant_id={self.assistant.id}"
                    )
                    final_chunk = self._build_final_chunk(
                        agent, execution_start, include_tool_errors, error_detail_level
                    )
                    self.save_chat_history(
                        ChatHistoryData(execution_start, request, response.generated, agent_queue.thoughts)
                    )
                    if final_chunk:
                        yield final_chunk.model_dump_json() + "\n"
                    break
        except Exception as e:
            logger.error(
                f"[HEDGED][coordinator] ERROR reading agent queue "
                f"exception={type(e).__name__}: {e} "
                f"chunk_count={chunk_count} "
                f"assistant_id={self.assistant.id} "
                f"elapsed={time() - execution_start:.3f}s"
            )
            raise

    def _handle_stream(
        self,
        request: AssistantChatRequest,
        raw_request: Request,
        execution_start: float,
        include_tool_errors: bool = False,
        error_detail_level: ErrorDetailLevel = ErrorDetailLevel.STANDARD,
    ) -> StreamingResponse:
        cfg = self.assistant.hedging_config
        timeout_s = (cfg.timeout_ms / 1000.0) if cfg else 0.2
        tool_name = self._tool_display_name()

        agent_queue = ThreadedGenerator(
            request_uuid=self.request_uuid,
            user_id=self.user.id,
            conversation_id=request.conversation_id,
        )
        fast_path_attempt: list[FastPathResult | None] = [None]
        first_result_ready = threading.Event()
        winner_lock = threading.Lock()
        winner: list[str | None] = [None]

        request_headers = extract_custom_headers(raw_request)
        _otel_ctx = get_otel_context_for_thread()
        logging_ctx = copy_logging_context()
        set_disable_prompt_cache(request.disable_cache or False)

        def _fast_path_with_winner():
            # FastPathResult is stored in the closure holder so the coordinator can read it.
            # Thread functions cannot return values; the holder (sibling to `winner`) is the
            # hand-off. Written before taking winner_lock, which provides the happens-before
            # barrier the coordinator relies on when reading under the same lock.
            token = attach_otel_context(_otel_ctx)
            try:
                restore_logging_context(logging_ctx)
                fast_path_attempt[0] = self._run_fast_path(request, raw_request.state.uuid, request_headers)
                with winner_lock:
                    if winner[0] is None:
                        winner[0] = "fast_path"
                first_result_ready.set()
            finally:
                detach_otel_context(token)

        agent = AssistantService.build_agent(
            assistant=self.assistant,
            request=request,
            user=self.user,
            request_uuid=raw_request.state.uuid,
            thread_generator=agent_queue,
            request_headers=request_headers,
        )

        def _agent_stream_with_winner():
            try:
                restore_logging_context(logging_ctx)
                agent.stream()
            finally:
                with winner_lock:
                    if winner[0] is None:
                        winner[0] = "agent"
                first_result_ready.set()

        fast_path_thread = threading.Thread(target=_fast_path_with_winner, daemon=True)
        fast_path_thread.start()

        agent_thread = threading.Thread(target=_agent_stream_with_winner, daemon=True)
        agent_thread.start()

        raw_request.state.on_disconnect(
            lambda: self._handle_client_disconnect(
                request=request,
                threaded_generator=agent_queue,
                execution_start=execution_start,
            )
        )

        return StreamingResponse(
            content=self._coordinate_stream(
                request=request,
                agent=agent,
                agent_queue=agent_queue,
                fast_path_attempt=fast_path_attempt,
                first_result_ready=first_result_ready,
                winner_lock=winner_lock,
                winner=winner,
                timeout_s=timeout_s,
                tool_name=tool_name,
                execution_start=execution_start,
                include_tool_errors=include_tool_errors,
                error_detail_level=error_detail_level,
                fast_path_thread=fast_path_thread,
            ),
            media_type=NDJSON_MEDIA_TYPE,
        )

    def _stream_fast_path_win(
        self,
        *,
        request: AssistantChatRequest,
        agent_queue: ThreadedGenerator,
        tool_name: str,
        result_str: str,
        execution_start: float,
    ):
        # close() sets is_closed()=True on the ThreadedGenerator; both AIToolsAgent
        # and LangGraphAgent check this flag at each chunk boundary and break early.
        # The reason kwarg lets the agent thread distinguish hedging cancellation
        # from a real user disconnect and demote its trace from ERROR accordingly.
        # Must close before any yields to prevent a concurrent disconnect from also
        # calling save_chat_history on the same request.
        agent_queue.close(reason=HedgingCancellationReason.FAST_PATH_WON)
        logger.info(f"[HEDGED] fast-path won, tool={tool_name} assistant_id={self.assistant.id}")
        logger.debug(
            f"[HEDGE-COMPLETED] Hedged fast-path won race: tool={tool_name} "
            f"assistant_id={self.assistant.id} hedge_latency_ms={(time() - execution_start) * 1000:.1f}"
        )
        display_name = tool_name.replace("_", " ").title()
        thought_id = str(uuid.uuid4())
        yield (
            StreamedGenerationResult(
                thought=Thought(
                    id=thought_id,
                    author_name=display_name,
                    author_type=ThoughtAuthorType.Tool,
                    input_text=request.text,
                    message="",
                    in_progress=True,
                )
            ).model_dump_json()
            + "\n"
        )
        yield (
            StreamedGenerationResult(
                thought=Thought(
                    id=thought_id,
                    author_name=display_name,
                    author_type=ThoughtAuthorType.Tool,
                    message=result_str + " \n\n",
                    in_progress=False,
                )
            ).model_dump_json()
            + "\n"
        )
        yield (
            StreamedGenerationResult(
                generated=result_str,
                generated_chunk="",
                last=True,
                time_elapsed=time() - execution_start,
            ).model_dump_json()
            + "\n"
        )
        self.save_chat_history(ChatHistoryData(execution_start, request, result_str, []))

    def _emit_hedging_metric(
        self,
        *,
        request: AssistantChatRequest,
        entry: str,
        served_by: str,
        winner: str,
        terminal_reason: str,
        telemetry: "FastPathResult | None",
        tool_name: str,
        execution_start: float,
    ) -> None:
        """Emit the single per-request analytics metric.

        ``telemetry`` is the fast-path attempt record (``None`` when the fast path did not
        finish before the coordinator resolved the race — reported as ``timeout``).
        """
        cfg = self.assistant.hedging_config
        payload = HedgingMetricPayload(
            entry=entry,
            served_by=served_by,
            fast_path_used=(served_by == "fast_path"),
            fast_path_outcome=telemetry.outcome if telemetry else "timeout",
            winner=winner,
            terminal_reason=terminal_reason,
            tool_name=tool_name,
            datasource_name=self._datasource_name(),
            timeout_ms=cfg.timeout_ms if cfg else 200,
            fast_path_latency_ms=telemetry.latency_ms if telemetry else None,
            total_latency_seconds=time() - execution_start,
            query=request.text,
            conversation_id=request.conversation_id,
            request_uuid=self.request_uuid,
        )
        HedgingMonitoringService.send_hedging_metric(
            user=self.user,
            assistant=self.assistant,
            payload=payload,
        )

    def _coordinate_stream(
        self,
        *,
        request: AssistantChatRequest,
        agent,
        agent_queue: ThreadedGenerator,
        fast_path_attempt: "list[FastPathResult | None]",
        first_result_ready: threading.Event,
        winner_lock: threading.Lock,
        winner: "list[str | None]",
        timeout_s: float,
        tool_name: str,
        execution_start: float,
        include_tool_errors: bool,
        error_detail_level: ErrorDetailLevel,
        fast_path_thread: threading.Thread,
    ):
        first_result_ready.wait(timeout=timeout_s)

        # Read both the race winner and the fast-path result under the same lock the
        # fast-path thread takes before signalling first_result_ready — this is the
        # happens-before barrier that makes the attempt write visible here.
        attempt: FastPathResult | None = None
        with winner_lock:
            current_winner = winner[0]
            attempt = fast_path_attempt[0]

        result_str: str | None = None
        if current_winner == "fast_path" and attempt is not None:
            result_str = self._parse_fast_path_result(attempt.result)

        served_by = "unknown"
        terminal_reason = "completed"
        try:
            if result_str is not None:
                served_by = "fast_path"
                yield from self._stream_fast_path_win(
                    request=request,
                    agent_queue=agent_queue,
                    tool_name=tool_name,
                    result_str=result_str,
                    execution_start=execution_start,
                )
            else:
                served_by = "agent"
                logger.info(f"[HEDGED] agent path won, tool={tool_name} assistant_id={self.assistant.id}")
                logger.debug(
                    f"[HEDGE-CANCELLED] Primary (agent) completed first: tool={tool_name} "
                    f"assistant_id={self.assistant.id}"
                )
                yield from self._stream_agent_path(
                    agent, agent_queue, execution_start, request, include_tool_errors, error_detail_level
                )
        except GeneratorExit:
            terminal_reason = "client_disconnect"
            raise
        except Exception:
            terminal_reason = "exception"
            raise
        finally:
            # When the agent won the race, the fast-path thread may still be completing
            # its work (writing fast_path_attempt[0]). Join it briefly here — after all
            # response chunks have been yielded — so the metric reflects the real
            # fast-path outcome rather than the "no data yet" fallback of "timeout".
            if attempt is None and fast_path_thread.is_alive():
                fast_path_thread.join(timeout=timeout_s)
                attempt = fast_path_attempt[0]
            self._emit_hedging_metric(
                request=request,
                entry="stream",
                served_by=served_by,
                winner=current_winner or "none",
                terminal_reason=terminal_reason,
                telemetry=attempt,
                tool_name=tool_name,
                execution_start=execution_start,
            )

    def _handle_sync(
        self,
        request: AssistantChatRequest,
        raw_request: Request,
        execution_start: float,
        include_tool_errors: bool = False,
        error_detail_level: ErrorDetailLevel = ErrorDetailLevel.STANDARD,
    ) -> BaseModelResponse:
        tool_name = self._tool_display_name()
        request_headers = extract_custom_headers(raw_request)
        attempt = self._run_fast_path(request, raw_request.state.uuid, request_headers)
        result_str = self._parse_fast_path_result(attempt.result)

        served_by = "unknown"
        terminal_reason = "completed"
        try:
            if result_str is not None:
                served_by = "fast_path"
                elapsed = time() - execution_start
                logger.info(f"[HEDGED] fast-path won, tool={tool_name} assistant_id={self.assistant.id}")
                logger.debug(
                    f"[HEDGE-COMPLETED] Hedged fast-path won race: tool={tool_name} "
                    f"assistant_id={self.assistant.id} hedge_latency_ms={elapsed * 1000:.1f}"
                )
                self.save_chat_history(ChatHistoryData(execution_start, request, result_str, []))
                return BaseModelResponse(generated=result_str, time_elapsed=elapsed, thoughts=[])
            served_by = "agent"
            logger.info(f"[HEDGED] agent path won, tool={tool_name} assistant_id={self.assistant.id}")
            logger.debug(
                f"[HEDGE-CANCELLED] Primary (agent) completed first: tool={tool_name} "
                f"assistant_id={self.assistant.id}"
            )
            return super()._handle_sync(request, raw_request, execution_start, include_tool_errors, error_detail_level)
        except Exception:
            terminal_reason = "exception"
            raise
        finally:
            self._emit_hedging_metric(
                request=request,
                entry="sync",
                served_by=served_by,
                winner="n/a",
                terminal_reason=terminal_reason,
                telemetry=attempt,
                tool_name=tool_name,
                execution_start=execution_start,
            )
