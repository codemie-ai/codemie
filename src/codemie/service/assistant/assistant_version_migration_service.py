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

"""Service for migrating legacy system_prompt_history to versions"""

from codemie.configs.logger import logger
from codemie.rest_api.models.assistant import Assistant, AssistantConfiguration, SystemPromptHistory


class AssistantVersionMigrationService:
    """Service for migrating legacy data to versioned model"""

    @classmethod
    def migrate_assistant_to_versions(cls, assistant: Assistant) -> int:
        """
        Migrate an assistant from system_prompt_history to version records.

        Args:
            assistant: The assistant to migrate

        Returns:
            Number of versions created
        """
        if not assistant.system_prompt_history:
            logger.info(f"Assistant {assistant.id} has no system_prompt_history to migrate")
            # Create single version from current state
            return cls._create_single_version_from_current(assistant)

        logger.info(f"Migrating assistant {assistant.id} with {len(assistant.system_prompt_history)} history entries")

        versions_created = 0
        version_number = 1

        # Migrate history entries (oldest first)
        for history_entry in reversed(assistant.system_prompt_history):
            cls._create_version_from_history(
                assistant=assistant, version_number=version_number, history_entry=history_entry
            )
            versions_created += 1
            version_number += 1

        # Create final version from current state
        cls._create_version_from_current(assistant=assistant, version_number=version_number)
        versions_created += 1

        # Update master record
        assistant.version_count = versions_created
        assistant.update()

        logger.info(f"Migration complete for assistant {assistant.id}. Created {versions_created} versions")

        return versions_created

    @classmethod
    def _create_single_version_from_current(cls, assistant: Assistant) -> int:
        """Create a single version from assistant's current state"""
        config = AssistantConfiguration(
            assistant_id=assistant.id,
            version_number=1,
            description=assistant.description,
            system_prompt=assistant.system_prompt,
            llm_model_type=assistant.llm_model_type,
            temperature=assistant.temperature,
            top_p=assistant.top_p,
            context=assistant.context,
            toolkits=assistant.toolkits,
            mcp_servers=assistant.mcp_servers,
            assistant_ids=assistant.assistant_ids,
            conversation_starters=assistant.conversation_starters,
            bedrock=assistant.bedrock,
            agent_card=assistant.agent_card,
            created_by=assistant.created_by,
            created_date=assistant.created_date,
            change_notes="Initial version (migrated)",
        )
        config.save()

        assistant.version_count = 1
        assistant.update()

        return 1

    @classmethod
    def _create_version_from_history(
        cls, assistant: Assistant, version_number: int, history_entry: SystemPromptHistory
    ) -> AssistantConfiguration:
        """Create a version record from a history entry"""
        config = AssistantConfiguration(
            assistant_id=assistant.id,
            version_number=version_number,
            # Use system prompt from history
            system_prompt=history_entry.system_prompt,
            # Copy all other fields from current assistant state
            description=assistant.description,
            llm_model_type=assistant.llm_model_type,
            temperature=assistant.temperature,
            top_p=assistant.top_p,
            context=assistant.context,
            toolkits=assistant.toolkits,
            mcp_servers=assistant.mcp_servers,
            assistant_ids=assistant.assistant_ids,
            conversation_starters=assistant.conversation_starters,
            bedrock=assistant.bedrock,
            agent_card=assistant.agent_card,
            # Use timestamp and user from history
            created_date=history_entry.date,
            created_by=history_entry.created_by,
            change_notes="Migrated from system_prompt_history",
        )
        config.save()
        return config

    @classmethod
    def _create_version_from_current(cls, assistant: Assistant, version_number: int) -> AssistantConfiguration:
        """Create a version record from assistant's current state"""
        config = AssistantConfiguration(
            assistant_id=assistant.id,
            version_number=version_number,
            description=assistant.description,
            system_prompt=assistant.system_prompt,
            llm_model_type=assistant.llm_model_type,
            temperature=assistant.temperature,
            top_p=assistant.top_p,
            context=assistant.context,
            toolkits=assistant.toolkits,
            mcp_servers=assistant.mcp_servers,
            assistant_ids=assistant.assistant_ids,
            conversation_starters=assistant.conversation_starters,
            bedrock=assistant.bedrock,
            agent_card=assistant.agent_card,
            created_by=assistant.created_by,
            created_date=assistant.updated_date or assistant.created_date,
            change_notes="Current version (migrated)",
        )
        config.save()
        return config

    @classmethod
    def migrate_all_assistants(cls) -> dict:
        """
        Migrate all assistants to versioned model.

        Returns:
            Summary statistics of migration
        """
        logger.info("Starting migration of all assistants to versioned model")

        assistants = Assistant.get_all()

        stats = {
            'total_assistants': len(assistants),
            'migrated': 0,
            'skipped': 0,
            'errors': 0,
            'total_versions_created': 0,
        }

        for assistant in assistants:
            try:
                # Check if already migrated
                existing_versions = AssistantConfiguration.count_versions(assistant.id)
                if existing_versions > 0:
                    logger.info(f"Assistant {assistant.id} already migrated, skipping")
                    stats['skipped'] += 1
                    continue

                versions_created = cls.migrate_assistant_to_versions(assistant)
                stats['migrated'] += 1
                stats['total_versions_created'] += versions_created

            except Exception as e:
                logger.error(f"Error migrating assistant {assistant.id}: {str(e)}", exc_info=True)
                stats['errors'] += 1

        logger.info(f"Migration complete. Stats: {stats}")
        return stats
