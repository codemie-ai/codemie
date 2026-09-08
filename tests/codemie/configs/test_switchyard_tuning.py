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

from unittest.mock import patch

from codemie.configs.config import config
from codemie.configs.llm_config import LLMConfig, SwitchyardTuning


def test_defaults_reproduce_previous_constants():
    t = SwitchyardTuning()
    assert t.recent_window == 3
    assert t.classifier_base_threshold == 0.65
    assert t.classifier_threshold_step == 0.15
    assert t.signal_threshold == 0.0
    assert t.classifier_threshold == 0.5
    assert t.classifier_model is None


def test_partial_override_preserves_other_defaults():
    t = SwitchyardTuning(classifier_base_threshold=0.7)
    assert t.classifier_base_threshold == 0.7
    assert t.classifier_threshold_step == 0.15  # untouched default
    assert t.recent_window == 3


def test_overlay_expression_keeps_global_for_unset_fields():
    """Unit-tests the exact overlay expression used in generate_switchyard_routers:
    a partial per-router tuning must only override the fields it explicitly set,
    leaving the global default for everything else.
    """
    global_tuning = SwitchyardTuning(classifier_model="global-classifier")
    partial = SwitchyardTuning(classifier_base_threshold=0.8)

    resolved = global_tuning.model_copy(update={f: getattr(partial, f) for f in partial.model_fields_set})

    assert resolved.classifier_base_threshold == 0.8  # overridden by the partial
    assert resolved.classifier_model == "global-classifier"  # preserved from the global
    assert resolved.classifier_threshold_step == 0.15  # untouched default
    assert resolved.recent_window == 3  # untouched default


def _write_yaml(tmp_path, body: str):
    yaml_file = tmp_path / "c.yaml"
    yaml_file.write_text(body)
    return yaml_file


def test_per_router_partial_tuning_overlays_global_default(tmp_path):
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
      modes: [signal]
      tuning:
        classifier_base_threshold: 0.9
  - base_name: 'eff'
    deployment_name: 'eff'
    enabled: true
embeddings_models: []
''',
    )
    with patch.object(config, "SWITCHYARD_CLASSIFIER_MODEL", None), patch.object(config, "SWITCHYARD_ENABLED", True):
        cfg = LLMConfig(yaml_file=yaml_file)
    router = next(r for r in cfg.llm_routers if r.base_name == 'cap-switchyard-eff-signal')
    assert router.switchyard.tuning.classifier_base_threshold == 0.9  # overridden
    assert router.switchyard.tuning.classifier_threshold_step == 0.15  # untouched default
    assert router.switchyard.tuning.recent_window == 3  # untouched default


def test_router_without_tuning_gets_full_default(tmp_path):
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
      modes: [signal]
  - base_name: 'eff'
    deployment_name: 'eff'
    enabled: true
embeddings_models: []
''',
    )
    with patch.object(config, "SWITCHYARD_CLASSIFIER_MODEL", None), patch.object(config, "SWITCHYARD_ENABLED", True):
        cfg = LLMConfig(yaml_file=yaml_file)
    router = next(r for r in cfg.llm_routers if r.base_name == 'cap-switchyard-eff-signal')
    assert router.switchyard.tuning == SwitchyardTuning()


def test_global_classifier_model_from_config_is_picked_up_as_default(tmp_path):
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
      modes: [classifier]
  - base_name: 'eff'
    deployment_name: 'eff'
    enabled: true
embeddings_models: []
''',
    )
    with (
        patch.object(config, "SWITCHYARD_CLASSIFIER_MODEL", "global-classifier-model"),
        patch.object(config, "SWITCHYARD_ENABLED", True),
    ):
        cfg = LLMConfig(yaml_file=yaml_file)
    router = next(r for r in cfg.llm_routers if r.base_name == 'cap-switchyard-eff-classifier')
    assert router.switchyard.tuning.classifier_model == "global-classifier-model"
