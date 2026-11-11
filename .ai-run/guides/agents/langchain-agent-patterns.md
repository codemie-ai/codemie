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

## Output-Limit Truncation (LangGraphAgent)

A model reply cut off at the output limit (`finish_reason=length`) while it writes a tool call stops the run with `TokenLimitExceededException`. A cut-off call is never executed. The user gets the limit message plus a status report of what the turn's tool calls already did.

### Stopping the run

Both graph builders wrap the post-model hook with `with_truncation_guard`, through `_guarded_post_model_hook`. The guard runs before the confirmation gate and the tools node. A sub-assistant runs inside its parent's node and does not wait for the stream consumer, so only this guard stops its tools node in time. The pre-model truncation hook is a backstop for graphs built without the guard. It reads the graph's own messages from `ORIGINAL_MESSAGES_KEY`, not the copy that earlier composed hooks replaced.

| Avoid | Prefer |
|---|---|
| Passing `self._confirmation_hook(...)` as `post_model_hook` in a new builder | `self._guarded_post_model_hook` |
| Raising `TokenLimitExceededException` without the run's messages | Fill `turn_messages` and `truncated_message`. The report is built from them. |
| Building sub-assistants without their agent objects | Pass `subagent_agents` next to `subagents`. The report needs their tools and their hide-tool-outputs setting. |

Evidence: `src/codemie/agents/supervisor/pre_model_hooks.py:135`, `:28`; `src/codemie/agents/langgraph_agent.py:380`, `:408`, `:411`; `src/codemie/service/assistant/assistant_engine_builder.py:357`.

### Status report

`_with_truncation_report` adds the report to the error message. `build_status_report` never raises; when the run's messages are unknown it returns `None`, and the message stays as it was.

| Case | Report |
|---|---|
| Normal | One extra model call without tools (`max_tokens=1024`, timed out after `AGENT_TRUNCATION_STATUS_REPORT_TIMEOUT_SECONDS`) |
| Timeout, model error, empty or cut-off report, or a report that names an issue key (of a project already seen) that no call or the request mentions | A list built by code from the same facts |
| `AGENT_TRUNCATION_STATUS_REPORT_ENABLED=False` or the stream is closed | The code-built list, without a model call |
| The assistant, its pinned version's master, or any sub-assistant hides tool outputs | Created keys only, without a model call. This fails closed: a master that cannot be read counts as hiding (`is_effectively_hidden`). |

Keep `AGENT_TRUNCATION_STATUS_REPORT_TIMEOUT_SECONDS` well below `AI_AGENT_QUEUE_POLL_TIMEOUT_SECONDS`, because on the streaming path the wait counts against the stream's inactivity timeout. The keys a call wrote come from the tool's `written_refs` (see `.ai-run/guides/agents/custom-tool-creation.md`). The report reads a per-run message log, `_run_messages`. A confirmation resume runs on a new agent, so its log is seeded from the checkpoint with `get_state(subgraphs=True)`. That brings in the calls sub-agents made before the pause.

Evidence: `src/codemie/agents/langgraph_agent.py:1290`, `:1319`, `:1448`, `:1462`; `src/codemie/agents/truncation_report.py:463`; `src/codemie/agents/truncation_run_log.py`; `src/codemie/service/assistant/tool_output_visibility.py:80`; `src/codemie/configs/config.py:903`.
