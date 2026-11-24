# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Workflow-level contract settings (EPMCDME-16503, T3): read_contract_settings and its defaults."""

import pytest
import yaml

from codemie.core.workflow_models import WorkflowConfig
from codemie.workflows.constants import ContractValidationMode
from codemie.workflows.contracts.settings import ContractSettings, read_contract_settings


def _config(yaml_config) -> WorkflowConfig:
    return WorkflowConfig(
        id="wf-1", name="wf", description="d", project="demo", mode="Sequential", yaml_config=yaml_config
    )


def _dump(**keys) -> str:
    return yaml.safe_dump({"assistants": [], "states": [], **keys})


def test_defaults_are_warn_and_not_required():
    settings = read_contract_settings(_config(_dump()))
    assert settings == ContractSettings(mode=ContractValidationMode.WARN, require_input_contracts=False)


@pytest.mark.parametrize(
    ("keys", "mode", "required"),
    [
        ({"save_validation": "strict"}, ContractValidationMode.STRICT, False),
        ({"save_validation": "warn"}, ContractValidationMode.WARN, False),
        ({"require_input_contracts": True}, ContractValidationMode.WARN, True),
        ({"save_validation": "strict", "require_input_contracts": True}, ContractValidationMode.STRICT, True),
        ({"require_input_contracts": False}, ContractValidationMode.WARN, False),
    ],
)
def test_reads_both_keys(keys, mode, required):
    settings = read_contract_settings(_config(_dump(**keys)))
    assert settings.mode is mode
    assert settings.require_input_contracts is required


@pytest.mark.parametrize(
    "keys",
    [
        {"save_validation": "foo"},
        {"save_validation": "STRICT"},
        {"save_validation": "off"},
        {"save_validation": 1},
        {"save_validation": None},
        {"save_validation": {"a": 1}},
        {"require_input_contracts": "true"},
        {"require_input_contracts": 1},
        {"require_input_contracts": None},
        {"require_input_contracts": [True]},
    ],
)
def test_non_conforming_values_fall_back_to_defaults_without_raising(keys):
    settings = read_contract_settings(_config(_dump(**keys)))
    assert settings == ContractSettings(mode=ContractValidationMode.WARN, require_input_contracts=False)


@pytest.mark.parametrize("yaml_config", [None, "", "   ", "{{not yaml", "- a\n- b", "just text", "42", "null"])
def test_missing_or_unreadable_yaml_gives_defaults(yaml_config):
    settings = read_contract_settings(_config(yaml_config))
    assert settings == ContractSettings(mode=ContractValidationMode.WARN, require_input_contracts=False)


def test_state_level_validation_is_not_a_workflow_setting():
    text = yaml.safe_dump({"states": [{"id": "s1", "save_validation": "strict"}]})
    assert read_contract_settings(_config(text)).mode is ContractValidationMode.WARN


def test_reader_does_not_need_parse_execution_config():
    config = _config(_dump(save_validation="strict"))
    assert config.states in (None, [])
    assert read_contract_settings(config).mode is ContractValidationMode.STRICT


def test_a_parsed_document_is_read_instead_of_the_text():
    config = _config(_dump(save_validation="warn"))
    settings = read_contract_settings(config, {"save_validation": "strict", "require_input_contracts": True})
    assert settings == ContractSettings(mode=ContractValidationMode.STRICT, require_input_contracts=True)


@pytest.mark.parametrize("document", [None, [], "strict"])
def test_a_document_that_is_not_a_mapping_falls_back_to_the_text(document):
    assert (
        read_contract_settings(_config(_dump(save_validation="strict")), document).mode is ContractValidationMode.STRICT
    )
