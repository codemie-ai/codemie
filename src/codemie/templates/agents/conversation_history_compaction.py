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

conversation_history_compaction_prompt = """
Summarize the earlier conversation so the assistant can continue the same task with less context.

Rules:
- Preserve confirmed user goals, constraints, and preferences.
- Preserve concrete facts, file names, identifiers, code decisions, and accepted answers.
- Preserve important tool activity: tool names, key inputs, important outputs, failures, and unresolved retries.
- Preserve open questions, blockers, and the current next step.
- Do not invent facts. Mark uncertain details as uncertain.
- Keep the summary compact and factual.

Return plain text with these sections:
Goals:
Facts:
Tool activity:
Open items:

Earlier conversation:
{history_text}
""".strip()
