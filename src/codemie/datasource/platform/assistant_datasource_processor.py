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

from codemie.configs import logger
from codemie.datasource.platform.base_platform_processor import BasePlatformDatasourceProcessor
from codemie.datasource.loader.platform.assistant_loader import AssistantLoader
from codemie.service.constants import FullDatasourceTypes


class AssistantDatasourceProcessor(BasePlatformDatasourceProcessor):
    """
    Processor for indexing assistants into marketplace datasource.

    This processor:
    - Uses AssistantLoader to fetch and sanitize published assistants
    - Indexes them into Elasticsearch for marketplace search
    - Supports full sync (all assistants) and incremental updates (single assistant)
    """

    INDEX_TYPE = FullDatasourceTypes.PLATFORM_ASSISTANT.value

    def _init_loader(self):
        """Initialize AssistantLoader."""
        return AssistantLoader()

    def _delete_stale_ids(self, candidate_ids: set) -> int:
        """Delete each candidate that is still unpublished, re-checked and deleted under one lock.

        The full reindex's `live_ids` snapshot and this prune step are not one
        transaction: a publish that commits (and indexes, via the incremental
        `index_assistant` path) in that window is a false positive here -- its
        document exists and is correct, it is just not in the stale snapshot.
        So each id is re-read and, if still not published, deleted *inside* the
        per-assistant advisory lock `marketplace_index_sync` takes for its own
        writes. A publish therefore either commits and indexes before the
        re-check (and the document is kept) or waits until the delete is done
        (and re-indexes it afterwards); it can no longer fall between the two.

        An id whose state cannot be read (`None`) is skipped, not deleted: the
        next sync will look at it again, whereas a wrong delete loses a live
        document until then.
        """
        from codemie.service.marketplace_management.marketplace_index_sync import _committed_state

        deleted = 0
        for assistant_id in sorted(candidate_ids):
            with _committed_state(assistant_id) as published:
                if published is None:
                    logger.warning(
                        "Could not read the state of prune candidate %s; not deleting",
                        assistant_id,
                        extra={"assistant_id": assistant_id},
                    )
                    continue
                if published:
                    logger.info(
                        "Prune candidate %s is published again as of the re-check; not deleting",
                        assistant_id,
                        extra={"assistant_id": assistant_id},
                    )
                    continue
                deleted += self._delete_ids_from_index({assistant_id})
        return deleted
