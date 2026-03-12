# LangGraph Workflow Patterns

**Quick Summary**: LangGraph workflows orchestrate multi-step AI agent execution using state graphs with nodes, edges, and persistent state. CodeMie uses LangGraph for sequential and parallel workflow execution with checkpointing.

**Category**: Workflow
**Complexity**: Medium
**Prerequisites**: LangChain agents, TypedDict, Pydantic models

---

## State Schema Patterns

### TypedDict with Reducers

State schemas use TypedDict with `Annotated` reducers for automatic state merging:

```python
# src/codemie/workflows/models.py:37-43
from typing import TypedDict, Annotated, Sequence
from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages

class AgentMessages(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]  # Auto-merges messages
    context_store: Annotated[dict[str, str], add_or_replace_context_store]  # Custom reducer
    next: Annotated[list, list.__add__]  # Concatenates lists
    final_summary: Annotated[list, list.__add__]
    user_input: str  # Simple field (replaced on update)
```

| Reducer | Behavior | Use Case |
|---------|----------|----------|
| `add_messages` | Appends/updates message sequence | LLM conversation history |
| `list.__add__` | Concatenates lists | Accumulating results |
| Custom reducer | User-defined logic | Context store with deletions |

### Custom Reducers

```python
# src/codemie/workflows/models.py:12-34
CONTEXT_STORE_DELETE_MARKER = "__DELETE_KEY__"

def add_or_replace_context_store(
    left: dict[str, str],
    right: dict[str, str] | None
) -> dict[str, str]:
    """Custom reducer: None clears store, DELETE_MARKER removes keys"""
    if right is None:
        return {}  # Clear context store

    merged = {**left, **right}  # Merge dicts
    # Filter out deletion markers
    return {k: v for k, v in merged.items() if v != CONTEXT_STORE_DELETE_MARKER}
```

### Supervisor State Extension

```python
# src/codemie/workflows/models.py:45-48
class SupervisorAgentMessages(AgentMessages):  # Extends base state
    task: Optional[str]  # Current task for agent
    reasoning: Optional[str]  # Supervisor's reasoning
```

---

## StateGraph Creation & Compilation

### Basic Workflow Creation

```python
# src/codemie/workflows/workflow.py:232-238
from langgraph.graph import StateGraph

def init_state_graph() -> StateGraph:
    return StateGraph(AgentMessages)  # Pass state schema type

workflow = init_state_graph()
workflow.set_entry_point("agent_node")  # First node to execute
# Add nodes and edges (see below)
compiled = workflow.compile(debug=True)  # Returns CompiledStateGraph
```

### Compilation with Checkpointing

```python
# src/codemie/workflows/workflow.py:224-230
from codemie.workflows.checkpoint_saver import CheckpointSaver

compile_args = {"debug": config.verbose}

if interrupt_before_states:
    compile_args["interrupt_before"] = interrupt_before_states  # Pause before specific nodes
    compile_args["checkpointer"] = CheckpointSaver()  # Enable persistence

compiled_workflow = workflow.compile(**compile_args)
```

---

## Node Implementation Patterns

### Base Node Pattern

All custom nodes inherit from `BaseNode[StateSchemaType]`:

```python
# src/codemie/workflows/nodes/base_node.py:41-95 (simplified)
from abc import ABC, abstractmethod
from typing import Generic, TypeVar, Type, Any

StateSchemaType = TypeVar('StateSchemaType', bound=dict[str, Any])

class BaseNode(ABC, Generic[StateSchemaType]):
    def __init__(self, callbacks, workflow_execution_service, thought_queue,
                 node_name="", execution_id=None, workflow_state=None, **kwargs):
        self.node_name = node_name
        self.callbacks = callbacks
        self.workflow_execution_service = workflow_execution_service
        self.thought_queue = thought_queue
        self.execution_id = execution_id
        self.workflow_state = workflow_state
        self.kwargs = kwargs

    @abstractmethod
    def execute(self, state_schema: Type[StateSchemaType], execution_context: dict) -> Any:
        """Core node logic - must implement"""
        pass

    @abstractmethod
    def get_task(self, state_schema: Type[StateSchemaType], *arg, **kwargs) -> str:
        """Task description for logging"""
        pass

    def __call__(self, state_schema: Type[StateSchemaType]) -> dict:
        """Lifecycle: callbacks → execute → post_process → finalize"""
        # ... lifecycle management
```

**Lifecycle Hooks**:
- `before_execution()`: Pre-execution validation/routing
- `execute()`: Core logic (required)
- `post_process_output()`: Format raw output
- `after_execution()`: Cleanup/logging
- `finalize_and_update_state()`: Update state schema

### Agent Node Pattern

```python
# src/codemie/workflows/nodes/agent_node.py:31-90 (simplified)
from codemie.agents.assistant_agent import AIToolsAgent

class AgentNode(BaseNode[AgentMessages]):
    def execute(self, state_schema: Type[StateSchemaType], execution_context: dict) -> Any:
        assistant: AIToolsAgent = execution_context.get("assistant")
        messages = get_messages_from_state_schema(state_schema=state_schema)
        return assistant.invoke_task(
            workflow_input=self.get_task(state_schema, self.args, self.kwargs),
            history=messages
        )
```

### Tool Node Pattern

```python
# src/codemie/workflows/nodes/tool_node.py:26-87 (simplified)
class ToolNode(BaseNode[AgentMessages]):
    """Execute tools directly without LLM (MCP or regular toolkits)"""

    def execute(self, state_schema: Type[StateSchemaType], execution_context: dict) -> Any:
        if self._tool_config.mcp_server:
            return self._execute_mcp_tool(state_schema)  # MCP server tool
        else:
            return self._execute_regular_tool(state_schema)  # Toolkit tool
```

### Adding Nodes to Workflow

```python
# src/codemie/workflows/workflow.py:242-264
def initialize_nodes(workflow: StateGraph, workflow_config: WorkflowConfig):
    for state in workflow_config.states:
        retry_policy = workflow_config.get_effective_retry_policy(state=state)

        if state.assistant_id:
            node = init_agent_node(state)
            workflow.add_node(state.id, node, retry=retry_policy)
        elif state.custom_node_id:
            node = init_custom_node(state)
            workflow.add_node(state.id, node, retry=retry_policy)
        elif state.tool_id:
            node = init_tool_node(state)
            workflow.add_node(state.id, node, retry=retry_policy)
```

---

## Edge Patterns

### Normal Edges (Sequential)

```python
# src/codemie/workflows/workflow.py:442
workflow.add_edge(source_node, target_node)  # Simple A → B transition
workflow.add_edge("agent_1", "agent_2")  # agent_1 → agent_2
workflow.add_edge("result_finalizer", END)  # Final node → workflow end
```

### Conditional Edges (Branching)

```python
# src/codemie/workflows/workflow.py:456-464
from langchain.graph import END

def routing_function(state_schema: AgentMessages) -> str:
    """Return next node name based on state"""
    if state_schema.get("next_key") == "then_branch":
        return "then_node"
    else:
        return "otherwise_node"

# Map returned string to actual node names
transition_nodes = {
    "then_node": "then_node",
    "otherwise_node": "otherwise_node",
    "source": "source"  # Include source for routing
}

workflow.add_conditional_edges(
    "source",
    routing_function,
    transition_nodes
)
```

### Switch Edges (Multi-Branch)

```python
# src/codemie/workflows/workflow.py:466-483
def switch_routing(state_schema: AgentMessages) -> str:
    """Route to multiple destinations based on state"""
    switch_value = state_schema.get("operation_type")

    if switch_value == "create":
        return "create_node"
    elif switch_value == "update":
        return "update_node"
    elif switch_value == "delete":
        return "delete_node"
    else:
        return "default_node"

transition_map = {
    "create_node": "create_node",
    "update_node": "update_node",
    "delete_node": "delete_node",
    "default_node": "default_node"
}

workflow.add_conditional_edges("switch_source", switch_routing, transition_map)
```

### Parallel Edges (Fan-out)

```python
# src/codemie/workflows/workflow.py:444-447
# Send to multiple nodes simultaneously
for next_state in ["node_a", "node_b", "node_c"]:
    workflow.add_edge("source", next_state)
# All execute in parallel, results merged by reducers
```

### Map-Reduce Pattern (Dynamic Parallelization)

```python
# src/codemie/workflows/workflow.py:354-406
from langgraph.types import Send

def continue_iteration(state_schema: dict, workflow_state) -> List[Send]:
    """Dynamically create parallel tasks from list"""
    items_to_process = state_schema.get("tasks", [])
    messages = state_schema.get("messages")

    return [
        Send(
            "process_task_node",  # Target node
            {
                "task": item,  # Individual task
                "messages": messages.copy(),  # Cloned state
                "context_store": {**context_store},  # Cloned context
                "iteration_number": index + 1,
                "total_iterations": len(items_to_process)
            }
        )
        for index, item in enumerate(items_to_process)
    ]

workflow.add_conditional_edges(
    "map_source",
    continue_iteration,
    {"process_task_node": "process_task_node"}
)
```

---

## Workflow Invocation Patterns

### Streaming Invocation

```python
# src/codemie/workflows/workflow.py:535-576
from langchain_core.runnables import RunnableConfig

workflow = init_workflow()
inputs = {"messages": [HumanMessage(content=user_input)], "context_store": {}}

graph_config = RunnableConfig(
    configurable={"thread_id": execution_id},
    recursion_limit=100,
    max_concurrency=10,
    callbacks=[]
)

for chunk in workflow.stream(inputs, config=graph_config):
    if not chunk:
        continue

    for node_name, value in chunk.items():
        print(f"Node {node_name}: {value}")
        if isinstance(value, dict) and "final_summary" in value:
            print(f"Summary: {value['final_summary']}")
```

### Resuming from Checkpoint

```python
# src/codemie/workflows/workflow.py:583-587
resume_execution = True
execution_id = "previous-execution-id"

if resume_execution and execution_id:
    inputs = None  # None = use last checkpoint
else:
    inputs = {"messages": [...], "context_store": {}}

# Workflow resumes from last saved checkpoint
for chunk in workflow.stream(inputs, config=graph_config):
    # ... process
```

### Interrupt Pattern (Human-in-Loop)

```python
# src/codemie/workflows/workflow.py:226-228, 564-568
# Compile with interrupt_before
compiled = workflow.compile(
    interrupt_before=["human_review_node"],  # Pause before this node
    checkpointer=CheckpointSaver()
)

# After streaming, check if interrupted
state = workflow.get_state(config=graph_config)
if state.next:  # Has pending next node
    last_message = state.values['messages'][-1].content
    # Prompt user, then resume with new inputs
```

---

## Checkpointing & Persistence

### Checkpoint Saver Implementation

```python
# src/codemie/workflows/checkpoint_saver.py:14-111 (simplified)
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple

class CheckpointSaver(BaseCheckpointSaver):
    """Postgres-backed checkpoint storage"""

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Retrieve checkpoint by execution_id + timestamp"""
        execution_id = config["configurable"]["thread_id"]
        timestamp = config["configurable"].get("thread_ts", None)

        workflow_execution = self._find_workflow_execution(execution_id)
        checkpoints = self._find_checkpoints(workflow_execution, timestamp)

        if len(checkpoints):
            checkpoint = checkpoints[-1]
            return CheckpointTuple(
                config=config,
                checkpoint=self.serde.loads(bytes(checkpoint.data, 'utf-8')),
                metadata=self.serde.loads(bytes(checkpoint.metadata, 'utf-8'))
            )
        return None

    def put(self, config: RunnableConfig, checkpoint, metadata, *args):
        """Save checkpoint to database"""
        execution_config = self._find_workflow_execution(config["configurable"]["thread_id"])
        execution_config.checkpoints.append(
            WorkflowExecutionCheckpoint(
                timestamp=checkpoint['ts'],
                data=self.serde.dumps(checkpoint),
                metadata=self.serde.dumps(metadata)
            )
        )
        execution_config.update(refresh=True)
```

**Checkpoint Storage**: Postgres via SQLModel/SQLAlchemy (not LangGraph's built-in SQLite)

---

## Debugging Techniques

### Graph Visualization

```python
# src/codemie/workflows/workflow.py:165-188
from langchain_core.runnables.graph import CurveStyle, NodeStyles

workflow = WorkflowExecutor.validate_workflow(workflow_config, user)
graph = workflow.get_graph()

# Remove self-loops (technical edges)
graph.edges = [edge for edge in graph.edges if edge.source != edge.target]

mermaid_syntax = graph.draw_mermaid(
    with_styles=False,
    curve_style=CurveStyle.BASIS,
    node_colors=NodeStyles(first="#c3a6ff", last="#c3a6ff", default="#c3a6ff"),
    wrap_label_n_words=9
)
# Render with MermaidService for SVG/PNG
```

### State Inspection

```python
# During streaming
for chunk in workflow.stream(inputs, config=graph_config):
    print(f"State update: {chunk}")  # Each node's output

# After execution
final_state = workflow.get_state(config=graph_config)
print(f"Messages: {final_state.values['messages']}")
print(f"Context store: {final_state.values['context_store']}")
print(f"Next pending node: {final_state.next}")
```

### Callback-Based Debugging

```python
# src/codemie/workflows/workflow.py:212-216
from codemie.workflows.callbacks.graph_callback import LanggraphNodeCallback

graph_callback = LanggraphNodeCallback(
    thought_queue,
    author=ThoughtAuthorType.Agent
)
callbacks = [graph_callback]

# Callbacks receive: on_node_start, on_node_end, on_node_fail
```

---

## Anti-Patterns

### ❌ Mutable State Sharing

**WRONG**:
```python
shared_list = []  # Mutable default

class BadState(TypedDict):
    data: list = shared_list  # Shared across all workflow instances!
```

**RIGHT**:
```python
from typing import Annotated

class GoodState(TypedDict):
    data: Annotated[list, list.__add__]  # Reducer creates new list
```

**Why**: Mutable defaults cause state pollution across workflow instances

### ❌ Missing Error Handling in Nodes

**WRONG**:
```python
def execute(self, state_schema, execution_context):
    return api_call()  # No try/except
```

**RIGHT**:
```python
def execute(self, state_schema, execution_context):
    try:
        return api_call()
    except APIException as e:
        logger.error(f"API call failed: {e}")
        return {"error": str(e), "status": "failed"}
```

**Why**: Uncaught exceptions abort entire workflow; return error state instead

### ❌ Circular Dependencies

**WRONG**:
```python
workflow.add_edge("node_a", "node_b")
workflow.add_edge("node_b", "node_a")  # Infinite loop!
```

**RIGHT**:
```python
workflow.add_edge("node_a", "node_b")
workflow.add_conditional_edges("node_b", lambda s: "END" if s.get("done") else "node_a", {...})
```

**Why**: Use conditional edges with exit conditions to avoid infinite loops

### ❌ Forgetting Checkpointer for Interrupts

**WRONG**:
```python
workflow.compile(interrupt_before=["review_node"])  # No checkpointer!
```

**RIGHT**:
```python
workflow.compile(
    interrupt_before=["review_node"],
    checkpointer=CheckpointSaver()  # Required for interrupts
)
```

**Why**: `interrupt_before` requires checkpointer to save/resume state

---

## When to Use

### Use LangGraph Workflows When

- [x] Need multi-step agent orchestration with state persistence
- [x] Require conditional branching or parallel execution
- [x] Need human-in-the-loop approvals (interrupt pattern)
- [x] Want automatic state management with reducers
- [x] Need workflow resumption after failures/restarts

### Don't Use LangGraph Workflows When

- [ ] Single-shot agent calls (use AIToolsAgent directly)
- [ ] No state sharing between steps required
- [ ] Simple linear execution without branching
- [ ] No need for checkpointing or resumption

---

## References

- **Source**: `src/codemie/workflows/workflow.py`, `src/codemie/workflows/models.py`, `src/codemie/workflows/nodes/base_node.py`
- **Related Patterns**: [LangChain Agent Patterns](../agents/langchain-agent-patterns.md)
- **External Resources**: [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
