"""Migrate autonomous WF to subassistants

Revision ID: 1935354d6b50
Revises: 3ecffd20260e
Create Date: 2025-09-09 12:11:10.484795

"""

from typing import Sequence, Union
from pydantic import BaseModel
from alembic import op
import sqlalchemy as sa
from alembic.context import get_context
from sqlalchemy import text, bindparam
import os
import sys
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import uuid4


from codemie.service.assistant_service import AssistantService

from codemie_sdk.models.assistant import ToolKitDetails, ToolDetails
from codemie_sdk.models.integration import IntegrationType, Integration, CredentialTypes


# revision identifiers, used by Alembic.
revision: str = '1935354d6b50'
down_revision: Union[str, None] = '09b0731e5066'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# Constants
AUTONOMOUS_MODE = "AUTONOMOUS"
DEFAULT_MODEL = "gpt-4o"
ASSISTANT_TYPE = "CODEMIE"
SYSTEM_CREATOR = "system"


class MigrationError(Exception):
    """Custom exception for migration-related errors."""

    pass


class MockUser(BaseModel):
    is_admin: bool = True


class DatabaseManager:
    """Handles all database operations."""

    @staticmethod
    def get_connection():
        """
        Use Alembic's connection for database operations.
        """
        logger.debug("Getting database connection from Alembic context")
        try:
            context = get_context()
            connection = context.bind
            logger.debug("Successful retrieval of database connection from Alembic context")
            return connection
        except Exception as e:
            logger.error(f"Failed to get database connection from Alembic context: {e}")
            raise MigrationError(f"Database connection failed: {e}")

    @staticmethod
    def fetch_autonomous_workflows() -> List[Dict[str, Any]]:
        """
        Returns a list of dicts with keys: 'assistants', 'created_by', 'project', 'supervisor_prompt', 'name'
        from the workflows table where mode = 'autonomous'.
        """
        query = text("""
            SELECT id, description, assistants, created_by, project, supervisor_prompt, name, icon_url
            FROM codemie.workflows
            WHERE mode = :mode
        """)

        logger.info("Fetching autonomous workflows from database")
        try:
            conn = DatabaseManager.get_connection()
            result = conn.execute(query, {"mode": AUTONOMOUS_MODE})
            rows = [dict(row._mapping) for row in result.fetchall()]
            logger.info(f"Found {len(rows)} autonomous workflows")
            return rows
        except Exception as e:
            logger.error(f"Database error while fetching autonomous workflows: {e}")
            return []

    @staticmethod
    def get_integration_by_project_and_alias(integration_alias: str, project: str) -> Optional[Integration]:
        """
        Retrieve integration settings by project and alias.
        """
        query = text("""
            SELECT * 
            FROM codemie.settings
            WHERE project_name = :project_name AND alias = :alias
        """)
        logger.debug(f"Fetching integration for project '{project}' and alias '{integration_alias}'")

        try:
            conn = DatabaseManager.get_connection()
            result = conn.execute(query, {"project_name": project, "alias": integration_alias})
            rows = result.fetchall()

            if not rows:
                logger.warning(f"No integration found for project '{project}' and alias '{integration_alias}'")
                return None

            entry = dict(rows[0]._mapping)
            logger.debug(f"Found integration: {entry['alias']}")

            credential_type = CredentialTypes[entry["credential_type"]]
            setting_type = IntegrationType[entry["setting_type"]]

            # Remove enum fields before creating Integration object
            entry_copy = dict(entry)
            del entry_copy["credential_type"]
            del entry_copy["setting_type"]

            integration = Integration(**entry_copy, credential_type=credential_type, setting_type=setting_type)
            return integration

        except Exception as e:
            logger.error(f"Error retrieving integration for project '{project}' and alias '{integration_alias}': {e}")

    @staticmethod
    def create_assistant_direct(
        name: str,
        model: str,
        system_prompt: str,
        description: str,
        toolkits: List[ToolKitDetails],
        slug: str,
        project: str,
        assistant_ids: List[str],
        icon_url: str,
    ) -> str:
        """
        Create an assistant by directly inserting into the database using SQLAlchemy JSONB.
        Returns the generated UUID of the new assistant.
        """
        assistant_id = str(uuid4())
        current_time = datetime.now()

        logger.info(f"Creating assistant '{name}' with ID: {assistant_id}")

        # Convert toolkits to JSON format
        toolkits_json = [toolkit.model_dump() for toolkit in toolkits] if toolkits else []

        try:
            conn = DatabaseManager.get_connection()

            query = text("""
                INSERT INTO codemie.assistants (
                    id, date, update_date, name, description, system_prompt, system_prompt_history,
                    project, llm_model_type, toolkits, conversation_starters, shared, is_react, 
                    is_global, created_date, creator, slug, context, mcp_servers, assistant_ids,
                    nested_assistants, type, unique_users_count, unique_likes_count, unique_dislikes_count, source, icon_url
                ) VALUES (
                    :id, :date, :update_date, :name, :description, :system_prompt, :system_prompt_history,
                    :project, :llm_model_type, :toolkits, :conversation_starters, :shared, :is_react,
                    :is_global, :created_date, :creator, :slug, :context, :mcp_servers, :assistant_ids,
                    :nested_assistants, :type, :unique_users_count, :unique_likes_count, :unique_dislikes_count, :source, :icon_url
                )
            """)

            conn.execute(
                query,
                {
                    "id": assistant_id,
                    "date": current_time,
                    "update_date": current_time,
                    "name": name,
                    "description": description,
                    "system_prompt": system_prompt,
                    "system_prompt_history": json.dumps([]),  # Explicitly convert to JSON string
                    "project": project,
                    "llm_model_type": model,
                    "toolkits": json.dumps(toolkits_json),  # Explicitly convert to JSON string
                    "conversation_starters": json.dumps([]),  # Explicitly convert to JSON string
                    "shared": False,
                    "is_react": False,
                    "is_global": False,
                    "created_date": current_time,
                    "creator": SYSTEM_CREATOR,
                    "slug": slug,
                    "context": json.dumps([]),  # Explicitly convert to JSON string
                    "mcp_servers": json.dumps([]),  # Explicitly convert to JSON string
                    "assistant_ids": json.dumps(assistant_ids),  # Explicitly convert to JSON string
                    "nested_assistants": json.dumps([]),  # Explicitly convert to JSON string
                    "type": ASSISTANT_TYPE,
                    "unique_users_count": 0,
                    "unique_likes_count": 0,
                    "unique_dislikes_count": 0,
                    "source": "AUTONOMOUS_WORKFLOW",
                    "icon_url": icon_url if icon_url else None,
                },
            )

            logger.info(f"Successfully created assistant '{name}' with ID: {assistant_id}")
            return assistant_id

        except Exception as e:
            logger.error(f"Database error while creating assistant '{name}': {e}")
            raise MigrationError(f"Failed to create assistant: {e}")

    @staticmethod
    def update_assistant_ownership(assistant_id: str, created_by: Dict[str, Any], project: str):
        """
        Update assistant ownership information.
        """
        query = text("""
            UPDATE codemie.assistants
            SET created_by = CAST(:created_by AS JSONB), project = :project
            WHERE id = :assistant_id
        """)

        logger.debug(f"Updating ownership for assistant {assistant_id}")

        try:
            conn = DatabaseManager.get_connection()
            created_by_format = {
                "id": created_by["user_id"],
                "name": created_by["name"],
                "username": created_by["username"],
            }

            # Convert the dictionary to JSON string
            conn.execute(
                query,
                {
                    "created_by": json.dumps(created_by_format),  # Convert dict to JSON string
                    "project": project,
                    "assistant_id": assistant_id,
                },
            )
            logger.debug(f"Successfully updated ownership for assistant {assistant_id}")

        except Exception as e:
            logger.error(f"Error updating ownership for assistant {assistant_id}: {e}")
            raise MigrationError(f"Failed to update assistant ownership: {e}")


class ToolkitManager:
    """Handles toolkit and tool-related operations."""

    def __init__(self, tools_data_file: str = "tools_data.json"):
        """Initialize with tools data from JSON file."""
        logger.info(f"Loading tools data from {tools_data_file}")
        user = MockUser()
        tools_info = AssistantService.get_tools_info(user, show_for_ui=True)

        self.tool_toolkits = json.loads(json.dumps(tools_info))
        logger.info(f"Loaded {len(self.tool_toolkits)} toolkit groups")

    def find_toolkit_for_tool(self, tool_name: str) -> Optional[str]:
        """
        Return the name of the toolkit that contains a tool with the given name.
        If multiple toolkits contain the tool, the first match is returned.
        If not found, returns None.
        """
        logger.debug(f"Searching for toolkit containing tool '{tool_name}'")

        for group in self.tool_toolkits:
            for tool in group.get("tools", []):
                if tool.get("name") == tool_name:
                    toolkit_name = group.get("toolkit")
                    logger.debug(f"Found tool '{tool_name}' in toolkit '{toolkit_name}'")
                    return toolkit_name

        logger.warning(f"No toolkit found for tool '{tool_name}'")
        return None

    def convert_workflow_tools_to_toolkit_with_settings(
        self, tools: List[Dict[str, Any]], project_name: str
    ) -> List[ToolKitDetails]:
        """
        Convert workflow tools to ToolKitDetails with integration settings.
        """
        logger.debug(f"Converting {len(tools)} tools to toolkits for project '{project_name}'")

        toolkits = []
        for tool in tools:
            tool_name = tool["name"]
            logger.debug(f"Processing tool: {tool_name}")

            integration = None
            if alias := tool.get("integration_alias"):
                logger.debug(f"Tool '{tool_name}' has integration alias: {alias}")
                integration = DatabaseManager.get_integration_by_project_and_alias(alias, project_name)
                if not integration:
                    logger.warning(f"Integration not found for alias '{alias}' in project '{project_name}'")
                    continue

            toolkit_name = self.find_toolkit_for_tool(tool_name)
            if not toolkit_name:
                logger.warning(f"Skipping tool '{tool_name}' - no toolkit found")
                continue

            settings = integration.model_dump() if integration else {}
            tool_details = ToolDetails(name=tool_name, settings=settings)
            toolkit = ToolKitDetails(toolkit=toolkit_name, tools=[tool_details])
            toolkits.append(toolkit)

        logger.debug(f"Created {len(toolkits)} toolkits")
        return toolkits


class WorkflowMigrator:
    """Main class for handling workflow migration."""

    def __init__(self):
        """Initialize the migrator with required components."""
        logger.info("Initializing WorkflowMigrator")

    def parse_workflow(self, workflow_data: Dict[str, Any]) -> tuple:
        """
        Parse workflow data into individual components.
        """
        logger.debug(f"Parsing workflow: {workflow_data.get('name', 'Unknown')}")

        return (
            workflow_data["id"],
            workflow_data["description"],
            workflow_data["assistants"],
            workflow_data["created_by"],
            workflow_data["supervisor_prompt"],
            workflow_data["project"],
            workflow_data["name"],
            workflow_data["icon_url"],
        )

    def create_or_extract_assistants(
        self, assistants: List[Dict[str, Any]], project_name: str, workflow_name: str, workflow_id: str, icon_url: str
    ) -> tuple[List[str], List[str]]:
        """
        Create new assistants or extract existing assistant IDs.
        """
        logger.info(f"Processing {len(assistants)} assistants for workflow '{workflow_name}'")

        created_ids = []
        existed_ids = []
        for i, assistant in enumerate(assistants):
            logger.debug(f"Processing assistant {i + 1}/{len(assistants)}")

            if assistant_id := assistant.get('assistant_id'):
                logger.debug(f"Using existing assistant ID: {assistant_id}")
                existed_ids.append(assistant_id)
            else:
                logger.debug("Creating new assistant")
                toolkits = self.toolkit_manager.convert_workflow_tools_to_toolkit_with_settings(
                    tools=assistant["tools"], project_name=project_name
                )

                assistant_name = assistant["id"]

                new_assistant_id = DatabaseManager.create_assistant_direct(
                    name=f"Sub-{assistant_name}",
                    model=assistant["model"],
                    system_prompt=assistant["system_prompt"],
                    description="Sub assistant of autonomous workflow",
                    toolkits=toolkits,
                    slug=f"wf-{assistant_name}-{workflow_id}",
                    project=project_name,
                    assistant_ids=[],
                    icon_url=icon_url,
                )
                created_ids.append(new_assistant_id)

        logger.info(f"Processed assistants. Total IDs: {len(created_ids + existed_ids)}")
        return created_ids, existed_ids

    def update_assistants_ownership(self, assistant_ids: List[str], project: str, created_by: Dict[str, Any]):
        """
        Update ownership for multiple assistants.
        """
        logger.info(f"Updating ownership for {len(assistant_ids)} assistants")

        for assistant_id in assistant_ids:
            try:
                DatabaseManager.update_assistant_ownership(assistant_id, created_by, project)
            except MigrationError as e:
                logger.error(f"Failed to update ownership for assistant {assistant_id}: {e}")
                # Continue with other assistants rather than failing completely

    def migrate_workflow(
        self,
        workflow_id: str,
        description: str,
        assistants: List[Dict[str, Any]],
        created_by: Dict[str, Any],
        supervisor_prompt: str,
        project: str,
        name: str,
        icon_url: str,
    ):
        """
        Migrate a single workflow.
        """
        logger.info(f"Starting migration for workflow '{name}' in project '{project}'")

        try:
            # Step 1: Create or extract sub assistants
            created_ids, existed_ids = self.create_or_extract_assistants(
                assistants, project, name, workflow_id, icon_url
            )

            sub_assistants = created_ids + existed_ids
            # Step 2: Create orchestrator
            logger.info(f"Creating orchestrator for workflow '{name}'")
            orchestrator_id = DatabaseManager.create_assistant_direct(
                name=f"Orchestrator-{name}",
                model=DEFAULT_MODEL,
                system_prompt=supervisor_prompt,
                description=description,
                toolkits=[],
                slug=f"wf-{name}-{workflow_id}",
                project=project,
                assistant_ids=sub_assistants,
                icon_url=icon_url,
            )

            # Step 3: Update ownership for all assistants
            new_assistant_ids = created_ids + [orchestrator_id]
            self.update_assistants_ownership(new_assistant_ids, project, created_by)

        except Exception as e:
            logger.error(f"Failed to migrate workflow '{name}': {e}")
            raise MigrationError(f"Workflow migration failed: {e}")

    def run_migration(self):
        """
        Main migration process.
        """
        logger.info("Starting workflow migration process")

        try:
            # Fetch all autonomous workflows
            workflows = DatabaseManager.fetch_autonomous_workflows()

            if not workflows:
                logger.info("No autonomous workflows found, skipping migration")
                return

            logger.info(f"Found {len(workflows)} workflows to migrate")

            # Request tools only after checking that we have workflows for migration
            self.toolkit_manager = ToolkitManager()

            # Process each workflow
            successful_migrations = 0
            failed_migrations = 0

            for i, workflow in enumerate(workflows, start=1):
                logger.info(f"Processing workflow {i}/{len(workflows)}")
                workflow_id, description, assistants, created_by, supervisor_prompt, project, name, icon_url = (
                    self.parse_workflow(workflow)
                )
                try:
                    self.migrate_workflow(
                        workflow_id, description, assistants, created_by, supervisor_prompt, project, name, icon_url
                    )
                    successful_migrations += 1

                except Exception as e:
                    logger.error(f"Failed to process workflow {workflow_id}: {e}")
                    failed_migrations += 1

            # Summary
            logger.info(f"Migration completed. Successful: {successful_migrations}, Failed: {failed_migrations}")

            if failed_migrations > 0:
                logger.warning(f"{failed_migrations} workflows failed to migrate. Check logs for details.")

        except Exception as e:
            logger.error(f"Migration process failed: {e}")
            raise MigrationError(f"Migration process failed: {e}")


def run_migration():
    """Main entry point."""
    try:
        migrator = WorkflowMigrator()
        migrator.run_migration()
        logger.info("Migration process completed successfully")

    except MigrationError as e:
        logger.error(f"Migration failed: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


def upgrade() -> None:
    """Upgrade schema: Add a nullable 'source' column."""
    op.add_column('assistants', sa.Column('source', sa.String(), nullable=True))
    run_migration()


def downgrade() -> None:
    """Downgrade schema: Remove rows with 'AUTONOMOUS_WORKFLOW' source, then drop the 'source' column."""
    # Remove all rows where source is 'AUTONOMOUS_WORKFLOW'
    op.execute("DELETE FROM assistants WHERE source = 'AUTONOMOUS_WORKFLOW'")

    # Now drop the column
    op.drop_column('assistants', 'source')
