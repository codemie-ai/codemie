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

"""Workflow-level contract settings (EPMCDME-16503, parent §6.1, §6.2).

`save_validation` and `require_input_contracts` are not declared fields of the workflow config; they are read from the
`yaml_config` text here, in one place. A missing or non-conforming value gives the default: rejecting bad values is
the schema stage's job, never this reader's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from codemie.workflows.constants import ContractValidationMode

if TYPE_CHECKING:
    from codemie.core.workflow_models.workflow_config import WorkflowConfigBase

DEFAULT_MODE = ContractValidationMode.WARN


@dataclass(frozen=True)
class ContractSettings:
    mode: ContractValidationMode = DEFAULT_MODE
    require_input_contracts: bool = False


def read_contract_settings(config: WorkflowConfigBase, document: object = None) -> ContractSettings:
    """Read the workflow-level `save_validation` and `require_input_contracts` keys from `config.yaml_config`.

    `document` is that text already parsed by the caller; without it the text is parsed here.
    """
    document = document if isinstance(document, dict) else _load_document(getattr(config, "yaml_config", None))
    return ContractSettings(
        mode=read_mode(document.get("save_validation")),
        require_input_contracts=document.get("require_input_contracts") is True,
    )


def _load_document(text: object) -> dict[str, object]:
    if not isinstance(text, str) or not text.strip():
        return {}
    try:
        document = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def read_mode(value: object, default: ContractValidationMode = DEFAULT_MODE) -> ContractValidationMode:
    """A `save_validation` value as a mode; anything that is not one of the modes gives `default`."""
    if isinstance(value, str):
        try:
            return ContractValidationMode(value)
        except ValueError:
            pass
    return default
