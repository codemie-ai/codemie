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

"""
Smart ReAct agent builder that wraps create_react_agent with dynamic tool selection.

This module provides full compatibility with create_react_agent features (including
structured output via response_format) while adding smart tool selection on top.

The key insight is that we wrap create_react_agent in a parent graph that handles
tool selection, rather than reimplementing the react agent logic.
"""

from collections.abc import Callable
from typing import Literal, Optional, Union

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel

from codemie.agents.smart_tool_selector import SmartToolSelector
from codemie.agents.smart_tool_state import SmartToolState, ToolRegistry
from codemie.configs import config
from codemie.configs.logger import logger


def create_smart_react_agent(
    model: BaseChatModel,
    tools: list[BaseTool],
    *,
    prompt: Optional[str] = None,
    response_format: Optional[Union[dict, BaseModel]] = None,
    name: Optional[str] = None,
    tool_selection_enabled: bool = False,
    tool_selection_limit: int = 3,
    parallel_tool_calls: Optional[bool] = None,
    pre_model_hook: Optional[Callable[[dict], dict]] = None,
):
    """
    Create a ReAct agent with smart tool selection.

    This function creates an agent that dynamically selects relevant tools using
    semantic search via ToolkitLookupService, while maintaining full compatibility
    with create_react_agent features including structured output.

    The implementation uses a wrapper graph that:
    1. Selects relevant tools based on the user query
    2. Invokes a standard create_react_agent with only those tools
    3. Preserves all create_react_agent features (response_format, hooks, etc.)

    Args:
        model: Language model to use for the agent
        tools: List of all available tools (will be filtered dynamically)
        prompt: System prompt for the agent
        response_format: Pydantic model or dict for structured output (fully supported)
        name: Name for the agent
        tool_selection_enabled: Whether to enable smart tool selection
        tool_selection_limit: Maximum number of tools to select per query
        parallel_tool_calls: Whether to enable parallel tool calling (None = use model default)

    Returns:
        Compiled StateGraph with smart tool selection capabilities
    """
    # Check if smart selection should be used
    use_smart_selection = tool_selection_enabled and len(tools) >= config.TOOL_SELECTION_THRESHOLD

    if not use_smart_selection:
        return _create_standard_react_agent(
            model=model,
            tools=tools,
            prompt=prompt,
            response_format=response_format,
            name=name,
            parallel_tool_calls=parallel_tool_calls,
            pre_model_hook=pre_model_hook,
        )

    # Build tool registry
    tool_registry_obj = ToolRegistry(tools)
    tool_selector = SmartToolSelector(
        tool_registry=tool_registry_obj.registry,
        default_limit=tool_selection_limit,
    )
    logger.info(f"SmartReactAgent: Dynamic tool selection enabled (total={len(tools)}, limit={tool_selection_limit})")

    # Build workflow graph with extracted nodes
    workflow = _build_smart_react_workflow(
        model=model,
        tools=tools,
        tool_registry_obj=tool_registry_obj,
        tool_selector=tool_selector,
        tool_selection_limit=tool_selection_limit,
        prompt=prompt,
        response_format=response_format,
        name=name,
        parallel_tool_calls=parallel_tool_calls,
        pre_model_hook=pre_model_hook,
    )

    # Compile and return
    compiled_graph = workflow.compile()

    logger.debug("SmartReactAgent: Graph compiled with tool selection and agent nodes")

    return compiled_graph


def _create_standard_react_agent(
    model: BaseChatModel,
    tools: list[BaseTool],
    prompt: Optional[str],
    response_format: Optional[Union[dict, BaseModel]],
    name: Optional[str],
    parallel_tool_calls: Optional[bool],
    pre_model_hook: Optional[Callable[[dict], dict]] = None,
):
    """Create a standard ReAct agent without smart tool selection."""
    logger.debug(
        f"SmartReactAgent: Using standard agent (tools={len(tools)}, threshold={config.TOOL_SELECTION_THRESHOLD})"
    )

    # Handle parallel_tool_calls setting if specified
    agent_model = model
    if parallel_tool_calls is not None:
        agent_model = model.bind_tools(tools, parallel_tool_calls=parallel_tool_calls)

    return create_react_agent(
        model=agent_model,
        tools=tools,
        prompt=prompt,
        response_format=response_format,
        name=name,
        pre_model_hook=pre_model_hook,
    )


def _build_smart_react_workflow(
    model: BaseChatModel,
    tools: list[BaseTool],
    tool_registry_obj: ToolRegistry,
    tool_selector: SmartToolSelector,
    tool_selection_limit: int,
    prompt: Optional[str],
    response_format: Optional[Union[dict, BaseModel]],
    name: Optional[str],
    parallel_tool_calls: Optional[bool],
    pre_model_hook: Optional[Callable[[dict], dict]] = None,
) -> StateGraph:
    """Build the smart ReAct workflow graph with tool selection and agent nodes."""
    # Build the wrapper graph
    workflow = StateGraph(SmartToolState)

    # Define tool selection node
    def tool_selection_node(state: SmartToolState) -> dict:
        return _execute_tool_selection(
            state=state,
            tool_selector=tool_selector,
            tool_selection_limit=tool_selection_limit,
        )

    # Define agent node
    def agent_node(state: SmartToolState) -> dict:
        return _execute_agent_with_selected_tools(
            state=state,
            model=model,
            all_tools=tools,
            tool_registry_obj=tool_registry_obj,
            prompt=prompt,
            response_format=response_format,
            name=name,
            parallel_tool_calls=parallel_tool_calls,
            pre_model_hook=pre_model_hook,
        )

    # Define routing function
    def should_continue(state: SmartToolState) -> Literal["tool_selection", "agent", END]:
        return _determine_next_step(state)

    # Add nodes
    workflow.add_node("tool_selection", tool_selection_node)
    workflow.add_node("agent", agent_node)

    # Set entry point
    workflow.set_entry_point("tool_selection")

    # Add edges
    # Flow: tool_selection → agent → END
    workflow.add_edge("tool_selection", "agent")
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {
            "tool_selection": "tool_selection",  # Re-select tools (rare)
            "agent": "agent",  # Continue with agent (rare)
            END: END,  # Done (typical path)
        },
    )

    return workflow


def _execute_tool_selection(
    state: SmartToolState,
    tool_selector: SmartToolSelector,
    tool_selection_limit: int,
) -> dict:
    """Select relevant tools based on the current message."""
    messages = state.get("messages", [])
    last_message = messages[-1] if messages else None

    if not last_message:
        # No messages yet, use default tools
        logger.debug("ToolSelectionNode: No messages, using default tools")
        tool_ids, tool_instances = tool_selector.get_default_tools(count=tool_selection_limit)
    else:
        # Extract query from last message and select tools
        tool_ids, tool_instances = _select_tools_for_message(
            last_message=last_message,
            tool_selector=tool_selector,
            tool_selection_limit=tool_selection_limit,
            message_history=messages[:-1],  # Exclude last message from history
        )

    logger.debug(f"ToolSelectionNode: Selected {len(tool_instances)} tools: {[t.name for t in tool_instances]}")

    # Update state with ONLY tool IDs (tool instances kept in closure)
    # This ensures state is serializable for LangGraph checkpointing
    return {
        "selected_tool_ids": tool_ids,
    }


def _select_tools_for_message(
    last_message,
    tool_selector: SmartToolSelector,
    tool_selection_limit: int,
    message_history: list,
) -> tuple[list[str], list[BaseTool]]:
    """Select tools based on the message content using semantic search."""
    # Extract query from last message
    query = str(last_message.content) if hasattr(last_message, 'content') else str(last_message)

    # Select tools using semantic search
    tool_ids, tool_instances = tool_selector.select_tools(
        query=query,
        limit=tool_selection_limit,
        history=message_history,
    )

    # Fallback if selection returns no tools
    if not tool_instances:
        logger.warning("ToolSelectionNode: Selection returned no tools, using defaults")
        return tool_selector.get_default_tools(count=tool_selection_limit)

    return tool_ids, tool_instances


def _execute_agent_with_selected_tools(
    state: SmartToolState,
    model: BaseChatModel,
    all_tools: list[BaseTool],
    tool_registry_obj: ToolRegistry,
    prompt: Optional[str],
    response_format: Optional[Union[dict, BaseModel]],
    name: Optional[str],
    parallel_tool_calls: Optional[bool],
    pre_model_hook: Optional[Callable[[dict], dict]] = None,
) -> dict:
    """Execute create_react_agent with currently available tools."""
    # Get selected tool IDs from state
    selected_tool_ids = state.get("selected_tool_ids", [])
    available_tools = _resolve_available_tools(selected_tool_ids, all_tools, tool_registry_obj)

    logger.debug(
        f"AgentNode: Invoking create_react_agent. "
        f"AvailableTools={[t.name for t in available_tools]}, "
        f"HasStructuredOutput={response_format is not None}"
    )

    # Create sub-agent with configured model and tools
    sub_agent = _create_sub_agent(
        model=model,
        available_tools=available_tools,
        prompt=prompt,
        response_format=response_format,
        name=name,
        parallel_tool_calls=parallel_tool_calls,
        pre_model_hook=pre_model_hook,
    )

    # Invoke sub-agent with current state
    try:
        result = sub_agent.invoke(state)
        logger.debug(f"AgentNode: Agent invocation completed. ResultKeys={list(result.keys())}")
        return result
    except Exception as e:
        logger.error(f"AgentNode: Agent invocation failed: {e}", exc_info=True)
        # Re-raise to let LangGraph handle the error
        raise


def _resolve_available_tools(
    selected_tool_ids: list[str],
    all_tools: list[BaseTool],
    tool_registry_obj: ToolRegistry,
) -> list[BaseTool]:
    """Resolve which tools to make available to the agent."""
    if not selected_tool_ids:
        logger.warning("AgentNode: No tool IDs in state, using all tools as fallback")
        return all_tools

    # Look up tool instances from registry by IDs
    available_tools = tool_registry_obj.get_tools_by_ids(selected_tool_ids)

    if not available_tools:
        logger.warning("AgentNode: No tools found for IDs in registry, using all tools as fallback")
        return all_tools

    return available_tools


def _create_sub_agent(
    model: BaseChatModel,
    available_tools: list[BaseTool],
    prompt: Optional[str],
    response_format: Optional[Union[dict, BaseModel]],
    name: Optional[str],
    parallel_tool_calls: Optional[bool],
    pre_model_hook: Optional[Callable[[dict], dict]] = None,
):
    """Create a sub-agent with selected tools."""
    # Handle parallel_tool_calls setting if specified
    agent_model = model
    if parallel_tool_calls is not None:
        # Bind tools with parallel_tool_calls setting before passing to create_react_agent
        agent_model = model.bind_tools(available_tools, parallel_tool_calls=parallel_tool_calls)

    # Create agent with selected tools
    # IMPORTANT: This creates a NEW agent instance with only selected tools
    # and preserves response_format for structured output
    return create_react_agent(
        model=agent_model,
        tools=available_tools,
        prompt=prompt,
        response_format=response_format,  # ✅ Structured output preserved
        name=name,
        pre_model_hook=pre_model_hook,
    )


def _determine_next_step(state: SmartToolState) -> Literal["tool_selection", "agent", END]:
    """Determine the next step in the workflow based on current state."""
    messages = state.get("messages", [])
    selected_tool_ids = state.get("selected_tool_ids", [])

    # Initial state conditions: need to select tools
    if not selected_tool_ids:
        logger.debug("Router: No tools selected, routing to tool_selection")
        return "tool_selection"

    if not messages:
        logger.debug("Router: No messages, routing to tool_selection")
        return "tool_selection"

    # Check for completion signals
    last_message = messages[-1]
    if _is_agent_complete(last_message):
        return END

    # Default path: end after one agent node execution
    # The agent_node creates a complete create_react_agent that handles
    # its own loop, so typically we're done after one agent_node execution
    logger.debug("Router: Continuing to END (default)")
    return END


def _is_agent_complete(last_message) -> bool:
    """Check if the agent has completed its execution."""
    # Check if this is the final response from agent
    # create_react_agent typically marks completion by not having tool_calls
    if hasattr(last_message, 'tool_calls') and not last_message.tool_calls:
        # No tool calls means agent is done
        logger.debug("Router: Agent complete (no tool calls), routing to END")
        return True

    # Check for additional_kwargs which might indicate completion
    if hasattr(last_message, 'additional_kwargs') and last_message.additional_kwargs.get('finish_reason') == 'stop':
        # Some models use different completion signals
        logger.debug("Router: Agent complete (finish_reason=stop), routing to END")
        return True

    return False
