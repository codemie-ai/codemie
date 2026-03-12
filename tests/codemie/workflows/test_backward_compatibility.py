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

"""
Test Area: Backward Compatibility

Tests for deprecated keep_history flag and migration to include_in_llm_history.

This module tests the following functionality:
- keep_history=True migration to include_in_llm_history=True
- keep_history=False migration to include_in_llm_history=False
- Precedence when both flags provided
- Default behavior
- Legacy workflow execution
"""

import yaml

from codemie.core.workflow_models import WorkflowNextState, WorkflowState


def test_tc_bc_001_keep_history_true_migration():
    """
    TC_BC_001: keep_history=True Migration

    Verify migration from keep_history=True to include_in_llm_history=True.
    """
    # Arrange & Act
    workflow_next = WorkflowNextState(
        state_id="next_node",
        keep_history=True,  # Deprecated
    )

    # Assert
    assert workflow_next.include_in_llm_history is True
    assert workflow_next.keep_history is True  # Original value preserved


def test_tc_bc_002_keep_history_false_migration():
    """
    TC_BC_002: keep_history=False Migration

    Verify migration from keep_history=False to include_in_llm_history=False.
    """
    # Arrange & Act
    workflow_next = WorkflowNextState(
        state_id="next_node",
        keep_history=False,  # Deprecated
    )

    # Assert
    assert workflow_next.include_in_llm_history is False
    assert workflow_next.keep_history is False


def test_tc_bc_003_both_flags_provided_new_takes_precedence():
    """
    TC_BC_003: Both Flags Provided (New Takes Precedence)

    Verify include_in_llm_history takes precedence when both flags provided.
    """
    # Arrange & Act
    workflow_next = WorkflowNextState(
        state_id="next_node",
        keep_history=False,  # Deprecated
        include_in_llm_history=True,  # New flag takes precedence
    )

    # Assert
    assert workflow_next.include_in_llm_history is True  # New flag wins


def test_tc_bc_004_only_new_flag_provided():
    """
    TC_BC_004: Only New Flag Provided

    Verify no migration needed when only include_in_llm_history provided.
    """
    # Arrange & Act
    workflow_next = WorkflowNextState(
        state_id="next_node",
        include_in_llm_history=True,
    )

    # Assert
    assert workflow_next.include_in_llm_history is True
    # keep_history has default value
    assert workflow_next.keep_history is True


def test_tc_bc_005_neither_flag_provided_default():
    """
    TC_BC_005: Neither Flag Provided (Default)

    Verify default is include_in_llm_history=True.
    """
    # Arrange & Act
    workflow_next = WorkflowNextState(state_id="next_node")

    # Assert
    assert workflow_next.include_in_llm_history is True  # Default
    assert workflow_next.keep_history is True  # Default


def test_tc_bc_006_legacy_workflow_execution():
    """
    TC_BC_006: Legacy Workflow Execution

    Test complete workflow with keep_history flag (integration test placeholder).
    """
    # Arrange
    workflow_state = WorkflowState(
        id="legacy_node",
        task="Legacy task",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            keep_history=False,  # Using deprecated flag
        ),
    )

    # Act & Assert
    assert workflow_state.next.include_in_llm_history is False

    # The node execution would work normally with migrated flag
    # Full integration test would be in test_base_node_lifecycle.py


def test_tc_bc_007_migration_warning_logging():
    """
    TC_BC_007: Migration Warning Logging

    Verify deprecation logged when keep_history used.

    Note: This test is a placeholder as logging verification would require
    capturing log output. The migration happens silently in the model_validator.
    """
    # The model_validator handle_keep_history_backward_compatibility
    # handles the migration without logging warnings

    # Arrange & Act
    workflow_next = WorkflowNextState(
        state_id="next",
        keep_history=False,
    )

    # Assert migration happened
    assert workflow_next.include_in_llm_history is False

    # In production, we might want to add logging in the validator


def test_tc_bc_008_pydantic_model_validation():
    """
    TC_BC_008: Pydantic Model Validation

    Test @model_validator handles migration.
    """
    # Arrange - Test with dict input (as from YAML/JSON)
    data = {
        "state_id": "test_node",
        "keep_history": False,  # Old field
    }

    # Act
    workflow_next = WorkflowNextState(**data)

    # Assert
    assert workflow_next.include_in_llm_history is False
    assert workflow_next.state_id == "test_node"


def test_tc_bc_009_yaml_config_parsing():
    """
    TC_BC_009: YAML Config Parsing

    Test parsing of old YAML configs with keep_history.
    """
    # Arrange
    yaml_content = """
state_id: process_node
keep_history: false
store_in_context: true
"""

    # Act
    data = yaml.safe_load(yaml_content)
    workflow_next = WorkflowNextState(**data)

    # Assert
    assert workflow_next.include_in_llm_history is False
    assert workflow_next.store_in_context is True
    assert workflow_next.state_id == "process_node"


def test_tc_bc_010_api_backward_compatibility():
    """
    TC_BC_010: API Backward Compatibility

    Test REST API accepts both flags (integration test placeholder).
    """
    # This would be tested in REST API tests
    # Verifying that POST /workflows accepts both old and new flags

    # Test with old flag
    old_config = {
        "state_id": "node1",
        "keep_history": True,
    }
    workflow_next_old = WorkflowNextState(**old_config)
    assert workflow_next_old.include_in_llm_history is True

    # Test with new flag
    new_config = {
        "state_id": "node1",
        "include_in_llm_history": True,
    }
    workflow_next_new = WorkflowNextState(**new_config)
    assert workflow_next_new.include_in_llm_history is True

    # Test with both (new takes precedence)
    both_config = {
        "state_id": "node1",
        "keep_history": False,
        "include_in_llm_history": True,
    }
    workflow_next_both = WorkflowNextState(**both_config)
    assert workflow_next_both.include_in_llm_history is True


# Additional edge case tests


def test_keep_history_none_value():
    """Test that None value for keep_history doesn't trigger migration."""
    # Arrange & Act
    workflow_next = WorkflowNextState(
        state_id="test",
        include_in_llm_history=False,
    )

    # Assert
    assert workflow_next.include_in_llm_history is False


def test_mixed_config_migration():
    """Test migration with mixed old and new config."""
    # Arrange
    config = {
        "state_id": "test",
        "keep_history": True,  # Old
        "store_in_context": True,  # New
        "clear_prior_messages": False,  # New
    }

    # Act
    workflow_next = WorkflowNextState(**config)

    # Assert
    assert workflow_next.include_in_llm_history is True  # Migrated
    assert workflow_next.store_in_context is True
    assert workflow_next.clear_prior_messages is False


def test_full_workflow_state_with_deprecated_flag():
    """Test full WorkflowState with deprecated flag in next."""
    # Arrange & Act
    workflow_state = WorkflowState(
        id="test_state",
        task="Test",
        assistant_id="assistant_1",
        next=WorkflowNextState(
            state_id="next",
            keep_history=False,
            clear_context_store=True,
        ),
    )

    # Assert
    assert workflow_state.next.include_in_llm_history is False
    assert workflow_state.next.clear_context_store is True


def test_yaml_workflow_config_with_keep_history():
    """Test parsing complete workflow YAML with keep_history."""
    # Arrange
    yaml_content = """
id: test_workflow
task: Process data
assistant_id: assistant_1
next:
  state_id: next_node
  keep_history: false
  store_in_context: true
  clear_prior_messages: true
"""

    # Act
    data = yaml.safe_load(yaml_content)
    workflow_state = WorkflowState(**data)

    # Assert
    assert workflow_state.next.include_in_llm_history is False
    assert workflow_state.next.store_in_context is True
    assert workflow_state.next.clear_prior_messages is True
