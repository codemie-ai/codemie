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

from __future__ import annotations

from codemie.service.analytics.delivery_framework import classify_delivery_framework


def test_sdlc_factory_wins_over_superpowers():
    result = classify_delivery_framework(["sdlc-factory:tech-analyst", "superpowers:brainstorming"])
    assert result == "CodeMie AI Factory"


def test_superpowers_wins_without_sdlc():
    result = classify_delivery_framework(["superpowers:brainstorming", "bmad-task"])
    assert result == "Superpowers"


def test_bmad():
    assert classify_delivery_framework(["bmad-task"]) == "BMAD"


def test_openspec():
    assert classify_delivery_framework(["opsx:something"]) == "OpenSpec"


def test_speckit():
    assert classify_delivery_framework(["speckit.something"]) == "Spec Kit"


def test_bmad_wins_over_openspec():
    result = classify_delivery_framework(["opsx:thing", "bmad-task"])
    assert result == "BMAD"


def test_pure_chat_empty_list():
    assert classify_delivery_framework([]) == "Pure chat"


def test_pure_chat_unknown_skill():
    assert classify_delivery_framework(["some-random-skill", "another-tool"]) == "Pure chat"


def test_sdlc_factory_prefix_only():
    assert classify_delivery_framework(["sdlc-factory:anything"]) == "CodeMie AI Factory"
