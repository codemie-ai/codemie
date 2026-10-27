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

"""A de-index of an absent document must not log a traceback.

`marketplace_index_sync.remove_assistant` now treats a 404 as success, which
removes the retry and the FAILED outcome. But the traceback is emitted one layer
lower, inside `BasePlatformDatasourceProcessor.remove_single_entity`:

    except Exception as e:
        logger.error(f"Failed to remove entity {entity_id}: {e}", ..., exc_info=True)
        raise

so an already-absent document still printed a full stack at ERROR before the
caller ever saw it. Two components each doing half the job is how the live log
ended up with two tracebacks for a no-op.

Idempotence belongs at the layer that knows what 404 means, so it is asserted
here as well: the caller must not have to know that "not found" is a synonym for
"already removed".
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from codemie.datasource.platform.base_platform_processor import BasePlatformDatasourceProcessor


class _NotFoundError(Exception):
    """Shaped like `elasticsearch.NotFoundError`."""

    status_code = 404

    def __init__(self) -> None:
        super().__init__(
            "NotFoundError(404, \"{'_index': 'marketplace_assistants', " "'_id': 'x', 'result': 'not_found'}\")"
        )


class _Processor(BasePlatformDatasourceProcessor):
    """Concrete only so the class can be instantiated; nothing else is used."""

    _index_name = "marketplace_assistants"  # shadows the base class property

    def _init_loader(self):  # pragma: no cover - never called by these tests
        raise AssertionError("remove_single_entity must not need a loader")


@pytest.fixture
def processor() -> BasePlatformDatasourceProcessor:
    """A processor whose only live part is the Elasticsearch client."""
    proc = object.__new__(_Processor)
    proc.index = MagicMock()
    proc.client = MagicMock()
    return proc


@pytest.mark.parametrize("absent", ["not_found_error", "nothing_deleted"])
def test_removing_an_absent_document_is_silent_and_does_not_raise(processor, codemie_logs, absent):
    # Removal goes through `delete_by_query` (every chunk of the entity): an absent entity is
    # either a 404 from a missing index or simply `deleted: 0`. Both are the requested end state.
    if absent == "not_found_error":
        processor.client.delete_by_query.side_effect = _NotFoundError()
    else:
        processor.client.delete_by_query.return_value = {"deleted": 0}

    processor.remove_single_entity("gone-already", "Gone Already")

    tracebacks = [r for r in codemie_logs.records if "Traceback (most recent call last)" in r.getMessage()]
    assert not tracebacks, (
        f"{len(tracebacks)} traceback(s) logged for a document that was already " "absent from the index"
    )
    if absent == "not_found_error":
        # A 404 means the index/document is simply gone: the requested end state, not a problem.
        assert not [
            r for r in codemie_logs.records if r.levelno >= logging.WARNING
        ], "an already-absent document is the requested end state, not a problem"
    # `deleted: 0` is logged as a warning by the shared removal path; only the stack and the
    # progress counter matter here.
    processor.index.decrease_progress.assert_not_called()


def test_a_real_removal_failure_still_raises_and_is_logged_with_its_stack(processor, codemie_logs):
    """The counterpart to the test above, and its control.

    It pins the behaviour that must survive -- a genuine failure still raises
    and still logs a stack -- and, by finding that stack through the same
    message search, proves the "no traceback" assertion above is looking
    somewhere a traceback can actually appear rather than passing vacuously.
    """
    processor.client.delete_by_query.side_effect = ConnectionError("connection refused")

    with pytest.raises(ConnectionError):
        processor.remove_single_entity("some-id", "Some Name")

    assert [
        r for r in codemie_logs.records if "Traceback (most recent call last)" in r.getMessage()
    ], "a real removal failure must still be logged with its stack"


def test_a_successful_removal_still_decrements_progress(processor):
    processor.client.delete_by_query.return_value = {"deleted": 1}

    processor.remove_single_entity("some-id", "Some Name")

    processor.index.decrease_progress.assert_called_once_with(chunks_count=1, processed_file="Some Name")
