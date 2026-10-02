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

from unittest.mock import MagicMock, call, patch

import pytest

from codemie_tools.data_management.code_executor.job_bridge import (
    JobBridgeOptions,
    JobToolCallBridge,
    is_bridge_path,
    new_job_bridge_options,
)

_JB = "codemie_tools.data_management.code_executor.job_bridge"


def _bridge() -> JobToolCallBridge:
    return JobToolCallBridge(
        JobBridgeOptions(exchange_dir=".codemie_bridge/ex1", tool_calling_timeout=120.0),
        namespace="test-ns",
        kubeconfig_path=None,
    )


class TestJobBridgeOptions:
    def test_options_are_frozen(self) -> None:
        options = JobBridgeOptions(exchange_dir=".codemie_bridge/ex1", tool_calling_timeout=120.0)

        with pytest.raises(AttributeError):
            options.tool_calling_timeout = 1.0  # type: ignore[misc]

    def test_new_options_build_a_fresh_exchange_dir_under_the_bridge_folder(self) -> None:
        first = new_job_bridge_options(45.0)
        second = new_job_bridge_options(45.0)

        assert first.tool_calling_timeout == 45.0
        assert first.exchange_dir.startswith(".codemie_bridge/")
        assert first.exchange_dir != second.exchange_dir


class TestIsBridgePath:
    @pytest.mark.parametrize(
        "path",
        [
            ".codemie_bridge",
            ".codemie_bridge/x/req.1.json",
            "./.codemie_bridge/x",
            "sub/../.codemie_bridge",
            "/.codemie_bridge/x",
            ".codemie_bridge\\x",
        ],
    )
    def test_bridge_spellings_are_detected(self, path: str) -> None:
        assert is_bridge_path(path)

    @pytest.mark.parametrize("path", ["a.txt", ".codemie_bridge_other.txt", "sub/.codemie_bridge/x", ""])
    def test_other_paths_are_not_bridge_paths(self, path: str) -> None:
        assert not is_bridge_path(path)


class TestJobToolCallBridge:
    def test_start_binds_a_channel_to_the_executor_container_of_the_pod(self) -> None:
        with patch(f"{_JB}.KubernetesExecRunner") as runner_cls, patch(f"{_JB}.ToolCallChannel") as channel_cls:
            _bridge().start("the-pod", "/workspace")

        runner_cls.assert_called_once_with(
            pod_name="the-pod",
            container_name="executor",
            namespace="test-ns",
            workdir="/workspace",
            kubeconfig_path=None,
        )
        channel_cls.assert_called_once_with(runner_cls.return_value, ".codemie_bridge/ex1")
        channel_cls.return_value.start.assert_called_once_with()

    def test_close_stops_the_channel_then_cleans_up_without_kill(self) -> None:
        bridge = _bridge()
        with patch(f"{_JB}.KubernetesExecRunner"), patch(f"{_JB}.ToolCallChannel") as channel_cls:
            bridge.start("the-pod", "/workspace")
            bridge.close()

        channel = channel_cls.return_value
        assert channel.method_calls[-2:] == [call.stop(), call.cleanup(kill=False)]
        channel.sweep.assert_not_called()

    def test_close_is_idempotent(self) -> None:
        bridge = _bridge()
        with patch(f"{_JB}.KubernetesExecRunner"), patch(f"{_JB}.ToolCallChannel") as channel_cls:
            bridge.start("the-pod", "/workspace")
            bridge.close()
            bridge.close()

        channel_cls.return_value.stop.assert_called_once_with()
        assert channel_cls.return_value.cleanup.call_args_list == [call(kill=False)]

    def test_close_before_start_does_nothing(self) -> None:
        _bridge().close()

    def test_close_still_cleans_up_when_stop_raises(self) -> None:
        bridge = _bridge()
        channel = MagicMock()
        channel.stop.side_effect = RuntimeError("boom")
        with patch(f"{_JB}.KubernetesExecRunner"), patch(f"{_JB}.ToolCallChannel", return_value=channel):
            bridge.start("the-pod", "/workspace")
            bridge.close()  # must not raise

        assert channel.cleanup.call_args_list == [call(kill=False)]

    def test_close_swallows_cleanup_errors(self) -> None:
        bridge = _bridge()
        channel = MagicMock()
        channel.cleanup.side_effect = RuntimeError("boom")
        with patch(f"{_JB}.KubernetesExecRunner"), patch(f"{_JB}.ToolCallChannel", return_value=channel):
            bridge.start("the-pod", "/workspace")
            bridge.close()  # must not raise

        channel.stop.assert_called_once_with()
