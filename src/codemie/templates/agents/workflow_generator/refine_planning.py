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

REFINE_PLANNING_PROMPT = """You are an expert workflow engineer. \
Analyse the existing workflow and the refinement instruction, then produce a RefinePlan.

## Existing workflow YAML
{existing_yaml_config}

## Refinement instruction
{refine_prompt}

## Available tools
{available_tools}

## Your task

Produce a RefinePlan with two fields:

### intent
A WorkflowIntent that describes the COMPLETE desired workflow after refinement:
- workflow_name / workflow_description: copy from the existing workflow unless the instruction \
explicitly requests a rename
- steps: list ALL steps in the final workflow in execution order
  - Preserved steps: copy id, description, and all properties from the existing workflow exactly
  - Modified steps: update only the properties the instruction targets; preserve everything else
  - New steps: describe the new step clearly
  - Removed steps: omit entirely
- data_sources / ambiguities: carry over from the existing workflow intent if discernible; \
otherwise use empty lists

### plans
One StepPlan per step in intent.steps, in the same order:
- Preserved steps: set transition_type, next_step_id, output_key, and all data-flow properties \
to match the original step exactly so the node generator reproduces it faithfully
- Modified / new steps: plan the data flow changes the instruction requires

## Rules
- ONLY use tool names that already appear in the existing workflow YAML. \
Do NOT invent new tool names.
- Never add "start" or "end" as entries in intent.steps.
- The last step's StepPlan must have next_step_id = "end".
- If the instruction would require a completely new workflow unrelated to the existing one, \
still return a RefinePlan; treat all steps as new.
"""
