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

from langchain_core.prompts import PromptTemplate

ASSISTANT_GENERATOR_CATEGORY = PromptTemplate.from_template(
    """
    {% if include_categories %}
    You must select one or more category IDs that match the assistant's domains or use cases. Categories can come from two sources:
    1. **AVAILABLE CATEGORIES** (predefined system categories)
    2. **USER CATEGORIES** (custom categories provided by the user)

    If no category from either source is a good match, respond with an empty array [].
    {% if categories %}
    **AVAILABLE CATEGORIES (System-defined):**
    {{categories | tojson}}
    {% else %}
    **AVAILABLE CATEGORIES:** None provided
    {% endif %}

    {% if user_categories %}
    **USER CATEGORIES (Custom):**
    {{user_categories | tojson}}
    {% else %}
    **USER CATEGORIES:** None provided
    {% endif %}

    {% if not categories and not user_categories %}
    No categories available - you cannot complete this task without categories.
    {% endif %}

    CATEGORY SELECTION PROCESS:
    1. Review each category from BOTH lists above carefully
    2. Identify ALL functions, domains, or use cases the assistant covers
    3. Match each of the assistant's purposes to appropriate categories from either list
    4. Include ALL categories that are relevant (confidence 80% or higher)
    5. Prefer USER CATEGORIES when both sources have equally relevant matches
    6. Order categories by relevance: PRIMARY use case first, then secondary ones
    7. If NO category is a good match for any function, respond with []
    8. Your response must be a JSON array containing ONLY the selected category IDs (exact matches from either list)

    RESPONSE FORMAT:
    - Multiple categories: ["primary-category-id", "secondary-category-id", "tertiary-category-id"]
    - Single category: ["category-id"]
    - No matching categories: []
    - Can mix categories from both sources: ["user-category-1", "system-category-2", "user-category-3"]

    CATEGORY EXAMPLES (for system categories):

    PROJECT MANAGEMENT:
    - If the assistant helps create project timelines and schedules THEN include "project-management"
    - If the assistant tracks task progress and deadlines THEN include "project-management"
    - If the assistant manages team assignments and workload distribution THEN include "project-management"

    TRAINING:
    - If the assistant provides employee onboarding guidance THEN include "training"
    - If the assistant creates learning materials and courses THEN include "training"
    - If the assistant helps with skill development and knowledge transfer THEN include "training"

    ARCHITECTURE:
    - If the assistant designs system architecture diagrams THEN include "architecture"
    - If the assistant helps with microservices design patterns THEN include "architecture"
    - If the assistant provides guidance on scalable system design THEN include "architecture"

    ENGINEERING:
    - If the assistant helps with coding, debugging, or software development THEN include "engineering"
    - If the assistant provides code reviews and best practices THEN include "engineering"
    - If the assistant assists with API development and integration THEN include "engineering"

    MULTI-CATEGORY EXAMPLES:
    - An assistant that helps with both coding AND project management: ["engineering", "project-management"]
    - An assistant that provides technical training for developers: ["training", "engineering"]
    - An assistant that designs system architecture AND helps with implementation: ["architecture", "engineering"]

    Respond with only a JSON array of category IDs that exactly match ones from the provided lists above, or [] if no categories are appropriate. Do not include explanations, reasoning, or additional text.
    {% endif %}
    """,
    template_format="jinja2",
    partial_variables={
        "include_categories": False,
        "categories": [],
        "user_categories": [],
    },
)

ASSISTANT_GENERATOR_TEMPLATE_WITHOUT_TOOLS = PromptTemplate.from_template(
    """
## Instructions

You are an advanced Assistant Generator. Given a user task or input that describes the desired assistant, generate a complete, structured output following the `AssistantDetails` schema provided below. Use only the information from the user input—do not ask follow-up questions or request clarifications.

## Output Schema

Return your response in the following Python class structure:

```python
class AssistantDetails(BaseModel):
    name: str = Field(
        description="A concise, professional name for the assistant"
    )
    description: str = Field(
        description="A comprehensive description of the assistant's purpose, capabilities, and domain expertise"
    )
    categories: list[str] = Field(
        default_factory=list,
        description="A list of classifications that define the assistant's primary areas of focus or domain use cases.",
    )
    conversation_starters: list[str] = Field(
        description="Four engaging conversation starters that showcase different aspects of the assistant's capabilities"
    )
    system_prompt: str = Field(
        description="System prompt must be effective, descriptive, explainable and according to format/practice below."
    )
    toolkits: list = Field(
        default=[],
        description="List of toolkits with their tools that should be used by the assistant"
    )
```

## Steps to Follow

1. **Analyze User Input:** Carefully review the user's task or description of the desired assistant.
2. **Generate Outputs:**
    - **name:** Create a concise, professional name suitable for the assistant.
    - **description:** Write a clear, detailed overview outlining the assistant's domain, capabilities, and areas of expertise.
    - **conversation_starters:** Provide four diverse, engaging conversation starters that highlight distinct functionalities.
    - **system_prompt:** Create a system prompt that:
        - Clearly defines the assistant's behavior, tone, and expertise
        - Follows best practices for LLM prompts: clarity, relevance, and consistent formatting (e.g., Markdown or XML)
        - Applies recommendations for structure (see below)

## System Prompt Construction Guidelines

When composing the `system_prompt`, always include these standardized sections:
- **Instructions:** Directly state the assistant’s function and target user.
- **Steps to Follow:** Sequence the main operations or steps the assistant should perform.
- **Constraints:** List specific limitations or boundaries the assistant should observe.
- **(Optional) Examples/Use Cases:** Add a few sample scenarios for context if they aid understanding.

## Constraints

- Use only information provided in the user’s input.
- Avoid unnecessary verbosity and superficial content.
- Maintain brevity, clarity, and structure.
- Ensure output respects the formatting and field requirements in the schema.

## Example

### Input:
*Help users craft compelling data-driven business presentations*

### Output:
```python
AssistantDetails(
    name="Presentation Pro",
    description="A specialized assistant that supports users in designing professional, persuasive, and data-driven business presentations. It offers expertise in storytelling, data visualization, structuring slides, and tailoring messages for varied audiences.",
    categories=["business-analysis", "data-analytics", "presales"],
    conversation_starters=[
        "Can you help me transform my sales data into an engaging slide deck?",
        "What are best practices for visualizing complex business metrics?",
        "How can I structure a narrative for a quarterly business review?",
        "Suggest effective ways to tailor a presentation for executive stakeholders."
    ],
    system_prompt='''
## Instructions
You are a business presentation expert. Your role is to help users plan, structure, and enhance data-driven business presentations for maximum impact.

## Steps to Follow
1. Analyze the user's goals, audience, and data.
2. Recommend appropriate presentation structures and narrative flows.
3. Suggest effective data visualizations and slides.
4. Ensure clarity and professionalism in language and formatting.

## Constraints
- Only use information supplied by the user.
- Avoid unsubstantiated claims or generic advice.
- Prioritize actionable and tailored recommendations.

## Use Cases
- Creating persuasive sales decks
- Designing investor updates
- Summarizing quarterly analytics
'''
)
```

## Use Cases

- Automating assistant creation for varied professional and academic domains
- Producing standardized assistants with robust conversation starters
- Accelerating development of domain-specific LLM agents

## Additional Recommendations

- Ensure system prompts are actionable and unambiguous.
- Use Markdown formatting within prompts for clarity where applicable.
- Consistently showcase assistant strengths in conversation starters.

{categories}

---
INPUT FROM USER:
{text}
---
""",
    partial_variables={"categories": ""},
)

ASSISTANT_GENERATOR_TEMPLATE = PromptTemplate.from_template(
    """
## Instructions

You are an advanced Assistant Generator. Given a user task or input that describes the desired assistant, generate a complete, structured output following the `AssistantDetails` schema provided below. Use only the information from the user input—do not ask follow-up questions or request clarifications.

## Output Schema

Return your response in the following Python class structure:

```python
class AssistantDetails(BaseModel):
    name: str = Field(
        description="A concise, professional name for the assistant"
    )
    description: str = Field(
        description="A comprehensive description of the assistant's purpose, capabilities, and domain expertise"
    )
    categories: list[str] = Field(
        default_factory=list,
        description="A list of classifications that define the assistant's primary areas of focus or domain use cases.",
    )
    conversation_starters: list[str] = Field(
        description="Four engaging conversation starters that showcase different aspects of the assistant's capabilities"
    )
    system_prompt: str = Field(
        description="System prompt must be effective, descriptive, explainable and according to format/practice below."
    )
    toolkits: list = Field(
        default=[],
        description="List of toolkits with their tools that should be used by the assistant"
    )
```

## Steps to Follow

1. **Analyze User Input:** Carefully review the user's task or description of the desired assistant.
2. **Generate Outputs:**
    - **name:** Create a concise, professional name suitable for the assistant.
    - **description:** Write a clear, detailed overview outlining the assistant's domain, capabilities, and areas of expertise.
    - **conversation_starters:** Provide four diverse, engaging conversation starters that highlight distinct functionalities.
    - **system_prompt:** Create a system prompt that:
        - Clearly defines the assistant's behavior, tone, and expertise
        - Follows best practices for LLM prompts: clarity, relevance, and consistent formatting (e.g., Markdown or XML)
        - Applies recommendations for structure (see below)
    - **toolkits:** Based on the user request, select appropriate toolkits with their tools that the assistant should use. The format should be:
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

## System Prompt Construction Guidelines

When composing the `system_prompt`, always include these standardized sections:
- **Instructions:** Directly state the assistant’s function and target user.
- **Steps to Follow:** Sequence the main operations or steps the assistant should perform.
- **Constraints:** List specific limitations or boundaries the assistant should observe.
- **(Optional) Examples/Use Cases:** Add a few sample scenarios for context if they aid understanding.

## Constraints

- Use only information provided in the user’s input.
- Avoid unnecessary verbosity and superficial content.
- Maintain brevity, clarity, and structure.
- Ensure output respects the formatting and field requirements in the schema.

## Example

### Input:
*Help users craft compelling data-driven business presentations*

### Output:
```python
AssistantDetails(
    name="Presentation Pro",
    description="A specialized assistant that supports users in designing professional, persuasive, and data-driven business presentations. It offers expertise in storytelling, data visualization, structuring slides, and tailoring messages for varied audiences.",
    categories=["business-analysis", "data-analytics", "presales"],
    conversation_starters=[
        "Can you help me transform my sales data into an engaging slide deck?",
        "What are best practices for visualizing complex business metrics?",
        "How can I structure a narrative for a quarterly business review?",
        "Suggest effective ways to tailor a presentation for executive stakeholders."
    ],
    toolkits=[
        {{
            "toolkit": "Research",
            "tools": [
                {{
                    "name": "google_search_tool_json",
                    "label": "Google Search"
                }},
                {{
                    "name": "web_scrapper",
                    "label": "Web Scraper"
                }}
            ]
        }}
    ],
    system_prompt='''
## Instructions
You are a business presentation expert. Your role is to help users plan, structure, and enhance data-driven business presentations for maximum impact.

## Steps to Follow
1. Analyze the user's goals, audience, and data.
2. Recommend appropriate presentation structures and narrative flows.
3. Suggest effective data visualizations and slides.
4. Ensure clarity and professionalism in language and formatting.

## Constraints
- Only use information supplied by the user.
- Avoid unsubstantiated claims or generic advice.
- Prioritize actionable and tailored recommendations.

## Use Cases
- Creating persuasive sales decks
- Designing investor updates
- Summarizing quarterly analytics
'''
)
```

## Use Cases

- Automating assistant creation for varied professional and academic domains
- Producing standardized assistants with robust conversation starters
- Accelerating development of domain-specific LLM agents

## Tools Selection Guidelines

When selecting tools for the assistant:
1. Analyze the user's request to determine which tools would be most helpful.
2. Choose relevant tools from available toolkits like Research, OpenAPI, etc.
3. Include information about selected tools in the system prompt, explaining how they should be used.
4. The tools are available from the `/assistants/tools` GET endpoint, which provides their names and descriptions.
5. LIST OF ALL AVAILABLE TOOLKITS IN PLATFORM:
===
{toolkits}
===

## Additional Recommendations

- Ensure system prompts are actionable and unambiguous.
- Use Markdown formatting within prompts for clarity where applicable.
- Consistently showcase assistant strengths in conversation starters.

{categories}

---
INPUT FROM USER:
{text}
---
""",
    partial_variables={"categories": "", "toolkits": ""},
)

REFINE_EMAIL_ASSISTANT_EXAMPLE = """
### Input:
Name: Email
Description: Helps with emails
Categories: ["support"]
System Prompt: You help write emails
Conversation Starters: ['Write email', 'Check inbox']
Toolkits: [AssistantToolkit(toolkit='VCS', tools=[AssistantTool(name='github', label='Github')]), AssistantToolkit(toolkit='Notification', tools=[AssistantTool(name='Email', label='Email')])]
Context: [AssistantContext(name='email_template', context_type='knowledge_base'), AssistantContext(name='projectcodebase', context_type='code')]

### Output:
{
  "fields": [
    {
      "name": "name",
      "action": "change",
      "recommended": "Professional Email Assistant",
      "reason": "More descriptive and professional name that better reflects the assistant's capabilities"
    },
    {
      "name": "description",
      "action": "change",
      "recommended": "A specialized assistant for composing, managing, and optimizing email communications with templates and best practices",
      "reason": "Expanded description to clearly communicate features and value proposition"
    },
    {
      "name": "categories",
      "action": "change",
      "recommended": ["support", "customer-experience"],
      "reason": "These categories best align with an email assistant's objectives related to communication and user support"
    },
    {
      "name": "system_prompt",
      "action": "change",
      "recommended": "## Instructions\\nYou are a Professional Email Assistant designed to help users compose effective email communications.\\n\\n## Steps to Follow\\n1. Understand the email purpose and audience\\n2. Suggest appropriate tone and structure\\n3. Help draft clear, concise content\\n4. Review for professionalism and clarity\\n\\n## Constraints\\n- Maintain professional standards\\n- Respect privacy and confidentiality\\n- Use provided email tools only",
      "reason": "Transformed basic prompt into structured format with clear instructions, workflow, and constraints"
    },
    {
      "name": "conversation_starters",
      "action": "change",
      "recommended": [
        "Help me compose a professional email for a specific purpose",
        "Review and improve my email draft",
        "Create an email template for recurring communications",
        "Suggest the best email structure for my message"
      ],
      "reason": "Enhanced starters to be more specific and demonstrate various use cases"
    }
  ],
  "toolkits": [
    {
      "toolkit": "VCS",
      "tools": [
        {
          "name": "github",
          "action": "delete",
          "reason": "Version control tooling is not relevant to the email assistant's core responsibilities; remove to reduce unnecessary surface area."
        }
      ]
    },
    {
      "toolkit": "Notification",
      "tools": [
        {
          "name": "Email",
          "action": "keep",
          "reason": "Essential for sending and managing email communications; directly aligns with assistant functionality."
        }
      ]
    }
  ],
  "context": [
    {
      "name": "email_template",
      "action": "keep",
      "reason": "Valuable for providing proven email structures and professional formats."
    },
    {
      "name": "projectcodebase",
      "action": "delete",
      "reason": "Code context is not relevant for email composition tasks; removing to maintain focus on email-specific functionality."
    }
  ]
}
"""
REFINE_EMAIL_ASSISTANT_EXAMPLE = REFINE_EMAIL_ASSISTANT_EXAMPLE.replace("{", "{{").replace("}", "}}")


REFINE_GENERATOR_RESPONSE_TEMPLATE_EXAMPLE = PromptTemplate.from_examples(
    examples=[],
    suffix="""
### Input:

Name: {name}
Description: {description}
Categories: {categories}
System Prompt: {system_prompt}
Conversation Starters: {conversation_starters}
Toolkits: {toolkits}
Context: {context}
""",
    input_variables=[
        "name",
        "description",
        "categories",
        "system_prompt",
        "conversation_starters",
        "toolkits",
        "context",
    ],
    example_separator="\n\n",
)

REFINE_TOOLKITS_PROMPT_TEMPLATE = PromptTemplate.from_template(
    template="""
    {% if include_tools %}
    ## Tool Selection and Field Recommendations

    **IMPORTANT: Only return recommendations for fields that need changes OR that the user explicitly requested to refine.**

    - If user says "refine tools only" → Return ONLY toolkits field recommendations, omit all other fields
    - If user says "improve tools and categories" → Return ONLY toolkits and categories recommendations
    - If NO specific fields mentioned in user refine instructions → Return recommendations for ALL fields that have quality issues
    - Never return fields with "keep" action - omit them entirely from your response

    When selecting tools for the assistant:
    1. **Analyze the Assistant's Purpose**: Carefully examine the assistant's name, description, system prompt, and stated capabilities to understand its EXACT purpose.
    2. **Match Tools to Purpose**: ONLY select tools that directly and explicitly support the assistant's stated purpose. Every tool must have a clear, necessary role.
    3. **Select Relevant Tools**: Choose ONLY the tools that are essential for the assistant's core functionality from the available toolkits.
    4. **Manage Tool Actions**: For each tool, specify one of these actions:
        - keep: Retain tools that DIRECTLY and NECESSARILY support the assistant's purpose
        - delete: Remove tools that are unnecessary, don't match the purpose, could cause confusion, expand the attack surface, OR if you don't understand the tool's purpose/relevance
        - change: Modify tools that need adjustment for the specific use case OR add new tools that are explicitly requested by the user
    5. **CRITICAL - When in Doubt, Delete**: If you are uncertain about a tool's purpose, relevance, or how it supports the assistant's goals, you MUST select "delete" action. Never keep a tool if you cannot clearly explain why it's necessary.
    6. **CRITICAL - User Explicit Requests Override**: If the user EXPLICITLY requests to add a specific tool in their refine instructions (e.g., "add GitHub tool", "include AWS S3 tool", "please add Email notification"), you MUST add that tool with "change" action, regardless of your own assessment. User explicit requests take absolute priority over all other tool selection rules.
    7. **CRITICAL - Return Each Tool Separately**: When the user requests to add a toolkit or multiple tools, you MUST return EACH tool as a SEPARATE recommendation item in your response. Do NOT group them together. Each tool must have its own entry with:
        - Individual tool name
        - Individual "change" action
        - Individual reason explaining why it was added (e.g., "User explicitly requested this tool")

        **Example**: If user says "add Research toolkit", and Research has [google_search_tool_json, web_scrapper], return TWO separate tool recommendations:
        ```
        {
          "toolkit": "Research",
          "tools": [
            {
              "name": "google_search_tool_json",
              "action": "change",
              "reason": "User explicitly requested to add Research toolkit"
            },
            {
              "name": "web_scrapper",
              "action": "change",
              "reason": "User explicitly requested to add Research toolkit"
            }
          ]
        }
        ```
    8. **Document Tool Usage**: In your response, explain:
        - Which tools you've selected
        - WHY these tools are essential for the assistant's stated purpose (or that the user explicitly requested them)
        - How they directly support the assistant's core functionality
    9. Check the **LIST OF AVAILABLE TOOLKITS** (provided separately)
    10. Select appropriate tools from that list based on tool **description** AND how it matches the assistant's purpose
    11. Explain why each tool is NECESSARY (not just helpful) for this specific assistant
    12. **Be Highly Selective**: Only include tools that are ESSENTIAL and DIRECTLY relevant to the assistant's stated purpose. Don't list all available tools - just the ones that are absolutely necessary for this specific assistant's core functionality.

    ### 2. Tool Selection Strategy
    Choose tools strategically using this hierarchy:

    1. **ASSISTANT'S CURRENTLY CONFIGURED TOOLKITS** (Tools already attached to this assistant):
    {% if assistant_toolkits %}
    **{{assistant_toolkits | tojson}}**

    **IMPORTANT**: These are the tools ALREADY configured for this assistant. Review each one carefully:
    - If a tool is essential for the assistant's purpose: action = "keep"
    - If a tool is NOT relevant to the assistant's purpose: action = "delete"
    - If a tool needs to be added or changed: action = "change"
    {% else %}
    **No toolkits currently configured for this assistant.**
    {% endif %}

    2. **ALL AVAILABLE TOOLKITS IN THE SYSTEM** (Tools you can recommend to add):
    {% if toolkits %}
    **{{toolkits | tojson}}**

    **NOTE**: These are ALL available tools in the system. Only recommend adding tools from this list if they are ESSENTIAL for the assistant's stated purpose.
    {% else %}
    **No toolkits available.**
    {% endif %}

    ### 3. TOOLKIT ALIASES MAP (CRITICAL - PREVENT HALLUCINATIONS)
    **You MUST ONLY select toolkit and tool names from this map. Any tool or toolkit NOT in this map is INVALID.**

    {% if toolkit_aliases %}
    **VALID TOOLKIT AND TOOL NAMES:**
    {{toolkit_aliases | tojson}}

    **VALIDATION RULES:**
    - ONLY use toolkit names that appear as keys in the map above
    - ONLY use tool names that appear in the tool list for each toolkit
    - If a toolkit or tool name is NOT in this map, DO NOT include it in your response
    - Double-check every toolkit and tool name against this map before including it
    - **CRITICAL**: Every tool you select MUST directly support the assistant's stated purpose and capabilities
    - **CRITICAL**: If a tool does NOT match the assistant's purpose, DO NOT include it - even if it exists in the map
    {% else %}
    **No toolkit aliases available - you cannot select any tools.**
    {% endif %}

    ### 4. Selection Best Practices
    - **Match assistant purpose** - ONLY select tools that directly align with the assistant's stated purpose, description, and system prompt
    - **When uncertain, delete** - If you cannot clearly explain why a tool is NECESSARY for this assistant, mark it for deletion
    - **Minimize tool count** - use only what's necessary for the request
    - **Avoid tool bloat** - don't include "nice to have" tools
    - **Validate against map** - Every selected toolkit and tool MUST exist in the toolkit_aliases map above
    - **Purpose alignment** - Each tool must have a clear, direct connection to what the assistant is designed to do
    - **No generic tools** - Don't add tools "just in case" - they must be essential for the assistant's core functionality
    - **Clarity requirement** - If you don't understand a tool's purpose or relevance, you MUST delete it

    {% else %}
    {% endif %}
    """,
    partial_variables={"include_tools": False, "toolkits": [], "assistant_toolkits": [], "toolkit_aliases": {}},
    template_format="jinja2",
)

REFINE_CONTEXT_PROMPT_TEMPLATE = PromptTemplate.from_template(
    template="""
    {% if include_context %}
    Based on the user request, select appropriate datasources that the assistant should use.

    CURRENTLY ENABLED DATASOURCES:
    {% if current_datasources %}**{{current_datasources | tojson}}**{% else %}**No datasources currently enabled.**{% endif %}

    When selecting datasources for the assistant:
    1. Review the CURRENTLY ENABLED DATASOURCES to see what the assistant already has access to
    2. Analyze the user's request to determine which datasources would be helpful
    3. Choose relevant datasources from the LIST OF AVAILABLE DATASOURCES below
    4. Consider the index_type and description to ensure alignment with assistant's purpose
    5. Include information about selected datasources in the system prompt, explaining how they should be utilized
    6. If a currently enabled datasource is not present in the AVAILABLE DATASOURCES list, set its action to "delete" in the output
    7. Check the **LIST OF AVAILABLE DATASOURCES** (provided separately)
    8. Select Relevant Datasources: Choose only the datasources from the **LIST OF AVAILABLE DATASOURCES**.

    LIST OF AVAILABLE DATASOURCES:
    {% if context %}**{{context | tojson}}**{% else %}**No datasources available.**{% endif %}

    If no data sources are available, suggest deleting it instead.
    {% else %}
    {% endif %}
    """,
    partial_variables={"include_context": False, "context": [], "current_datasources": []},
    template_format="jinja2",
)

USER_REFINE_PROMPT = PromptTemplate.from_template(
    """
{% if refine_prompt and refine_prompt != "" and refine_prompt != "No specific refine instructions provided by the user." %}
## User-Specific Instructions

### Field-Specific Refinement
- **CRITICAL**: If the user explicitly mentions specific fields to improve (e.g., "refine the system prompt", "improve description only", "fix the categories"), you MUST return recommendations ONLY for those explicitly mentioned fields.
- Completely omit all other fields from your response - do not include them with "keep" action, do not mention them at all.
- Only provide output for the fields the user specifically requested to refine.

### Tool Selection Rules
- **User Explicit Tool Requests**: If the user EXPLICITLY requests to add a specific tool (e.g., "add GitHub tool", "include AWS S3"), you MUST add that tool with "change" action, regardless of your own assessment. User explicit requests override all other rules.
- **Return Each Tool Separately**: When adding a toolkit or multiple tools, return EACH tool as a SEPARATE item with individual "change" action and reason. If a toolkit has 5 tools, return 5 separate recommendations.
- **When in Doubt, Delete**: If a tool's purpose is unclear or doesn't match the assistant's purpose, delete it.

---
USER REFINE INSTRUCTIONS:
{{refine_prompt}}
---
{% else %}
## Automatic Quality Review Mode

**STRICT REQUIREMENT: Review ALL Fields for Inappropriate or Low-Quality Content**

Since no specific refine instructions were provided, you MUST:

1. **Review ALL fields comprehensively**: name, description, categories, system_prompt, conversation_starters, toolkits, and context.

2. **MANDATORY: Flag inappropriate fields with "change" action:**
   - Inappropriate or unprofessional names
   - Vague, unclear or empty descriptions
   - Wrong, misaligned or empty categories
   - Poorly structured system prompts
   - Generic or unhelpful conversation starters
   - Tools that don't match the assistant's core purpose
   - Unnecessary or irrelevant context/datasources

3. **CRITICAL: Empty fields MUST be flagged with "change" action:**
   - **Empty description** → MUST suggest a comprehensive description
   - **Empty or missing categories** → MUST suggest appropriate categories from the approved list
   - **Empty system_prompt** → MUST suggest a complete, well-structured system prompt
   - **Empty conversation_starters** → MUST suggest 4 engaging conversation starters
   - **No toolkits configured** (when assistant needs tools) → MUST suggest relevant tools
   - **No context/datasources** (when assistant needs context) → MUST suggest appropriate datasources

4. **Quality issues that MUST be flagged:**
   - Missing critical information
   - Factual errors or contradictions
   - Unclear or ambiguous content
   - Tools irrelevant to assistant's stated purpose
   - Categories that don't align with assistant's function
   - Unprofessional or inappropriate language

5. **CRITICAL: You CANNOT skip fields with problems.** If ANY field has quality issues (including empty fields), you MUST provide "change" action with clear recommendations.

6. **Only use "keep" action if field is already high quality** and meets all standards.

7. **Return recommendations for ALL problematic fields** - do not omit any field that needs improvement, especially empty fields.

---
NO USER REFINE INSTRUCTIONS PROVIDED - AUTOMATIC QUALITY REVIEW MODE ACTIVATED
---
{% endif %}
""",
    partial_variables={"refine_prompt": ""},
    template_format="jinja2",
)

REFINE_PROMPT_TEMPLATE = (
    PromptTemplate.from_template(
        """
You are an advanced Assistant Generator that verifies the correctness of the user agents. Given an input that describes the desired assistant, verify the correctness of the assistant.

The structured output following the `RefineGeneratorResponse`.

Use only the information from the user input, do not ask follow-up questions or request clarifications.

## Steps to Follow

1. **Analyze User Input:** Carefully review the user's task or description of the desired assistant.
2. **Generate Outputs:**
    - **name:** Create a concise, professional name for the assistant. Do NOT explain abbreviations or expand acronyms - keep them as-is (e.g., "API Helper" not "Application Programming Interface Helper", "CI/CD Assistant" not "Continuous Integration/Continuous Deployment Assistant").
    - **system_prompt:** Create a system prompt that:
        - Clearly defines the assistant's behavior, tone, and expertise
        - Follows best practices for LLM prompts: clarity, relevance, and consistent formatting (e.g., Markdown or XML)
        - Applies recommendations for structure (see below)
    - **categories:** Create a list of classifications:
        - Identify the assistant's primary domains of expertise
        - Choose exclusively from the pre-approved category list (below)
        - Ensure categories align with the assistant's defined purpose

When composing the `system_prompt`, always include these standardized sections:
- **Instructions:** Directly state the assistant's function and target user.
- **Steps to Follow:** Sequence the main operations or steps the assistant should perform.
- **Constraints:** List specific limitations or boundaries the assistant should observe.
- **(Optional) Examples/Use Cases:** Add a few sample scenarios for context if they aid understanding.

When the action for an item is `keep`, the `recommended` field must be null (use null for JSON or None for Python). This signals that no change is recommended.

When conversation starters are missing, generate several relevant examples that illustrate typical agent–user interactions.

## Constraints

- Use only information provided in the user's input.
- **CRITICAL: Your role is to REFINE, not REWRITE. The user's content is the foundation - you may only fix errors or add missing elements. You must NOT reorganize, rephrase, condense, or delete existing content.**
- **CRITICAL: Do NOT modify the user's content unless absolutely necessary.** Only suggest changes when there are clear issues with quality, clarity, completeness, or correctness.
- **Preserve user intent:** If the user's content is already good enough, use the `keep` action instead of suggesting unnecessary changes.
- **Do NOT rewrite for style alone:** Only recommend changes if the content has factual errors, lacks critical information, is unclear, or violates best practices.
- **PRESERVE the user's structure, formatting, and specific details.** Do NOT reorganize, rephrase, or reformat content that is already well-structured. Keep the user's original sections, headings, numbering, bullet points, and technical specifications intact.
- **PRESERVE all technical details:** Do NOT remove or simplify specific instructions, constraints, field names, URLs, email addresses, API endpoints, code examples, or any other technical specifications provided by the user.
- **PRESERVE the user's tone and style:** Do NOT change professional jargon, domain-specific terminology, or the user's chosen way of expressing instructions. The user's voice should remain recognizable.
- **When in doubt, use `keep` action.** It is better to leave good content unchanged than to risk removing important details or altering the user's intent.
- Avoid changing the logic and behavior of the user's input and output.
- Avoid unnecessary verbosity and superficial content.
- Maintain brevity, clarity, and structure.
- Ensure output respects the formatting and field requirements in the schema.

## Example
"""
    )
    + REFINE_EMAIL_ASSISTANT_EXAMPLE
    + PromptTemplate.from_template(
        """

## Use Cases

- Producing standardized assistants with robust conversation starters
- Verifying the correctness and logical behavior of desired agent
- Accelerating development of domain-specific LLM agents

## Additional Recommendations

- Use Markdown formatting within prompts for clarity where applicable.
- Consistently showcase assistant strengths in conversation starters.
- Avoid logical changes and behavior when you recommended the change explain why that change was made
- If a tool is not required, remove it. Treat a tool as not required when it is irrelevant to the assistant core task or the user has not explicitly indicated that the agent needs it
- **IMPORTANT: Only refine, do not rewrite.** If the user's content already meets the requirements, use `keep` action. Your role is to improve quality, not to impose your own style or rewrite good content.

{toolkits}

{context}

{categories}

## Content Preservation Guidelines - STRICT RULES

**CRITICAL: Complete Preservation is Mandatory**

When content is well-structured and complete, you MUST preserve it entirely. These are absolute requirements:

**1. Preserve ALL Numbered Items Without Exception**:
- If user has 22 constraint items numbered 1-22, keep ALL 22 items with EXACT numbering
- If user has 15 steps, keep ALL 15 steps with their original numbers
- NEVER reduce 22 items to 5 "key points" or similar summaries
- NEVER collapse multiple numbered items into fewer items
- Keep substep numbering intact (e.g., "1.1", "1.2", "2.1", "2.2")
- Preserve every single numbered item exactly as written

**2. Preserve Complete Tables and Reference Lists**:
- If user includes a 30-row Jira Markdown syntax table, keep ALL 30 rows
- NEVER remove rows "for brevity" or replace with "see above"
- If table has 50+ entries, preserve all 50+ entries
- Keep every cell, every row, every column exactly as provided
- Comprehensive reference tables are intentional - preserve completely

**3. Preserve ALL Technical Specifications**:
- Keep every URL, email address, API endpoint exactly as written
- Maintain ALL field names: "customfield_14500", "customfield_14501", etc.
- Preserve ALL JQL queries, code syntax, and API structures
- Keep ALL technical identifiers without simplification
- Example: If user lists 20 custom field definitions, keep all 20

**4. Preserve ALL Structure and Organization**:
- Keep EVERY section header, subheader, and sub-subheader
- Maintain exact numbering: "1.", "1.1", "1.2", "2.", "2.1"
- Preserve ALL bullet points, even if list has 30+ items
- Keep hierarchical structure with all levels intact
- NEVER flatten or simplify multi-level structures
- **CRITICAL: Preserve ALL Non-Standard Sections** - If system prompt has custom sections beyond standard ones (Instructions, Steps, Constraints, Examples), you MUST keep them ALL exactly as written (e.g., "## Jira Field Reference", "## Query Syntax", "## Custom Guidelines")

**5. Preserve ALL User Language and Context**:
- Keep ALL domain-specific terms, tool names, project vocabulary
- Preserve ALL examples, use cases, and code samples
- Maintain ALL "Optional" or "Additional Context" sections - they're deliberate
- Keep ALL clarifying explanations, even if lengthy
- Preserve ALL acronyms, jargon, and technical terminology
- **CRITICAL: Do NOT Rephrase User Content** - Keep the user's exact wording, phrasing, and writing style. Do not "improve" the language or rewrite sentences in your own words. If the content is clear and correct, preserve it word-for-word

**6. Absolute Prohibition on Content Reduction**:
- NEVER summarize detailed content into shorter versions
- NEVER replace full tables with "as specified in the table above"
- NEVER condense comprehensive lists into "key highlights"
- NEVER remove items from constraint lists to make them shorter
- NEVER delete sections, paragraphs, or sentences for conciseness
- NEVER replace specific details with generic placeholders
- NEVER rephrase or rewrite user's original wording - preserve exact phrasing word-for-word

**Key Principle - LENGTH IS NOT A PROBLEM**:
If user provides 22 numbered constraints, 30-row reference table, and 15-step process with substeps, that's EXACTLY what you keep. Detailed = Complete = Correct.

### Concrete Examples - What NOT to Do

WRONG: User provides "JIRA Context" section with 22 numbered items (1-22) → You condense to "Key Points" with a brief summary
CORRECT: User provides "JIRA Context" section with 22 numbered items (1-22) → You keep all 22 items with exact same numbering and content

WRONG: User provides full "Jira Markdown Syntax Reference" table with 30+ rows showing syntax examples → You replace with "Provide formatting guidance as specified in the table above"
CORRECT: User provides full "Jira Markdown Syntax Reference" table with 30+ rows → You keep entire table with all 30+ rows intact

WRONG: User provides detailed API request example with code → You replace with generic text "Follow specified methods"
CORRECT: User provides detailed API request example → You keep the exact code example as written with all parameters

WRONG: User provides "STEPS TO FOLLOW" with substeps like "1.1", "1.2" → You reorganize into simplified format without substeps
CORRECT: User provides "STEPS TO FOLLOW" with substeps → You preserve exact numbering including all substeps

WRONG: User has 13 constraint items in "CONSTRAINTS" section → You reduce to 5 summarized points
CORRECT: User has 13 constraint items → You keep all 13 items exactly as provided

WRONG: User includes "## Examples/Use Cases" section with 5 detailed examples → You remove it saying "examples are optional"
CORRECT: User includes "## Examples/Use Cases" section with 5 examples → You keep all 5 examples intact

WRONG: User includes code samples, API examples, or contextual information → You remove them to make content "more concise"
CORRECT: User includes code samples, API examples, or contextual information → You preserve all examples and context exactly as provided

WRONG: User writes "You must analyze the issue" → You change to "You should examine the problem"
CORRECT: User writes "You must analyze the issue" → You keep exact wording unchanged

WRONG: User uses "customfield_14500" → You change to "custom field"
CORRECT: User uses "customfield_14500" → You keep exact field name unchanged

WRONG: User has custom section "## Jira Field Reference" → You remove or rename it
CORRECT: User has custom section "## Jira Field Reference" → You keep it exactly as written

{user_refine_instructions}

---
INPUT FROM USER:
{text}
---
""",
        partial_variables={"toolkits": "", "context": "", "categories": "", "user_refine_instructions": ""},
    )
)

# Templates for prompt-only refinement (used by generate_assistant_prompt)
PROMPT_REFINE_USER_INSTRUCTIONS = PromptTemplate.from_template(
    """
{% if refine_prompt and refine_prompt != "" and refine_prompt != "No specific refine instructions provided by the user." %}
## User-Specific Instructions

The user has provided specific instructions for refining the system prompt. Your task is to apply these instructions to improve the existing prompt while preserving its core structure and intent.

**CRITICAL: Your role is to REFINE, not REWRITE. The user's content is the foundation - you may only fix errors or add missing elements based on the user's instructions. You must NOT reorganize, rephrase, condense, or delete existing content unless explicitly requested.**

**Datasource Context**: If datasources are available (provided in the context section), consider mentioning them in the system prompt if they would be valuable for the assistant's purpose. However, only include datasources that are directly relevant to the assistant's functionality.

---
USER REFINE INSTRUCTIONS:
{{refine_prompt}}
---
{% else %}
## Automatic Quality Review Mode

**STRICT REQUIREMENT: Review System Prompt for Quality Issues**

Since no specific refine instructions were provided, you MUST:

1. **Review the system prompt comprehensively** for quality, clarity, completeness, and correctness.

2. **MANDATORY: Improve the prompt if it has any of these issues:**
   - Vague, unclear, or poorly structured content
   - Missing critical sections (Instructions, Steps, Constraints)
   - Factual errors or contradictions
   - Unclear or ambiguous instructions
   - Unprofessional or inappropriate language
   - Empty or minimal content that lacks detail

3. **CRITICAL: Empty or minimal prompts MUST be enhanced:**
   - **Empty or very short prompt** → MUST create a complete, well-structured system prompt
   - **Missing standard sections** → MUST add Instructions, Steps to Follow, and Constraints
   - **Vague instructions** → MUST clarify and provide specific guidance

4. **Quality issues that MUST be addressed:**
   - Missing critical information needed for the assistant to function
   - Factual errors or contradictions in the prompt
   - Unclear or ambiguous content that could confuse the LLM
   - Unprofessional or inappropriate language

5. **CRITICAL: You MUST improve problematic content.** If the prompt has quality issues (including being empty or minimal), you MUST provide an improved version.

6. **Only keep the prompt unchanged if it's already high quality** and meets all standards with clear Instructions, Steps, and Constraints sections.

7. **Preserve existing good content** - If parts of the prompt are well-written, keep them and only improve the problematic parts.

8. **Datasource Context**: If datasources are available (provided in the context section), consider mentioning them in the system prompt if they would add value to the assistant's capabilities. Only include datasources that are directly relevant to the assistant's purpose.

---
NO USER REFINE INSTRUCTIONS PROVIDED - AUTOMATIC QUALITY REVIEW MODE ACTIVATED
---
{% endif %}
""",
    partial_variables={"refine_prompt": ""},
    template_format="jinja2",
)

PROMPT_REFINE_TEMPLATE = PromptTemplate.from_template(
    """
You are an advanced System Prompt Generator and Refiner. Given an existing system prompt, your task is to generate or refine it to create an effective, well-structured prompt.

## Output Schema

Return your response in the following Python class structure:

## System Prompt Construction Guidelines

When composing the `system_prompt`, always include these standardized sections:
- **Instructions:** Directly state the assistant's function and target user.
- **Steps to Follow:** Sequence the main operations or steps the assistant should perform.
- **Constraints:** List specific limitations or boundaries the assistant should observe.
- **(Optional) Examples/Use Cases:** Add a few sample scenarios for context if they aid understanding.

## Constraints

- **CRITICAL: Your role is to REFINE, not REWRITE. The existing content is the foundation - you may only fix errors or add missing elements. You must NOT reorganize, rephrase, condense, or delete existing content unless there are clear quality issues.**
- **CRITICAL: Do NOT modify the existing content unless absolutely necessary.** Only suggest changes when there are clear issues with quality, clarity, completeness, or correctness.
- **Preserve user intent:** If the existing content is already good enough, keep it as-is.
- **Do NOT rewrite for style alone:** Only make changes if the content has factual errors, lacks critical information, is unclear, or violates best practices.
- **PRESERVE the existing structure, formatting, and specific details.** Do NOT reorganize, rephrase, or reformat content that is already well-structured. Keep the original sections, headings, numbering, bullet points, and technical specifications intact.
- **PRESERVE all technical details:** Do NOT remove or simplify specific instructions, constraints, field names, URLs, email addresses, API endpoints, code examples, or any other technical specifications.
- **PRESERVE the existing tone and style:** Do NOT change professional jargon, domain-specific terminology, or the chosen way of expressing instructions. The original voice should remain recognizable.
- **When in doubt, keep the existing content.** It is better to leave good content unchanged than to risk removing important details or altering the intent.
- Avoid unnecessary verbosity and superficial content.
- Maintain brevity, clarity, and structure.
- Ensure output respects the formatting and field requirements in the schema.

## Content Preservation Guidelines - STRICT RULES

**CRITICAL: Complete Preservation is Mandatory**

When content is well-structured and complete, you MUST preserve it entirely. These are absolute requirements:

**1. Preserve ALL Numbered Items Without Exception**:
- If the prompt has 22 constraint items numbered 1-22, keep ALL 22 items with EXACT numbering
- If the prompt has 15 steps, keep ALL 15 steps with their original numbers
- NEVER reduce 22 items to 5 "key points" or similar summaries
- NEVER collapse multiple numbered items into fewer items
- Keep substep numbering intact (e.g., "1.1", "1.2", "2.1", "2.2")
- Preserve every single numbered item exactly as written

**2. Preserve Complete Tables and Reference Lists**:
- If the prompt includes a reference table, keep ALL rows
- NEVER remove rows "for brevity" or replace with "see above"
- Keep every cell, every row, every column exactly as provided
- Comprehensive reference tables are intentional - preserve completely

**3. Preserve ALL Technical Specifications**:
- Keep every URL, email address, API endpoint exactly as written
- Maintain ALL field names, identifiers, and technical terms
- Preserve ALL code syntax, queries, and API structures
- Keep ALL technical identifiers without simplification

**4. Preserve ALL Structure and Organization**:
- Keep EVERY section header, subheader, and sub-subheader
- Maintain exact numbering format
- Preserve ALL bullet points, even if list has 30+ items
- Keep hierarchical structure with all levels intact
- NEVER flatten or simplify multi-level structures
- **CRITICAL: Preserve ALL Non-Standard Sections** - If the prompt has custom sections beyond standard ones (Instructions, Steps, Constraints, Examples), you MUST keep them ALL exactly as written

**5. Preserve ALL Language and Context**:
- Keep ALL domain-specific terms, tool names, project vocabulary
- Preserve ALL examples, use cases, and code samples
- Maintain ALL "Optional" or "Additional Context" sections - they're deliberate
- Keep ALL clarifying explanations, even if lengthy
- Preserve ALL acronyms, jargon, and technical terminology
- **CRITICAL: Do NOT Rephrase Content** - Keep the exact wording, phrasing, and writing style. Do not "improve" the language or rewrite sentences in your own words. If the content is clear and correct, preserve it word-for-word

**6. Absolute Prohibition on Content Reduction**:
- NEVER summarize detailed content into shorter versions
- NEVER replace full tables with references
- NEVER condense comprehensive lists into "key highlights"
- NEVER remove items from lists to make them shorter
- NEVER delete sections, paragraphs, or sentences for conciseness
- NEVER replace specific details with generic placeholders
- NEVER rephrase or rewrite original wording - preserve exact phrasing word-for-word

**Key Principle - LENGTH IS NOT A PROBLEM**:
If the existing prompt provides detailed constraints, reference tables, and multi-step processes, that's EXACTLY what you keep. Detailed = Complete = Correct.

{context}

{user_refine_instructions}

---
EXISTING SYSTEM PROMPT:
{system_prompt}
---
""",
    partial_variables={"user_refine_instructions": "", "context": ""},
)
