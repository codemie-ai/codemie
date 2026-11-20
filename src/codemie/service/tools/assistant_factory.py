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

import contextvars
import time
from collections.abc import Callable
from concurrent.futures import CancelledError, ThreadPoolExecutor
from typing import Any, List, Optional

from codemie.agents.callbacks.agent_streaming_callback import AgentStreamingCallback
from codemie.clients.postgres import _current_session
from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.thread import MessageQueue
from codemie.core.models import AssistantChatRequest
from codemie.core.constants import UniqueThoughtParentIds
from codemie.core.dependecies import scoped_llm_context
from codemie.core.exception_formatting import describe_exception
from codemie.service.assistant.assistant_engine_builder import (
    SubagentBuildContext,
    SubagentHandle,
)
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.mcp.models import MCPToolLoadException


def _validate_remote_entity_exists_and_cleanup(assistant: Assistant) -> Optional[str]:
    # Import here to avoid circular imports
    from codemie.service.aws_bedrock.bedrock_agent_service import BedrockAgentService

    return BedrockAgentService.validate_remote_entity_exists_and_cleanup(assistant)


def _timed_call(name: str, kind: str, fn: Callable[[], Any]) -> tuple[Any, BaseException | None]:
    start = time.perf_counter()
    try:
        return fn(), None
    except Exception as e:
        return None, e
    finally:
        logger.debug(f"subagent={name} kind={kind} duration_ms={int((time.perf_counter() - start) * 1000)}")


def _pooled_call(name: str, kind: str, fn: Callable[[], Any]) -> tuple[Any, BaseException | None]:
    # Runs in a copied context: never share an ambient DB session (transactional_session) across threads.
    _current_session.set(None)
    return _timed_call(name, kind, fn)


def run_builds(
    jobs: list[tuple[str, str, Callable[[], Any]]],
    is_fatal: Callable[[str, BaseException], bool] = lambda _kind, _e: False,
) -> list[tuple[Any, BaseException | None]]:
    """Run build jobs (name, kind, fn) on a bounded per-call pool; outcomes keep submission order.

    is_fatal(kind, error) marks errors that abort the phase. On the first fatal error (scanning in order)
    unstarted jobs are cancelled; running ones settle.
    """
    start = time.perf_counter()
    if len(jobs) <= 1:
        return [_timed_call(*job) for job in jobs]

    workers = min(config.SUBAGENT_BUILD_MAX_WORKERS, len(jobs))
    outcomes: list[tuple[Any, BaseException | None]] = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="subagent-build") as pool:
        futures = [pool.submit(contextvars.copy_context().run, _pooled_call, *job) for job in jobs]
        for i, future in enumerate(futures):
            outcome = future.result()
            outcomes.append(outcome)
            if outcome[1] is not None and is_fatal(jobs[i][1], outcome[1]):
                for rest in futures[i + 1 :]:
                    rest.cancel()
                outcomes.extend((None, CancelledError()) if f.cancelled() else f.result() for f in futures[i + 1 :])
                break
    logger.debug(
        f"Subagent build phase total_ms={int((time.perf_counter() - start) * 1000)} "
        f"count={len(jobs)} max_workers={workers}"
    )
    return outcomes


class AssistantFactory:
    """
    Factory for creating agent executors from assistants.

    This class is responsible for creating agent executors (compiled graphs)
    based on assistant configurations for use as subagents in multi-agent systems.
    """

    def __init__(
        self,
        assistant: Assistant,
        user: Optional[User] = None,
        request: Optional[AssistantChatRequest] = None,
        request_uuid: Optional[str] = None,
        thread_generator: Optional[MessageQueue] = None,
        build_context: SubagentBuildContext | None = None,
    ):
        self.assistant = assistant
        self.user = user
        self.request = request
        self.request_uuid = request_uuid
        self.thread_generator = thread_generator
        self.build_context = build_context or SubagentBuildContext()

    def build(self) -> SubagentHandle:
        """
        Build an agent executor (compiled graph) for the assistant.

        Model policy is not checked here. AssistantService.build_agent resolves the sub-assistant's
        model via resolve_assistant_model under the sub-assistant's own project: an unconfigured
        whitelist is unrestricted, an empty whitelist raises ModelNotWhitelistedException, a
        populated whitelist falls back from the user's pick to the sub-assistant's own model and
        then the project default, and global assistants are exempt.

        Returns:
            SubagentHandle with the built agent instance, its compiled executor graph and its
            tool-confirmation flags

        Raises:
            ModelNotWhitelistedException: If the sub-assistant's project allows no models (400)
        """
        try:
            # Import here to avoid circular imports
            from codemie.service.assistant_service import AssistantService

            # Pre-build the agent. Its billing context must not leak: the orchestrator builds its own
            # model after its sub-assistants and would otherwise be charged by the last one (EPMCDME-16625).
            with scoped_llm_context():
                handle = AssistantService.build_agent_handle(
                    assistant=self.assistant,
                    request=self.request.model_copy(),
                    user=self.user,
                    request_uuid=self.request_uuid,
                    thread_generator=self.thread_generator,
                    tool_callbacks=[AgentStreamingCallback(self.thread_generator)],
                    is_subagent=True,
                    build_context=self.build_context,
                )
            handle.agent.set_thread_context({}, UniqueThoughtParentIds.LATEST.value)

            return handle

        except Exception as e:
            logger.error(f"Failed to create agent executor for assistant {self.assistant.id}: {describe_exception(e)}")
            raise


def prepare_assistants(
    assistant_ids: List[str],
    user: Optional[User] = None,
    request: Optional[AssistantChatRequest] = None,
    parent_assistant: Optional[Assistant] = None,
) -> list[Assistant]:
    """Serial prelude: fetch sub-assistants, skip remotely deleted ones, pin requested versions."""
    sub_assistants_versions = _sub_assistants_versions(request)

    try:
        assistants = Assistant.get_by_ids(user, assistant_ids, parent_assistant=parent_assistant)
    except Exception as e:
        logger.error(f"Error fetching assistants: {str(e)}")
        return []

    prepared = []
    for assistant in assistants:
        deleted_name = _validate_remote_entity_exists_and_cleanup(assistant)
        if deleted_name:
            logger.info(f"Assistant {assistant.id} has been deleted remotely: {deleted_name}")
            continue
        try:
            prepared.append(_with_pinned_version(assistant, sub_assistants_versions))
        except Exception as e:
            # A stale/invalid version pin on one sub-assistant must not abort the whole batch;
            # skip it and keep building the rest, matching the prior per-assistant degradation.
            logger.error(f"Error applying version pin for assistant {assistant.id}: {str(e)}")
    return prepared


def is_fatal_custom_failure(kind: str, error: BaseException) -> bool:
    """Only a custom sub-assistant's MCP load failure aborts the build phase."""
    return kind == "custom" and isinstance(error, MCPToolLoadException)


def custom_build_jobs(  # noqa: PLR0913 -- mirrors the sub-assistant build signature used throughout this module
    assistants: list[Assistant],
    user: User | None,
    request: AssistantChatRequest | None,
    request_uuid: str | None,
    thread_generator: MessageQueue | None,
    build_context: SubagentBuildContext,
) -> list[tuple[str, str, Callable[[], Any]]]:
    """Build (name, kind, fn) jobs for ``run_builds``; each child's build_context is allocated here, serially."""
    return [
        (
            assistant.name,
            "custom",
            AssistantFactory(
                assistant=assistant,
                user=user,
                request=request,
                request_uuid=request_uuid,
                thread_generator=thread_generator,
                build_context=build_context.child(),
            ).build,
        )
        for assistant in assistants
    ]


def collect_custom_executors(
    assistants: list[Assistant], outcomes: list[tuple[Any, BaseException | None]]
) -> list[SubagentHandle]:
    """Walk build outcomes in list order: skip regular failures, re-raise MCP load failures with context."""
    handles: list[SubagentHandle] = []
    for assistant, (handle, error) in zip(assistants, outcomes, strict=False):
        if error is None:
            handles.append(handle)
        elif isinstance(error, MCPToolLoadException):
            error.attach_assistant_context(assistant_name=assistant.name, assistant_id=assistant.id)
            logger.error(
                f"Failed to create executor for sub-assistant '{assistant.name}' (id={assistant.id}) "
                f"due to MCP server '{error.server_name}': {str(error.original_error)}"
            )
            raise error
        else:
            logger.error(f"Failed to create executor for assistant {assistant.id}: {describe_exception(error)}")
    return handles


def create_assistant_executors(
    assistant_ids: list[str],
    user: Optional[User] = None,
    request: Optional[AssistantChatRequest] = None,
    request_uuid: str | None = None,
    thread_generator: MessageQueue | None = None,
    parent_assistant: Assistant | None = None,
    build_context: SubagentBuildContext | None = None,
) -> list[SubagentHandle]:
    """
    Create agent executors from assistant IDs with optional version pinning.

    Args:
        assistant_ids: List of sub-assistant IDs to create executors from
        user: User making the request
        request: Chat request containing optional sub_assistants_versions
        request_uuid: Unique request identifier
        thread_generator: Thread generator for streaming
        parent_assistant: Parent assistant (orchestrator) that contains these sub-assistants
        build_context: Build context of the parent; each sub-assistant gets a child of it

    Returns:
        List[SubagentHandle]: the built sub-assistants
    """
    if build_context is None:
        build_context = SubagentBuildContext()

    assistants = prepare_assistants(assistant_ids, user, request, parent_assistant)
    jobs = custom_build_jobs(assistants, user, request, request_uuid, thread_generator, build_context)
    return collect_custom_executors(assistants, run_builds(jobs, is_fatal_custom_failure))


def _sub_assistants_versions(request: AssistantChatRequest | None) -> dict[str, int] | None:
    """The sub-assistant version pins of the request, or ``None`` when it pins none."""
    if request and request.sub_assistants_versions:
        return request.sub_assistants_versions
    return None


def _with_pinned_version(assistant: Assistant, sub_assistants_versions: dict[str, int] | None) -> Assistant:
    """Apply the version pinned for the sub-assistant in ``sub_assistants_versions``, if any."""
    if not sub_assistants_versions or assistant.id not in sub_assistants_versions:
        return assistant
    version_number = sub_assistants_versions[assistant.id]
    logger.debug(f"Applying version {version_number} to sub-assistant {assistant.id}")

    from codemie.service.assistant.assistant_version_service import AssistantVersionService

    return AssistantVersionService.apply_version_to_assistant(assistant, version_number)
