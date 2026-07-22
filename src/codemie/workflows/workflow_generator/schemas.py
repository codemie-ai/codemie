# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field, model_validator


class RetryPolicy(BaseModel):
    initial_interval: Optional[int] = Field(
        default=None,
        description="Seconds before first retry",
    )
    backoff_factor: Optional[float] = Field(
        default=None,
        description="Multiplier applied to interval after each retry",
    )
    max_interval: Optional[int] = Field(
        default=None,
        description="Maximum seconds between retries",
    )
    max_attempts: Optional[int] = Field(
        default=None,
        description="Maximum total attempts (including first). 3 is standard for external API calls.",
    )


class WorkflowStep(BaseModel):
    id: str = Field(description="Unique step identifier in kebab-case (e.g., 'analyze-input')")
    description: str = Field(description="What this step does")
    state_type: Literal["agent"] = Field(
        description="Execution type: always 'agent' — all workflow steps use assistant nodes"
    )
    next_step_id: Optional[str] = Field(
        default=None,
        description="ID of the next step, or null if this is the last step",
    )
    produces: list[str] = Field(
        default_factory=list,
        description="Named context store keys this step outputs (e.g. 'analysis_result', 'ticket_list')",
    )
    consumes: list[str] = Field(
        default_factory=list,
        description="Named context store keys this step reads from prior steps",
    )
    needs_iteration: bool = Field(
        default=False,
        description="True if this step processes each item in a list independently",
    )
    needs_branching: bool = Field(
        default=False,
        description="True if the step's output determines which path to take next",
    )
    has_side_effect: bool = Field(
        default=False,
        description="True if the step sends email, creates ticket, deploys, or writes to an external system",
    )
    needs_human_approval: bool = Field(
        default=False,
        description="True if has_side_effect and a human should confirm before execution",
    )


class WorkflowIntent(BaseModel):
    workflow_name: str = Field(description="Kebab-case name derived from the goal (max 5 words)")
    workflow_description: str = Field(description="One-sentence description of what the workflow accomplishes")
    goal: Optional[str] = Field(default=None, description="One-sentence goal of the workflow")
    steps: list[WorkflowStep] = Field(description="Ordered list of workflow steps")
    data_sources: list[str] = Field(
        default_factory=list,
        description="Data sources: git_repo|confluence|jira|google_docs|file_upload|none",
    )
    ambiguities: list[str] = Field(
        default_factory=list,
        description="Anything underspecified in the request",
    )


class StepOutputContract(BaseModel):
    step_id: str = Field(description="Step id this contract belongs to")
    output_key: str = Field(description="Context store key name (snake_case) this step writes to")
    output_is_json: bool = Field(
        default=False,
        description="True when the output is a structured object or array — drives include_in_llm_history: false",
    )
    output_schema: Optional[str] = Field(
        default=None,
        description="JSON schema string — required when output_is_json is true. "
        "Defines the exact shape the assistant must produce. "
        "Example: '{\"type\":\"object\",\"properties\":{\"score\":{\"type\":\"number\"},"
        "'issues':{\"type\":\"array\"}}}'",
    )
    description: str = Field(description="One-sentence description of what this output contains")

    @model_validator(mode="after")
    def validate_schema_when_json(self) -> "StepOutputContract":
        if self.output_is_json and not self.output_schema:
            raise ValueError("output_schema is required when output_is_json is True")
        return self


class StepInputBinding(BaseModel):
    step_id: str = Field(description="The consuming step id")
    from_step_id: str = Field(description="The producing step id")
    is_iter_item: bool = Field(
        default=False,
        description="True when this binding is the current iterator item (runtime-resolved). "
        "When True, 'paths' lists sub-fields of the current item; empty paths = use whole item.",
    )
    key: str = Field(description="Context store key being consumed")
    paths: list[str] = Field(
        default_factory=list,
        description="Specific dot-paths needed from this key (e.g. ['score', 'issues[0].severity']). "
        "Empty list means the entire value is needed (plain text, or whole object).",
    )


class DataFlowPlan(BaseModel):
    outputs: list[StepOutputContract] = Field(
        description="One entry per step that produces data other steps or routing need"
    )
    inputs: list[StepInputBinding] = Field(description="One entry per step-to-step data dependency")

    @model_validator(mode="after")
    def validate_data_contracts(self) -> "DataFlowPlan":
        seen: set[str] = set()
        for o in self.outputs:
            if o.output_key in seen:
                raise ValueError(f"Duplicate output_key '{o.output_key}' in DataFlowPlan.outputs")
            seen.add(o.output_key)
        for inp in self.inputs:
            if inp.key not in seen:
                raise ValueError(
                    f"StepInputBinding.key '{inp.key}' (step '{inp.step_id}') not found in any DataFlowPlan.outputs"
                )
        return self


class TaskVariable(BaseModel):
    placeholder: str  # exact Jinja2 string to embed: "{{test_result.coverage}}"
    key: str  # context store key
    path: Optional[str] = None  # dot-path into key, None = whole value
    is_iter_item: bool = False  # True = runtime-resolved iterator item
    description: str  # "coverage % produced by run-tests"


class ContextStoreWrite(BaseModel):
    key: str = Field(description="Context store key name (snake_case)")
    description: str = Field(default="", description="What this key contains")
    is_json: bool = Field(default=False, description="True if the output is structured JSON")
    append: bool = Field(default=False, description="True for iterative accumulation into a list")


class ContextStoreRead(BaseModel):
    key: str = Field(description="Context store key from a prior state")
    access_paths: list[str] = Field(
        default_factory=list,
        description="Dot-paths of sub-fields needed (e.g. ['analysis.score', 'analysis.issues']). "
        "Empty list means the entire value is needed.",
    )


class ContextStore(BaseModel):
    writes: list[ContextStoreWrite] = Field(default_factory=list)
    reads: list[ContextStoreRead] = Field(default_factory=list)


class MappedSwitchCase(BaseModel):
    condition: str = Field(description="Python expression — first matching case wins")
    state_id: str = Field(description="State id to transition to when this condition matches")


class MappedNode(BaseModel):
    step_id: str = Field(description="ID of the step from WorkflowIntent this node implements")
    state_type: Literal["agent"] = Field(description="Always 'agent' — all nodes are assistant nodes")
    assistant_ref: Optional[str] = Field(
        default=None, description="Assistant identifier (e.g., 'code-analyzer'). Required for all nodes."
    )
    task: str = Field(description="Task instructions for this node")
    assistant_system_prompt: Optional[str] = Field(
        default=None, description="System prompt for the assistant. Set during tool/assistant matching."
    )
    output_schema: Optional[str] = Field(
        default=None,
        description="JSON schema string for the structured output — copied from data_flow_plan, do not set manually",
    )
    tools: list[str] = Field(
        default_factory=list,
        description="Exact tool names from the catalog assigned to this assistant node. "
        "Include 'code_executor' if needs_code_executor was true for this step.",
    )
    interrupt_before: bool = Field(
        default=False,
        description="True when needs_human_approval is true — pauses for user confirmation before execution",
    )
    transition_type: Optional[str] = Field(
        default=None,
        description="simple|conditional|parallel|iterative|switch",
    )
    next_state_ids: list[str] = Field(
        default_factory=list,
        description="State ids this node transitions to",
    )
    context_store: Optional[ContextStore] = Field(
        default=None,
        description="What this node writes to and reads from the context store",
    )
    finish_iteration: bool = Field(
        default=False,
        description="True if this node is the per-item processor inside an iterative loop, false otherwise",
    )
    include_in_iterator_context: Optional[list[str]] = Field(
        default=None,
        description="Keys to copy into each iterator branch context. None = copy everything. "
        "Set explicit list to avoid large data duplication.",
    )
    # Structured transition fields — replaces free-text transition_notes
    condition_expression: Optional[str] = Field(
        default=None,
        description="Python expression for conditional routing (e.g. 'test_result.passed == true'). "
        "Set only when transition_type is 'conditional'.",
    )
    then_state_id: Optional[str] = Field(
        default=None,
        description="State id for the TRUE branch. Set only when transition_type is 'conditional'.",
    )
    otherwise_state_id: Optional[str] = Field(
        default=None,
        description="State id for the FALSE branch. Set only when transition_type is 'conditional'.",
    )
    switch_cases: list[MappedSwitchCase] = Field(
        default_factory=list,
        description="Ordered list of condition → state_id pairs for switch routing. "
        "Set only when transition_type is 'switch'. First matching case wins.",
    )
    switch_default: Optional[str] = Field(
        default=None,
        description="Default state id when no switch case matches. Required when transition_type is 'switch'.",
    )
    iter_key: Optional[str] = Field(
        default=None,
        description="RFC 6901 JSON Pointer path to the array inside this state's JSON output "
        "(e.g. '/issues' for {'issues': [...]}, '/result/items' for nested). "
        "Set only when transition_type is 'iterative'.",
    )


class NodeMappingPlan(BaseModel):
    nodes: list[MappedNode] = Field(description="One MappedNode per WorkflowStep")
    assistants_needed: Optional[int] = Field(default=None, description="Number of distinct assistants required")


class ConditionedTransition(BaseModel):
    next_step_id: str = Field(description="Target step ID for this branch")
    condition: str = Field(
        description=(
            "Python expression that must be true to take this branch "
            "(e.g. 'score <= 2', 'status == \"failed\"'). "
            "Use the literal string 'default' for the fallback/else branch — exactly one entry must be 'default'."
        )
    )


class StepPlan(BaseModel):
    step_id: str = Field(description="Step ID matching WorkflowIntent.steps[*].id")
    transition_type: Literal["simple", "conditional", "parallel", "iterative", "switch"] = Field(
        description="Routing type for this step's transition"
    )
    next_step_id: Optional[str] = Field(
        default=None,
        description=(
            "The single next state ID for simple/iterative transitions. "
            "Use 'end' for terminal steps. "
            "For conditional/switch steps: leave None — branches are in condition_steps_ids. "
            "For terminal iteration branches (finish_iteration=True): set to the POST-LOOP aggregation step id, "
            "NOT the immediately following step in the list."
        ),
    )
    condition_steps_ids: list[ConditionedTransition] = Field(
        default_factory=list,
        description=(
            "Explicit branch targets with conditions. Set on any step that routes to multiple targets:\n"
            "- conditional/switch evaluator: lists all branch targets with their condition expressions.\n"
            "- iterative PRODUCER whose next step is a conditional evaluator: copy the evaluator's branches here "
            "so the node generator can add switch signalization on the producer's next transition.\n"
            "Exactly one entry must have condition='default' (the else/fallback branch).\n"
            "Example: [{next_step_id: 'analyze', condition: 'score <= 2'}, "
            "{next_step_id: 'mark-passed', condition: 'default'}]"
        ),
    )
    output_key: Optional[str] = Field(
        default=None,
        description="snake_case key written to context store. None if step produces no downstream data.",
    )
    output_is_json: bool = Field(default=False, description="True when the output is a structured JSON object or array")
    output_props: list[str] = Field(
        default_factory=list,
        description="Top-level property names when output_is_json=True (e.g. ['issues', 'severity'])",
    )
    inputs_from: list[str] = Field(
        default_factory=list,
        description="output_key values from prior steps this step reads",
    )
    condition_hint: Optional[str] = Field(
        default=None,
        description="Brief routing logic description for conditional or switch steps",
    )
    is_per_item_processor: bool = Field(
        default=False,
        description=(
            "True when this step is the ENTRY point of a per-item iterative loop (runs inside the "
            "iterator box). Set by the step planner for the step with needs_iteration=true. "
            "Its PRODUCER (step before) gets transition_type='iterative'. "
            "For a simple per-item processor: transition_type='simple', "
            "is_per_item_processor=True, finish_iteration=True. "
            "For a conditional per-item processor: transition_type='conditional'/'switch', "
            "is_per_item_processor=True, finish_iteration=False — "
            "finish_iteration=True goes on each TERMINAL BRANCH instead."
        ),
    )
    finish_iteration: bool = Field(
        default=False,
        description=(
            "True when this step signals the end of one iterator item. "
            "Simple per-item processor: finish_iteration=True (same step as is_per_item_processor). "
            "Conditional per-item processor: finish_iteration=False on the evaluator; "
            "finish_iteration=True on each terminal branch (the last state in each branch path). "
            "Node generator must set finish_iteration=true and context_store.writes[0].append=true."
        ),
    )
    has_side_effect: bool = Field(
        default=False,
        description=(
            "Copied verbatim from WorkflowIntent step. True when the step writes to an external system "
            "(deploy, create ticket, send message, call external API). "
            "Node generator must assign at least one external tool from Available Tools when this is true."
        ),
    )


class WorkflowPlan(BaseModel):
    plans: list[StepPlan] = Field(
        description="One StepPlan per WorkflowStep, in the same order as WorkflowIntent.steps"
    )


class RefinePlan(BaseModel):
    """Structured output from RefinePlannerNode — combines intent and step plans in one LLM call."""

    intent: WorkflowIntent = Field(
        description=(
            "Complete desired WorkflowIntent for the refined workflow. "
            "workflow_name and workflow_description should match the existing workflow unless "
            "the refine_prompt explicitly requests renaming. "
            "steps must list ALL steps in the final workflow — preserved, modified, and new — "
            "in execution order. Removed steps must be absent."
        )
    )
    plans: list[StepPlan] = Field(
        description=(
            "One StepPlan per step in intent.steps, in the same order. "
            "Preserved steps carry a description that matches the original node configuration "
            "so the generator can reproduce them faithfully. "
            "Modified and new steps carry the desired change description."
        )
    )


class GeneratedCondition(BaseModel):
    expression: str = Field(
        description="Python expression evaluated against context store variables using bare names (e.g. status == 'ok')"
    )
    then: str = Field(description="State id to transition to when expression evaluates to True")
    otherwise: str = Field(description="State id to transition to when expression evaluates to False")


class GeneratedSwitchCase(BaseModel):
    condition: str = Field(description="Python expression — first matching case wins")
    state_id: str = Field(description="State id to transition to when this condition matches")


class GeneratedSwitch(BaseModel):
    cases: list[GeneratedSwitchCase] = Field(description="Ordered list of condition → state_id pairs; first match wins")
    default: str = Field(description="State id to use when no case matches — required, never omit")


class GeneratedNextState(BaseModel):
    state_id: Optional[str] = Field(
        default=None,
        description=(
            "ID of the next state for simple transitions. Use 'end' for terminal state. "
            "Mutually exclusive with state_ids."
        ),
    )
    state_ids: Optional[list[str]] = Field(
        default=None,
        description=(
            "List of state ids for parallel fan-out. All listed states run concurrently. "
            "Mutually exclusive with state_id."
        ),
    )
    iter_key: Optional[str] = Field(
        default=None,
        description="RFC 6901 JSON Pointer path to the array inside this state's JSON output. "
        "Top-level: '/issues'; nested: '/result/items'; deeply nested: '/data/response/items'. "
        "Each dict item has its root fields promoted as top-level context vars {{field}}.",
    )
    condition: Optional[GeneratedCondition] = Field(
        default=None,
        description=(
            "Conditional routing — Python expression determines which of two paths to take. "
            "Mutually exclusive with switch."
        ),
    )
    switch: Optional[GeneratedSwitch] = Field(
        default=None,
        description="Switch/case routing — first matching case wins. Mutually exclusive with condition.",
    )
    output_key: Optional[str] = Field(
        default=None,
        description=(
            "Context store key under which this state's output is stored. "
            "Required for all agent states that produce data."
        ),
    )
    store_in_context: Optional[bool] = Field(
        default=None,
        description="Set false only when output is used only for routing and not needed downstream",
    )
    include_in_llm_history: Optional[bool] = Field(
        default=None,
        description="Set false when the state outputs raw JSON (keeps LLM history clean)",
    )
    append_to_context: Optional[bool] = Field(
        default=None,
        description="Set true for iterative states so each iteration accumulates into a list",
    )
    clear_prior_messages: Optional[bool] = Field(
        default=None,
        description="Set true to wipe LLM message history at this transition — useful at phase boundaries",
    )
    clear_context_store: Optional[Union[bool, Literal["keep_current"]]] = Field(
        default=None,
        description="False=keep all (default), True=clear all, 'keep_current'=keep only this state's output",
    )
    reset_keys_in_context_store: Optional[list[str]] = Field(
        default=None,
        description="Specific context store keys to remove at this transition. Keys not present are ignored.",
    )
    include_in_iterator_context: Optional[list[str]] = Field(
        default=None,
        description=(
            "Keys to copy into each iterator branch context. Default ['*'] copies everything. "
            "Use specific keys to avoid large data duplication."
        ),
    )


class GeneratedState(BaseModel):
    id: str = Field(description="Unique state identifier in kebab-case")
    task: str = Field(default="", description="Task instructions for the assistant node")
    next: GeneratedNextState = Field(description="Routing to the next state")
    assistant_id: Optional[str] = Field(default=None, description="Required — matches an id in assistants[]")
    output_schema: Optional[str] = Field(
        default=None,
        description="JSON schema string for the structured output. Set for states that produce JSON. "
        "Example: '{\"type\": \"object\", \"properties\": {\"score\": {\"type\": \"number\"}}}'",
    )
    finish_iteration: Optional[bool] = Field(
        default=None,
        description="Set true on the state that processes ONE item inside an iterative loop — "
        "signals the platform to advance to the next list item after this state completes",
    )
    resolve_dynamic_values_in_prompt: Optional[bool] = Field(
        default=None,
        description="Set true when task contains {{variable}} references to context store keys",
    )
    interrupt_before: Optional[bool] = Field(
        default=None,
        description="Set true to pause for human approval before this state executes",
    )
    retry_policy: Optional[RetryPolicy] = Field(
        default=None,
        description=(
            "Retry configuration for transient failures. "
            "Set for states that call external systems (Jira, Confluence, HTTP, Git). "
            "Standard value: {max_attempts: 3, initial_interval: 2, backoff_factor: 2, max_interval: 30}. "
            "Omit for pure compute states or states where duplicate execution causes side effects."
        ),
    )


class GeneratedAssistant(BaseModel):
    id: str = Field(description="Unique assistant identifier matching assistant_id used in states")
    system_prompt: Optional[str] = Field(default=None, description="System prompt for the assistant")
    model: Optional[str] = Field(default=None, description="LLM model override")
    tools: list[str] = Field(
        default_factory=list,
        description="Exact tool names from the catalog to attach to this assistant. "
        "Include 'code_executor' when the assistant needs file/script processing.",
    )
    temperature: Optional[float] = Field(
        default=None,
        description="LLM temperature override for this assistant (0.0–1.0)",
    )


class GeneratedWorkflowConfig(BaseModel):
    states: list[GeneratedState] = Field(
        description="List of workflow states. Each must have id, assistant_id, task, and next."
    )
    assistants: list[GeneratedAssistant] = Field(
        description="List of assistants. Each must have an id matching an assistant_id used in states."
    )
