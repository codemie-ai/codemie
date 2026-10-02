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

import logging
import posixpath
from dataclasses import dataclass

from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import BRIDGE_DIR_NAME
from codemie_tools.data_management.code_executor.tool_call_channel import (
    KubernetesExecRunner,
    ToolCallChannel,
    exchange_dir_path,
    new_exchange_dir_name,
)

logger = logging.getLogger(__name__)

_JOB_CONTAINER_NAME = "executor"


@dataclass(frozen=True)
class JobBridgeOptions:
    exchange_dir: str
    tool_calling_timeout: float


def new_job_bridge_options(tool_calling_timeout: float) -> JobBridgeOptions:
    return JobBridgeOptions(
        exchange_dir=exchange_dir_path(new_exchange_dir_name()),
        tool_calling_timeout=tool_calling_timeout,
    )


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

    def start(self, pod_name: str, workdir: str) -> None:
        """Start the channel on its own Kubernetes client."""
        exec_runner = KubernetesExecRunner(
            pod_name=pod_name,
            container_name=_JOB_CONTAINER_NAME,
            namespace=self._namespace,
            workdir=workdir,
            kubeconfig_path=self._kubeconfig_path,
        )
        channel = ToolCallChannel(exec_runner, self._options.exchange_dir)
        channel.start()
        self._channel = channel

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
            channel.cleanup(kill=False)
        except Exception:  # noqa: BLE001 - housekeeping must not mask the run result
            logger.warning("Tool-call channel cleanup failed", exc_info=True)
