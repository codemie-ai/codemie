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


import importlib
import re
from types import SimpleNamespace

from codemie.chains.base import Thought
from codemie.service.file_service.sandbox_links import SANDBOX_FILE_RE, collect_message_file_tokens


def _message(text=None, file_names=None, thoughts=None):
    return SimpleNamespace(message=text, file_names=file_names, thoughts=thoughts)


def test_backticked_link_stops_before_the_backtick() -> None:
    assert SANDBOX_FILE_RE.findall("URL `sandbox:/v1/files/tok-1`") == ["sandbox:/v1/files/tok-1"]


def test_pattern_stops_at_markdown_delimiters() -> None:
    text = "[a](sandbox:/v1/files/t1) <sandbox:/v1/files/t2> \"sandbox:/v1/files/t3\" 'sandbox:/v1/files/t4'"

    assert [m.rsplit("/", 1)[1] for m in SANDBOX_FILE_RE.findall(text)] == ["t1", "t2", "t3", "t4"]


def test_importers_share_one_pattern() -> None:
    agent = importlib.import_module("codemie.agents.tools.agent")

    assert agent.SANDBOX_FILE_RE is SANDBOX_FILE_RE
    assert isinstance(SANDBOX_FILE_RE, re.Pattern)


def test_collects_file_names_and_inline_links_across_messages() -> None:
    messages = [
        _message("see sandbox:/v1/files/tok-a", file_names=["tok-b"]),
        _message("none"),
        _message(None, file_names=None),
    ]

    assert collect_message_file_tokens(messages) == ["tok-b", "tok-a"]


def test_thought_only_tokens_are_not_collected() -> None:
    thought = Thought(id="t1", message="[Download](sandbox:/v1/files/tok-thought)")

    assert collect_message_file_tokens([_message("done", thoughts=[thought])]) == []


def test_empty_history_yields_nothing() -> None:
    assert collect_message_file_tokens([]) == []
