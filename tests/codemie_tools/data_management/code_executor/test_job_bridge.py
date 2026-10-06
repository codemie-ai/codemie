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

import contextvars
from collections.abc import Mapping
from unittest.mock import MagicMock, call, patch

import pytest

from codemie_tools.data_management.code_executor.job_bridge import (
    JobBridgeOptions,
    JobToolCallBridge,
    is_bridge_path,
    new_job_bridge_options,
)
from codemie_tools.data_management.code_executor.tool_call_protocol import ToolCallHandler
from codemie_tools.data_management.code_executor.tool_calling_limits import RunBudget, ToolCallingSettings

_JB = "codemie_tools.data_management.code_executor.job_bridge"

_probe: contextvars.ContextVar[str] = contextvars.ContextVar("job_bridge_test_probe", default="unset")

_SETTINGS = ToolCallingSettings(run_timeout_seconds=120.0, max_payload_bytes=1000, max_parallel_calls=3)
_BUDGET = RunBudget(seconds=180.0, deadline=1234.5)


def _options(**kwargs: object) -> JobBridgeOptions:
    return JobBridgeOptions(exchange_dir=".codemie_bridge/ex1", settings=_SETTINGS, **kwargs)  # type: ignore[arg-type]


def _bridge(**kwargs: object) -> JobToolCallBridge:
    return JobToolCallBridge(_options(**kwargs), namespace="test-ns", kubeconfig_path=None)


class TestJobBridgeOptions:
    def test_options_are_frozen(self) -> None:
        with pytest.raises(AttributeError):
            _options().settings = ToolCallingSettings()  # type: ignore[misc]

    def test_new_options_build_a_fresh_exchange_dir_under_the_bridge_folder(self) -> None:
        first = new_job_bridge_options(_SETTINGS)
        second = new_job_bridge_options(_SETTINGS)

        assert first.settings is _SETTINGS
        assert first.exchange_dir.startswith(".codemie_bridge/")
        assert first.exchange_dir != second.exchange_dir

    def test_handlers_and_the_settled_listener_default_to_none(self) -> None:
        options = new_job_bridge_options(_SETTINGS)

        assert options.handlers is None
        assert options.on_settled is None

    def test_new_options_carry_the_given_handlers_and_listener(self) -> None:
        handlers: Mapping[str, ToolCallHandler] = {"tool.call": lambda _params: {"ok": True}}

        def listener(outcome: object) -> None: ...

        options = new_job_bridge_options(_SETTINGS, handlers, listener)

        assert options.handlers is handlers
        assert options.on_settled is listener


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
        with (
            patch(f"{_JB}.KubernetesExecRunner") as runner_cls,
            patch(f"{_JB}.ToolCallChannel") as channel_cls,
            patch(f"{_JB}.RequestDispatcher") as dispatcher_cls,
        ):
            _bridge().start("the-pod", "/workspace", _BUDGET)

        runner_cls.assert_called_once_with(
            pod_name="the-pod",
            container_name="executor",
            namespace="test-ns",
            workdir="/workspace",
            kubeconfig_path=None,
        )
        channel_cls.assert_called_once_with(
            runner_cls.return_value,
            ".codemie_bridge/ex1",
            dispatcher=dispatcher_cls.return_value,
            max_parallel_calls=3,
            done_path=None,
            deadline=1234.5,
            on_settled=None,
        )
        channel_cls.return_value.start.assert_called_once_with()

    def test_the_dispatcher_gets_the_payload_cap_and_the_clock_of_the_job_budget(self) -> None:
        with (
            patch(f"{_JB}.KubernetesExecRunner"),
            patch(f"{_JB}.ToolCallChannel"),
            patch(f"{_JB}.RequestDispatcher") as dispatcher_cls,
        ):
            _bridge().start("the-pod", "/workspace", _BUDGET)

        kwargs = dispatcher_cls.call_args.kwargs
        assert kwargs["max_payload_bytes"] == 1000
        assert kwargs["time_left"] == _BUDGET.time_left

    def test_the_settled_listener_reaches_the_channel(self) -> None:
        def listener(outcome: object) -> None: ...

        with patch(f"{_JB}.KubernetesExecRunner"), patch(f"{_JB}.ToolCallChannel") as channel_cls:
            _bridge(on_settled=listener).start("the-pod", "/workspace", _BUDGET)

        assert channel_cls.call_args.kwargs["on_settled"] is listener

    def test_close_stops_the_channel_then_removes_the_exchange_folder(self) -> None:
        bridge = _bridge()
        with patch(f"{_JB}.KubernetesExecRunner"), patch(f"{_JB}.ToolCallChannel") as channel_cls:
            bridge.start("the-pod", "/workspace", _BUDGET)
            bridge.close()

        channel = channel_cls.return_value
        assert channel.method_calls[-2:] == [call.stop(), call.remove_exchange_dir()]

    def test_close_is_idempotent(self) -> None:
        bridge = _bridge()
        with patch(f"{_JB}.KubernetesExecRunner"), patch(f"{_JB}.ToolCallChannel") as channel_cls:
            bridge.start("the-pod", "/workspace", _BUDGET)
            bridge.close()
            bridge.close()

        channel_cls.return_value.stop.assert_called_once_with()
        channel_cls.return_value.remove_exchange_dir.assert_called_once_with()

    def test_close_before_start_does_nothing(self) -> None:
        _bridge().close()

    def test_close_still_cleans_up_when_stop_raises(self) -> None:
        bridge = _bridge()
        channel = MagicMock()
        channel.stop.side_effect = RuntimeError("boom")
        with patch(f"{_JB}.KubernetesExecRunner"), patch(f"{_JB}.ToolCallChannel", return_value=channel):
            bridge.start("the-pod", "/workspace", _BUDGET)
            bridge.close()  # must not raise

        channel.remove_exchange_dir.assert_called_once_with()

    def test_close_swallows_cleanup_errors(self) -> None:
        bridge = _bridge()
        channel = MagicMock()
        channel.remove_exchange_dir.side_effect = RuntimeError("boom")
        with patch(f"{_JB}.KubernetesExecRunner"), patch(f"{_JB}.ToolCallChannel", return_value=channel):
            bridge.start("the-pod", "/workspace", _BUDGET)
            bridge.close()  # must not raise

        channel.stop.assert_called_once_with()


def _started_handlers(handlers: Mapping[str, ToolCallHandler]) -> Mapping[str, ToolCallHandler]:
    """Start a bridge with the given handlers and return what the dispatcher was built with."""
    bridge = _bridge(handlers=handlers)
    with (
        patch(f"{_JB}.KubernetesExecRunner"),
        patch(f"{_JB}.ToolCallChannel"),
        patch(f"{_JB}.RequestDispatcher") as dispatcher_cls,
    ):
        bridge.start("the-pod", "/workspace", _BUDGET)
    return dispatcher_cls.call_args.args[0]


class TestJobToolCallBridgeHandlers:
    def test_start_passes_the_option_handlers_to_the_dispatcher(self) -> None:
        wrapped = _started_handlers({"tool.call": lambda _params: {"ok": True}})

        assert set(wrapped) == {"tool.call"}
        assert wrapped["tool.call"]({}) == {"ok": True}

    def test_handler_sees_the_contextvars_of_the_thread_that_called_start(self) -> None:
        seen: list[str] = []
        _probe.set("request-value")

        wrapped = _started_handlers({"tool.call": lambda _params: seen.append(_probe.get())})
        _probe.set("changed-after-start")
        # The channel serves handlers from its own threads, which have none of the request's contextvars.
        channel_thread_ctx = contextvars.Context()
        channel_thread_ctx.run(wrapped["tool.call"], {})

        assert seen == ["request-value"]

    def test_handler_writes_do_not_leak_into_the_next_call(self) -> None:
        seen: list[str] = []
        _probe.set("base")

        def handler(_params: Mapping[str, object]) -> None:
            seen.append(_probe.get())
            _probe.set("written-by-call")

        wrapped = _started_handlers({"tool.call": handler})
        wrapped["tool.call"]({})
        wrapped["tool.call"]({})

        assert seen == ["base", "base"]
        assert _probe.get() == "base"

    def test_no_handlers_leaves_the_dispatcher_on_its_defaults(self) -> None:
        with (
            patch(f"{_JB}.KubernetesExecRunner"),
            patch(f"{_JB}.ToolCallChannel"),
            patch(f"{_JB}.RequestDispatcher") as dispatcher_cls,
        ):
            _bridge().start("the-pod", "/workspace", _BUDGET)

        assert dispatcher_cls.call_args.args[0] is None


class TestJobToolCallBridgeDeadlineAndDone:
    def test_start_hands_the_job_deadline_and_the_done_marker_to_the_channel(self) -> None:
        with patch(f"{_JB}.KubernetesExecRunner"), patch(f"{_JB}.ToolCallChannel") as channel_cls:
            _bridge().start("the-pod", "/workspace", _BUDGET, done_path=".done")

        assert channel_cls.call_args.kwargs["deadline"] == 1234.5
        assert channel_cls.call_args.kwargs["done_path"] == ".done"

    def test_wait_until_done_asks_the_channel(self) -> None:
        bridge = _bridge()
        with patch(f"{_JB}.KubernetesExecRunner"), patch(f"{_JB}.ToolCallChannel") as channel_cls:
            channel_cls.return_value.wait_until_done.return_value = True
            bridge.start("the-pod", "/workspace", _BUDGET, done_path=".done")

            assert bridge.wait_until_done(99.0) is True

        channel_cls.return_value.wait_until_done.assert_called_once_with(99.0)

    def test_wait_until_done_is_false_when_the_channel_could_not_tell(self) -> None:
        bridge = _bridge()
        with patch(f"{_JB}.KubernetesExecRunner"), patch(f"{_JB}.ToolCallChannel") as channel_cls:
            channel_cls.return_value.wait_until_done.return_value = False
            bridge.start("the-pod", "/workspace", _BUDGET, done_path=".done")

            assert bridge.wait_until_done(99.0) is False

    def test_wait_until_done_before_start_and_after_close_is_false_without_waiting(self) -> None:
        bridge = _bridge()
        assert bridge.wait_until_done(99.0) is False

        with patch(f"{_JB}.KubernetesExecRunner"), patch(f"{_JB}.ToolCallChannel"):
            bridge.start("the-pod", "/workspace", _BUDGET)
            bridge.close()

        assert bridge.wait_until_done(99.0) is False
