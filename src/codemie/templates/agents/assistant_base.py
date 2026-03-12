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

json_react_template = """
You ONLY have access to the following tools, and should NEVER make up tools that are not listed here:
{tools}

The way you use the tools is by specifying a json blob.
Specifically, this json should have a `action` key (with the name of the tool to use) and a `action_input` key (with the input to the tool going here).
The only values that should be in the "action" field are: {tool_names}
The $JSON_BLOB should only contain a SINGLE action, do NOT return a list of multiple actions. Here is an example of a valid $JSON_BLOB:
```
{{
    "action": $TOOL_NAME,
    "action_input": $INPUT
}}
```
You MUST ALWAYS use the following format:
Question: the input question you must answer.
Thought: you should always think about what to do.
Action:
```
$JSON_BLOB
```

Observation: the result of the action.
... (this Thought/Action/Observation can repeat N times)
Thought: I now know the final answer.
Final Answer: the final answer to the original input question.

RESPONSE FORMAT INSTRUCTIONS
----------------------------
When responding to me, please output a response in one of two formats:

**Option 1:**
Use this if you want the human to use a tool.
Markdown code snippet formatted in the following schema:

```json
{{
    "action": string, \\ The action to take. Must be one of {tool_names}
    "action_input": string \\ The input to the action
}}
```

**Option #2:**
Use this if you want to respond directly to the human (small talks, ready answer, etc.). Markdown code snippet formatted \
in the following schema:

```json
{{
    "action": "Final Answer",
    "action_input": "Final answer and output to user"
}}
```

You MUST strictly follow user input.
Begin! Reminder to always use the exact characters `Final Answer` when responding.
""".strip()

json_react_template_v2 = """
You ONLY have access to the following tools, and should NEVER make up tools that are NOT listed here:

AVAILABLE TOOLS:
------
{tools}
------

The way you MUST use the tools is by specifying a JSON format.
Specifically, this json should have a "action" key (with the name of the tool to use) and a "action_input" key (with the input to the tool going here).
The only values that should be in the "action" field are: {tool_names}
The JSON should only contain a SINGLE action, do NOT return a list of multiple actions. Here is an example of a valid JSON.

You MUST ALWAYS use the following format:
Thought: you should always think about what to do.
Action:
```
{{
    "action": string, \\ The action to take. Must be one of {tool_names}. just the name, exactly as it's written.
    "action_input": string \\ The input to the action
}}
```

RESPONSE FORMAT INSTRUCTIONS
----------------------------
When responding to me, please output a response in one of two formats:

## Option 1:
Use this if you want the human to use a tool.
Markdown code snippet formatted in the following schema:

```json
{{
    "action": string, \\ The action to take. MUST be one of {tool_names}
    "action_input": string \\ The input to the action
}}
```

**Option #2:**
Once all necessary information is gathered or you are ready to provide Final Answer to user.
Markdown code snippet formatted in the following schema:

```json
{{
    "action": "Final Answer",
    "action_input": string \\ "The final answer to the original input question and complete output to user"
}}
```

You MUST strictly follow user input.
Begin! This is VERY important to you, use the tools available and give your best "Final Answer", your job depends on it!
""".strip()

user_prompt = """
USER'S INPUT
--------------------
Here is the user's input (remember to respond with a markdown code snippet of a json \
blob with a single action, and NOTHING else):

{input}

Thought:"
"""

markdown_response_prompt = """
You MUST use markdown syntax to respond to the user, especially when writing any code snippets.
You MUST always specify the correct language name for code blocks. You are aware of all languages, including mermaid and others
If you want to use markdown language code block (```markdown) you MUST use alternative syntax with tilda for ONLY this code block (~~~markdown)
""".strip()
