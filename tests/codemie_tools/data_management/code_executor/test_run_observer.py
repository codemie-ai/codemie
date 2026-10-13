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

"""The capacity facts of one sandbox run."""

from __future__ import annotations

import threading

from langchain_core.tools import ToolException

from codemie_tools.data_management.code_executor.run_observer import RunOutcome, SandboxFailure, SandboxRunObserver


def test_a_new_observer_has_seen_nothing() -> None:
    observer = SandboxRunObserver()

    assert (observer.requested, observer.waited, observer.rejected) == (False, False, False)


def test_a_wait_is_also_a_request() -> None:
    observer = SandboxRunObserver()

    observer.mark_waited()

    assert (observer.requested, observer.waited, observer.rejected) == (True, True, False)


def test_a_rejection_is_also_a_wait_and_a_request() -> None:
    observer = SandboxRunObserver()

    observer.mark_rejected()

    assert (observer.requested, observer.waited, observer.rejected) == (True, True, True)


def test_marking_twice_changes_nothing() -> None:
    observer = SandboxRunObserver()

    observer.mark_waited()
    observer.mark_waited()

    assert (observer.requested, observer.waited, observer.rejected) == (True, True, False)


def test_it_can_be_marked_from_another_thread() -> None:
    observer = SandboxRunObserver()

    thread = threading.Thread(target=observer.mark_waited)
    thread.start()
    thread.join()

    assert observer.waited is True


def test_a_run_with_nothing_wrong_is_ok() -> None:
    assert SandboxRunObserver().outcome is RunOutcome.OK


def test_a_zero_exit_code_is_ok_and_a_non_zero_one_is_a_script_error() -> None:
    observer = SandboxRunObserver()
    observer.set_exit_code(0)
    assert observer.outcome is RunOutcome.OK

    observer.set_exit_code(2)
    assert observer.outcome is RunOutcome.SCRIPT_ERROR


def test_a_failure_names_the_outcome() -> None:
    observer = SandboxRunObserver()

    observer.fail(SandboxFailure("pod did not start", RunOutcome.TIMEOUT))

    assert observer.outcome is RunOutcome.TIMEOUT


def test_any_other_exception_is_a_plain_error() -> None:
    observer = SandboxRunObserver()

    observer.fail(RuntimeError("boom"))

    assert observer.outcome is RunOutcome.ERROR


def test_the_first_failure_stays() -> None:
    observer = SandboxRunObserver()

    observer.fail(SandboxFailure("a", RunOutcome.SANDBOX_ERROR))
    observer.fail(RuntimeError("later"))

    assert observer.outcome is RunOutcome.SANDBOX_ERROR


def test_a_rejection_wins_over_everything_else() -> None:
    observer = SandboxRunObserver()
    observer.set_exit_code(1)
    observer.fail(SandboxFailure("at capacity", RunOutcome.SANDBOX_ERROR))

    observer.mark_rejected()

    assert observer.outcome is RunOutcome.CAPACITY_REJECTED


def test_a_sandbox_failure_keeps_the_message_the_user_sees() -> None:
    assert str(SandboxFailure("Code failed security validation", RunOutcome.POLICY_DENIED)) == (
        "Code failed security validation"
    )


def test_the_timings_start_unset_and_are_kept_once_set() -> None:
    observer = SandboxRunObserver()
    assert (observer.acquire_seconds, observer.pod_start_seconds, observer.pod_seconds) == (None, None, None)

    observer.set_acquire_seconds(0.5)
    observer.set_pod_start_seconds(2.0)
    observer.set_pod_seconds(9.0)

    assert (observer.acquire_seconds, observer.pod_start_seconds, observer.pod_seconds) == (0.5, 2.0, 9.0)


def test_a_script_that_failed_stays_a_script_error_when_the_tool_then_raised() -> None:
    observer = SandboxRunObserver()
    observer.set_exit_code(1)

    observer.fail(ToolException("Code execution failed."))

    assert observer.outcome is RunOutcome.SCRIPT_ERROR


def test_a_named_failure_wins_over_the_exit_code() -> None:
    observer = SandboxRunObserver()
    observer.set_exit_code(1)

    observer.fail(SandboxFailure("pod never ran", RunOutcome.TIMEOUT))

    assert observer.outcome is RunOutcome.TIMEOUT
