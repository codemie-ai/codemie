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
    CrossRefError,
)

# Line lookup utilities
from .line_lookup import (
    YamlLineFinder,
    NullYamlLineFinder,
    extract_line_numbers,
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
    "CrossRefError",
    # Line lookup
    "YamlLineFinder",
    "NullYamlLineFinder",
    "extract_line_numbers",
]
