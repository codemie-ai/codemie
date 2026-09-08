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

import dataclasses

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from codemie.enterprise.switchyard.extractor import SwitchyardRoutingExtractor
from codemie.enterprise.switchyard.routing_meta import SwitchyardMeta, _SWITCHYARD_RESPONSE_META_KEY


def _llm_result(response_metadata: dict) -> LLMResult:
    msg = AIMessage(content="", response_metadata=response_metadata)
    return LLMResult(generations=[[ChatGeneration(message=msg)]])


def _with_meta(meta: SwitchyardMeta) -> LLMResult:
    return _llm_result({_SWITCHYARD_RESPONSE_META_KEY: dataclasses.asdict(meta)})


def test_extracts_routed_model_and_classifier_cost_from_switchyard_meta():
    meta = SwitchyardMeta(routed_model="claude-4-5-haiku", classifier_cost_usd=0.0012)
    info = SwitchyardRoutingExtractor().extract(_with_meta(meta))
    assert info.routed_model == "claude-4-5-haiku"
    assert info.classifier_cost_usd == 0.0012
    assert info.routed_model_label is None


def test_empty_when_no_switchyard_metadata():
    assert SwitchyardRoutingExtractor().extract(_llm_result({})).is_empty()
