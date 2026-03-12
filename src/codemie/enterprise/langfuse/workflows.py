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

from __future__ import annotations

from typing import Any

# Import dependencies from the same package
from .dependencies import is_langfuse_enabled

# Store workflow trace contexts in-memory (keyed by execution_id)
_workflow_trace_contexts: dict[str, Any] = {}


def create_workflow_trace_context(
    execution_id: str,
    workflow_id: str | None,
    workflow_name: str,
    user_id: str | None,
    session_id: str | None = None,
    tags: list[str] | None = None,
):
    """
    Create workflow trace context if Langfuse enabled.

    This helper encapsulates all Langfuse enterprise logic for workflow trace creation.
    Returns None if enterprise package not available or Langfuse disabled.

    Args:
        execution_id: Workflow execution identifier (required)
        workflow_id: Workflow configuration identifier
        workflow_name: Workflow name (required)
        user_id: User identifier
        session_id: Session identifier for Langfuse. If not provided, execution_id will be used
        tags: Additional tags for trace

    Returns:
        TraceContext if created successfully, None otherwise

    Usage:
        # In workflow.py _prepare_graph_config():
        trace_context = create_workflow_trace_context(
            execution_id=self.execution_id,
            workflow_id=self.workflow_config.id,
            workflow_name=self.workflow_config.name,
            user_id=str(self.user.id),
            session_id=self.session_id  # Optional
        )
        if trace_context:
            # Use metadata from trace_context
            graph_config["metadata"].update(trace_context.metadata)
    """
    from codemie.configs import logger

    if not is_langfuse_enabled():
        return None

    try:
        from codemie.enterprise import LangfuseContextManager, build_workflow_metadata

        # Use provided session_id, fallback to execution_id for backward compatibility
        effective_session_id = session_id if session_id is not None else execution_id

        # Build standardized metadata with the resolved session_id
        metadata = build_workflow_metadata(
            execution_id=execution_id,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            user_id=user_id,
            session_id=effective_session_id,
            tags=tags,
        )

        # Create and store context
        trace_context = LangfuseContextManager.create_workflow_trace_context(
            execution_id=execution_id,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            session_id=effective_session_id,
            user_id=user_id,
            tags=metadata.get("langfuse_tags", []),
            metadata=metadata,
        )

        logger.info(
            f"Created workflow trace context: execution_id={execution_id}, "
            f"session_id={effective_session_id}, workflow={workflow_name}"
        )

        return trace_context

    except Exception as e:
        logger.warning(f"Failed to create workflow trace context: {e}")
        return None


def get_workflow_trace_context(execution_id: str):
    """
    Get workflow trace context if Langfuse enabled.

    Args:
        execution_id: Workflow execution identifier

    Returns:
        TraceContext if found, None otherwise

    Usage:
        # In agent_node.py _get_execution_context():
        trace_context = get_workflow_trace_context(self.execution_id)
        if trace_context:
            assistant = init_assistant(trace_context=trace_context, ...)
    """
    from codemie.configs import logger

    if not is_langfuse_enabled():
        return None

    try:
        from codemie.enterprise import LangfuseContextManager

        return LangfuseContextManager.get_current_trace_context(execution_id)

    except Exception as e:
        logger.warning(f"Failed to get workflow trace context: {e}")
        return None


def clear_workflow_trace_context(execution_id: str) -> bool:
    """
    Clear workflow trace context if Langfuse enabled.

    Should be called in workflow finally block to prevent memory leaks.

    Args:
        execution_id: Workflow execution identifier

    Returns:
        True if cleared successfully, False otherwise

    Usage:
        # In workflow.py _execute() finally block:
        finally:
            self.thought_queue.close()
            if config.LANGFUSE_TRACES:
                clear_workflow_trace_context(self.execution_id)
    """
    from codemie.configs import logger

    if not is_langfuse_enabled():
        return False

    try:
        from codemie.enterprise import LangfuseContextManager

        cleared = LangfuseContextManager.clear_trace_context(execution_id)
        if cleared:
            logger.debug(f"Cleared workflow trace context: execution_id={execution_id}")
        return cleared

    except Exception as e:
        logger.warning(f"Failed to clear workflow trace context: {e}")
        return False


def build_agent_metadata_with_workflow_context(
    agent_name: str,
    conversation_id: str,
    llm_model: str,
    username: str | None = None,
    tags: list[str] | None = None,
    trace_context=None,
) -> dict:
    """
    Build agent metadata with optional workflow context.

    This helper uses the centralized metadata builder from enterprise package.
    Falls back to basic metadata if enterprise not available.

    Args:
        agent_name: Agent name
        conversation_id: Conversation/session ID
        llm_model: LLM model identifier
        username: User name
        tags: Additional tags
        trace_context: Optional TraceContext for workflow nesting

    Returns:
        Dictionary with Langfuse metadata

    Usage:
        # In agents/utils.py get_run_config():
        metadata = build_agent_metadata_with_workflow_context(
            agent_name=agent_name,
            conversation_id=conversation_id,
            llm_model=llm_model,
            username=username,
            tags=langfuse_tags,
            trace_context=trace_context,
        )
    """
    from codemie.configs import logger

    # Try to use enterprise metadata builder
    if is_langfuse_enabled():
        try:
            from codemie.enterprise import build_agent_metadata

            return build_agent_metadata(
                agent_name=agent_name,
                conversation_id=conversation_id,
                llm_model=llm_model,
                username=username,
                tags=tags,
                trace_context=trace_context,
            )
        except Exception as e:
            logger.warning(f"Failed to build agent metadata via enterprise: {e}")
            # Fall through to basic metadata

    # Fallback: basic metadata (enterprise not available)
    return {
        "langfuse_session_id": conversation_id,
        "langfuse_user_id": username if username else "unknown",
        "langfuse_tags": tags or [],
        "run_name": agent_name,
        "llm_model": llm_model,
    }
