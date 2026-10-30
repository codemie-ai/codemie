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

import pytest

from codemie.service.analytics.delivery_framework import classify_delivery_framework, get_framework_labels


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


@pytest.mark.parametrize(
    "skill_name",
    ["stlc-factory:exploratory", "stlc-feature", "stlc-factory:stlc-foundation", "stlc-factory:suite-health"],
)
def test_stlc_factory_skills(skill_name):
    assert classify_delivery_framework([skill_name]) == "STLC Factory (QA factory)"


@pytest.mark.parametrize("skill_name", ["stlc-feature", "stlc-feature-x"])
def test_stlc_feature_is_an_open_prefix(skill_name):
    # The open prefix (no "$" in the rule) is deliberate: a "$" would drop the rule from the Lucene framework_regex().
    assert classify_delivery_framework([skill_name]) == "STLC Factory (QA factory)"


def test_sdlc_factory_wins_over_stlc_factory():
    assert classify_delivery_framework(["sdlc-factory:x", "stlc-factory:exploratory"]) == "CodeMie AI Factory"


def test_stlc_factory_wins_over_superpowers():
    result = classify_delivery_framework(["stlc-factory:exploratory", "superpowers:brainstorming"])
    assert result == "STLC Factory (QA factory)"


def test_a_subagent_type_classifies_by_the_same_rule_as_a_skill_name():
    assert classify_delivery_framework(["sdlc-factory:complexity-assessor"]) == "CodeMie AI Factory"
    assert classify_delivery_framework(["superpowers:code-reviewer"]) == "Superpowers"


@pytest.mark.parametrize("agent_type", ["Explore", "general-purpose", "Plan"])
def test_a_builtin_subagent_type_stays_pure_chat(agent_type):
    assert classify_delivery_framework([agent_type]) == "Pure chat"


def test_framework_labels_include_stlc_factory_before_pure_chat():
    labels = get_framework_labels()
    assert "STLC Factory (QA factory)" in labels
    assert labels[-1] == "Pure chat"
