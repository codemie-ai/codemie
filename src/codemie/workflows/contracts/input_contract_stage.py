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

"""Schema-stage wiring of the `input_schema` structural check (AC-16).

Kept beside `input_contract.py` so the validation layer only imports it: the check runs on the parsed YAML dict, before
any model exists, and its problems are shaped like the JSON-schema errors the stage already formats.
"""

from __future__ import annotations

from jsonschema import ValidationError

from codemie.workflows.contracts.input_contract import check_input_contract

INPUT_CONTRACT_VALIDATOR = "input_contract"
# Literal keys: this module must not import the validation layer, which imports it.
_STATE_SECTIONS = ("states", "orphaned_states")


def validate_state_input_contracts(workflow_config: object) -> list[ValidationError]:
    """
    Structural check of `input_schema` on every state that carries the key (AC-16).

    Covers `states` and `orphaned_states` (the same state shape, as the schema error lookups treat them). Runs on
    reachable and unreachable states alike and does not depend on any contract mode. A value that is not a string is
    already reported by the YAML schema (`type: string`), so only text is checked here.
    """
    if not isinstance(workflow_config, dict):
        return []
    errors: list[ValidationError] = []
    for section in _STATE_SECTIONS:
        states = workflow_config.get(section)
        if not isinstance(states, list):
            continue
        for index, state in enumerate(states):
            if not isinstance(state, dict) or not isinstance(state.get("input_schema"), str):
                continue
            for problem in check_input_contract(state["input_schema"]):
                errors.append(
                    ValidationError(
                        problem.message,
                        validator=INPUT_CONTRACT_VALIDATOR,
                        validator_value=problem.code,
                        path=[section, index, "input_schema"],
                    )
                )
    return errors
