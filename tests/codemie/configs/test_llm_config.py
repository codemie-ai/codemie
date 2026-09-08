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

import pathlib

import pytest
from codemie.configs.llm_config import LLMConfig, RoutingMode

_CONFIGS_DIR = pathlib.Path(__file__).parents[3] / "config" / "llms"
_DIAL_CONFIG = _CONFIGS_DIR / "llm-dial-config.yaml"

_DIAL_EXPECTED_ROUTER_NAMES: frozenset[str] = frozenset(
    {
        "claude-opus-4-5-20251101-switchyard-claude-4-5-sonnet-signal",
        "claude-opus-4-5-20251101-switchyard-claude-4-5-sonnet-classifier",
        "claude-opus-4-6-20260205-switchyard-claude-sonnet-4-6-signal",
        "claude-opus-4-6-20260205-switchyard-claude-sonnet-4-6-classifier",
        "claude-4-5-sonnet-switchyard-claude-4-5-haiku-signal",
        "claude-4-5-sonnet-switchyard-claude-4-5-haiku-classifier",
        "claude-sonnet-4-6-switchyard-claude-4-5-haiku-signal",
        "claude-sonnet-4-6-switchyard-claude-4-5-haiku-classifier",
    }
)


@pytest.fixture
def llm_config_yaml(tmp_path):
    yaml_content = '''
llm_models:
  - base_name: 'model-a'
    deployment_name: 'deployment-a'
    label: 'Model A'
    enabled: true
    cost:
      input: 0.01
      output: 0.02
  - base_name: 'model-b'
    deployment_name: 'deployment-b'
    enabled: false
embeddings_models:
  - base_name: 'embedding-a'
    deployment_name: 'embedding-deployment-a'
    label: 'Embedding A'
    enabled: true
'''
    yaml_file = tmp_path / "llm_config.yaml"
    yaml_file.write_text(yaml_content)
    return yaml_file


def test_llm_config_loading(llm_config_yaml):
    config = LLMConfig(yaml_file=llm_config_yaml)
    assert len(config.llm_models) == 2
    assert config.llm_models[0].base_name == 'model-a'
    assert config.llm_models[0].deployment_name == 'deployment-a'
    assert config.llm_models[0].label == 'Model A'
    assert config.llm_models[0].enabled is True
    # Checking the cost values with a tolerance
    assert config.llm_models[0].cost.input == pytest.approx(0.01, rel=1e-9)
    assert config.llm_models[0].cost.output == pytest.approx(0.02, rel=1e-9)
    assert config.llm_models[1].base_name == 'model-b'
    assert config.llm_models[1].deployment_name == 'deployment-b'
    assert config.llm_models[1].enabled is False
    assert config.embeddings_models[0].base_name == 'embedding-a'
    assert config.embeddings_models[0].deployment_name == 'embedding-deployment-a'
    assert config.embeddings_models[0].label == 'Embedding A'
    assert config.embeddings_models[0].enabled is True


def _write_yaml(tmp_path, body: str):
    yaml_file = tmp_path / "c.yaml"
    yaml_file.write_text(body)
    return yaml_file


def test_model_switchyard_field_parses(tmp_path):
    yaml_file = _write_yaml(
        tmp_path,
        '''
llm_models:
  - base_name: 'capable'
    deployment_name: 'capable'
    enabled: true
    switchyard:
      efficient: 'efficient'
      modes: [signal, classifier]
  - base_name: 'efficient'
    deployment_name: 'efficient'
    enabled: true
embeddings_models: []
''',
    )
    cfg = LLMConfig(yaml_file=yaml_file)
    capable = cfg.llm_models[0]
    assert capable.switchyard is not None
    assert capable.switchyard.efficient == 'efficient'
    assert capable.switchyard.modes == [RoutingMode.SIGNAL, RoutingMode.CLASSIFIER]


def test_switchyard_routers_generated(tmp_path, monkeypatch):
    from codemie.configs.config import config as app_config

    monkeypatch.setattr(app_config, "SWITCHYARD_ENABLED", True)
    yaml_file = _write_yaml(
        tmp_path,
        '''
llm_models:
  - base_name: 'cap'
    deployment_name: 'cap'
    label: 'Cap Model'
    enabled: true
    switchyard:
      efficient: 'eff'
      modes: [signal, classifier]
  - base_name: 'eff'
    deployment_name: 'eff'
    enabled: true
embeddings_models: []
''',
    )
    cfg = LLMConfig(yaml_file=yaml_file)
    names = {r.base_name for r in cfg.llm_routers}
    assert names == {'cap-switchyard-eff-signal', 'cap-switchyard-eff-classifier'}
    signal = next(r for r in cfg.llm_routers if r.switchyard.mode == RoutingMode.SIGNAL)
    assert signal.label == 'SY (Signal) Cap Model'
    assert signal.switchyard.capable_model == 'cap'
    assert signal.switchyard.efficient_model == 'eff'
    assert signal.enabled is True


def test_switchyard_unknown_efficient_skipped(tmp_path):
    yaml_file = _write_yaml(
        tmp_path,
        '''
llm_models:
  - base_name: 'cap'
    deployment_name: 'cap'
    enabled: true
    switchyard:
      efficient: 'missing'
      modes: [signal]
embeddings_models: []
''',
    )
    cfg = LLMConfig(yaml_file=yaml_file)
    assert cfg.llm_routers == []


def test_switchyard_disabled_generates_no_routers(tmp_path, monkeypatch) -> None:
    """With SWITCHYARD_ENABLED off, no routers are generated so /llm_models never
    advertises a model the proxy engine would refuse to route."""
    from codemie.configs.config import config as app_config

    monkeypatch.setattr(app_config, "SWITCHYARD_ENABLED", False)
    yaml_file = _write_yaml(
        tmp_path,
        '''
llm_models:
  - base_name: 'cap'
    deployment_name: 'cap'
    enabled: true
    switchyard:
      efficient: 'eff'
      modes: [signal, classifier]
  - base_name: 'eff'
    deployment_name: 'eff'
    enabled: true
embeddings_models: []
''',
    )
    cfg = LLMConfig(yaml_file=yaml_file)
    assert cfg.llm_routers == []


@pytest.mark.skipif(not _DIAL_CONFIG.exists(), reason="DIAL config not present")
def test_dial_config_generates_expected_router_names(monkeypatch) -> None:
    from codemie.configs.config import config as app_config

    monkeypatch.setattr(app_config, "SWITCHYARD_ENABLED", True)
    cfg = LLMConfig(yaml_file=_DIAL_CONFIG)
    generated = {r.base_name for r in cfg.llm_routers}
    assert generated == _DIAL_EXPECTED_ROUTER_NAMES
