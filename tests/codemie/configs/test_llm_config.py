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
from codemie.configs.llm_config import LLMConfig


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
