# LangChain Agent Patterns

## Agent Runtime

Keep agent construction aligned with existing `AIToolsAgent` behavior.

| Avoid | Prefer |
|---|---|
| Creating a parallel custom agent runtime for one feature | Extend or configure existing agent classes |
| Bypassing callbacks and monitoring | Reuse callback setup and run config helpers |

Evidence: `AIToolsAgent` composes LangChain agents, callbacks, tools, and request context at `src/codemie/agents/assistant_agent.py:136`.

## Tool Execution Results

Normalize tool and model outputs before passing them through workflows.

| Avoid | Prefer |
|---|---|
| Passing arbitrary objects as message content | Serialize dicts/models to strings or JSON |
| Dropping intermediate steps accidentally | Preserve intermediate steps when response shape includes them |

Evidence: `TaskResult.from_agent_response` normalizes output at `src/codemie/agents/assistant_agent.py:99`.
