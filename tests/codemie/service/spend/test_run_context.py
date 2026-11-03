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

"""Which individual LLM call is in flight.

The defect these guard is a run id from one request being stamped on another request's call: a
well-formed value, no error, and the cost attributed to the wrong rows.
"""

import asyncio

from codemie.service.spend.run_context import current_run_id, publish_run_scope, reset_run_scope


class TestTheRunIdIsReadableByTheRequestThatOwnsIt:
    def test_the_published_run_id_is_returned_for_its_own_request(self):
        token = publish_run_scope(request_id="req-1", run_id="run-1")
        try:
            assert current_run_id("req-1") == "run-1"
        finally:
            reset_run_scope(token)

    def test_nothing_published_yields_none(self):
        assert current_run_id("req-1") is None

    def test_no_request_id_yields_none(self):
        token = publish_run_scope(request_id="req-1", run_id="run-1")
        try:
            assert current_run_id(None) is None
        finally:
            reset_run_scope(token)


class TestAScopeFromAnotherRequestIsNeverBorrowed:
    def test_a_foreign_request_id_reads_none_rather_than_the_other_requests_run(self):
        """A model built without the publishing callback must not inherit a stale scope.

        Borrowing it would attribute this call's cost to the other request's rows - no error, no
        NULL, nothing to notice. A NULL run id is a state every consumer already handles.
        """
        token = publish_run_scope(request_id="req-1", run_id="run-1")
        try:
            assert current_run_id("req-2") is None
        finally:
            reset_run_scope(token)


class TestItIsCorrectUnderConcurrency:
    def test_two_concurrent_tasks_do_not_see_each_others_run_id(self):
        """The contextvar is copied per task, which is why this needs no lock."""

        async def one(request_id: str, run_id: str) -> str | None:
            publish_run_scope(request_id=request_id, run_id=run_id)
            await asyncio.sleep(0)  # let the sibling task run and publish its own
            return current_run_id(request_id)

        async def both() -> list[str | None]:
            return await asyncio.gather(one("req-a", "run-a"), one("req-b", "run-b"))

        assert asyncio.run(both()) == ["run-a", "run-b"]
