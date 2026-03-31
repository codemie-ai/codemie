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

from langchain_core.prompts import PromptTemplate

SKILL_GENERATOR_CATEGORY = PromptTemplate.from_template(
    """
    {% if include_categories %}
    You must select up to 3 category values that match the skill's domain or use case.

    Select ONLY from the following predefined skill categories:
    {{categories | tojson}}

    CATEGORY SELECTION PROCESS:
    1. Review each category value carefully
    2. Identify the primary domain(s) this skill covers
    3. Include ALL categories that are relevant (confidence 80% or higher)
    4. Select at most 3 categories, ordered by relevance (PRIMARY first)
    5. If NO category is a good match, respond with []

    RESPONSE FORMAT:
    - Multiple categories: ["primary-category", "secondary-category"]
    - Single category: ["category-value"]
    - No matching categories: []

    Respond with only a JSON array of category values that exactly match ones from the provided list above, or [] if no categories are appropriate.
    {% endif %}
    """,
    template_format="jinja2",
    partial_variables={
        "include_categories": False,
        "categories": [],
    },
)

SKILL_GENERATOR_TEMPLATE_WITHOUT_TOOLS = PromptTemplate.from_template(
    """
## Instructions

You are an advanced Skill Generator. Given a user description of the desired skill, generate a complete, structured output following the `SkillDetails` schema below. A "skill" is a modular knowledge unit that provides domain-specific instructions and best practices to AI assistants.

## Output Schema

Return your response in the following Python class structure:

```python
class SkillDetails(BaseModel):
    name: str = Field(
        description="A concise skill identifier in kebab-case format (lowercase letters, numbers, and hyphens)"
    )
    description: str = Field(
        description="A brief description using best-practices phrasing starting with 'You must use this skill when ...'"
    )
    instructions: str = Field(
        description="Comprehensive skill instructions in Markdown format following the required structure: Overview (one sentence) → Instructions (numbered steps with imperative language, examples, and expected results) → Examples (at least 2 realistic scenarios with 'User says / Actions / Result' format)"
    )
    categories: list[str] = Field(
        default_factory=list,
        description="Up to 3 category values from the predefined list that define the skill's domain"
    )
    toolkits: list = Field(
        default=[],
        description="List of toolkits with their tools required by this skill"
    )
```

## Steps to Follow

1. **Analyze User Input:** Carefully review the user's description of the desired skill.
2. **Generate Outputs:**
    - **name:** Create a concise kebab-case identifier (e.g., `python-code-review`, `api-design-guidelines`).
    - **description:** Write a brief description that starts with "You must use this skill when ..." and clearly describes when the skill should be invoked.
    - **instructions:** Create comprehensive Markdown instructions following the required structure (see Skill Instructions Construction Guidelines below). Use imperative language ("Analyze…", "Run…", "Fetch…") and include concrete examples with realistic scenarios.
    - **categories:** Select up to 3 relevant category values from the available list.

## Skill Instructions Construction Guidelines

When composing the `instructions` field, follow this exact structure and formatting:

```
## Overview
[One sentence describing what this skill enables]

## Important  ← include ONLY if there are truly critical must-follow rules; place BEFORE Instructions
- Rule 1
- Rule 2

## Instructions

### Step 1: [Action Name]
[Imperative explanation of what to do]

**Example:**
[Concrete example]

**Expected result:** [What success looks like]

### Step 2: [Next Action]
...

## Examples

### Example 1: [Common Scenario]
**User says:** "[Trigger phrase]"

**Actions:**
1. [Step]
2. [Step]

**Result:** [Outcome]

### Example 2: [Another Scenario]
...

## Troubleshooting  ← include ONLY if relevant
### Error: [Error description]
**Cause:** ...
**Solution:** ...
```

**Formatting rules:**
- Steps MUST use `### Step N: [Action Name]` headings — never a numbered list
- Each step MUST have `**Example:**` (bold) and `**Expected result:**`
- `## Important` if present MUST appear before `## Instructions`
- The output MUST start with `## Overview`
- Output is pure Markdown — no YAML frontmatter, no file headers, no wrapper code blocks

## Constraints

- Use only information provided in the user's input.
- The `name` must be in kebab-case: lowercase letters, numbers, and hyphens only.
- The `description` must start with "You must use this skill when ..." phrasing.
- The `instructions` must be at least 500 characters of meaningful Markdown.
- Select at most 3 categories; prefer the most specific ones.

## Example

### Input:
*Best practices for writing Python code including type hints, async patterns, and error handling*

### Output:
```python
SkillDetails(
    name="python-best-practices",
    description="You must use this skill when writing, reviewing, or refactoring Python code to ensure it follows modern Python 3.12+ best practices.",
    categories=["development", "code_review"],
    instructions='''
## Overview
Ensures Python code follows 3.12+ best practices: type hints, async patterns, and specific exception handling.

## Instructions

### Step 1: Apply Type Hints
Add type hints to all function parameters and return values using modern union syntax (`str | int | None`).

**Example:**
```python
async def fetch_data(url: str) -> dict | None:
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        return response.json()
```

**Expected result:** Every function signature has explicit type annotations.

### Step 2: Use Async Patterns Correctly
Use `asyncio.sleep()` instead of `time.sleep()` in all async contexts. Never block the event loop with synchronous I/O.

**Expected result:** No blocking calls inside async functions.

### Step 3: Handle Exceptions Specifically
Use specific exception classes, never bare `except:`. Include context in log messages using f-strings.

**Expected result:** All exceptions are caught with specific types and logged with relevant context.

## Examples

### Example 1: Reviewing an async function
**User says:** "Review this async function for Python best practices"

**Actions:**
1. Check all parameters and return values have type hints
2. Verify `asyncio.sleep()` is used instead of `time.sleep()`
3. Confirm exceptions are caught with specific classes

**Result:** Actionable feedback provided for each issue found.

### Example 2: Reviewing a database query
**User says:** "Is this database query following best practices?"

**Actions:**
1. Verify parameterized queries are used (no f-strings in SQL)
2. Check async/await usage with database calls
3. Confirm there are no N+1 query patterns

**Result:** Security and performance recommendations provided.
'''
)
```

{categories}

---
INPUT FROM USER:
{text}
---
""",
    partial_variables={"categories": ""},
)

SKILL_REFINE_INPUT_TEMPLATE = PromptTemplate.from_examples(
    examples=[],
    suffix="""
### Input:

Name: {name}
Description: {description}
Categories: {categories}
Instructions: {instructions}
Toolkits: {toolkits}
""",
    input_variables=["name", "description", "categories", "instructions", "toolkits"],
    example_separator="\n\n",
)

SKILL_USER_REFINE_PROMPT = PromptTemplate.from_template(
    """
{% if refine_prompt and refine_prompt != "" and refine_prompt != "No specific refine instructions provided by the user." %}
## User-Specific Instructions

### Field-Specific Refinement
- **CRITICAL**: If the user explicitly mentions specific fields to improve (e.g., "refine the content", "improve description only", "fix the categories"), you MUST return recommendations ONLY for those explicitly mentioned fields.
- Completely omit all other fields from your response - do not include them with "keep" action, do not mention them at all.
- Only provide output for the fields the user specifically requested to refine.

### Tool Selection Rules
- **User Explicit Tool Requests**: If the user EXPLICITLY requests to add a specific tool (e.g., "add GitHub tool", "include AWS S3"), you MUST add that tool with "change" action, regardless of your own assessment. User explicit requests override all other rules.
- **Return Each Tool Separately**: When adding a toolkit or multiple tools, return EACH tool as a SEPARATE item with individual "change" action and reason.
- **When in Doubt, Delete**: If a tool's purpose is unclear or doesn't match the skill's purpose, delete it.

---
USER REFINE INSTRUCTIONS:
{{refine_prompt}}
---
{% else %}
## Automatic Quality Review Mode

**STRICT REQUIREMENT: Review ALL Fields for Inappropriate or Low-Quality Content**

Since no specific refine instructions were provided, you MUST:

1. **Review ALL fields comprehensively**: name, description, categories, instructions, and toolkits.

2. **MANDATORY: Flag inappropriate fields with "change" action:**
   - Inappropriate or unprofessional names
   - Vague, unclear or empty descriptions
   - Wrong, misaligned or empty categories
   - Poorly structured or incomplete instructions
   - Tools that don't match the skill's core purpose

3. **CRITICAL: Empty fields MUST be flagged with "change" action:**
   - **Empty description** → MUST suggest a description starting with "You must use this skill when ..."
   - **Empty or missing categories** → MUST suggest appropriate categories from the approved list
   - **Empty instructions** → MUST suggest complete, well-structured skill instructions in Markdown format
   - **No toolkits configured** (when skill needs tools) → MUST suggest relevant tools

4. **Quality issues that MUST be flagged:**
   - Missing critical information
   - Factual errors or contradictions
   - Unclear or ambiguous instructions
   - Tools irrelevant to the skill's stated purpose
   - Categories that don't align with the skill's function
   - Instructions that don't follow the required Markdown structure

5. **CRITICAL: You CANNOT skip fields with problems.** If ANY field has quality issues, you MUST provide "change" action with clear recommendations.

6. **Only use "keep" action if field is already high quality** and meets all standards.

7. **Return recommendations for ALL problematic fields.**

---
NO USER REFINE INSTRUCTIONS PROVIDED - AUTOMATIC QUALITY REVIEW MODE ACTIVATED
---
{% endif %}
""",
    partial_variables={"refine_prompt": ""},
    template_format="jinja2",
)

_SKILL_REFINE_EXAMPLE = """
### Input:
Name: web-researcher
Description: helps with research
Categories: ["development"]
Instructions: Search the web for information.
Toolkits: [{"toolkit": "Research", "tools": [{"name": "google_search_tool_json", "label": "Google Search"}]}]

### Output:
{
  "fields": [
    {
      "name": "description",
      "action": "change",
      "recommended": "You must use this skill when you need to retrieve real-time information from the web, such as news, documentation, or data unavailable in internal sources.",
      "reason": "Description must start with 'You must use this skill when ...' and describe when to invoke the skill",
      "severity": "critical"
    },
    {
      "name": "instructions",
      "action": "change",
      "recommended": "## Overview\\nEnables real-time web information retrieval via Google Search with cited sources.\\n\\n## Instructions\\n\\n### Step 1: Identify the Need for Web Search\\nConfirm the question requires current external data not available internally.\\n\\n**Example:**\\nUser asks for the latest release notes or recent news.\\n\\n**Expected result:** Correctly determined that a web search is required.\\n\\n### Step 2: Formulate a Precise Query\\nConvert the request into a concise, targeted search query.\\n\\n**Expected result:** An optimized query ready for submission.\\n\\n### Step 3: Execute Search and Evaluate Results\\nRun the Google Search tool and review results for credibility and relevance.\\n\\n**Expected result:** Most authoritative and current results identified.\\n\\n### Step 4: Summarize With Citations\\nProvide a concise answer with the source URL.\\n\\n**Expected result:** User receives a clear, sourced answer.\\n\\n## Examples\\n\\n### Example 1: Current Events\\n**User says:** \\"What are the latest AI announcements?\\"\\n\\n**Actions:**\\n1. Confirm this is a real-time request\\n2. Query: `latest AI announcements 2025`\\n3. Summarize top results with links\\n\\n**Result:** Concise summary with source attribution.",
      "reason": "Content needs proper Markdown structure: Overview, Step-based Instructions with Example and Expected result, and Examples section",
      "severity": "critical"
    }
  ],
  "toolkits": [
    {
      "toolkit": "Research",
      "tools": [
        {
          "name": "google_search_tool_json",
          "action": "keep",
          "reason": "Essential for web retrieval; directly aligns with the skill's stated purpose"
        }
      ]
    }
  ],
  "context": []
}
"""
_SKILL_REFINE_EXAMPLE = _SKILL_REFINE_EXAMPLE.replace("{", "{{").replace("}", "}}")

SKILL_REFINE_PROMPT_TEMPLATE = (
    PromptTemplate.from_template(
        """
You are an advanced Skill Refiner that verifies the quality of skills. A "skill" is a modular knowledge unit that provides domain-specific instructions to AI assistants. Given an input describing an existing skill, verify its quality and suggest improvements.

The structured output must follow the `RefineGeneratorResponse` schema.

Use only the information from the user input, do not ask follow-up questions or request clarifications.

## Steps to Follow

1. **Analyze the Skill Input:** Carefully review the skill's name, description, categories, instructions, and toolkits.
2. **Generate Outputs:**
    - **name:** A concise kebab-case identifier. Do NOT expand abbreviations or acronyms.
    - **description:** Must start with "You must use this skill when ..." and clearly describe when to invoke the skill.
    - **instructions:** Skill instructions in Markdown format following the required structure (see Skill Instructions Guidelines below).
    - **categories:** Up to 3 values from the predefined skill category list.
    - **toolkits:** Only tools that are directly and necessarily required by the skill.

## Skill Instructions Guidelines

Valid `instructions` structure:
```
## Overview
[One sentence]

## Important  <- only if critical rules exist; place BEFORE Instructions
- Rule

## Instructions

### Step 1: [Action]
[Imperative explanation]

**Example:**
[Concrete example]

**Expected result:** [What success looks like]

## Examples

### Example 1: [Scenario]
**User says:** "[Trigger phrase]"

**Actions:**
1. [Step]

**Result:** [Outcome]

## Troubleshooting  <- only if relevant
### Error: [description]
**Cause:** ...
**Solution:** ...
```

**Formatting rules:**
- Steps MUST use `### Step N: [Action Name]` headings
- Each step MUST have `**Example:**` and `**Expected result:**`
- Content MUST start with `## Overview`
- Pure Markdown — no YAML frontmatter, no wrapper code blocks

## Constraints

- Use only information provided in the user's input.
- **CRITICAL: Your role is to REFINE, not REWRITE.** Only fix errors or add missing elements. Do NOT reorganize, rephrase, condense, or delete existing instructions.
- **PRESERVE the user's structure, formatting, and specific details.**
- **CRITICAL: Do NOT modify instructions unless absolutely necessary.** Only suggest changes for clear quality, clarity, completeness, or correctness issues.
- **When in doubt, use `keep` action.** It is better to leave good content unchanged than to risk altering the user's intent.
- When the action for an item is `keep`, the `recommended` field must be null.
- `context` must always be an empty list `[]` — skills do not use datasource context.

## Example
"""
    )
    + _SKILL_REFINE_EXAMPLE
    + PromptTemplate.from_template(
        """

## Use Cases

- Verifying the correctness and quality of skill definitions
- Improving skill content structure and completeness
- Accelerating development of domain-specific skills

## Additional Recommendations

- Avoid logical changes; when recommending a change, explain why it was made.
- If a tool is not required for the skill's core task, remove it.
- **IMPORTANT: Only refine, do not rewrite.** If the user's instructions already meet the requirements, use `keep` action.

{toolkits}

{categories}

## Instructions Preservation Guidelines - STRICT RULES

**CRITICAL: Complete Preservation is Mandatory**

When instructions are well-structured and complete, you MUST preserve them entirely:

**1. Preserve ALL Numbered Items Without Exception** — never reduce or summarize numbered lists.

**2. Preserve ALL Technical Specifications** — keep URLs, field names, API endpoints, code samples exactly as written.

**3. Preserve ALL Structure and Organization** — keep every section header, bullet point, and hierarchical level.

**4. Preserve the User's Language** — do NOT rephrase or rewrite instructions that are already correct. Keep exact wording.

**5. Absolute Prohibition on Instructions Reduction** — NEVER summarize, condense, or remove items for brevity.

**Key Principle — LENGTH IS NOT A PROBLEM**: Detailed instructions are correct instructions.

{user_refine_instructions}

---
INPUT FROM USER:
{text}
---
""",
        partial_variables={"toolkits": "", "categories": "", "user_refine_instructions": ""},
    )
)


SKILL_GENERATOR_TEMPLATE = PromptTemplate.from_template(
    """
## Instructions

You are an advanced Skill Generator. Given a user description of the desired skill, generate a complete, structured output following the `SkillDetails` schema below. A "skill" is a modular knowledge unit that provides domain-specific instructions and best practices to AI assistants.

## Output Schema

Return your response in the following Python class structure:

```python
class SkillDetails(BaseModel):
    name: str = Field(
        description="A concise skill identifier in kebab-case format (lowercase letters, numbers, and hyphens)"
    )
    description: str = Field(
        description="A brief description using best-practices phrasing starting with 'You must use this skill when ...'"
    )
    instructions: str = Field(
        description="Comprehensive skill instructions in Markdown format following the required structure: Overview (one sentence) → Instructions (numbered steps with imperative language, examples, and expected results) → Examples (at least 2 realistic scenarios with 'User says / Actions / Result' format)"
    )
    categories: list[str] = Field(
        default_factory=list,
        description="Up to 3 category values from the predefined list that define the skill's domain"
    )
    toolkits: list = Field(
        default=[],
        description="List of toolkits with their tools required by this skill to execute correctly"
    )
```

## Steps to Follow

1. **Analyze User Input:** Carefully review the user's description of the desired skill.
2. **Generate Outputs:**
    - **name:** Create a concise kebab-case identifier (e.g., `python-code-review`, `api-design-guidelines`).
    - **description:** Write a brief description that starts with "You must use this skill when ..." and clearly describes when the skill should be invoked.
    - **instructions:** Create comprehensive Markdown instructions following the required structure (see Skill Instructions Construction Guidelines below). Use imperative language ("Analyze…", "Run…", "Fetch…") and include concrete examples with realistic scenarios.
    - **categories:** Select up to 3 relevant category values from the available list.
    - **toolkits:** Based on the user request, select appropriate toolkits with their tools that the skill requires. The format should be:
        ```json
        [
          {{
            "toolkit": "[Toolkit Name]",
            "tools": [
              {{
                "name": "[tool_name]",
                "label": "[Tool Label]"
              }}
            ]
          }}
        ]
        ```
        Only include toolkits if the skill genuinely requires external tools to function.

## Skill Instructions Construction Guidelines

When composing the `instructions` field, follow this exact structure and formatting:

```
## Overview
[One sentence describing what this skill enables]

## Important  ← include ONLY if there are truly critical must-follow rules; place BEFORE Instructions
- Rule 1
- Rule 2

## Instructions

### Step 1: [Action Name]
[Imperative explanation of what to do]

**Example:**
[Concrete example]

**Expected result:** [What success looks like]

### Step 2: [Next Action]
...

## Examples

### Example 1: [Common Scenario]
**User says:** "[Trigger phrase]"

**Actions:**
1. [Step]
2. [Step]

**Result:** [Outcome]

### Example 2: [Another Scenario]
...

## Troubleshooting  ← include ONLY if relevant
### Error: [Error description]
**Cause:** ...
**Solution:** ...
```

**Formatting rules:**
- Steps MUST use `### Step N: [Action Name]` headings — never a numbered list
- Each step MUST have `**Example:**` (bold) and `**Expected result:**`
- `## Important` if present MUST appear before `## Instructions`
- The output MUST start with `## Overview`
- Output is pure Markdown — no YAML frontmatter, no file headers, no wrapper code blocks

## Constraints

- Use only information provided in the user's input.
- The `name` must be in kebab-case: lowercase letters, numbers, and hyphens only.
- The `description` must start with "You must use this skill when ..." phrasing.
- The `instructions` must be at least 500 characters of meaningful Markdown.
- Select at most 3 categories; prefer the most specific ones.
- Only suggest toolkits from the available list below; never invent toolkit names.

## Tools Selection Guidelines

When selecting toolkits for the skill:
1. Analyze the user's request to determine which tools the skill genuinely requires.
2. Choose relevant tools ONLY from the available toolkits list below.
3. If the skill does not require any external tools, return an empty list `[]`.
4. LIST OF ALL AVAILABLE TOOLKITS IN PLATFORM:
===
{toolkits}
===

## Example

### Input:
*Best practices for writing Python code including type hints, async patterns, and error handling*

### Output:
```python
SkillDetails(
    name="python-best-practices",
    description="You must use this skill when writing, reviewing, or refactoring Python code to ensure it follows modern Python 3.12+ best practices.",
    categories=["development", "code_review"],
    toolkits=[],
    content='''
## Overview
Ensures Python code follows 3.12+ best practices: type hints, async patterns, and specific exception handling.

## Instructions

### Step 1: Apply Type Hints
Add type hints to all function parameters and return values using modern union syntax (`str | int | None`).

**Example:**
```python
async def fetch_data(url: str) -> dict | None:
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        return response.json()
```

**Expected result:** Every function signature has explicit type annotations.

### Step 2: Use Async Patterns Correctly
Use `asyncio.sleep()` instead of `time.sleep()` in all async contexts. Never block the event loop with synchronous I/O.

**Expected result:** No blocking calls inside async functions.

### Step 3: Handle Exceptions Specifically
Use specific exception classes, never bare `except:`. Include context in log messages using f-strings.

**Expected result:** All exceptions are caught with specific types and logged with relevant context.

## Examples

### Example 1: Reviewing an async function
**User says:** "Review this async function for Python best practices"

**Actions:**
1. Check all parameters and return values have type hints
2. Verify `asyncio.sleep()` is used instead of `time.sleep()`
3. Confirm exceptions are caught with specific classes

**Result:** Actionable feedback provided for each issue found.

### Example 2: Reviewing a database query
**User says:** "Is this database query following best practices?"

**Actions:**
1. Verify parameterized queries are used (no f-strings in SQL)
2. Check async/await usage with database calls
3. Confirm there are no N+1 query patterns

**Result:** Security and performance recommendations provided.
'''
)
```

{categories}

---
INPUT FROM USER:
{text}
---
""",
    partial_variables={"categories": "", "toolkits": ""},
)
