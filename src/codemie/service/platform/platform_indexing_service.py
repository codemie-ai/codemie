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

from typing import Dict, Type, Optional
from codemie.configs import logger, config
from codemie.datasource.platform.base_platform_processor import BasePlatformDatasourceProcessor
from codemie.datasource.platform.assistant_datasource_processor import AssistantDatasourceProcessor
from codemie.rest_api.security.user import User


class PlatformIndexingService:
    """
    Service for synchronizing platform datasources on node startup.
    Similar to ToolkitLookupService.index_all_tools().

    This service:
    - Syncs all platform datasources (assistants, workflows, datasources) on startup
    - Provides methods for incremental updates (publish/unpublish)
    - Manages platform datasource lifecycle
    """

    # Map datasource name -> processor class
    PLATFORM_DATASOURCES: Dict[str, Type[BasePlatformDatasourceProcessor]] = {
        config.PLATFORM_MARKETPLACE_DATASOURCE_NAME: AssistantDatasourceProcessor,
    }

    @classmethod
    def sync_all_platform_datasources(cls, user: Optional['User'] = None) -> Dict[str, int]:
        """
        Synchronize all platform datasources on node startup.

        Args:
            user: Optional user who triggered the sync (for admin reindex). If None, uses SYSTEM_USER.

        Returns:
            Dict mapping datasource name to number of indexed documents
        """
        logger.info("Starting platform datasources synchronization")

        results = {}

        for datasource_name, processor_class in cls.PLATFORM_DATASOURCES.items():
            try:
                count = cls._sync_datasource(datasource_name, processor_class, user=user)
                results[datasource_name] = count
                logger.info(
                    f"Synced {datasource_name}: {count} documents",
                    extra={"datasource": datasource_name, "count": count},
                )
            except Exception as e:
                logger.error(
                    f"Failed to sync {datasource_name}: {e}",
                    extra={"datasource": datasource_name, "error": str(e)},
                    exc_info=True,
                )
                results[datasource_name] = 0

        total = sum(results.values())
        logger.info(
            f"Platform datasources sync completed. Total: {total} documents",
            extra={"total": total, "results": results},
        )

        return results

    @classmethod
    def _sync_datasource(
        cls,
        datasource_name: str,
        processor_class: Type[BasePlatformDatasourceProcessor],
        user: Optional['User'] = None,
    ) -> int:
        """
        Synchronize a single datasource.

        Args:
            datasource_name: Name of the datasource
            processor_class: Processor class to use
            user: Optional user who triggered the sync. If None, SYSTEM_USER will be used by processor.

        Returns:
            Number of documents indexed
        """
        logger.info(f"Syncing platform datasource: {datasource_name}", extra={"datasource": datasource_name})

        # Create processor
        processor = processor_class(
            datasource_name=datasource_name,
            user=user,
        )

        processor.process()

        count = processor.index.current_state if processor.index else 0

        logger.info(
            f"Completed syncing {datasource_name}",
            extra={"datasource": datasource_name, "indexed_count": count},
        )

        return count

    @classmethod
    def index_single_assistant(cls, assistant_id: str, user: Optional['User'] = None, is_update: bool = False) -> None:
        """
        Index a single assistant (called on publish or update).

        Args:
            assistant_id: ID of the assistant to index
            user: User who triggered the publish action (for cost tracking and logging)
            is_update: If True, this is an update of an existing assistant (won't increment progress counters)
        """
        logger.info(f"Indexing single assistant: {assistant_id}", extra={"assistant_id": assistant_id})

        processor = AssistantDatasourceProcessor(
            datasource_name=config.PLATFORM_MARKETPLACE_DATASOURCE_NAME,
            user=user,
        )
        processor.index_single_entity(assistant_id, is_update=is_update)

        logger.info(f"Successfully indexed assistant {assistant_id}", extra={"assistant_id": assistant_id})

    @classmethod
    def remove_single_assistant(cls, assistant_id: str, assistant_name: str, user: Optional['User'] = None) -> None:
        """
        Remove a single assistant from index (called on unpublish).

        Args:
            assistant_id: ID of the assistant to remove
            user: User who triggered the unpublish action (for logging)
        """
        logger.info(f"Removing single assistant: {assistant_id}", extra={"assistant_id": assistant_id})

        processor = AssistantDatasourceProcessor(
            datasource_name=config.PLATFORM_MARKETPLACE_DATASOURCE_NAME,
            user=user,
        )
        processor.remove_single_entity(assistant_id, assistant_name)

        logger.info(f"Successfully removed assistant {assistant_id}", extra={"assistant_id": assistant_id})
