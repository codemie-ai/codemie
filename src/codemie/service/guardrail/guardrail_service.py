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

from typing import List, Literal, Optional, Tuple
from fastapi import status
from sqlalchemy.exc import IntegrityError

from codemie.core.ability import Ability, Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.guardrail import (
    Guardrail,
    GuardrailAssignment,
    GuardrailAssignmentItem,
    GuardrailAssignmentRequestResponse,
    GuardrailEntity,
    GuardrailMode,
    GuardrailSource,
)
from codemie.rest_api.security.user import User
from codemie.service.guardrail.guardrail_repository import GuardrailRepository
from codemie.configs.logger import logger
from codemie.service.guardrail.utils import EntityConfig, batch_content


# Entity type configuration with lazy model class loading
ENTITY_TYPE_CONFIG = {
    GuardrailEntity.ASSISTANT: {
        "entity_name": "Assistant",
        "model_class": lambda: _get_assistant_class(),
        "project_field_name": "project",
    },
    GuardrailEntity.WORKFLOW: {
        "entity_name": "Workflow",
        "model_class": lambda: _get_workflow_class(),
        "project_field_name": "project",
    },
    GuardrailEntity.KNOWLEDGEBASE: {
        "entity_name": "Datasource",
        "model_class": lambda: _get_index_class(),
        "project_field_name": "project_name",
    },
}


def _get_assistant_class():
    """Lazy import Assistant to avoid circular dependency."""
    from codemie.rest_api.models.assistant import Assistant

    return Assistant


def _get_workflow_class():
    """Lazy import WorkflowConfig to avoid circular dependency."""
    from codemie.core.workflow_models.workflow_config import WorkflowConfig

    return WorkflowConfig


def _get_index_class():
    """Lazy import IndexInfo to avoid circular dependency."""
    from codemie.rest_api.models.index import IndexInfo

    return IndexInfo


class GuardrailService:
    @staticmethod
    def get_guardrail_assignments(user: User, guardrail_id: str) -> dict:
        """
        Get all assignments for a guardrail.

        Returns assignments in the same structure as the bulk assignment request,
        organized by entity type (project, assistants, workflows, datasources).
        """
        repo = GuardrailRepository()
        assignments = repo.get_all_assignments_for_guardrail(guardrail_id)

        # Organizing assignments by entity type - using dicts to group settings by entity_id
        result = {
            "project": {"settings": []},
            "assistants": {"settings": [], "items": {}},
            "workflows": {"settings": [], "items": {}},
            "datasources": {"settings": [], "items": {}},
        }

        # Track unique entity IDs to load
        entity_ids_to_load = {
            GuardrailEntity.ASSISTANT: set(),
            GuardrailEntity.WORKFLOW: set(),
            GuardrailEntity.KNOWLEDGEBASE: set(),
        }

        # First pass: organize assignments and collect entity IDs
        for assignment in assignments:
            settings_dict = {
                "mode": assignment.mode,
                "source": assignment.source,
            }

            GuardrailService._process_get_guardrail_assignment(assignment, settings_dict, result)

            # Collect entity IDs for entity-specific assignments
            if assignment.entity_type in entity_ids_to_load and assignment.scope is None:
                entity_ids_to_load[assignment.entity_type].add(assignment.entity_id)

        # Load entity details
        entity_details = GuardrailService._load_entity_details(user, entity_ids_to_load)

        # Transform items dict to list format with entity details
        # Filter out None values (entities not found)
        return {
            "project": {"settings": result["project"]["settings"]},
            "assistants": {
                "settings": result["assistants"]["settings"],
                "items": [
                    item
                    for entity_id, settings_list in result["assistants"]["items"].items()
                    if (
                        item := GuardrailService._build_entity_item(
                            entity_id,
                            settings_list,
                            entity_details[GuardrailEntity.ASSISTANT].get(entity_id),
                            GuardrailEntity.ASSISTANT,
                        )
                    )
                    is not None
                ],
            },
            "workflows": {
                "settings": result["workflows"]["settings"],
                "items": [
                    item
                    for entity_id, settings_list in result["workflows"]["items"].items()
                    if (
                        item := GuardrailService._build_entity_item(
                            entity_id,
                            settings_list,
                            entity_details[GuardrailEntity.WORKFLOW].get(entity_id),
                            GuardrailEntity.WORKFLOW,
                        )
                    )
                    is not None
                ],
            },
            "datasources": {
                "settings": result["datasources"]["settings"],
                "items": [
                    item
                    for entity_id, settings_list in result["datasources"]["items"].items()
                    if (
                        item := GuardrailService._build_entity_item(
                            entity_id,
                            settings_list,
                            entity_details[GuardrailEntity.KNOWLEDGEBASE].get(entity_id),
                            GuardrailEntity.KNOWLEDGEBASE,
                        )
                    )
                    is not None
                ],
            },
        }

    @staticmethod
    def sync_guardrail_assignments_for_entity(
        user: User,
        entity_type: GuardrailEntity,
        entity_id: str,
        entity_project_name: str,
        guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None,
    ):
        """
        Sync guardrail assignments for a specific entity by comparing existing vs requested assignments.

        The database updates are not done in a single transactions, the method is idempotent and uses
        eventual consistency.

        **Caller Responsibilities (must validate before calling):**
        - Entity exists (verify entity_id is valid)
        - User has WRITE permission on the entity
        - project_name correctly matches the entity's project

        **This Method Validates:**
        - Each guardrail exists (by guardrail_id)
        - Each guardrail belongs to the same project as the entity
        - User has DELETE permission on each guardrail being assigned or removed
        - No cross-project assignments (guardrail.project_name == project_name)

        **Behavior:**
        - If guardrail_assignments is None: no changes are made (preserves existing assignments)
        - If guardrail_assignments is []: all assignments are removed
        - Creates new assignments that don't exist
        - Keeps existing assignments that match the request
        - Deletes assignments that are no longer in the request
        - Only validates permissions for guardrails that will actually change (optimization)
        """
        if guardrail_assignments is None:
            # None means "don't touch existing assignments"
            return

        repo = GuardrailRepository()

        # Step 1: Get all existing assignments for this entity
        existing_assignments = repo.get_guardrail_assignments_for_entity(entity_type, entity_id)

        # Step 2: Build existing keys and ID map
        existing_keys = set()
        existing_assignments_map: dict[tuple, str] = {}
        keys_to_delete = set()

        for assignment in existing_assignments:
            key = (
                assignment.guardrail_id,
                assignment.source,
                assignment.mode,
                assignment.scope,
                assignment.project_name,
            )
            existing_assignments_map[key] = assignment.id

            # If assignment belongs to a different project, mark for deletion
            if assignment.project_name != entity_project_name:
                keys_to_delete.add(key)
            else:
                # Only add to existing_keys if it matches the current project
                existing_keys.add(key)

        # Step 3: Build a set of "desired" assignment keys from the request
        # Key format: (guardrail_id, source, mode, scope)
        desired_keys = set()

        for assignment_item in guardrail_assignments:
            # Add to desired set (scope is None for entity-specific assignments)
            desired_keys.add(
                (
                    assignment_item.guardrail_id,
                    assignment_item.source,
                    assignment_item.mode,
                    None,  # scope is None for direct entity assignments
                    entity_project_name,
                )
            )

        # Step 4: Determine what to create and what to delete (additional to cross-project deletes)
        keys_to_create = desired_keys - existing_keys
        keys_to_delete.update(existing_keys - desired_keys)

        # Step 5: Validate permissions only for guardrails that will change
        # Collect unique guardrail IDs that are being added or removed
        guardrail_ids_to_validate = set()

        # Guardrails being added (keys_to_create)
        for key in keys_to_create:
            guardrail_id, _, _, _, _ = key
            guardrail_ids_to_validate.add(guardrail_id)

        # Guardrails being removed (keys_to_delete)
        for key in keys_to_delete:
            guardrail_id, _, _, _, assignment_project_name = key

            # No need to validate deletion of assignments when the project mismatches, just delete
            if entity_project_name == assignment_project_name:
                guardrail_ids_to_validate.add(guardrail_id)

        # Validate permissions for each guardrail that will change
        for guardrail_id_to_validate in guardrail_ids_to_validate:
            GuardrailService._validate_guardrail_user_and_project_permissions(
                user=user,
                guardrail_id=guardrail_id_to_validate,
                project_name=entity_project_name,
            )

        # Step 6: Create new assignments
        for key in keys_to_create:
            guardrail_id, source, mode, scope, _ = key
            try:
                repo.assign_guardrail_to_entity(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    guardrail_id=guardrail_id,
                    source=source,
                    mode=mode,
                    project_name=entity_project_name,
                    user=user,
                    scope=scope,
                )
            except IntegrityError:
                logger.warning(
                    f"Guardrail assignment already exists (guardrail_id={guardrail_id}, "
                    f"entity_type={entity_type}, entity_id={entity_id}, source={source}, "
                    f"mode={mode}, scope={scope}). Skipping duplicate."
                )
                # Do not fail the whole process, just ignore duplicates
                continue

        # Step 7: Delete by IDs
        if keys_to_delete:
            assignment_ids_to_delete = [existing_assignments_map[key] for key in keys_to_delete]
            repo.remove_guardrails_assignments_by_ids(assignment_ids_to_delete)

    @staticmethod
    def sync_guardrail_bulk_assignments(
        guardrail_id: str,
        guardrail_project_name: str,
        user: User,
        request: GuardrailAssignmentRequestResponse,
    ) -> tuple[int, int, List[str]]:
        """
        Sync all assignments for a guardrail across multiple entities based on a bulk assignment request.

        The database updates are not done in a single transactions, the method is idempotent and uses
        eventual consistency.

        **Caller Responsibilities (must validate before calling):**
        - Guardrail exists (verify guardrail_id is valid)
        - User has DELETE permission on the guardrail
        - project_name correctly matches the guardrail's project

        **This Method Validates:**
        - User is admin for project-level assignments
        - Each entity exists (by entity_id)
        - User has WRITE permission on each entity being assigned
        - Each entity belongs to the guardrail's project (no cross-project assignments)

        **Behavior:**
        - Retrieves all existing assignments for this guardrail
        - Compares with requested assignments
        - Creates new assignments that don't exist
        - Keeps existing assignments that match the request
        - Deletes assignments that are no longer in the request
        - Returns success/failure counts for bulk operations

        Returns:
            Tuple of (success_count, failed_count, errors):
            - success_count: Number of assignments successfully created
            - failed_count: Number of assignments that failed
            - errors: List of error messages for failed assignments

        Raises:
            PermissionError: If user lacks required permissions
            ValueError: If entity not found or invalid entity type
            ExtendedHTTPException: If cross-project assignment attempted
        """
        repo = GuardrailRepository()

        # Step 1: Get all existing assignments for this guardrail
        existing_assignments = repo.get_all_assignments_for_guardrail(guardrail_id)

        # Step 2: Compute changes
        keys_to_create, keys_to_delete, existing_assignments_map = GuardrailService._compute_bulk_assignment_changes(
            request=request,
            project_name=guardrail_project_name,
            existing_assignments=existing_assignments,
        )

        success_count = 0
        failed_count = 0
        errors = []

        # Step 3: Delete by IDs - has to be done first to deal with possible entity project changes
        if keys_to_delete:
            try:
                assignment_ids_to_delete = [existing_assignments_map[key] for key in keys_to_delete]
                repo.remove_guardrails_assignments_by_ids(assignment_ids_to_delete)
            except Exception as e:
                failed_count += len(keys_to_delete)
                errors.append(f"Failed to remove {len(keys_to_delete)} obsolete assignments: {str(e)}")

        # Step 4: Create new assignments
        for key in keys_to_create:
            entity_type, entity_id, source, mode, scope, _ = key
            try:
                GuardrailService._validate_and_create_assignment_in_bulk_assignments(
                    repo=repo,
                    user=user,
                    guardrail_id=guardrail_id,
                    guardrail_project_name=guardrail_project_name,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    source=source,
                    mode=mode,
                    scope=scope,
                )
                success_count += 1
            except IntegrityError:
                # Assignment already exists - race condition - this is OK
                logger.warning(f"Assignment already exists: {key}")
                success_count += 1
            except Exception as e:
                failed_count += 1
                error_message = e.message if isinstance(e, ExtendedHTTPException) else str(e)
                errors.append(f"{entity_type.value} {entity_id} assignment failed: {error_message}")

        return success_count, failed_count, errors

    @staticmethod
    def get_entity_guardrail_assignments(
        user: User,
        entity_type: GuardrailEntity,
        entity_id: str,
    ) -> List[GuardrailAssignmentItem]:
        """
        Get guardrail assignments for a single entity to be returned from the APIs.

        Returns a list of GuardrailAssignmentItem objects representing all guardrails
        assigned to this specific entity.
        """
        repo = GuardrailRepository()
        assignments: List[GuardrailAssignment] = repo.get_entity_guardrail_assignments(
            entity_type,
            entity_id,
        )

        if not assignments:
            return []

        # Collect unique guardrail IDs
        guardrail_ids = list({assignment.guardrail_id for assignment in assignments})

        # Bulk load guardrails
        guardrails = repo.get_guardrails_by_ids(guardrail_ids)

        # Create a map of guardrail_id -> guardrail_name
        guardrail_name_map = {}
        for guardrail in guardrails:
            if guardrail.bedrock and guardrail.bedrock.bedrock_name and guardrail.bedrock.bedrock_version:
                guardrail_name_map[guardrail.id] = (
                    f"{guardrail.bedrock.bedrock_name}:{guardrail.bedrock.bedrock_version}"
                )

        result = []

        for assignment in assignments:
            # using DELETE for strict access control
            editable = Ability(user).can(Action.DELETE, assignment)

            result.append(
                GuardrailAssignmentItem(
                    guardrail_id=assignment.guardrail_id,
                    mode=assignment.mode,
                    source=assignment.source,
                    editable=editable,
                    guardrail_name=guardrail_name_map.get(assignment.guardrail_id),
                )
            )

        return result

    @staticmethod
    def remove_guardrail_assignments_for_entity(
        entity_type: GuardrailEntity,
        entity_id: str,
    ):
        repo = GuardrailRepository()
        repo.remove_guardrail_assignments_for_entity(entity_type=entity_type, entity_id=entity_id)

    @staticmethod
    def remove_guardrail_assignments_for_guardrail(guardrail_id: str):
        repo = GuardrailRepository()
        repo.remove_guardrail_assignments_by_guardrail_id(guardrail_id=guardrail_id)

    @staticmethod
    def apply_guardrails_for_entity(
        entity_type: GuardrailEntity,
        entity_id: str,
        project_name: str,
        input: str | List[str],
        source: GuardrailSource,
        output_scope: Literal["INTERVENTIONS", "FULL"] = "INTERVENTIONS",
        guardrails: Optional[List[Guardrail]] = None,
    ):
        return GuardrailService.apply_guardrails_for_entities(
            entity_configs=[EntityConfig(entity_type=entity_type, entity_id=entity_id, project_name=project_name)],
            input=input,
            source=source,
            output_scope=output_scope,
            guardrails=guardrails,
        )

    @staticmethod
    def apply_guardrails_for_entities(
        entity_configs: List[EntityConfig],
        input: str | List[str],
        source: GuardrailSource,
        output_scope: Literal["INTERVENTIONS", "FULL"] = "INTERVENTIONS",
        guardrails: Optional[List[Guardrail]] = None,  # if empty or non, we load for the entities
    ) -> Tuple[str | List[str], Optional[List]]:
        """
        Apply guardrails to one or more input strings efficiently.

        Batches all inputs together to minimize AWS API calls while maintaining order.
        """
        is_single_input = isinstance(input, str)
        chunks = [input] if is_single_input else list(input)

        if not chunks or all(not c.strip() for c in chunks):
            return input, None

        if not guardrails:
            guardrails = GuardrailService.get_effective_guardrails(entity_configs, source)

        if not guardrails:
            return input, None

        unique_guardrails = GuardrailService._deduplicate_guardrails(guardrails)

        # Import here to avoid circular imports
        from codemie.service.aws_bedrock.bedrock_guardrail_service import BedrockGuardrailService

        # Apply unique guardrails sequentially - in semi-random order - this is intended
        for guardrail in unique_guardrails:
            chunks = GuardrailService._apply_single_guardrail_to_chunks(
                guardrail=guardrail,
                chunks=chunks,
                source=source,
                output_scope=output_scope,
                is_single_input=is_single_input,
                bedrock_service=BedrockGuardrailService,
            )

            # If chunks is a tuple, it means we got blocked
            if isinstance(chunks, tuple):
                return chunks

        # Return in original format
        return chunks[0] if is_single_input else chunks, None

    @staticmethod
    def get_effective_guardrails_for_entity(
        entity_type: GuardrailEntity,
        entity_id: str,
        project_name: str,
        source: GuardrailSource,
    ) -> List[Guardrail]:
        """
        Get all guardrails that should be applied for a specific entity.
        This aggregates guardrails from project-level, project-entity-level, and entity-specific assignments.

        Args:
            entity_type: The type of the entity (e.g., ASSISTANT, KNOWLEDGEBASE, WORKFLOW)
            entity_id: The ID of the specific entity
            project_name: The name of the project the entity belongs to
            source: Whether this is for input or output
        """
        return GuardrailService.get_effective_guardrails(
            entity_configs=[EntityConfig(entity_type=entity_type, entity_id=entity_id, project_name=project_name)],
            source=source,
        )

    @staticmethod
    def get_effective_guardrails(
        entity_configs: List[EntityConfig],
        source: GuardrailSource,
    ) -> List[Guardrail]:
        """
        Get all guardrails that should be applied across multiple entities.
        This aggregates guardrails from all provided entity configurations and returns unique guardrails.

        Args:
            entity_configs: List of entity configurations to check
            source: Whether this is for input or output

        Returns:
            List of unique Guardrail objects to apply
        """
        repository = GuardrailRepository()
        all_guardrail_ids = set()

        for config in entity_configs:
            # Get unique guardrail IDs for this entity
            guardrail_ids = repository.get_all_effective_guardrail_ids_for_entity(
                entity_type=config.entity_type,
                entity_id=config.entity_id,
                project_name=config.project_name,
                source=source,
            )
            all_guardrail_ids.update(guardrail_ids)

        if not all_guardrail_ids:
            return []

        guardrails: List[Guardrail] = Guardrail.get_by_ids(list(all_guardrail_ids))  # type: ignore

        return guardrails

    @staticmethod
    def _process_get_guardrail_assignment(assignment: GuardrailAssignment, settings_dict: dict, result: dict):
        # Handle project-level assignments
        if assignment.entity_type == GuardrailEntity.PROJECT:
            if assignment.scope == GuardrailEntity.PROJECT:
                result["project"]["settings"].append(settings_dict)
            elif assignment.scope == GuardrailEntity.ASSISTANT:
                result["assistants"]["settings"].append(settings_dict)
            elif assignment.scope == GuardrailEntity.WORKFLOW:
                result["workflows"]["settings"].append(settings_dict)
            elif assignment.scope == GuardrailEntity.KNOWLEDGEBASE:
                result["datasources"]["settings"].append(settings_dict)

        # Handle entity-specific assignments - GROUP BY entity_id
        elif assignment.entity_type == GuardrailEntity.ASSISTANT:
            if assignment.entity_id not in result["assistants"]["items"]:
                result["assistants"]["items"][assignment.entity_id] = []
            result["assistants"]["items"][assignment.entity_id].append(settings_dict)

        elif assignment.entity_type == GuardrailEntity.WORKFLOW:
            if assignment.entity_id not in result["workflows"]["items"]:
                result["workflows"]["items"][assignment.entity_id] = []
            result["workflows"]["items"][assignment.entity_id].append(settings_dict)

        elif assignment.entity_type == GuardrailEntity.KNOWLEDGEBASE:
            if assignment.entity_id not in result["datasources"]["items"]:
                result["datasources"]["items"][assignment.entity_id] = []
            result["datasources"]["items"][assignment.entity_id].append(settings_dict)

    @staticmethod
    def _load_entity_details(user: User, entity_ids_to_load: dict) -> dict:
        entity_details = {
            GuardrailEntity.ASSISTANT: {},
            GuardrailEntity.WORKFLOW: {},
            GuardrailEntity.KNOWLEDGEBASE: {},
        }

        GuardrailService._load_assistant_details(user, entity_ids_to_load, entity_details)
        GuardrailService._load_workflow_details(user, entity_ids_to_load, entity_details)
        GuardrailService._load_datasource_details(user, entity_ids_to_load, entity_details)

        return entity_details

    @staticmethod
    def _load_assistant_details(user: User, entity_ids_to_load: dict, entity_details: dict) -> None:
        """Load assistant details with permission checks."""
        if not entity_ids_to_load[GuardrailEntity.ASSISTANT]:
            return

        from codemie.rest_api.models.assistant import Assistant

        assistants: List[Assistant] = Assistant.get_by_ids_no_permission_check(
            list(entity_ids_to_load[GuardrailEntity.ASSISTANT])
        )  # type: ignore

        for assistant in assistants:
            entity_details[GuardrailEntity.ASSISTANT][assistant.id] = (
                {
                    "id": assistant.id,
                    "name": assistant.name,
                    "icon_url": assistant.icon_url,
                }
                if Ability(user).can(Action.READ, assistant)
                else {}
            )

    @staticmethod
    def _load_workflow_details(user: User, entity_ids_to_load: dict, entity_details: dict) -> None:
        """Load workflow details with permission checks."""
        if not entity_ids_to_load[GuardrailEntity.WORKFLOW]:
            return

        from codemie.core.workflow_models.workflow_config import WorkflowConfig

        workflows: List[WorkflowConfig] = WorkflowConfig.get_by_ids(list(entity_ids_to_load[GuardrailEntity.WORKFLOW]))  # type: ignore

        for workflow in workflows:
            entity_details[GuardrailEntity.WORKFLOW][workflow.id] = (
                {
                    "id": workflow.id,
                    "name": workflow.name,
                    "icon_url": workflow.icon_url,
                }
                if Ability(user).can(Action.READ, workflow)
                else {}
            )

    @staticmethod
    def _load_datasource_details(user: User, entity_ids_to_load: dict, entity_details: dict) -> None:
        """Load datasource details with permission checks."""
        if not entity_ids_to_load[GuardrailEntity.KNOWLEDGEBASE]:
            return

        from codemie.rest_api.models.index import IndexInfo

        datasources: List[IndexInfo] = IndexInfo.get_by_ids(list(entity_ids_to_load[GuardrailEntity.KNOWLEDGEBASE]))  # type: ignore

        for datasource in datasources:
            entity_details[GuardrailEntity.KNOWLEDGEBASE][datasource.id] = (
                {
                    "id": datasource.id,
                    "name": datasource.repo_name,
                    "index_type": datasource.index_type,
                }
                if Ability(user).can(Action.READ, datasource)
                else {}
            )

    @staticmethod
    def _build_entity_item(
        entity_id: str,
        settings_list: list,
        entity_detail: Optional[dict],
        entity_type: GuardrailEntity,
    ) -> Optional[dict]:
        if entity_detail is None:
            # Entity not found - clean up assignments
            logger.warning(f"Entity {entity_type.value} with ID {entity_id} not found. Cleaning up assignments.")
            GuardrailService.remove_guardrail_assignments_for_entity(entity_type, entity_id)

            return None

        # Build item with entity details
        item = {
            "id": entity_id,
            "settings": settings_list,
        }

        if not entity_detail:
            # User lacks permission to view entity details
            return item

        # Add entity-specific fields
        if entity_type == GuardrailEntity.ASSISTANT or entity_type == GuardrailEntity.WORKFLOW:
            item["name"] = entity_detail["name"]
            item["icon_url"] = entity_detail["icon_url"]
        elif entity_type == GuardrailEntity.KNOWLEDGEBASE:
            item["name"] = entity_detail["name"]
            item["index_type"] = entity_detail["index_type"]

        return item

    @staticmethod
    def _validate_guardrail_user_and_project_permissions(user: User, guardrail_id: str, project_name: str):
        guardrail: Guardrail = Guardrail.find_by_id(guardrail_id)  # type: ignore
        if not guardrail:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="Guardrail not found",
                details=f"No guardrail found with the id '{guardrail_id}'.",
                help="Please check the guardrail id and ensure it is correct.",
            )

        if guardrail.project_name != project_name:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Cross-project assignment not allowed",
                details=(
                    f"Guardrail belongs to project '{guardrail.project_name}' but entity belongs to '{project_name}'"
                ),
            )

        # User can only access guardrails it owns, or is an admin for
        if not Ability(user).can(Action.DELETE, guardrail):
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message="Permission denied",
                details="You must own this guardrail or be an admin to write or delete.",
                help="Contact the guardrail owner or an administrator for assistance.",
            )

    @staticmethod
    def _deduplicate_guardrails(guardrails: List[Guardrail]):
        # Deduplicate by bedrock_guardrail_id + bedrock_version combo
        seen = set()
        unique_guardrails = []

        for guardrail in guardrails:
            # Include non-bedrock guardrails (they shouldn't exist - defensive)
            if (
                not guardrail.bedrock
                or not guardrail.bedrock.bedrock_guardrail_id
                or not guardrail.bedrock.bedrock_version
            ):
                unique_guardrails.append(guardrail)
                continue

            key = (guardrail.bedrock.bedrock_guardrail_id, guardrail.bedrock.bedrock_version)

            if key not in seen:
                seen.add(key)
                unique_guardrails.append(guardrail)

        return unique_guardrails

    @staticmethod
    def _compute_bulk_assignment_changes(
        request: GuardrailAssignmentRequestResponse,
        project_name: str,
        existing_assignments: List[GuardrailAssignment],
    ) -> tuple[set[tuple], set[tuple], dict[tuple, str]]:
        """
        Compute which assignments need to be created and deleted based on the request.

        Args:
            request: The bulk assignment request containing desired assignments
            project_name: The project name for project-level assignments
            existing_assignments: List of existing assignment objects

        Returns:
            Tuple of (keys_to_create, keys_to_delete, existing_assignments_map)
            where keys are (entity_type, entity_id, source, mode, scope)
        """
        # Build desired assignment keys from request
        desired_keys = GuardrailService._build_desired_bulk_assignment_keys(request, project_name)

        # Build existing keys and ID map
        existing_keys = set()
        existing_assignments_map: dict[tuple, str] = {}

        for assignment in existing_assignments:
            key = (
                assignment.entity_type,
                assignment.entity_id,
                assignment.source,
                assignment.mode,
                assignment.scope,
                assignment.project_name,
            )
            existing_keys.add(key)
            existing_assignments_map[key] = assignment.id

        # Determine what to create and what to delete
        keys_to_create = desired_keys - existing_keys
        keys_to_delete = existing_keys - desired_keys

        return keys_to_create, keys_to_delete, existing_assignments_map

    @staticmethod
    def _build_desired_bulk_assignment_keys(  # NOSONAR - S3776: nested request structure requires complex loops
        request: GuardrailAssignmentRequestResponse,
        project_name: str,
    ) -> set[tuple]:
        """
        Build a set of desired assignment keys from the bulk assignment request.

        Returns:
            Set of tuples (entity_type, entity_id, source, mode, scope, project_name)
        """
        desired_keys = set()

        # Process project assignments
        if request.project and request.project.settings:
            for setting in request.project.settings:
                desired_keys.add(
                    (
                        GuardrailEntity.PROJECT,
                        project_name,
                        setting.source,
                        setting.mode,
                        GuardrailEntity.PROJECT,  # scope
                        project_name,
                    )
                )

        # Process entity type assignments (assistants, workflows, datasources)
        for entity_type, entity_config in [
            (GuardrailEntity.ASSISTANT, request.assistants),
            (GuardrailEntity.WORKFLOW, request.workflows),
            (GuardrailEntity.KNOWLEDGEBASE, request.datasources),
        ]:
            if not entity_config:
                continue

            # Project-level settings for this entity type
            if entity_config.settings:
                for setting in entity_config.settings:
                    desired_keys.add(
                        (
                            GuardrailEntity.PROJECT,
                            project_name,
                            setting.source,
                            setting.mode,
                            entity_type,  # scope
                            project_name,
                        )
                    )

            # Individual entity assignments
            if entity_config.items:
                for item in entity_config.items:
                    for setting in item.settings:
                        desired_keys.add(
                            (
                                entity_type,
                                item.id,
                                setting.source,
                                setting.mode,
                                None,  # scope is None for direct entity assignments
                                project_name,
                            )
                        )

        return desired_keys

    @staticmethod
    def _validate_and_create_assignment_in_bulk_assignments(
        repo: GuardrailRepository,
        user: User,
        guardrail_id: str,
        guardrail_project_name: str,
        entity_type: GuardrailEntity,
        entity_id: str,
        source: GuardrailSource,
        mode: GuardrailMode,
        scope: Optional[GuardrailEntity],
    ):
        """Validate permissions and create a single guardrail assignment."""
        # Validate permissions based on entity type
        if (
            entity_type == GuardrailEntity.PROJECT
            and not user.is_admin
            and guardrail_project_name not in user.admin_project_names
        ):
            raise PermissionError(f"You don't have permission to modify project {guardrail_project_name}")

        if entity_type != GuardrailEntity.PROJECT:
            # Validate entity-specific permissions
            entity_config = ENTITY_TYPE_CONFIG.get(entity_type)
            if not entity_config:
                raise ValueError(f"Unsupported or incorrectly set entity type: {entity_type}")

            entity_name = entity_config["entity_name"]
            entity_project_field_name = entity_config["project_field_name"]
            model_class = entity_config["model_class"]()
            entity = model_class.find_by_id(entity_id)

            if not entity:
                raise ValueError(f"{entity_name} with ID {entity_id} not found")

            # Using DELETE ability here to circumvent remote entity WRITE access being False
            if not Ability(user).can(Action.DELETE, entity):
                raise PermissionError(f"You don't have permission to modify {entity_name.lower()} {entity_id}")

            entity_project = getattr(entity, entity_project_field_name)
            if entity_project != guardrail_project_name:
                raise ExtendedHTTPException(
                    code=status.HTTP_400_BAD_REQUEST,
                    message="Cross-project assignment not allowed",
                    details=(
                        f"Guardrail belongs to project '{guardrail_project_name}' "
                        f"but entity belongs to '{entity_project}'"
                    ),
                )

        repo.assign_guardrail_to_entity(
            entity_type=entity_type,
            entity_id=entity_id,
            guardrail_id=guardrail_id,
            source=source,
            mode=mode,
            project_name=guardrail_project_name,
            user=user,
            scope=scope,
        )

    @staticmethod
    def _apply_single_guardrail_to_chunks(
        guardrail: Guardrail,
        chunks: List[str],
        source: GuardrailSource,
        output_scope: Literal["INTERVENTIONS", "FULL"],
        is_single_input: bool,
        bedrock_service,
    ) -> List[str] | Tuple[str | List[str], List]:
        """
        Apply a single guardrail to all chunks.

        Returns either:
        - List[str]: Processed chunks if successful
        - Tuple: (blocked_text, blocked_reasons) if blocked
        """
        processed_outputs = []

        # Batch all chunks - batch_content handles splitting large chunks
        for batch in batch_content(chunks):
            try:
                response = bedrock_service.apply_guardrail(
                    guardrail=guardrail,
                    content=batch,
                    source="OUTPUT" if source == GuardrailSource.OUTPUT else "INPUT",
                    output_scope=output_scope,
                )
            except Exception as e:
                # On connection error or unexpected apply error, log and continue without this guardrail
                logger.warning(
                    f"Failed to apply guardrail {guardrail.id} due to error: {str(e)}. "
                    f"Ignoring this guardrail and continuing without it."
                )
                # Return original chunks unchanged
                return chunks

            # Check if blocked and return early
            blocked_result = GuardrailService._check_for_blocked_response(response, is_single_input, len(chunks))
            if blocked_result:
                return blocked_result

            # Collect outputs
            outputs = response.get("outputs", [])
            for i, item in enumerate(batch):
                if i < len(outputs) and outputs[i].get("text"):
                    processed_outputs.append(outputs[i].get("text"))
                else:
                    # No change, keep original
                    processed_outputs.append(item["text"]["text"])

        # Use processed as input for next guardrail
        # Note: processed_outputs length should match original chunks length
        # because batch_content preserves individual chunks
        return processed_outputs

    @staticmethod
    def _check_for_blocked_response(
        response: dict,
        is_single_input: bool,
        original_chunks_count: int,
    ) -> Optional[Tuple[str | List[str], List]]:
        """
        Check if the guardrail response indicates blocked content.

        Returns:
        - None if not blocked
        - Tuple of (blocked_text, blocked_reasons) if blocked
        """
        if (
            response.get("action") != "GUARDRAIL_INTERVENED"
            or "blocked" not in response.get("actionReason", "").lower()
        ):
            return None

        blocked_reasons = GuardrailService._extract_blocked_reasons(response)
        outputs = response.get("outputs", [])
        blocked_text = outputs[0].get("text", "BLOCKED") if outputs else "BLOCKED"

        if is_single_input:
            return blocked_text, blocked_reasons
        else:
            # Return blocked for all original inputs
            return [blocked_text] * original_chunks_count, blocked_reasons

    @staticmethod
    def _extract_blocked_reasons(response: dict) -> list:
        """
        Extracts all BLOCKED reasons from an apply_guardrail API response.
        Returns a list of dicts describing each block reason.
        """
        reasons = []
        assessments = response.get("assessments", [])

        for assessment in assessments:
            GuardrailService._extract_policy_blocked_reasons(assessment, reasons)

        return reasons

    @staticmethod
    def _extract_policy_blocked_reasons(assessment: dict, reasons: list) -> None:
        """
        Extract blocked reasons from a single assessment across all policy types.
        Modifies the reasons list in place.
        """
        # Topic Policy
        for topic in assessment.get("topicPolicy", {}).get("topics", []):
            if topic.get("action") in ("BLOCKED", "DENY"):
                reasons.append(
                    {
                        "policy": "topicPolicy",
                        "type": topic.get("type"),
                        "name": topic.get("name"),
                        "reason": topic.get("action"),
                        "detected": topic.get("detected"),
                    }
                )

        # Content Policy
        for filt in assessment.get("contentPolicy", {}).get("filters", []):
            if filt.get("action") == "BLOCKED":
                reasons.append(
                    {
                        "policy": "contentPolicy",
                        "type": filt.get("type"),
                        "reason": filt.get("action"),
                        "detected": filt.get("detected"),
                        "confidence": filt.get("confidence"),
                    }
                )

        # Word Policy - Managed Word Lists
        for word in assessment.get("wordPolicy", {}).get("managedWordLists", []):
            if word.get("action") == "BLOCKED":
                reasons.append(
                    {
                        "policy": "wordPolicy",
                        "type": word.get("type"),
                        "match": word.get("match"),
                        "reason": word.get("action"),
                        "detected": word.get("detected"),
                    }
                )

        # Word Policy - Custom Words
        for word in assessment.get("wordPolicy", {}).get("customWords", []):
            if word.get("action") == "BLOCKED":
                reasons.append(
                    {
                        "policy": "wordPolicy",
                        "type": word.get("type"),
                        "match": word.get("match"),
                        "reason": word.get("action"),
                        "detected": word.get("detected"),
                    }
                )

        # Sensitive Information Policy
        for pii in assessment.get("sensitiveInformationPolicy", {}).get("piiEntities", []):
            if pii.get("action") == "BLOCKED":
                reasons.append(
                    {
                        "policy": "sensitiveInformationPolicy",
                        "type": pii.get("type"),
                        "match": pii.get("match"),
                        "reason": pii.get("action"),
                        "detected": pii.get("detected"),
                    }
                )
