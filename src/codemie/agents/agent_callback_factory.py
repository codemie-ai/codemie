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

from typing import Sequence

from langchain_core.callbacks import BaseCallbackHandler

from codemie.agents.agent_runtime_utils import is_unique_callback
from codemie.agents.callbacks.agent_invoke_callback import AgentInvokeCallback
from codemie.agents.callbacks.agent_streaming_callback import AgentStreamingCallback
from codemie.agents.callbacks.monitoring_callback import MonitoringCallback
from codemie.agents.callbacks.utils.name_resolver import NameResolver
from codemie.core.thread import ThreadedGenerator


def build_thought_callback(
    *,
    thread_generator: ThreadedGenerator | None,
    stream_steps: bool,
    name_resolver: NameResolver | None = None,
) -> BaseCallbackHandler:
    if stream_steps and thread_generator:
        return AgentStreamingCallback(thread_generator, name_resolver=name_resolver)
    return AgentInvokeCallback(name_resolver=name_resolver)


def build_tool_callbacks(
    existing_callbacks: Sequence[BaseCallbackHandler] | None,
    *,
    thread_generator: ThreadedGenerator | None,
    stream_steps: bool,
    tool_error_callback: BaseCallbackHandler | None = None,
    name_resolver: NameResolver | None = None,
    include_monitoring: bool = True,
) -> list[BaseCallbackHandler]:
    callbacks = list(existing_callbacks or [])
    default_callbacks: list[BaseCallbackHandler] = []

    if include_monitoring:
        default_callbacks.append(MonitoringCallback())

    default_callbacks.append(
        build_thought_callback(
            thread_generator=thread_generator,
            stream_steps=stream_steps,
            name_resolver=name_resolver,
        )
    )

    if tool_error_callback:
        default_callbacks.append(tool_error_callback)

    callbacks.extend(callback for callback in default_callbacks if is_unique_callback(callbacks, callback))
    return callbacks


def build_supervisor_callbacks(
    existing_callbacks: Sequence[BaseCallbackHandler] | None,
    *,
    thread_generator: ThreadedGenerator | None,
    stream_steps: bool,
    name_resolver: NameResolver | None = None,
) -> list[BaseCallbackHandler]:
    callbacks = list(existing_callbacks or [])
    supervisor_callback = build_thought_callback(
        thread_generator=thread_generator,
        stream_steps=stream_steps,
        name_resolver=name_resolver,
    )
    if is_unique_callback(callbacks, supervisor_callback):
        callbacks.append(supervisor_callback)
    return callbacks
