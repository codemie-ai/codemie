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

"""Tool-call bridge for one Job run: options, the channel bound to the Job pod, and the bridge-folder path check."""

from __future__ import annotations

import contextvars
import logging
import posixpath
from collections.abc import Mapping
from dataclasses import dataclass

from codemie_tools.data_management.code_executor.exec_runner import KubernetesExecRunner
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import BRIDGE_DIR_NAME
from codemie_tools.data_management.code_executor.tool_call_channel import (
    SettledCallback,
    ToolCallChannel,
    exchange_dir_path,
    new_exchange_dir_name,
)
from codemie_tools.data_management.code_executor.tool_call_protocol import RequestDispatcher, ToolCallHandler
from codemie_tools.data_management.code_executor.tool_calling_limits import RunBudget, ToolCallingSettings

logger = logging.getLogger(__name__)

_JOB_CONTAINER_NAME = "executor"


@dataclass(frozen=True)
class JobBridgeOptions:
    """What the runner needs to bridge one Job run: the exchange folder, the run's settings and who answers."""

    exchange_dir: str
    settings: ToolCallingSettings
    handlers: Mapping[str, ToolCallHandler] | None = None
    on_settled: SettledCallback | None = None


def new_job_bridge_options(
    settings: ToolCallingSettings,
    handlers: Mapping[str, ToolCallHandler] | None = None,
    on_settled: SettledCallback | None = None,
) -> JobBridgeOptions:
    return JobBridgeOptions(
        exchange_dir=exchange_dir_path(new_exchange_dir_name()),
        settings=settings,
        handlers=handlers,
        on_settled=on_settled,
    )


def _run_in_request_context(handler: ToolCallHandler, base: contextvars.Context) -> ToolCallHandler:
    """Wrap a handler so each call runs in a fresh copy of the request's context.

    The channel serves handlers from its own threads, which do not see the request thread's contextvars; a fresh
    copy per call also keeps one call's contextvar writes from leaking into the next.
    """

    def run(params: Mapping[str, object]) -> object:
        return base.copy().run(handler, params)

    return run


def is_bridge_path(rel_path: str) -> bool:
    """True when the path is the tool-call bridge folder or lives under it, however it is spelled."""
    normalized = posixpath.normpath(rel_path.replace("\\", "/")).lstrip("/")
    return normalized.split("/", 1)[0] == BRIDGE_DIR_NAME


class JobToolCallBridge:
    """Answers a script's tool calls through a ToolCallChannel bound to the Job pod's executor container."""

    def __init__(self, options: JobBridgeOptions, *, namespace: str, kubeconfig_path: str | None) -> None:
        self._options = options
        self._namespace = namespace
        self._kubeconfig_path = kubeconfig_path
        self._channel: ToolCallChannel | None = None

    def start(self, pod_name: str, workdir: str, budget: RunBudget, *, done_path: str | None = None) -> None:
        """Start the channel on its own Kubernetes client.

        ``budget`` is the Job's budget (the channel stops at its deadline and refuses calls it can no longer
        deliver); ``done_path`` is the script's done marker, relative to the workspace root.
        """
        settings = self._options.settings
        exec_runner = KubernetesExecRunner(
            pod_name=pod_name,
            container_name=_JOB_CONTAINER_NAME,
            namespace=self._namespace,
            workdir=workdir,
            kubeconfig_path=self._kubeconfig_path,
        )
        dispatcher = RequestDispatcher(
            self._request_scoped_handlers(),
            max_payload_bytes=settings.max_payload_bytes,
            time_left=budget.time_left,
        )
        channel = ToolCallChannel(
            exec_runner,
            self._options.exchange_dir,
            dispatcher=dispatcher,
            max_parallel_calls=settings.max_parallel_calls,
            done_path=done_path,
            deadline=budget.deadline,
            on_settled=self._options.on_settled,
        )
        channel.start()
        self._channel = channel

    def _request_scoped_handlers(self) -> Mapping[str, ToolCallHandler] | None:
        handlers = self._options.handlers
        if handlers is None:
            return None
        base = contextvars.copy_context()
        return {name: _run_in_request_context(handler, base) for name, handler in handlers.items()}

    def wait_until_done(self, deadline: float) -> bool:
        """Wait for the script's done marker through the channel's poll; ``False`` means the caller must check itself."""
        channel = self._channel
        return False if channel is None else channel.wait_until_done(deadline)

    def close(self) -> None:
        """Stop the polling thread and drop the exchange folder; idempotent, never raises."""
        channel, self._channel = self._channel, None
        if channel is None:
            return
        try:
            channel.stop()
        except Exception:  # noqa: BLE001 - housekeeping must not mask the run result
            logger.warning("Tool-call channel stop failed", exc_info=True)
        try:
            channel.remove_exchange_dir()
        except Exception:  # noqa: BLE001 - housekeeping must not mask the run result
            logger.warning("Tool-call channel cleanup failed", exc_info=True)
