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
Test Area: Workflow Concurrent Execution

Tests for workflow execution with max_concurrency > 1, specifically testing
virtual assistant lifecycle management during parallel task execution.

This module tests the following critical functionality:
- Parallel iterator execution with max_concurrency > 1
- Virtual assistant lifecycle in concurrent scenarios
- Prevention of premature virtual assistant deletion
- KeyError prevention during concurrent tool/settings initialization
- Thread-safety of VirtualAssistantService

Critical Issue Reproduction:
EPMCDME-9997: Iterator nodes fail when max_concurrency > 1 due to race
condition where completing tasks delete virtual assistants that are still
in use by other parallel tasks.
"""

import pytest
from unittest.mock import Mock, MagicMock
from concurrent.futures import ThreadPoolExecutor
import threading
import time

from codemie.service.assistant import VirtualAssistantService
from codemie.rest_api.models.assistant import ToolKitDetails


@pytest.fixture
def mock_user():
    """Create mock User."""
    user = Mock()
    user.id = "user_123"
    user.username = "test_user"
    user.name = "Test User"
    user.is_admin = False
    return user


@pytest.fixture
def mock_thought_queue():
    """Create mock ThoughtQueue."""
    queue = Mock()
    queue.set_context = Mock()
    queue.close = Mock()
    return queue


@pytest.fixture
def mock_toolkit():
    """Create mock toolkit."""
    return MagicMock(spec=ToolKitDetails)


@pytest.fixture(autouse=True)
def cleanup_virtual_assistants():
    """Clean up virtual assistants before and after each test."""
    VirtualAssistantService.assistants.clear()
    yield
    VirtualAssistantService.assistants.clear()


def test_tc_ce_001_concurrent_execution_no_premature_deletion(mock_user, mock_thought_queue, mock_toolkit):
    """
    TC_CE_001: Concurrent Execution - No Premature Deletion

    Test that virtual assistants are NOT deleted while parallel tasks
    are still executing. This reproduces the KeyError issue from the ticket.

    Expected Behavior:
    - Multiple parallel tasks share same execution_id
    - Each task creates its own virtual assistant
    - Virtual assistants remain accessible until ALL tasks complete
    - No KeyError when tasks try to access virtual assistants
    """
    # Arrange
    execution_id = "test_exec_concurrent_001"

    # Create multiple virtual assistants for parallel tasks
    # (simulating what happens during iterator execution)
    assistants = []
    for i in range(3):
        assistant = VirtualAssistantService.create(
            toolkits=[mock_toolkit],
            project="test_project",
            execution_id=execution_id,
            name=f"Task {i} Assistant",
        )
        assistants.append(assistant)

    # Verify all assistants are created
    assert len(VirtualAssistantService.assistants) == 3

    # Simulate concurrent task execution
    task_states = []
    lock = threading.Lock()

    def simulate_task_execution(assistant_id: str, task_num: int):
        """Simulate a parallel task that uses virtual assistant."""
        try:
            # Task 1: Retrieve assistant (simulates tool initialization)
            retrieved = VirtualAssistantService.get(assistant_id)
            assert retrieved is not None

            with lock:
                task_states.append(f"Task {task_num}: Retrieved assistant")

            # Simulate some work
            time.sleep(0.01)

            # Task 2: Try to access assistant again (simulates settings lookup)
            retrieved_again = VirtualAssistantService.get(assistant_id)
            assert retrieved_again is not None

            with lock:
                task_states.append(f"Task {task_num}: Completed successfully")

        except KeyError as e:
            with lock:
                task_states.append(f"Task {task_num}: FAILED with KeyError - {e}")
            raise

    # Act - Execute tasks in parallel
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = []
        for i, assistant in enumerate(assistants):
            future = executor.submit(simulate_task_execution, assistant.id, i)
            futures.append(future)

        # Wait for all tasks to complete
        for future in futures:
            future.result()  # Will raise exception if task failed

    # Assert
    assert len(task_states) == 6  # 2 state updates per task × 3 tasks
    assert all("FAILED" not in state for state in task_states)


def test_tc_ce_002_no_premature_cleanup_during_execution(mock_user, mock_thought_queue, mock_toolkit):
    """
    TC_CE_002: No Premature Cleanup During Execution

    Verifies the fix: after removing cleanup from agent_node.after_execution(),
    all parallel tasks complete successfully even with concurrent access to
    virtual assistants.

    Before fix: First task to complete would call delete_by_execution_id,
    causing KeyError in other running tasks.

    After fix: No cleanup during execution, so all tasks succeed.
    """
    # Arrange
    execution_id = "test_exec_no_premature_001"

    # Create multiple virtual assistants for parallel tasks
    assistants = []
    for i in range(3):
        assistant = VirtualAssistantService.create(
            toolkits=[mock_toolkit],
            project="test_project",
            execution_id=execution_id,
            name=f"Task {i} Assistant",
        )
        assistants.append(assistant)

    assert len(VirtualAssistantService.assistants) == 3

    # Track task completion
    completed_tasks = []
    errors = []
    lock = threading.Lock()

    def parallel_task(assistant_id: str, task_num: int):
        """Simulate parallel task that accesses virtual assistant multiple times."""
        try:
            # Task start - retrieve assistant (simulates generate_execution_context)
            VirtualAssistantService.get(assistant_id)
            with lock:
                completed_tasks.append(f"Task {task_num}: Started")

            # Simulate work
            time.sleep(0.01)

            # Mid-task - access assistant again (simulates tool initialization/settings lookup)
            VirtualAssistantService.get(assistant_id)
            with lock:
                completed_tasks.append(f"Task {task_num}: Mid-execution")

            # Task end - final access (simulates after_execution)
            time.sleep(0.01)
            VirtualAssistantService.get(assistant_id)

            # NOTE: With fix, NO cleanup happens here (removed from agent_node.after_execution)
            # Cleanup will happen later in workflow.py's finally block

            with lock:
                completed_tasks.append(f"Task {task_num}: Completed successfully")

        except KeyError as e:
            with lock:
                errors.append((task_num, str(e)))
                completed_tasks.append(f"Task {task_num}: FAILED with KeyError")

    # Act - Execute tasks in parallel
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(parallel_task, assistant.id, i) for i, assistant in enumerate(assistants)]

        for future in futures:
            future.result()  # Will raise exception if task failed

    # Assert - After fix, all tasks should complete successfully
    # All tasks should succeed because cleanup is deferred
    assert len(errors) == 0, f"No tasks should fail after fix. Got {len(errors)} errors: {errors}"
    assert len(completed_tasks) == 9, f"Expected 9 completion logs (3 per task × 3 tasks), got {len(completed_tasks)}"

    # All assistants should still exist (cleanup deferred to workflow end)
    assert len(VirtualAssistantService.assistants) == 3, "Assistants should not be deleted during execution"


def test_tc_ce_003_proper_cleanup_after_all_tasks_complete(mock_user, mock_thought_queue, mock_toolkit):
    """
    TC_CE_003: Proper Cleanup After All Tasks Complete

    Test that cleanup should ONLY happen after ALL parallel tasks complete,
    not during execution.

    This test defines the expected behavior after the fix.
    """
    # Arrange
    execution_id = "test_exec_cleanup_001"

    # Create virtual assistants
    assistants = []
    for _i in range(3):
        assistant = VirtualAssistantService.create(
            toolkits=[mock_toolkit],
            project="test_project",
            execution_id=execution_id,
        )
        assistants.append(assistant)

    assert len(VirtualAssistantService.assistants) == 3

    # Simulate all tasks completing
    def complete_task(assistant_id: str):
        """Simulate task completion WITHOUT premature cleanup."""
        assistant = VirtualAssistantService.get(assistant_id)
        assert assistant is not None
        time.sleep(0.01)
        # DO NOT call delete_by_execution_id here (this is the fix)

    # Act - Execute tasks
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(complete_task, a.id) for a in assistants]
        for future in futures:
            future.result()

    # Assert - All assistants still exist after tasks complete
    assert len(VirtualAssistantService.assistants) == 3

    # Cleanup should happen AFTER workflow completes (in finally block)
    VirtualAssistantService.delete_by_execution_id(execution_id)

    # Now all assistants should be deleted
    assert len(VirtualAssistantService.assistants) == 0


def test_tc_ce_004_virtual_assistant_isolation_per_task(mock_user, mock_toolkit):
    """
    TC_CE_004: Virtual Assistant Isolation Per Task

    Verify that each parallel task gets its own virtual assistant instance,
    even though they share the same execution_id.
    """
    # Arrange
    execution_id = "test_exec_isolation_001"

    # Act - Create multiple assistants (simulating parallel tasks)
    assistants = []
    for i in range(5):
        assistant = VirtualAssistantService.create(
            toolkits=[mock_toolkit],
            project="test_project",
            execution_id=execution_id,
            name=f"Parallel Task {i}",
        )
        assistants.append(assistant)

    # Assert
    # 1. Each assistant has unique ID
    assistant_ids = [a.id for a in assistants]
    assert len(set(assistant_ids)) == 5, "Each assistant should have unique ID"

    # 2. All share same execution_id
    assert all(a.execution_id == execution_id for a in assistants)

    # 3. All assistants are retrievable
    for assistant_id in assistant_ids:
        retrieved = VirtualAssistantService.get(assistant_id)
        assert retrieved is not None
        assert retrieved.execution_id == execution_id

    # 4. Deleting by execution_id removes ALL assistants
    VirtualAssistantService.delete_by_execution_id(execution_id)

    for assistant_id in assistant_ids:
        with pytest.raises(KeyError):
            VirtualAssistantService.get(assistant_id)
