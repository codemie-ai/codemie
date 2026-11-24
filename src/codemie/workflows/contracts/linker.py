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

"""Public entry points of the contract linker (EPMCDME-16503, T8).

`link_workflow_contracts(config, resolver=None)` runs the whole pipeline on one parsed workflow config and returns a
`ContractLinkReport`: settings reader, graph-wide simulation, then the mode policy. The report is a per-call value;
nothing here is kept at class, module or context level, so concurrent validations never share state.

Shaping a report into API issues and the rejection envelope lives in `validation/contract_issues.py`; this module is a
leaf and imports nothing from `validation`.

The linker never raises: a crash of the whole analysis becomes the single graph-level "could not analyse" record of
`failure_report`, which only blocks in strict mode (AC-19). Imports only leaf modules.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from codemie.workflows.constants import ContractValidationMode
from codemie.workflows.contracts.models import ContractLinkReport
from codemie.workflows.contracts.policy import (
    CAUSE_SETTINGS_FAILED,
    CAUSE_SIMULATION_FAILED,
    build_report,
    failure_report,
)
from codemie.workflows.contracts.settings import ContractSettings, read_contract_settings
from codemie.workflows.contracts.simulate import simulate_workflow

if TYPE_CHECKING:
    from codemie.core.workflow_models.workflow_config import WorkflowConfigBase
    from codemie.workflows.contracts.protocols import ToolSchemaResolver

logger = logging.getLogger(__name__)


def link_workflow_contracts(
    config: WorkflowConfigBase, resolver: ToolSchemaResolver | None = None, document: object = None
) -> ContractLinkReport:
    """Link the contracts of one workflow whose `parse_execution_config()` has run, and return the report.

    `resolver` answers the mandatory parameters of a tool (None means every tool schema is unknown). `document` is
    `config.yaml_config` already parsed by the caller, so the settings reader does not parse it again. The caller
    decides what to do with the report: `report.blocking_records()` is the rejection set.
    """
    states = getattr(config, "states", None) or []
    try:
        settings = read_contract_settings(config, document)
    except Exception:
        logger.warning("Contract linker could not read the workflow settings", exc_info=True)
        # Fail closed: the workflow-level mode is unknown, so a `save_validation: strict` must not be silently dropped.
        return failure_report(
            ContractSettings(mode=ContractValidationMode.STRICT), CAUSE_SETTINGS_FAILED, states=states
        )
    try:
        result = simulate_workflow(
            states,
            custom_nodes=getattr(config, "custom_nodes", None),
            tools=getattr(config, "tools", None),
            resolver=resolver,
        )
    except Exception:
        logger.warning("Contract linker simulation failed", exc_info=True)
        return failure_report(settings, CAUSE_SIMULATION_FAILED, states=states)
    return build_report(result, states=states, settings=settings)
