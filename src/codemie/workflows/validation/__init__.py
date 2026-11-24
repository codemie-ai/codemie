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

"""
Workflow validation module.

This package provides functionality for validating workflow configurations:
- Schema validation against JSON schema
- Cross-reference validation between workflow components
- Resource availability validation (assistants, tools, datasources)
- YAML line number tracking for precise error reporting
- Contract linking (re-exported from `codemie.workflows.contracts.linker`)
"""

# Schema validation
from .schema import (
    WorkflowExecutionConfigError,
    WorkflowExecutionParsingError,
    SchemaError,
    WorkflowExecutionConfigSchemaValidationError,
    WorkflowExecutionConfigCrossReferenceValidationError,
    validate_workflow_execution_config_yaml,
    WORKFLOW_EXECUTION_CONFIG_SCHEMA,
    WORKFLOW_EXECUTION_CONFIG_SCHEMA_YAML_FILE_PATH,
)

# Resource validation
from .resources import (
    WorkflowConfigResourcesValidationError,
    validate_workflow_config_resources_availability,
)

# Error transformers
from .transformers import (
    PydanticErrorTransformer,
)

# Data models
from .models import (
    WorkflowValidationErrorDetail,
    ContractMeta,
    CrossRefError,
)

# Line lookup utilities
from .line_lookup import (
    YamlLineFinder,
    NullYamlLineFinder,
    extract_line_numbers,
)

# Contract linker (EPMCDME-16503): leaf modules only, so this import cannot cycle with `core.workflow_models`
from ..contracts.linker import link_workflow_contracts

# Contract issue shaping (EPMCDME-16504)
from .contract_issues import (
    CONTRACT_WARNINGS_CAP,
    ContractIssues,
    build_contract_issues,
    build_contract_rejection,
    contract_line_finder,
    format_config_path,
)

__all__ = [
    # Schema validation
    "WorkflowExecutionConfigError",
    "WorkflowExecutionParsingError",
    "SchemaError",
    "WorkflowExecutionConfigSchemaValidationError",
    "WorkflowExecutionConfigCrossReferenceValidationError",
    "validate_workflow_execution_config_yaml",
    "WORKFLOW_EXECUTION_CONFIG_SCHEMA",
    "WORKFLOW_EXECUTION_CONFIG_SCHEMA_YAML_FILE_PATH",
    # Resource validation
    "WorkflowConfigResourcesValidationError",
    "validate_workflow_config_resources_availability",
    # Error transformers
    "PydanticErrorTransformer",
    # Data models
    "WorkflowValidationErrorDetail",
    "ContractMeta",
    "CrossRefError",
    # Line lookup
    "YamlLineFinder",
    "NullYamlLineFinder",
    "extract_line_numbers",
    # Contract linker
    "link_workflow_contracts",
    # Contract issues
    "CONTRACT_WARNINGS_CAP",
    "ContractIssues",
    "build_contract_issues",
    "build_contract_rejection",
    "contract_line_finder",
    "format_config_path",
]
