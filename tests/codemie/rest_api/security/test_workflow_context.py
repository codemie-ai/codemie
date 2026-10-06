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

"""Tests for the workflow id context variable."""

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor

from codemie.rest_api.security.workflow_context import (
    get_current_workflow_id,
    set_current_workflow_id,
)
from codemie_tools.data_management.code_executor.job_bridge import JobBridgeOptions, JobToolCallBridge
from codemie_tools.data_management.code_executor.tool_calling_limits import ToolCallingSettings


def test_workflow_id_is_readable_until_it_is_cleared():
    assert get_current_workflow_id() is None

    set_current_workflow_id("workflow-1")
    try:
        assert get_current_workflow_id() == "workflow-1"
    finally:
        # Clearing is an unconditional set, never a restore of whatever ran before: a pooled thread
        # must not inherit the previous execution's workflow.
        set_current_workflow_id(None)

    assert get_current_workflow_id() is None


def test_setting_none_keeps_resolution_outside_a_workflow():
    set_current_workflow_id(None)

    assert get_current_workflow_id() is None


def test_workflow_id_is_visible_in_a_channel_handler_thread_through_the_request_scoped_handlers():
    """AC 10 guard: the channel serves handlers from its own threads; they must still see the run's workflow id."""
    seen: list[str | None] = []

    def handler(params: Mapping[str, object]) -> object:
        seen.append(get_current_workflow_id())
        return None

    options = JobBridgeOptions(exchange_dir="/exchange", settings=ToolCallingSettings(), handlers={"tool": handler})
    bridge = JobToolCallBridge(options, namespace="ns", kubeconfig_path=None)

    set_current_workflow_id("workflow-1")
    try:
        scoped = bridge._request_scoped_handlers()
    finally:
        set_current_workflow_id(None)
    assert scoped is not None

    # A pooled thread does not inherit the request's contextvars; the copied context is what carries the workflow.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(scoped["tool"], {}).result()

    assert seen == ["workflow-1"]
    assert get_current_workflow_id() is None
