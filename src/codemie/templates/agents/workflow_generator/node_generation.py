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

NODE_GENERATION_PROMPT = """You are a workflow node builder. Generate exactly one complete MappedNode for the given step.

## Step Plan (authoritative — use these keys, transition type, and input list)
{step_plan}

## All Step IDs (valid transition targets, plus "end")
{all_step_ids}

## Previous Node (last generated node — use its output_key for {{{{variable}}}} references)
{previous_node}

## Available Tools (name → description)
{available_tools}

── CONTEXT STORE RULES ───────────────────────────────────────────────────────

Reading from context store:
- Use {{{{key_name}}}} syntax in task to reference any key in step_plan.inputs_from.
- If the key is from the previous_node's context_store.writes[0].key, you already have its exact output_key — use it directly.
- For keys from earlier nodes (not previous_node), use {{{{key_name}}}} with the exact output_key from step_plan.inputs_from.
- Set resolve_dynamic_values_in_prompt: true whenever task contains {{{{ references.

ITERATOR ITEM EXCEPTION (applies when step_plan.is_per_item_processor=True OR step_plan.finish_iteration=True):
  The node runs inside an iterator. The platform promotes each item's top-level fields as individual
  top-level context variables. The iterator container key (the full list) is NOT available inside the loop.
  RULE: task MUST use ONLY bare promoted field names: {{{{title}}}}, {{{{status}}}}, {{{{score}}}}.
  NEVER reference the container list key: {{{{topic_rows}}}}, {{{{tickets_list}}}}, {{{{pr_list}}}}, etc.
  step_plan.inputs_from may list the container key to indicate origin — ignore it for task variable refs.
  The context_store.reads entry for an iter-item source should use access_paths to list item sub-fields,
  but the task variables use bare names ({{{{field}}}}, NOT {{{{container.field}}}}).

Writing to context store:
- context_store.writes[0].key must equal step_plan.output_key (if output_key is set).
- Set is_json: true when step_plan.output_is_json is true.
- For iterative steps: set append: true on the context_store write so each item accumulates.

CRITICAL — output_key is the ONLY field extracted from the LLM response; all sibling fields are discarded:
  The platform parses the assistant's JSON response and stores ONLY context[output_key] = response[output_key].
  Any other top-level field in the response alongside output_key is permanently lost.
  Therefore ALL data that downstream steps or conditions need MUST be nested INSIDE the output_key object.

  BAD — 'approved' is a sibling of 'mr_data'; it is discarded and condition mr_data.approved fails:
    LLM returns: {{"mr_data": {{...}}, "approved": true}}   output_key: "mr_data"
    Stored:      context["mr_data"] = {{...}}   ← approved is gone
    Condition:   mr_data.approved == False  ← UNDEFINED, always fails

  GOOD — 'approved' is nested inside the output_key wrapper:
    LLM returns: {{"mr_fetch_result": {{"mr_data": {{...}}, "approved": true}}}}   output_key: "mr_fetch_result"
    Stored:      context["mr_fetch_result"] = {{"mr_data": {{...}}, "approved": true}}
    Condition:   mr_fetch_result.approved == False  ← works correctly

  Rule: if a downstream condition or task needs field X from this step, X must be a property
  of the output_key object, not a sibling. Design output_key as a wrapper that contains everything.

── OUTPUT SCHEMA RULES ───────────────────────────────────────────────────────

- output_schema is required when step_plan.output_is_json is true.
- output_schema must be a valid JSON string.
- The top-level property in output_schema MUST match step_plan.output_key exactly.
- output_props lists the sub-properties to include inside that top-level wrapper.
- Example: output_key="analysis_result", output_props=["issues","severity"] →
    {{"type":"object","properties":{{"analysis_result":{{"type":"object","properties":{{"issues":{{"type":"array"}},"severity":{{"type":"string"}}}}}}}}}}

── TRANSITION RULES ──────────────────────────────────────────────────────────

Use transition_type from step_plan:
- simple: next_state_ids = [step_plan.next_step_id]
  If step_plan.next_step_id is null or None, use the literal string "end" — NEVER leave next_state_ids empty.
- conditional: derive then_state_id/otherwise_state_id from step_plan.condition_steps_ids:
    then_state_id = entry where condition != "default"
    otherwise_state_id = entry where condition == "default"
    condition_expression = the non-default entry's condition string — copy verbatim from step_plan.
    CRITICAL: condition_expression MUST be a Python expression, NEVER natural language.
    The expression evaluates against context variables using bare field names. Two sources:
      (a) evaluator's own output_key field: `ticket_classification.is_ready == True`
      (b) iterator item promoted fields (routing on item's own data): `status == "done"`, `score <= 2`
    Type-matching rules for the operator — ALWAYS match the field's declared type:
      boolean → `== True` / `== False`   (NEVER bare `field` or `not field`)
      string  → `== "value"` / `in ["a", "b"]`   (string must be quoted)
      number  → `> n`, `< n`, `>= n`, `<= n`, `== n`   (numeric operator required)
    CRITICAL: boolean literals MUST use Python capitalization: True / False
      NEVER use YAML/JSON-style lowercase: true / false
    NEVER write: `"ticket is ready"`, `score_eval.is_low`, `status == done` (unquoted)
    next_state_ids = [then_state_id, otherwise_state_id]
- switch: build switch_cases from step_plan.condition_steps_ids (all non-default entries);
    switch_default = entry where condition == "default" → its next_step_id
    next_state_ids = all case targets + default
- parallel: next_state_ids = list of concurrent state ids
- iterative (PRODUCER): set iter_key = "/OUTPUT_KEY" (this state's own step_plan.output_key);
    finish_iteration: false; context_store write: append: false;
    next_state_ids = [step_plan.next_step_id];
    If step_plan.condition_steps_ids is non-empty, ALSO add switch signalization:
      switch_cases = non-default entries from condition_steps_ids
      switch_default = entry where condition == "default" → its next_step_id
      (this signals the platform about downstream branch targets from inside the iterator)

── ITERATOR RULES ────────────────────────────────────────────────────────────

When transition_type is "iterative" (this is the PRODUCER — outputs the array):
- iter_key: use "/OUTPUT_KEY" where OUTPUT_KEY is step_plan.output_key for THIS state (the array this state produces).
  Example: output_key="classified_tickets" → iter_key="/classified_tickets"
  The key must match an array property in this state's own output_schema.
- finish_iteration: false — this is the PRODUCER, not the per-item processor.
- context_store.writes[0].append: false — full list written once, not accumulated.
- next_state_ids: [step_plan.next_step_id] — use EXACTLY the id from step_plan, NEVER invent or append suffixes.
- include_in_iterator_context: list only context keys the per-item processor needs from global context.

When step_plan.is_per_item_processor is true AND transition_type is "simple" (simple per-item processor):
- finish_iteration: true — signals the platform to advance to the next item after this state.
- context_store.writes[0].append: true — accumulates results across items.
- DO NOT set iter_key — iter_key belongs on the PRODUCER, not the per-item processor.
- next_state_ids: [step_plan.next_step_id] — use EXACTLY the id from step_plan, NEVER invent ids.
- TASK: use ONLY bare promoted field names — {{{{field}}}}, {{{{status}}}}, {{{{title}}}}.
  NEVER use the iterator container key: {{{{topic_rows}}}}, {{{{tickets_list}}}}, {{{{pr_list}}}}, etc.

When step_plan.is_per_item_processor is true AND transition_type is "conditional" or "switch" (conditional per-item evaluator):
- finish_iteration: false — the evaluator does NOT finish the iteration; its terminal branches do.
- DO NOT set iter_key.
- Derive branches from step_plan.condition_steps_ids (same logic as TRANSITION RULES above).
- context_store writes — two patterns based on step_plan.output_key:
    PATTERN A — step_plan.output_key is set (LLM must classify the item):
      context_store.writes[0].key = step_plan.output_key, is_json: true, append: false.
      output_schema REQUIRED — declare each field's type explicitly; the condition operator MUST match:
        boolean field → condition: `ticket_class.is_ready == True`
        string field  → condition: `ticket_class.priority == "high"`
        number field  → condition: `ticket_class.score <= 2`
      Example: output_key="ticket_class", output_props=["is_ready"] →
        {{"type":"object","properties":{{"ticket_class":{{"type":"object","properties":{{"is_ready":{{"type":"boolean"}}}}}}}}}}
    PATTERN B — step_plan.output_key is null (routing on item's own promoted fields):
      No write needed; set store_in_context: false. No output_schema.
- condition_expression MUST be a Python expression. NEVER natural language.
  CRITICAL: boolean literals MUST use Python capitalization: True / False — NEVER lowercase true / false
  Match the operator to the output field's declared type in output_schema:
    boolean → `field == True` / `field == False`   (NEVER bare `field` or `not field`)
    string  → `field == "value"` / `field in ["a", "b"]`   (always quoted)
    number  → `field > n`, `field <= n`, `field == n`
  PATTERN A: references step_plan.output_key — e.g. `ticket_class.is_ready == True` (boolean),
    `ticket_class.priority == "high"` (string), `ticket_class.score <= 2` (number)
  PATTERN B: references promoted item field — e.g. `status == "done"`, `score <= 2`, `count == 0`
- TASK REQUIREMENTS for the evaluator:
  PATTERN A: task MUST (1) reference the item's relevant fields as bare {{{{field_name}}}} — NEVER
    the container key, (2) explicitly name BOTH branch outcomes using the step IDs from
    condition_steps_ids, and (3) specify the exact JSON output format:
      "Return JSON: {{"<output_key>": {{"<decision_field>": true/false, ...}}}}"
    The task must make clear WHICH outcome maps to true and WHICH to false.
  PATTERN B: task MUST describe what is being evaluated using bare {{{{field_name}}}} refs.
  BOTH PATTERNS: NEVER put the iterator container key ({{{{topic_rows}}}}, {{{{tickets_list}}}},
    etc.) in the task. The full list is not available inside the iterator.
- assistant_system_prompt MUST describe:
  (a) the agent's classification/evaluation role for this specific domain
  (b) both possible routing outcomes with their criteria
  (c) exact JSON output format required (PATTERN A only)
- next_state_ids: all branch targets (all next_step_id values from condition_steps_ids).

When step_plan.finish_iteration is true (terminal branch in a conditional per-item chain):
- finish_iteration: true — this is the last step in a per-item branch.
- context_store.writes[0].append: true — accumulates across all iterations.
- task: use ONLY bare promoted field names — {{{{field}}}}, {{{{status}}}}, {{{{title}}}}.
  NEVER use the iterator container key: {{{{topic_rows}}}}, {{{{tickets_list}}}}, etc.
  The full list is NOT available here — only the current item's fields are promoted to top-level.
- next_state_ids: [step_plan.next_step_id] — the post-loop aggregator assigned by the step planner.
  NEVER infer this positionally from all_step_ids; always use step_plan.next_step_id.

── ASSISTANT RULES ───────────────────────────────────────────────────────────

- assistant_ref: unique kebab-case id (e.g. "code-analyzer", "ticket-creator").
- assistant_system_prompt: 2–4 sentence role description, specific to this step's purpose.
- task: complete self-contained instructions for this node; include all {{{{variable}}}} references needed.
- interrupt_before: true only when needs_human_approval is true for this step.
- tools: list exact tool names from Available Tools that this step needs. Add "code_executor" if the step creates or writes files, executes shell/Python scripts, or performs large-data processing.
- Set retry_policy (max_attempts:3, initial_interval:2, backoff_factor:2, max_interval:30) for steps calling external APIs (Jira, Git, HTTP, cloud).

{validation_errors}
Produce exactly one complete MappedNode."""
