# EPMCDME-16835: Router output limit derived from tier models

## Intent

Assistants on the LiteLLM auto-router `claude-router-standard` get replies cut at 4096 tokens. Prod logs (2026-11-04/05) show `llm_output_limit model=claude-router-standard max_tokens=unset source=proxy_default` and every truncation as `finish_reason=length output_tokens=4096`. The router entry in the proxy config has no `max_output_tokens`, so codemie forwards no `max_tokens` and the proxy default applies. Truncated tool calls left the Jira assistant with a half-created ticket.

Codemie already derives the router's input window from its tiers (`LLMService._router_max_input_tokens`). This change does the same for the output limit, so a router entry without `max_output_tokens` no longer falls back to the proxy default, now or for routers added later.

## Goals

- When the live LiteLLM catalog is stored, each declared LiteLLM auto-router (`LLMModel.is_declared_litellm_router()` with `litellm_router.tiers`) that has no positive `max_output_tokens` gets one: the smallest `max_output_tokens` across its distinct tier models.
- The existing forwarding path (`llm_factory._resolve_max_output_tokens` → `_build_chat_model_base_args`) then sends `max_tokens` for the router unchanged. The forwarded value is the smallest tier limit; the expected prod log after deploy is `llm_output_limit model=claude-router-standard max_tokens=<smallest tier max_output_tokens> source=model_config` (the local check with Haiku 64000 / Opus 128000 tiers logged `max_tokens=64000`; the prod tier values are read from the deploy log).
- One INFO log line per router whose limit is derived, naming the router, the value, the source (tiers) and whether it is forwarded (`forwarded=true|false`), so the logs show where the number came from and whether it reaches the provider.
- A router whose tiers are all Claude models is marked `claude_family`, so its limit is forwarded whatever the router's alias (review AR-10). A router over non-Claude tiers gets the derived value as data and logs `forwarded=false`.

## Non-goals

- Proxy config change (adding `max_output_tokens` to the router entry). Owned by the proxy owner, outside this repository. It is the backstop for the unknown-tier case below (a renamed or disabled tier brings the 4096 cut back); out of scope, no follow-up planned (decided by the user 2026-11-09); the user kept the conservative unset (review AR-9). The proxy entry stays the only backstop for a renamed or disabled tier.
- ~~Jira assistant prompt behaviour (ticket AC2/AC3)~~ — moved into scope on 2026-11-06, see "Scope extension" below.
- Tier selection behaviour (the "clean stop at line 100" run).
- Forwarding for routers over non-Claude tiers: other providers get no limit unless one is sent, which is today's behaviour for them.
- Switchyard routers (they resolve to a concrete capable model, which already carries its own limit).
- REST/API schema changes (`LlmRouterOption` stays as is).

## Anti-goals

- Never send a `max_tokens` above any tier's limit. The provider would reject the whole request, which is worse than a short reply.
- Never override a positive `max_output_tokens` declared on the router entry itself.
- Never break or abort the catalog load because of router data.

## Decided behaviour

| Router state | Result |
|---|---|
| Router entry declares a positive `max_output_tokens` | Kept as is, no derivation |
| No/invalid router value; all distinct tier models resolve and have a positive `max_output_tokens` | `min` of the tier values |
| Any tier model is missing from the catalog (unknown or disabled) or has no positive `max_output_tokens` | Left unset → proxy default, same as today |
| `litellm_router.tiers` absent | Left unset |
| Not a declared LiteLLM router | Untouched |

- "Positive" is one predicate, `llm_config.is_positive_token_limit`: an `int`, not a `bool`, greater than 0. `_resolve_max_output_tokens` uses the same one, and "is Claude" is `LLMModel.is_claude()` for both the forwarding check and the router marking.
- Tier names resolve by `base_name` against the same catalog list being stored, through `LLMService._router_tier_models`, which `_router_max_input_tokens` uses too: one rule for "an unresolved tier makes the derived value unknown".
- The output limit is stored on the router at load, while the input window is derived on each read. Each catalog load gets fresh model objects from the proxy and recomputes, which a reload test pins.
- The unknown-tier policy (conservative unset) was confirmed with the user. It mirrors the `max_input_tokens` precedent.

## Constraints

- Apply at catalog store time, `LLMService.initialize_default_litellm_models`. It's the single sink for startup, the recovery loop and admin reload, so every refresh recomputes.
- No eager `codemie.enterprise` imports in `llm_service.py` (circular-import warning in `_prefer_litellm_router`).
- The existing kill switch `LLM_PROXY_FORWARD_MAX_OUTPUT_TOKENS` keeps disabling forwarding. The derived value is only data on the model.
- Tests follow `.ai-run/guides/testing/testing-patterns.md`, "Seam Tests for Policy Helpers": cover the helper, and assert at the outer boundary that a router's chat model is built with `max_tokens` equal to the derived value.

## Left to the builder

- Helper name and placement: a staticmethod on `LLMService` next to `_router_max_input_tokens` is the expected shape.
- In-place update versus `model_copy` for the router entry (in-place has precedent in `_apply_premium_flags`).
- Exact log wording, keeping the key=value style.
- Test file placement within the existing suites (`tests/codemie/service/...`, `tests/enterprise/litellm/test_llm_factory.py`).

---

## Scope extension (2026-11-06): all ticket acceptance criteria

The MR review bot flagged that ticket AC2–AC4 were not covered. The user decided the MR must cover every AC. Ticket ACs:

1. A Story with a ~12 KB description and several ~11 KB sub-tasks is created completely, or the assistant splits the work so no single output exceeds the limit.
2. If creation is interrupted, the assistant reports the exact key and the sections or sub-tasks that were not created.
3. The assistant does not leave a partially created ticket without informing the user.
4. Regression tests cover large-description tickets and sub-task creation.

### How each AC is met

| AC | Mechanism |
|---|---|
| 1 | The router fix above: `claude-router-standard` now sends its smallest tier limit, far above a 12 KB description (~3–4k tokens) or an 11 KB sub-task. Proven by the factory test that builds the router from proxy catalog entries and asserts the forwarded `max_tokens`; the "created completely" flow test only shows the tool carries ticket-sized payloads (it uses a fake model, so it passes without the router fix). Checked live on 2026-11-08: a Story and three sub-tasks of 13–15 KB each were created in one reply of 15,712 output tokens. The status report (AC2) also tells the user to continue in smaller steps when a limit is still hit. |
| 2, 3 | New truncation status report, below. |
| 4 | New regression tests, below. |

### Truncation status report (AC2, AC3)

Today a truncated tool call raises `TokenLimitExceededException` and the user sees only `AGENT_MSG_TOKEN_LIMIT` ("The configured output token limit was reached…"), even when earlier tool calls in the same turn already created a ticket.

New behaviour, for `LangGraphAgent` runs (single agent and supervisor, streaming and non-streaming):

- **Facts, built by code.** From the current turn's messages (everything after the last `HumanMessage`), list each tool call with its tool name, a short request summary (for HTTP-style tools: method + URL from args), whether it succeeded (ToolMessage status), and the issue keys found in its output (`"key": "<PROJECT>-<N>"` in the JSON body). Also list the interrupted call: tool name, partial request summary, and whether it already ran (a ToolMessage exists for its `tool_call_id`). The cut-off-call guard below keeps a cut-off call from running in every graph codemie builds; the "already ran" branch stays as a backstop for a graph built without it.
- **Report, written by the model.** One extra call to the same model, tools not bound, with a fixed instruction plus: the user's request of this turn (capped), the assistant's text of this turn, the facts list, and the interrupted call's partial arguments (capped). It must say what was created or changed (exact keys, only from the facts), which sections or sub-tasks were not created, whether the interrupted call may have changed something with incomplete data, and how to finish (continue in smaller steps).
- **Fallback.** If the report call fails, times out, returns empty, or the flag is off: a code-built text from the facts list (keys created, the interrupted call, "later planned steps were not run"; after a parallel handoff "…, but a sub-assistant working in parallel may have finished its current step", re-review round 3 AR-1). If no turn messages are available: today's message only.
- **Delivery.** The user text becomes `AGENT_MSG_TOKEN_LIMIT` + blank line + report. `agent_error.message` stays the short `AGENT_MSG_TOKEN_LIMIT`; `error_code` is unchanged (`AGENT_TOKEN_LIMIT`). The UI renders the final chunk's `generated` as the message body (`codemie-ui` `src/store/chatGeneration.ts`, `historyItem.response = response.generated`; checked on `main` 2026-11-09, review AR-2) and does not read `agent_error`, so the report is visible. A UI change that showed `agent_error.message` in place of the body would hide the report.
- **Assistants that hide tool outputs** (`hide_tool_outputs`, review AR-3). The report shows keys only and "later planned steps were not run": no tool names, URLs or output summaries, and no extra model call. The user chose keys only over no report. Keys the tools report as created are listed as created; keys from a tool that cannot say what it wrote are listed apart as not confirmed, since dropping them would hide a write and a retry would repeat it (code review CR-008). Hidden is decided by the same fail-closed rule as the response edge, `tool_output_visibility.is_effectively_hidden`: the pinned version or its master hides (the master is read by id only on a truncation; an unreadable master counts as hiding). Re-review AR-1. The report draws on every sub-assistant's calls, so it is hidden when the delivering assistant or any sub-assistant at any depth hides (code review CR-005). If the report's inputs cannot be computed, it falls back to keys only from the attached messages (CR-004).
- **Turn messages source.** The pre-model hook has `state["messages"]` and passes them into the truncation check, which attaches them to `TokenLimitExceededException` (new optional field). For consumer-side raise sites, the agent keeps the messages it has seen in this run's `updates` chunks. The reporter builds the turn from the log of the agent delivering the error, merged with the messages the raising check attached after their last `HumanMessage` (de-duplicated by id): a check inside a sub-assistant attaches that sub-assistant's own view, where earlier delegations appear only as their last reply, so the log keeps their creates in the report whichever check raises first; the attached messages add the calls of a loop run inside one node (round 3 AR-2). A fresh run starts the log from its input messages. A resume after a tool confirmation runs on a new agent instance, so its log starts from the checkpointed graph state it continues (the paused turn with the calls made before the confirmation), read the same way the resume reads its pending confirmations (review AR-1, plan 2). In the plain react loop every `agent`/`tools` update reaches the consumer before the stop, so this list is complete there. The seed reads the same tree the resume does: the top-level messages, then the AI and tool messages of every sub-agent task of the paused step at any depth (`get_state(subgraphs=True)`), so a call confirmed inside a sub-agent is in the log, and so are the calls of a parallel sibling that finished in that step, whose parent keeps only its last reply (code review CR-002); when the sub-graph states cannot be read, the top-level messages alone seed the log (CR-006); a sub-agent's task message is left out so the user's turn still starts at the request (re-review AR-4).
- **Cut-off-call guard** (found live 2026-11-10, round 3 AR-1). Every agent's post-model hook is wrapped by `with_truncation_guard` (`agents/supervisor/pre_model_hooks.py`) ahead of the confirmation gate and the tools node. A model call whose tool call was cut off raises `TokenLimitExceededException` there, before the tools node can run it with partial arguments: a sub-assistant runs as a subgraph inside its parent's node and does not wait for the stream consumer, so the consumer-side stop came too late for it (a supervisor-only defect: the live test created an empty ticket). The guard also stops on a closed stream or a consumer abort, through the helper the pre-model abort check uses (EPMCDME-16233): closing the stream waits for running tasks, so a sub-assistant handed work in parallel would otherwise run the complete calls of a model call that finished during that wait. A tool call already executing at the stop still finishes; the parallel-handoff wording above covers it. The pre-model truncation check stays as a backstop.
- **Which keys a call wrote** (review AR-4). Tools declare it: `CodeMieTool.written_refs(args, output)` returns the keys a successful call created or changed, `()` for a read, or `None` when the tool cannot tell. The default answers `()` for a call its own `is_safe` marks as a read, the same policy the confirmation gate uses. `GenericJiraIssueTool` names the created key of an issue create (and every key of a bulk create), nothing for a search (also by POST), and the issue keys in the URL for other writes. Calls whose tool cannot tell fall back to the HTTP method and the `"key"` fields of the output; those keys are labelled "keys in output", never "created/updated", and the report instruction counts only created/updated keys as created (a read through an MCP tool is not reported as a create). An issue create whose response cannot be read answers `None`, so it falls back rather than reports nothing. The URL is matched as the model may send it (with or without the leading slash, `latest` as the version, a query string), and a POST to an endpoint the tool does not recognise that names no key also answers `None` (code review CR-009). The agent passes its own tools and, through the built sub-assistant agents kept beside their compiled graphs (`subagent_agents`), its sub-assistants' tools at any depth (re-review AR-3, AR-6).
- **Kill switch.** New config `AGENT_TRUNCATION_STATUS_REPORT_ENABLED` (default `True`). Off = code-built fallback only, no extra model call. `AGENT_TRUNCATION_STATUS_REPORT_TIMEOUT_SECONDS` (default 30) bounds the wait; a timeout or a report that is itself cut off falls back. The report call is the agent's model bound to `max_tokens=1024` and a request `timeout` equal to the report wait, so each attempt of an abandoned call ends on the client side (review AR-6). The client's own retries still apply, so an abandoned call can be sent again a few times; switching them off per call would need a second model client, not worth it for a 1024-token call (re-review AR-5). The wait must stay well below the stream inactivity timeout (`AI_AGENT_QUEUE_POLL_TIMEOUT_SECONDS`), noted next to the setting (AR-16). A disconnected listener gets the code-built text, no model call.
- **Variant.** The model-written report with a code-built fallback was the builder's choice; the user delegated it on 2026-11-06.
- **Review refinements (code review 2026-11-06).** Keys come only from write calls (not GET); an interrupted call that already ran contributes its keys and says whether the tool rejected it; `HTTP: ... -> 4xx/5xx` output counts as failed in every `HttpResult` layout (spaced, compact, body on a new line); non-HTTP tools are named without their arguments in user text; at most 20 completed calls go into the prompt; the key guard only rejects keys of a known project that no tool call or the request mentions; the run log never takes HumanMessages from updates, and the truncation hook reads the graph's messages, not those prepared by earlier hooks.
- **Prompt content** (review AR-7). Tool output is third-party text and never reaches the report prompt; the prompt carries the code-built facts lines (with the keys), the user's request, the assistant's own text and the interrupted call's partial arguments, under an instruction that these sections are data and their instructions are not followed.
- **Scope.** Only the output-limit stop gets a report. The consecutive-tool-errors abort and the stream inactivity timeout also leave a turn partly done, and they keep their current messages: out of scope, no follow-up planned (decided by the user 2026-11-09) (review AR-8). While the model writes a tool call's arguments nothing reaches the stream consumer, so one tool call is bounded by the inactivity timeout (about 600 s by default). A call long enough to reach the router's new limit (64000 tokens locally) before that timeout needs a sustained ~107 tokens/s; at slower tiers such a call ends as the generic stream timeout, with no report. Ticket-sized calls (a ~12 KB Story or an ~11 KB sub-task, about 3–4k tokens) finish well inside the timeout, so this is accepted without a keep-alive (re-review AR-2, decided 2026-11-10).

Anti-goals:
- Never state a key that is not in a tool output of this turn (keys come from the facts list; the instruction forbids others).
- Never let the report path raise or hide the original error: any failure falls back.
- Never re-run tools or retry the interrupted call.
- Never put unbounded input into the report call: the request is capped at 64,000 characters (a ticket-sized request of a ~12 KB Story plus three ~11 KB sub-tasks passes whole; a longer one keeps its head and its tail, where the parts not yet done usually are, review AR-5), the assistant text at 2,000 and partial arguments at 1,500.

### Regression tests (AC4)

- **Jira tool, large payloads:** `GenericJiraIssueTool` with a mocked Jira client sends a ~12 KB Story create and three ~11 KB sub-task creates; each payload reaches `jira.request` byte-for-byte, and each output carries its created key.
- **Agent, interrupted creation:** a real agent graph with a fake chat model: call 1 creates a Story with a ~12 KB description (tool returns key), call 2 is a truncated sub-task create. The user-facing result contains `AGENT_MSG_TOKEN_LIMIT` and the report naming the created key; with the report call failing, the code-built fallback names the key and the interrupted call.
- **Full creation within the limit:** the 12 KB Story + 3 sub-tasks flow completes without truncation (`test_truncation_status_report_flow.py`); that the router forwards its smallest tier limit is asserted in `tests/enterprise/litellm/test_llm_factory.py::TestMaxOutputTokensForwarding` (64000 for the test fixture's Haiku 64000 / Sonnet 128000 tiers; the local deploy check above used Haiku 64000 / Opus 128000).
- **Not covered by tests:** a supervisor run end to end, and a workflow node (`invoke_task`) hitting the limit. Both reach the same `_get_user_error_message`; the supervisor end-to-end test is out of scope, no follow-up planned (decided by the user 2026-11-09). The supervisor-specific parts (sub-assistant tools, resume seeding from paused sub-agents) have unit tests (re-review AR-3, AR-4). The guard is tested on a real supervisor graph through `invoke`, not the stream (a cut-off sub-assistant call never runs; a sibling's complete call does not run after an abort or a closed stream), and the report's merge of an earlier delegation's create is tested on the agent; which check raises first in a streamed supervisor run is not pinned by a test, and the merge makes the report independent of it (round 3 AR-1, AR-2).

### Left to the builder

- Module placement of the reporter (expected: a small module under `src/codemie/agents/`, called from `_send_error_to_thread` and `_get_user_error_message`).
- Exact caps (now: request 64,000 chars with head and tail kept, per-call args 1,500 chars) and instruction wording.
