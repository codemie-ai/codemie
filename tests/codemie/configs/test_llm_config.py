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
from codemie.configs.llm_config import LLMConfig, LLMProvider, RoutingMode

_CONFIGS_DIR = pathlib.Path(__file__).parents[3] / "config" / "llms"
_DIAL_CONFIG = _CONFIGS_DIR / "llm-dial-config.yaml"

_DIAL_EXPECTED_ROUTER_NAMES: frozenset[str] = frozenset(
    {
        "sy-signal-claude-opus",
        "sy-classifier-claude-opus",
        "sy-signal-claude",
        "sy-classifier-claude",
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
      - base_name: 'capable-switchyard-efficient-signal'
        label: 'SY (Signal) Capable'
        efficient: 'efficient'
        mode: signal
      - base_name: 'capable-switchyard-efficient-classifier'
        label: 'SY (Classifier) Capable'
        efficient: 'efficient'
        mode: classifier
  - base_name: 'efficient'
    deployment_name: 'efficient'
    enabled: true
embeddings_models: []
''',
    )
    cfg = LLMConfig(yaml_file=yaml_file)
    capable = cfg.llm_models[0]
    assert len(capable.switchyard) == 2
    assert {e.mode for e in capable.switchyard} == {RoutingMode.SIGNAL, RoutingMode.CLASSIFIER}
    assert all(e.efficient == 'efficient' for e in capable.switchyard)


def test_switchyard_routers_generated(tmp_path):
    yaml_file = _write_yaml(
        tmp_path,
        '''
llm_models:
  - base_name: 'cap'
    deployment_name: 'cap'
    label: 'Cap Model'
    enabled: true
    switchyard:
      - base_name: 'cap-switchyard-eff-signal'
        label: 'SY (Signal) Cap Model'
        efficient: 'eff'
        mode: signal
      - base_name: 'cap-switchyard-eff-classifier'
        label: 'SY (Classifier) Cap Model'
        efficient: 'eff'
        mode: classifier
        classifier_model: 'eff'
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


def test_switchyard_forbidden_for_web_overrides_model(tmp_path):
    """A switchyard entry's own forbidden_for_web overrides the capable model's flag, so a
    router can be hidden from web without hiding the capable model itself (and vice versa:
    a router can stay visible even when the capable model is forbidden_for_web). An entry
    that leaves forbidden_for_web unset falls back to the capable model's flag."""
    yaml_file = _write_yaml(
        tmp_path,
        '''
llm_models:
  - base_name: 'cap'
    deployment_name: 'cap'
    enabled: true
    forbidden_for_web: false
    switchyard:
      - base_name: 'cap-switchyard-eff-signal'
        efficient: 'eff'
        mode: signal
        forbidden_for_web: true
      - base_name: 'cap-switchyard-eff-classifier'
        efficient: 'eff'
        mode: classifier
        classifier_model: 'eff'
  - base_name: 'eff'
    deployment_name: 'eff'
    enabled: true
embeddings_models: []
''',
    )
    cfg = LLMConfig(yaml_file=yaml_file)
    routers = {r.base_name: r for r in cfg.llm_routers}
    assert routers['cap-switchyard-eff-signal'].forbidden_for_web is True
    assert routers['cap-switchyard-eff-classifier'].forbidden_for_web is False


def test_switchyard_router_inherits_provider_from_capable_model(tmp_path):
    """A generated router is never itself executed (execution hard-fails on a router
    base_name), but it still needs to know its effective provider for metadata/REST
    consumers — inherited from the capable model, same as label/enabled semantics."""
    yaml_file = _write_yaml(
        tmp_path,
        '''
llm_models:
  - base_name: 'cap'
    deployment_name: 'cap'
    enabled: true
    provider: 'aws_bedrock'
    switchyard:
      - base_name: 'cap-switchyard-eff-signal'
        efficient: 'eff'
        mode: signal
  - base_name: 'eff'
    deployment_name: 'eff'
    enabled: true
embeddings_models: []
''',
    )
    cfg = LLMConfig(yaml_file=yaml_file)
    router = next(r for r in cfg.llm_routers if r.base_name == 'cap-switchyard-eff-signal')
    assert router.provider == LLMProvider.AWS_BEDROCK


def test_switchyard_classifier_without_classifier_model_skipped(tmp_path):
    """A classifier-mode entry with no classifier_model set (it's a required field at the
    same level as mode, no global fallback) must not generate a router at all — it must not
    silently generate one that claims RoutingMode.CLASSIFIER but can never invoke a
    classifier (see engine.py::pick_model, which picks its confidence threshold from
    routing_mode, not from classifier availability)."""
    yaml_file = _write_yaml(
        tmp_path,
        '''
llm_models:
  - base_name: 'cap'
    deployment_name: 'cap'
    enabled: true
    switchyard:
      - base_name: 'cap-switchyard-eff-signal'
        efficient: 'eff'
        mode: signal
      - base_name: 'cap-switchyard-eff-classifier'
        efficient: 'eff'
        mode: classifier
  - base_name: 'eff'
    deployment_name: 'eff'
    enabled: true
embeddings_models: []
''',
    )
    cfg = LLMConfig(yaml_file=yaml_file)
    assert {r.base_name for r in cfg.llm_routers} == {'cap-switchyard-eff-signal'}


def test_switchyard_classifier_with_unresolvable_classifier_model_skipped(tmp_path):
    """A classifier-mode entry whose classifier_model doesn't resolve to any known model in
    the catalog is just as invalid as a missing one — must not generate a router."""
    yaml_file = _write_yaml(
        tmp_path,
        '''
llm_models:
  - base_name: 'cap'
    deployment_name: 'cap'
    enabled: true
    switchyard:
      - base_name: 'cap-switchyard-eff-classifier'
        efficient: 'eff'
        mode: classifier
        classifier_model: 'does-not-exist'
  - base_name: 'eff'
    deployment_name: 'eff'
    enabled: true
embeddings_models: []
''',
    )
    cfg = LLMConfig(yaml_file=yaml_file)
    assert cfg.llm_routers == []


def test_switchyard_unknown_efficient_skipped(tmp_path):
    yaml_file = _write_yaml(
        tmp_path,
        '''
llm_models:
  - base_name: 'cap'
    deployment_name: 'cap'
    enabled: true
    switchyard:
      - base_name: 'cap-switchyard-missing-signal'
        efficient: 'missing'
        mode: signal
embeddings_models: []
''',
    )
    cfg = LLMConfig(yaml_file=yaml_file)
    assert cfg.llm_routers == []


def test_switchyard_capable_is_litellm_router_skipped(tmp_path):
    """Router-on-router is not supported: a capable model that is itself declared as a
    LiteLLM auto-router must not generate a Switchyard router (see
    engine.py::get_proxy_switchyard_router for the symmetric live-catalog check)."""
    yaml_file = _write_yaml(
        tmp_path,
        '''
llm_models:
  - base_name: 'cap'
    deployment_name: 'cap'
    enabled: true
    litellm_router: {}
    switchyard:
      - base_name: 'cap-switchyard-eff-signal'
        efficient: 'eff'
        mode: signal
  - base_name: 'eff'
    deployment_name: 'eff'
    enabled: true
embeddings_models: []
''',
    )
    cfg = LLMConfig(yaml_file=yaml_file)
    assert cfg.llm_routers == []


def test_switchyard_efficient_is_litellm_router_skipped(tmp_path):
    """Same as above, but the efficient model is the one declared as a LiteLLM auto-router."""
    yaml_file = _write_yaml(
        tmp_path,
        '''
llm_models:
  - base_name: 'cap'
    deployment_name: 'cap'
    enabled: true
    switchyard:
      - base_name: 'cap-switchyard-eff-signal'
        efficient: 'eff'
        mode: signal
  - base_name: 'eff'
    deployment_name: 'eff'
    enabled: true
    litellm_router: {}
embeddings_models: []
''',
    )
    cfg = LLMConfig(yaml_file=yaml_file)
    assert cfg.llm_routers == []


def test_switchyard_base_name_collides_with_model_skipped(tmp_path):
    """A switchyard entry whose base_name matches an existing regular model's base_name must
    be rejected — router identities now live in free-form YAML, so nothing else guarantees
    they can't collide with a real model."""
    yaml_file = _write_yaml(
        tmp_path,
        '''
llm_models:
  - base_name: 'cap'
    deployment_name: 'cap'
    enabled: true
    switchyard:
      - base_name: 'eff'
        efficient: 'eff'
        mode: signal
  - base_name: 'eff'
    deployment_name: 'eff'
    enabled: true
embeddings_models: []
''',
    )
    cfg = LLMConfig(yaml_file=yaml_file)
    assert cfg.llm_routers == []


@pytest.mark.skipif(not _DIAL_CONFIG.exists(), reason="DIAL config not present")
def test_dial_config_generates_expected_router_names() -> None:
    cfg = LLMConfig(yaml_file=_DIAL_CONFIG)
    generated = {r.base_name for r in cfg.llm_routers}
    assert generated == _DIAL_EXPECTED_ROUTER_NAMES


def test_llm_model_litellm_router_defaults_to_none():
    from codemie.configs.llm_config import LLMModel

    model = LLMModel(base_name="gpt-5.6-luna", deployment_name="gpt-5.6-luna", enabled=True)
    assert model.litellm_router is None


def test_llm_model_litellm_router_declared():
    from codemie.configs.llm_config import LLMModel, LiteLLMRouterConfig

    model = LLMModel(
        base_name="smart-router",
        deployment_name="smart-router",
        enabled=True,
        litellm_router=LiteLLMRouterConfig(),
    )
    assert model.litellm_router is not None
    assert model.litellm_router.is_router is True


def test_litellm_router_counterfactual_model_parses():
    from codemie.configs.llm_config import LiteLLMRouterConfig

    router = LiteLLMRouterConfig(counterfactual_model="claude-opus-5")

    assert router.counterfactual_model == "claude-opus-5"


def test_litellm_router_config_parses_strategy_tiers_classifier_model():
    """A live LiteLLM catalog entry's litellm_router block can declare the same
    strategy/tiers/classifier_model contract as a generated switchyard router — this is the
    shape codemie-ops hand-authors directly into LiteLLM's model_info for a litellm_auto
    router (see LlmRouterOption)."""
    from codemie.configs.llm_config import LiteLLMRouterConfig, RoutingMode

    router = LiteLLMRouterConfig(
        strategy="classifier",
        classifier_model="gpt-5-mini",
        tiers={
            "simple": {"model": "gpt-5-mini", "label": "GPT-5 mini"},
            "medium": {"model": "gpt-5", "label": "GPT-5"},
            "complex": {"model": "gpt-5", "label": "GPT-5"},
            "reasoning": {"model": "gpt-5-pro", "label": "GPT-5 Pro"},
        },
    )

    assert router.strategy == RoutingMode.CLASSIFIER
    assert router.classifier_model == "gpt-5-mini"
    assert router.tiers is not None
    assert router.tiers.simple.model == "gpt-5-mini"
    assert router.tiers.simple.label == "GPT-5 mini"
    assert router.tiers.reasoning.model == "gpt-5-pro"
    assert router.tiers.reasoning.label == "GPT-5 Pro"
