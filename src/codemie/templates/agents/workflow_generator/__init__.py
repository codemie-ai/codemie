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

from codemie.templates.agents.workflow_generator.intent_analysis import INTENT_ANALYSIS_PROMPT
from codemie.templates.agents.workflow_generator.node_generation import NODE_GENERATION_PROMPT
from codemie.templates.agents.workflow_generator.refine_planning import REFINE_PLANNING_PROMPT
from codemie.templates.agents.workflow_generator.step_planning import STEP_PLANNING_PROMPT
from codemie.templates.agents.workflow_generator.tools_selection import TOOLS_SELECTION_PROMPT

__all__ = [
    "INTENT_ANALYSIS_PROMPT",
    "NODE_GENERATION_PROMPT",
    "REFINE_PLANNING_PROMPT",
    "STEP_PLANNING_PROMPT",
    "TOOLS_SELECTION_PROMPT",
]
