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

import pytest
from pydantic import ValidationError

from codemie.rest_api.models.assistant import Assistant, AssistantBase, AssistantRequest
from codemie.service.headroom.models import HeadroomConfig, HeadroomStrategy


def _request(**kwargs) -> AssistantRequest:
    return AssistantRequest(name="a", system_prompt="prompt", llm_model_type="gpt-4o", **kwargs)


def test_request_headroom_config_defaults_none():
    req = _request()
    assert req.headroom_config is None
    assert "headroom_config" not in req.model_fields_set


def test_request_accepts_dict():
    req = _request(headroom_config={"enabled": True, "strategy": "balanced"})
    assert isinstance(req.headroom_config, HeadroomConfig)
    assert req.headroom_config.enabled is True
    assert req.headroom_config.strategy == HeadroomStrategy.BALANCED
    assert "headroom_config" in req.model_fields_set


def test_request_accepts_headroom_config_instance():
    cfg = HeadroomConfig(enabled=True, strategy=HeadroomStrategy.AGGRESSIVE)
    req = _request(headroom_config=cfg)
    assert req.headroom_config == cfg
    assert "headroom_config" in req.model_fields_set


def test_request_rejects_invalid_strategy():
    with pytest.raises(ValidationError):
        _request(headroom_config={"enabled": True, "strategy": "nope"})


def test_assistant_base_declares_headroom_config_column():
    assert AssistantBase.model_fields["headroom_config"].default is None


def test_map_assistant_request_applies_headroom_config():
    assistant = Assistant(name="a", description="d", system_prompt="p")
    assert assistant.headroom_config is None
    assistant._map_assistant_request(_request(headroom_config={"enabled": True, "strategy": "lossless"}))
    assert assistant.headroom_config == HeadroomConfig(enabled=True, strategy=HeadroomStrategy.LOSSLESS)


def test_map_assistant_request_without_headroom_config_keeps_existing():
    assistant = Assistant(name="a", description="d", system_prompt="p")
    assistant.headroom_config = HeadroomConfig(enabled=True)
    assistant._map_assistant_request(_request())
    assert assistant.headroom_config == HeadroomConfig(enabled=True)


def test_assistant_round_trips_through_model_dump():
    assistant = Assistant(
        name="a",
        description="d",
        system_prompt="p",
        headroom_config=HeadroomConfig(enabled=True, strategy=HeadroomStrategy.AGGRESSIVE),
    )
    restored = Assistant(**assistant.model_dump())
    assert restored.headroom_config == assistant.headroom_config
