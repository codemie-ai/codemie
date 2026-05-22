# LangGraph Workflows

## Workflow Executor

Use the existing executor and node model for workflow behavior.

| Avoid | Prefer |
|---|---|
| Creating a separate graph execution path for one workflow | Extend `WorkflowExecutor`, nodes, or validation utilities |
| Bypassing workflow config parsing | Use existing config parsing and validation |

Evidence: `WorkflowExecutor` imports `StateGraph` and workflow validation utilities at `src/codemie/workflows/workflow.py:29`.

## Nodes And State

Keep node behavior in workflow node packages and state transitions in workflow utilities/config.

| Avoid | Prefer |
|---|---|
| Embedding node-specific behavior in services | Add or extend workflow nodes |
| Manually evaluating transition expressions in feature code | Use workflow utility functions |

Evidence: workflow nodes are imported from `codemie.workflows.nodes` at `src/codemie/workflows/workflow.py:86`.
