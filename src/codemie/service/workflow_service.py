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

import json
import math
import os
import uuid
from datetime import datetime
from typing import List, Optional

from elasticsearch import NotFoundError
from sqlalchemy import Float, Integer
from sqlmodel import Session, func, select, text
from sqlmodel.sql.expression import and_, or_

from codemie.configs import config, logger
from codemie.core.ability import Ability, Action
from codemie.core.constants import ChatRole, MermaidMimeType
from codemie.core.models import UserEntity
from codemie.core.utils import safe_divide
from codemie.core.workflow_models import (
    WorkflowConfig,
    WorkflowConfigTemplate,
    WorkflowExecution,
    WorkflowExecutionResponse,
    WorkflowExecutionStatusEnum,
    YamlConfigHistory,
)
from codemie.repository.repository_factory import FileRepositoryFactory
from codemie.rest_api.models.conversation import GeneratedMessage
from codemie.rest_api.security.user import User
from codemie.rest_api.utils.default_applications import ensure_application_exists
from codemie.service.monitoring.workflow_monitoring_service import WorkflowMonitoringService

MAX_ITEMS_PER_PAGE = 10_000


class WorkflowService:
    _cached_prebuilt_workflows = []
    _editable_non_boolean_fields = {
        "project",
        "name",
        "description",
        "icon_url",
        "mode",
        "yaml_config",
        "supervisor_prompt",
        "meta_config",
    }

    def get_workflow(self, workflow_id: str, user: Optional[User] = None) -> WorkflowConfig:
        try:
            workflow = WorkflowConfig.get_by_id(workflow_id)
            if user:
                workflow.user_abilities = Ability(user).list(workflow)
            return workflow
        except Exception as e:
            logger.error(f"Failed to get workflow: {e}")
            raise e

    def delete_workflow(self, workflow_config: WorkflowConfig, user: User):
        try:
            self.delete_all_executions_by_workflow_id(workflow_config.id)
            WorkflowConfig.delete(workflow_config.id)

            WorkflowMonitoringService.send_delete_workflow_metric(
                workflow_id=workflow_config.id,
                user_id=user.id,
                user_name=user.name,
                workflow_name=workflow_config.name,
                project=workflow_config.project,
                success=True,
            )
        except Exception as e:
            logger.error(f"Failed to delete workflow: {e}")
            raise e

    def create_workflow(self, workflow_config: WorkflowConfig, user: User):
        try:
            # Ensure Application exists for the project
            if workflow_config.project:
                ensure_application_exists(workflow_config.project)

            workflow_config.created_by = user.as_user_model()
            workflow_config.save(refresh=True)
            WorkflowMonitoringService.send_create_workflow_metric(
                workflow_id=workflow_config.id,
                user_id=user.id,
                user_name=user.name,
                workflow_name=workflow_config.name,
                project=workflow_config.project,
                success=True,
                mode=workflow_config.mode,
            )
            return workflow_config
        except Exception as e:
            logger.error(f"Failed to create workflow: {e}")
            WorkflowMonitoringService.send_create_workflow_metric(
                user_id=user.id,
                user_name=user.name,
                workflow_name=workflow_config.name,
                project=workflow_config.project,
                success=False,
                additional_attributes={
                    "error_class": e.__class__.__name__,
                },
                mode=workflow_config.mode,
            )
            raise e

    def update_workflow(self, stored_config: WorkflowConfig, updated_workflow_config: WorkflowConfig, user: User):
        try:
            self._update_workflow_values(stored_config, updated_workflow_config, user)
            return stored_config
        except Exception as e:
            logger.error(f"Failed to update workflow: {e}")
            self._send_workflow_update_failed_metric(e, stored_config.id, updated_workflow_config, user)
            raise e

    def _send_workflow_update_failed_metric(
        self, e: Exception, stored_config_id: str, updated_workflow_config: WorkflowConfig, user: User
    ):
        WorkflowMonitoringService.send_update_workflow_metric(
            user_id=user.id,
            user_name=user.name,
            workflow_id=stored_config_id,
            workflow_name=updated_workflow_config.name,
            project=updated_workflow_config.project,
            success=False,
            additional_attributes={
                "error_class": e.__class__.__name__,
            },
            mode=updated_workflow_config.mode,
        )

    def _update_workflow_history(self, workflow_id: str, new_history_entry: YamlConfigHistory) -> None:
        sql = text(f"""
            UPDATE {WorkflowConfig.__tablename__}
            SET yaml_config_history = :new_history_entry || yaml_config_history
            WHERE id = :workflow_id
        """)
        stmt = sql.bindparams(
            workflow_id=workflow_id, new_history_entry=json.dumps([new_history_entry.model_dump(mode="json")])
        )
        with Session(WorkflowExecution.get_engine()) as session:
            session.execute(stmt)
            session.commit()

    def _update_workflow_values(
        self, stored_config: WorkflowConfig, updated_workflow_config: WorkflowConfig, user: User
    ) -> None:
        new_history_entry = YamlConfigHistory(
            yaml_config=stored_config.yaml_config,
            date=datetime.now(),
            created_by=user.as_user_model(),
        )
        yaml_config_updated = False

        for attr_name in self._editable_non_boolean_fields:
            new_value = getattr(updated_workflow_config, attr_name)
            if new_value:
                if attr_name == "yaml_config":
                    yaml_config_updated = True
                setattr(stored_config, attr_name, new_value)

        if yaml_config_updated:
            stored_config.parse_execution_config()

        if stored_config.shared != updated_workflow_config.shared:
            stored_config.shared = updated_workflow_config.shared

        stored_config.updated_by = user.as_user_model()
        logger.debug(f"Store workflow: {stored_config.yaml_config}")
        stored_config.update(refresh=True)
        self._update_workflow_history(stored_config.id, new_history_entry)
        logger.info(f"Workflow updated with ID: {stored_config.id}")
        WorkflowMonitoringService.send_update_workflow_metric(
            workflow_id=stored_config.id,
            user_id=user.id,
            user_name=user.name,
            workflow_name=stored_config.name,
            project=stored_config.project,
            success=True,
            mode=stored_config.mode,
        )

    @staticmethod
    def _augment_user_input_with_history(user_input: str, conversation_id: Optional[str], workflow_id: str) -> str:
        """
        Augment user input with previous workflow execution history for chat mode.

        Args:
            user_input: Original user input
            conversation_id: Conversation ID if in chat mode
            workflow_id: Workflow ID to query executions

        Returns:
            Augmented input with history prepended, or original input if no history
        """
        if not conversation_id:
            return user_input

        from codemie.service.workflow_execution.workflow_execution_history_formatter import format_execution_history

        # Format and inject previous execution history
        history_context = format_execution_history(conversation_id, workflow_id)
        if history_context:
            # Prepend history to user input for workflow execution
            augmented_input = f"{history_context}\n{user_input}"
            logger.debug(
                "Injected execution history into workflow input",
                extra={
                    "workflow_id": workflow_id,
                    "history_length": len(history_context),
                    "original_input_length": len(user_input),
                    "augmented_input_length": len(augmented_input),
                },
            )
            return augmented_input

        return user_input

    @staticmethod
    def create_workflow_execution(
        workflow_config: WorkflowConfig,
        user: UserEntity,
        user_input: Optional[str] = '',
        file_names: Optional[list[str]] = None,
        conversation_id: Optional[str] = None,
    ) -> WorkflowExecution:
        try:
            from codemie.rest_api.models.conversation import Conversation

            file_names = file_names or []
            # Only create conversation history for streamable executions (when conversation_id is provided)
            is_chat_execution = conversation_id is not None

            # Materialize execution history for chat mode
            augmented_input = WorkflowService._augment_user_input_with_history(
                user_input, conversation_id, workflow_config.id
            )

            if is_chat_execution:
                # Generate conversation_id if empty string provided
                if not conversation_id:
                    conversation_id = str(uuid.uuid4())

                # Get or create conversation entity
                try:
                    conversation = Conversation.get_by_id(conversation_id)
                    logger.debug(f"Using existing conversation {conversation_id} for workflow execution")
                except Exception:
                    # Create new conversation for this workflow chat
                    conversation = Conversation(
                        id=conversation_id,
                        conversation_id=conversation_id,
                        conversation_name='',  # Will be set from first user message
                        user_id=user.user_id,
                        user_name=user.username,
                        history=[],
                        assistant_ids=[workflow_config.id],  # Use workflow_id as assistant_id
                        initial_assistant_id=workflow_config.id,
                        project=workflow_config.project,
                        is_workflow_conversation=True,  # Mark as workflow-based conversation
                    )
                    conversation.save(refresh=True)
                    logger.debug(f"Created new conversation {conversation_id} for workflow chat")

                # Determine history_index from conversation history
                history_index = 0
                if conversation.history:
                    max_index = max(
                        (msg.history_index for msg in conversation.history if msg.history_index is not None), default=-1
                    )
                    history_index = max_index + 1

            # Create execution
            execution_id = str(uuid.uuid4())

            # Create history for WorkflowExecution (for standalone executions and backward compatibility)
            execution_history = [
                GeneratedMessage(
                    date=datetime.now(),
                    role=ChatRole.USER.value,
                    message=user_input,
                    message_raw=user_input,
                    history_index=0,
                    file_names=file_names,
                ),
                GeneratedMessage(
                    date=datetime.now(),
                    role=ChatRole.ASSISTANT.value,
                    history_index=0,
                    assistant_id=workflow_config.id,
                    thoughts=[],
                    message='',  # Will be updated by _update_assistant_response_in_history
                ),
            ]

            execution_config = WorkflowExecution(
                workflow_id=workflow_config.id,
                execution_id=execution_id,
                overall_status=WorkflowExecutionStatusEnum.IN_PROGRESS,
                created_by=user,
                history=execution_history,  # Store history for standalone executions
                project=workflow_config.project,
                prompt=augmented_input,  # Use augmented input with history for workflow execution
                file_names=file_names,
                conversation_id=conversation_id if is_chat_execution else None,
            )
            execution_config.save(refresh=True)

            # Only add conversation history for chat executions (streamable workflows)
            if is_chat_execution:
                # Add user message and assistant reference to conversation history
                user_message = GeneratedMessage(
                    date=datetime.now(),
                    role=ChatRole.USER.value,
                    message=user_input,
                    message_raw=user_input,
                    history_index=history_index,
                    file_names=file_names,
                )

                # Add assistant message as a reference to the workflow execution
                assistant_message_ref = GeneratedMessage(
                    date=datetime.now(),
                    role=ChatRole.ASSISTANT.value,
                    history_index=history_index,
                    assistant_id=workflow_config.id,
                    workflow_execution_ref=True,  # Mark as reference
                    execution_id=execution_id,  # Reference to the execution
                    thoughts=[],  # Will be materialized on retrieval
                    message=None,  # Will be materialized on retrieval
                )

                # Append to conversation history
                conversation.history = [*(conversation.history or []), user_message, assistant_message_ref]
                conversation.update()

                logger.info(
                    "Created workflow execution with conversation reference",
                    extra={
                        "workflow_id": workflow_config.id,
                        "execution_id": execution_id,
                        "history_index": history_index,
                    },
                )
            else:
                logger.info(
                    "Created non-chat workflow execution (no conversation history)",
                    extra={
                        "workflow_id": workflow_config.id,
                        "execution_id": execution_id,
                    },
                )

            return execution_config
        except Exception as e:
            logger.error(f"Failed to create workflow execution: {e}", exc_info=True)
            raise e

    @classmethod
    def find_workflow_execution_by_id(cls, execution_id: str) -> WorkflowExecution:
        try:
            executions = WorkflowExecution.get_by_execution_id(execution_id)
            return executions[0] if len(executions) > 0 else None
        except Exception as e:
            logger.error(f"Failed to get workflow execution: {e}")
            raise e

    @classmethod
    def get_recent_workflows_for_user(cls, user: User, limit: int = 3):
        """
        Get recent workflows used in chat by the user.
        Returns unique workflows (not conversations) ordered by most recent use.

        This follows the assistant conversation pattern - queries Conversations table
        and uses is_workflow_conversation flag to filter.

        Args:
            user: The authenticated user
            limit: Maximum number of workflows to return (default: 3)

        Returns:
            List of workflows with their metadata, ordered by most recent use
        """
        try:
            from codemie.rest_api.models.conversation import Conversation

            # Get all workflow conversations for user (similar to get_user_conversations)
            conversations = Conversation.get_all_by_fields(
                {
                    "user_id.keyword": user.id,
                    "is_workflow_conversation": True,
                }
            )

            # Track unique workflows by initial_assistant_id and find most recent usage
            workflow_last_used = {}  # workflow_id -> most_recent_date

            for conv in conversations:
                # Skip conversations without workflow reference
                if not conv.initial_assistant_id:
                    continue

                workflow_id = conv.initial_assistant_id
                conv_date = conv.update_date or conv.date

                # Keep only the most recent date for each workflow
                if workflow_id not in workflow_last_used or conv_date > workflow_last_used[workflow_id]:
                    workflow_last_used[workflow_id] = conv_date

            # Sort workflows by most recent use and take top N
            sorted_workflow_ids = sorted(
                workflow_last_used.keys(),
                key=lambda wid: workflow_last_used[wid],
                reverse=True,
            )[:limit]

            # Build workflow list with details
            workflows = []
            for workflow_id in sorted_workflow_ids:
                try:
                    workflow = WorkflowConfig.get_by_id(workflow_id)

                    # Check user has access to this workflow
                    if not Ability(user).can(Action.READ, workflow):
                        continue

                    workflows.append(
                        {
                            "id": workflow.id,
                            "name": workflow.name,
                            "description": workflow.description,
                            "icon_url": workflow.icon_url,
                            "project": workflow.project,
                            "last_used": workflow_last_used[workflow_id],
                            "user_abilities": Ability(user).list(workflow),
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to get workflow details for {workflow_id}: {e}")
                    continue

            return workflows

        except Exception as e:
            logger.error(f"Failed to get recent workflows for user: {e}", exc_info=True)
            raise e

    @classmethod
    def get_workflow_execution_list(
        cls,
        user: User,
        workflow_id: str,
        project: str,
        page: int = 0,
        per_page: int = MAX_ITEMS_PER_PAGE,
        filter_by_project: bool = True,
    ):
        with Session(WorkflowExecution.get_engine()) as session:
            query = select(WorkflowExecution).where(WorkflowExecution.workflow_id == workflow_id)

            if not user.is_admin:
                user_id_expr = WorkflowExecution.created_by['user_id'].astext == user.id

                exprs = []
                if user.is_application_admin(project) and filter_by_project:
                    exprs.append(WorkflowExecution.project == project)

                query = query.where(or_(user_id_expr, *exprs))

            # Get total count for pagination
            total = session.exec(select(func.count()).select_from(query.subquery())).one()

            # Apply sorting and pagination
            query = query.order_by(WorkflowExecution.date.desc())
            query = query.offset(page * per_page).limit(per_page)

            executions = session.exec(query).all()

        # Convert to response model
        items = [
            WorkflowExecutionResponse(**execution.model_dump())
            for execution in executions
            if Ability(user).list(execution)
        ]

        pages = math.ceil(total / per_page)
        meta = {"page": page, "per_page": per_page, "total": total, "pages": pages}

        return {"data": items, "pagination": meta}

    def get_workflow_executions(self, workflow_id: str, user=None) -> List[WorkflowExecution]:
        try:
            return WorkflowExecution.get_by_workflow_id(workflow_id, user)
        except NotFoundError as e:
            logger.debug(f"Workflow executions index not found: {e}")
            return []
        except Exception as e:
            logger.error(f"Failed to get workflow executions: {e}")
            raise e

    def delete_workflow_execution(self, execution_config_id: str):
        try:
            return WorkflowExecution.delete(execution_config_id)
        except Exception as e:
            logger.error(f"Failed to delete workflow execution: {e}")
            raise e

    def delete_all_executions_by_workflow_id(self, workflow_id: str, user=None):
        try:
            workflow_executions = self.get_workflow_executions(workflow_id, user)
            for execution in workflow_executions:
                if execution.conversation_id is None:
                    self.delete_workflow_execution(execution.id)
        except Exception as e:
            logger.error(f"Failed to delete all workflow executions for workflow_id {workflow_id}: {e}")
            raise e

    def get_prebuilt_workflows(self) -> List[WorkflowConfigTemplate]:
        if self._cached_prebuilt_workflows:
            return self._cached_prebuilt_workflows

        prebuilt_workflows = []
        templates_dir = config.WORKFLOW_TEMPLATES_DIR
        logger.info(f"Loading prebuilt templates from {templates_dir}")
        try:
            for filename in os.listdir(templates_dir):
                if not filename.endswith("_template.yaml"):
                    continue
                with open(os.path.join(templates_dir, filename), 'r') as file:
                    workflow_config = WorkflowConfigTemplate.from_yaml(file.read())
                    if workflow_config:
                        prebuilt_workflows.append(workflow_config)
            self._cached_prebuilt_workflows = sorted(prebuilt_workflows, key=lambda wf: wf.name)
        except Exception as e:
            logger.error(f"Failed to load prebuilt workflows: {e}")
        return self._cached_prebuilt_workflows

    def get_prebuilt_workflow_by_slug(self, slug: str) -> Optional[WorkflowConfigTemplate]:
        prebuilt_workflows = self.get_prebuilt_workflows()
        matches = [item for item in prebuilt_workflows if item.slug == slug]
        if len(matches) == 1:
            return matches[0]
        elif len(matches) == 0:
            return None
        else:
            raise ValueError(f"Multiple workflows found with slug '{slug}'")

    def save_workflow_schema(self, workflow_config: WorkflowConfig, workflow_schema: bytes):
        try:
            if workflow_schema:
                files_repo = FileRepositoryFactory().get_current_repository()
                result = files_repo.write_file(
                    name=f"workflows/{workflow_config.id}.svg",
                    content=workflow_schema,
                    owner=config.CODEMIE_STORAGE_BUCKET_NAME,
                    mime_type=MermaidMimeType.SVG,
                )
                logger.debug(f"Saving workflow schema for workflow_id: {result}")

                workflow_config.schema_url = result.to_encoded_url()
                workflow_config.update()
        except Exception as e:
            logger.error(f"Failed to save workflow schema: {str(e)}")

    @classmethod
    def belongs_to_project(cls, workflow_id: str, project_name: str) -> bool:
        """
        Verify if a workflow with the given ID belongs to the specified project.

        Args:
            workflow_id: The ID of the workflow to verify
            project_name: The name of the project to check against

        Returns:
            bool: True if the workflow belongs to the project, False otherwise
        """
        try:
            workflow = WorkflowConfig.find_by_id(workflow_id)

            return workflow and workflow.project == project_name
        except Exception:
            return False

    @staticmethod
    def _build_workflow_filters(
        user_name: Optional[str] = None,
        workflow_id: Optional[str] = None,
        project: Optional[str] = None,
        since_date: Optional[datetime] = None,
    ) -> List:
        """Build filter conditions for workflow execution queries."""
        filters = []

        if user_name:
            # Add null safety check for created_by JSONB field with case-insensitive comparison
            user_name_field = func.jsonb_extract_path_text(WorkflowExecution.created_by, 'name')
            filters.append(
                and_(WorkflowExecution.created_by.isnot(None), func.lower(user_name_field) == user_name.lower())
            )

        if workflow_id:
            filters.append(WorkflowExecution.workflow_id == workflow_id)

        if project:
            filters.append(WorkflowExecution.project == project)

        if since_date:
            filters.append(WorkflowExecution.date >= since_date)

        return filters

    @staticmethod
    def _extract_token_field(field_name: str, field_type):
        """
        Extract a field from tokens_usage JSONB column with null safety.

        Returns COALESCE to 0 for null values to prevent aggregation issues.
        """
        extracted = func.jsonb_extract_path_text(WorkflowExecution.tokens_usage, field_name)
        # Use COALESCE to handle null values - cast to text '0' first, then to target type
        return func.coalesce(extracted, '0').cast(field_type)

    @classmethod
    def _get_workflow_totals(cls, session: Session, filters: List) -> dict:
        """Get total spending across all workflow executions."""
        total_stmt = select(
            func.sum(cls._extract_token_field('money_spent', Float)).label('total_money'),
            func.sum(cls._extract_token_field('input_tokens', Integer)).label('total_input'),
            func.sum(cls._extract_token_field('output_tokens', Integer)).label('total_output'),
            func.count(WorkflowExecution.id).label('total_executions'),
        )

        if filters:
            total_stmt = total_stmt.where(and_(*filters))

        total_result = session.exec(total_stmt).one()

        return {
            'total_money_spent': float(total_result.total_money or 0),
            'total_input_tokens': int(total_result.total_input or 0),
            'total_output_tokens': int(total_result.total_output or 0),
            'total_workflow_executions': int(total_result.total_executions or 0),
        }

    @classmethod
    def _get_workflow_breakdown(cls, session: Session, filters: List) -> List[dict]:
        """Get spending breakdown by individual workflows."""
        # Join WorkflowExecution with WorkflowConfig to get workflow names
        breakdown_stmt = (
            select(
                WorkflowExecution.workflow_id,
                WorkflowConfig.name,
                func.sum(cls._extract_token_field('money_spent', Float)).label('money_spent'),
                func.sum(cls._extract_token_field('input_tokens', Integer)).label('input_tokens'),
                func.sum(cls._extract_token_field('output_tokens', Integer)).label('output_tokens'),
                func.count(WorkflowExecution.id).label('execution_count'),
            )
            .join(WorkflowConfig, WorkflowExecution.workflow_id == WorkflowConfig.id, isouter=True)
            .group_by(WorkflowExecution.workflow_id, WorkflowConfig.name)
        )

        if filters:
            breakdown_stmt = breakdown_stmt.where(and_(*filters))

        breakdown_stmt = breakdown_stmt.order_by(func.sum(cls._extract_token_field('money_spent', Float)).desc())

        breakdown_results = session.exec(breakdown_stmt).all()

        breakdown = []
        for row in breakdown_results:
            workflow_id_val = row[0]
            workflow_name = row[1] or workflow_id_val
            money_spent = float(row[2] or 0)
            input_tokens = int(row[3] or 0)
            output_tokens = int(row[4] or 0)
            exec_count = int(row[5] or 0)
            avg_cost = safe_divide(money_spent, exec_count)

            breakdown.append(
                {
                    'dimension_type': 'workflow',
                    'dimension_id': workflow_id_val,
                    'dimension_name': workflow_name,
                    'money_spent': money_spent,
                    'input_tokens': input_tokens,
                    'output_tokens': output_tokens,
                    'workflow_execution_count': exec_count,
                    'average_cost_per_item': avg_cost,
                }
            )

        return breakdown

    @classmethod
    def get_workflow_spending_analytics(
        cls,
        user_name: Optional[str] = None,
        workflow_id: Optional[str] = None,
        project: Optional[str] = None,
        since_date: Optional[datetime] = None,
        include_breakdown: bool = False,
    ) -> dict:
        """
        Get workflow execution spending analytics.

        Args:
            user_name: Filter by user name (case-insensitive)
            workflow_id: Filter by specific workflow ID
            project: Filter by project name
            since_date: Filter by creation date (>=)
            include_breakdown: Include detailed breakdown by individual workflows

        Returns:
            Dict with keys: total_money_spent, total_input_tokens, total_output_tokens,
                           total_workflow_executions, breakdown (list of dicts)
        """
        filters = cls._build_workflow_filters(user_name, workflow_id, project, since_date)

        with Session(WorkflowExecution.get_engine()) as session:
            # Get totals
            totals = cls._get_workflow_totals(session, filters)

            # Get breakdown if requested
            breakdown = []
            if include_breakdown:
                breakdown = cls._get_workflow_breakdown(session, filters)

            return {
                'total_money_spent': totals['total_money_spent'],
                'total_input_tokens': totals['total_input_tokens'],
                'total_output_tokens': totals['total_output_tokens'],
                'total_workflow_executions': totals['total_workflow_executions'],
                'breakdown': breakdown,
            }
