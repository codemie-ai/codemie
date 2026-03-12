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

"""
Prompt templates for AI-based marketplace assistant validation workflow.

This module contains all prompt templates used in the LangGraph-based validation
workflow for marketplace assistant publication.
"""

from langchain_core.prompts import PromptTemplate

# =============================================================================
# METADATA VALIDATION PROMPTS (Name, Description, Categories)
# =============================================================================

METADATA_VALIDATION_TEMPLATE = PromptTemplate.from_template(
    """## Task
Evaluate the assistant's Name and Description for quality, clarity, and marketplace readiness.

## Context
You are validating an AI assistant for publication in a marketplace. The metadata must be clear, professional, accurate, and help users understand what the assistant does.

## Input
**Name**: {name}
**Description**: {description}
**System Prompt** (for context): {system_prompt}

## Evaluation Guidelines

### 1. Name Validation
- **Clarity**: Is the name clear and descriptive?
- **Length**: Appropriate length (not too short, not too long - typically 2-5 words)?
- **Abbreviations**: Technical abbreviations and acronyms are ACCEPTABLE and ENCOURAGED (e.g., "AWS", "K8s", "CI/CD", "ML", "API", "SDK"). Do NOT flag abbreviations as unclear for technical assistants.
- **Product Names**: Names that reference the product (e.g., "CodeMie Onboarding Assistant", "AI/Run FAQ") are ACCEPTABLE when the assistant is specifically designed for that product.
- **FAQ/Help Assistants**: Names like "FAQ", "Help", "Onboarding" are ACCEPTABLE and CLEAR when combined with context (e.g., "CodeMie FAQ", "Platform Onboarding").
- **Professionalism**: Professional and marketplace-appropriate?
- **Uniqueness**: Distinctive and not generic, EXCEPT when the assistant's purpose is intentionally generic (e.g., FAQ assistants, onboarding guides, help desks).
- **Alignment**: Does it match the description and system prompt?

### 2. Description Validation
- **Technical Descriptions**: Technical terminology, jargon, and detailed technical descriptions are ACCEPTABLE and ENCOURAGED for technical assistants (e.g., engineering, DevOps, cloud, data science assistants). Do NOT flag technical language as unclear.
- **Abbreviations**: Abbreviations and acronyms do NOT need to be explained or spelled out in descriptions. Technical abbreviations (AWS, K8s, CI/CD, ML, API, SDK, ECS, EKS, AKS, GKE, etc.) are widely understood by technical audiences and should PASS validation without requiring expansion or explanation.
- **Product References**: Descriptions that mention product names (e.g., "CodeMie", "AI/Run") are ACCEPTABLE and ENCOURAGED when the assistant is designed for that product. These references help users understand context.
- **Onboarding/FAQ Descriptions**: Descriptions for onboarding or FAQ assistants that explain they "help with onboarding", "answer questions about capabilities", or "guide users" are VALID and CLEAR - do NOT flag as vague if they specify the product/platform they support.
- **Professionalism**: Professional tone and grammar?
- **Alignment**: Does it match the name and system prompt?

## Severity Guidelines
Determine the severity level for each invalid field. Use OPTIONAL for most common issues - reserve CRITICAL only for severe problems.

- **OPTIONAL**: Suggestions that improve quality but assistant is publishable
  - Examples: Name could be more specific or descriptive (not generic, but could be clearer)
  - Examples: Description lacks detail or context (understandable but could be enhanced)
  - Examples: Minor alignment issues between name and description
  - Examples: Could add more context or clarify terminology
  - Examples: Wording improvements, style suggestions, formatting issues
  - **Rule**: When in doubt between OPTIONAL and CRITICAL, choose OPTIONAL

- **CRITICAL**: Issues that make the assistant unusable or unpublishable
  - Examples: Completely missing description
  - Examples: Name is just "Bot", "Helper", or single generic word with no context
  - Examples: Description is one word like "Helps" with no explanation
  - Examples: Severe misalignment making assistant purpose completely unclear

## Output Format
{{
  "fields": [
    {{
      "field_name": "name",
      "is_valid": true/false,
      "issues": ["Issue 1", "Issue 2"],
      "recommendation": "Improved name" // Only if is_valid=false - string for name
      "severity": "critical" or "optional" // REQUIRED for invalid fields
    }},
    {{
      "field_name": "description",
      "is_valid": true/false,
      "issues": ["Issue 1"],
      "recommendation": "Improved description" // Only if is_valid=false - string for description
      "severity": "critical" or "optional" // REQUIRED for invalid fields
    }}
  ],
  "is_valid": true/false, // true only if ALL fields are valid
  "overall_reasoning": "Brief summary of overall metadata quality"
}}

## Examples

### Example 1: Valid Metadata
**Input**:
- Name: "Python Code Reviewer"
- Description: "Reviews Python code for quality, best practices, and PEP 8 compliance. Provides actionable feedback to improve your code."
- Categories: ["engineering", "productivity"]

**Output**:
{{
  "fields": [
    {{
      "field_name": "name",
      "is_valid": true,
      "issues": [],
      "recommendation": null
    }},
    {{
      "field_name": "description",
      "is_valid": true,
      "issues": [],
      "recommendation": null
    }}
  ],
  "is_valid": true,
  "overall_reasoning": "All metadata fields are clear, professional, and well-aligned with each other."
}}

### Example 2: Invalid Name and Description
**Input**:
- Name: "Bot"
- Description: "Helps"
- Categories: ["productivity"]

**Output**:
{{
  "fields": [
    {{
      "field_name": "name",
      "is_valid": false,
      "issues": ["Too generic - 'Bot' doesn't describe what the assistant does", "Too short - not descriptive enough"],
      "recommendation": "Task Assistant Bot",
      "severity": "critical"
    }},
    {{
      "field_name": "description",
      "is_valid": false,
      "issues": ["Extremely vague - doesn't explain what kind of help", "No value proposition or specific capabilities mentioned"],
      "recommendation": "A versatile assistant that helps you manage tasks, organize information, and boost productivity with smart suggestions and reminders.",
      "severity": "critical"
    }}
  ],
  "is_valid": false,
  "overall_reasoning": "Name and description are too vague and don't provide users with enough information about the assistant's capabilities."
}}

### Example 2b: Invalid Name and Description
**Input**:
- Name: "Helper Bot"
- Description: "Helps users with tasks and answers questions."
- Categories: ["productivity"]

**Output**:
{{
  "fields": [
    {{
      "field_name": "name",
      "is_valid": false,
      "issues": ["Name could be more specific about what type of helper - lacks domain context"],
      "recommendation": "Productivity Task Helper",
      "severity": "optional"
    }},
    {{
      "field_name": "description",
      "is_valid": false,
      "issues": ["Description is generic - could specify what tasks and types of questions"],
      "recommendation": "Assists with productivity tasks like scheduling, reminders, and note-taking, and answers general knowledge questions to boost your efficiency.",
      "severity": "optional"
    }}
  ],
  "is_valid": false,
  "overall_reasoning": "Name and description are understandable but could be more specific to help users understand the assistant's exact capabilities."
}}

### Example 3: Invalid Categories
**Input**:
- Name: "Research Assistant"
- Description: "Helps with competitor analysis, SWOT analysis, and persona creation for businesses."
- Categories: ["engineering", "invalid-category"]

**Output**:
{{
  "fields": [
    {{
      "field_name": "name",
      "is_valid": true,
      "issues": [],
      "recommendation": null
    }},
    {{
      "field_name": "description",
      "is_valid": true,
      "issues": [],
      "recommendation": null
    }}
  ],
  "is_valid": false,
  "overall_reasoning": "Name and description are good, but categories don't accurately reflect the business/marketing focus of the assistant."
}}

### Example 4: Valid Name with Abbreviations (Abbreviations are ACCEPTABLE)
**Input**:
- Name: "AWS CI/CD Pipeline Specialist"
- Description: "Automates CI/CD pipelines on AWS using CodePipeline, CodeBuild, and CodeDeploy. Manages containerized deployments to ECS and EKS clusters."
- Categories: ["engineering", "devops"]

**Output**:
{{
  "fields": [
    {{
      "field_name": "name",
      "is_valid": true,
      "issues": [],
      "recommendation": null
    }},
    {{
      "field_name": "description",
      "is_valid": true,
      "issues": [],
      "recommendation": null
    }}
  ],
  "is_valid": true,
  "overall_reasoning": "Name with abbreviations (AWS, CI/CD) is VALID and APPROPRIATE for technical assistants. Technical abbreviations are expected and widely understood by the target audience. Description with acronyms (ECS, EKS) is also appropriate. Do NOT flag abbreviations or technical terminology as unclear."
}}

### Example 5: Valid Technical Description (Technical Language is ACCEPTABLE)
**Input**:
- Name: "K8s DevOps Engineer"
- Description: "Manages Kubernetes clusters, deploys containerized workloads using Helm charts, configures ingress controllers, and implements GitOps workflows with ArgoCD. Monitors cluster health with Prometheus and Grafana."
- Categories: ["engineering", "devops"]

**Output**:
{{
  "fields": [
    {{
      "field_name": "name",
      "is_valid": true,
      "issues": [],
      "recommendation": null
    }},
    {{
      "field_name": "description",
      "is_valid": true,
      "issues": [],
      "recommendation": null
    }}
  ],
  "is_valid": true,
  "overall_reasoning": "Name uses common abbreviation (K8s for Kubernetes) which is VALID and widely recognized in technical communities. Technical description is APPROPRIATE for a DevOps/engineering assistant. Technical terminology (Kubernetes, Helm, ArgoCD, Prometheus, Grafana, GitOps, ingress controllers) is expected and helpful for the target technical audience. Do NOT flag abbreviations or technical language as unclear."
}}

### Example 6: Valid Description with Multiple Unexplained Abbreviations (MUST PASS)
**Input**:
- Name: "BriAnnA"
- Description: "Business Analyst Assistant - expert to work with Jira. Used for creating/getting/managing Jira tickets in EPM-CDME project (Epics, Stories, Tasks, and Bugs). Main role is to analyze requirements from the request, clarify additional questions if necessary, generate requirements with the description structure defined in the prompt and additional details from the request, and create tickets in EPM-CDME project Jira. The Assistant uses Generic Jira tool for Jira tickets creation."
- Categories: ["business", "productivity"]

**Output**:
{{
  "fields": [
    {{
      "field_name": "name",
      "is_valid": true,
      "issues": [],
      "recommendation": null
    }},
    {{
      "field_name": "description",
      "is_valid": true,
      "issues": [],
      "recommendation": null
    }}
  ],
  "is_valid": true,
  "overall_reasoning": "Description is VALID and comprehensive. Contains multiple abbreviations (Jira, EPM-CDME, Epics, Stories, Tasks, Bugs) without explanation - this is ACCEPTABLE. Technical terminology and project-specific names do NOT need to be spelled out. The description clearly explains the assistant's purpose, capabilities, and workflow. Do NOT flag unexplained abbreviations or project names as issues."
}}

### Example 7: Misalignment Between Fields
**Input**:
- Name: "Email Assistant"
- Description: "Analyzes code repositories and provides architectural insights."
- Categories: ["communication"]

**Output**:
{{
  "fields": [
    {{
      "field_name": "name",
      "is_valid": false,
      "issues": ["Name suggests email functionality but description is about code analysis - misalignment"],
      "recommendation": "Code Architecture Analyzer"
    }},
    {{
      "field_name": "description",
      "is_valid": false,
      "issues": ["Description doesn't match name (email vs code)", "Misalignment with stated category (communication vs engineering)"],
      "recommendation": "Helps you analyze code repositories, identify architectural patterns, and provide insights for better software design and structure."
    }}
  ],
  "is_valid": false,
  "overall_reasoning": "Severe misalignment between name, description, and categories. The assistant's actual purpose is unclear to users."
}}

### Example 8: Valid Onboarding/FAQ Assistant with Product Name (MUST PASS)
**Input**:
- Name: "AI/Run FAQ"
- Description: "This is a smart CodeMie assistant which can help you with the onboarding process. CodeMie can answer all your questions about capabilities, usage, and more."
- System Prompt: "You are a helpful onboarding assistant for CodeMie platform..."

**Output**:
{{
  "fields": [
    {{
      "field_name": "name",
      "is_valid": true,
      "issues": [],
      "recommendation": null
    }},
    {{
      "field_name": "description",
      "is_valid": true,
      "issues": [],
      "recommendation": null
    }}
  ],
  "is_valid": true,
  "overall_reasoning": "Name and description are VALID for an onboarding/FAQ assistant. Product references (CodeMie, AI/Run) provide necessary context. Description clearly states the assistant helps with onboarding and answers questions about capabilities and usage - this is specific and appropriate for an FAQ assistant. Do NOT flag as vague when the purpose is clearly stated as helping with onboarding/FAQ for a specific product."
}}

### Example 9: Valid Onboarding Assistant (MUST PASS)
**Input**:
- Name: "CodeMie Onboarding Guide"
- Description: "Guides new users through CodeMie platform setup, explains key features, and answers common questions about getting started."
- System Prompt: "You help users get started with CodeMie..."

**Output**:
{{
  "fields": [
    {{
      "field_name": "name",
      "is_valid": true,
      "issues": [],
      "recommendation": null
    }},
    {{
      "field_name": "description",
      "is_valid": true,
      "issues": [],
      "recommendation": null
    }}
  ],
  "is_valid": true,
  "overall_reasoning": "Excellent metadata for an onboarding assistant. Name includes product context (CodeMie) which is helpful. Description clearly explains the onboarding role, mentioning setup, features, and common questions - this is appropriately specific for a help/onboarding assistant."
}}

---
Analyze the input metadata above and provide your assessment. Ensure all fields are aligned with each other and the system prompt.
"""
)

# =============================================================================
# SYSTEM PROMPT VALIDATION PROMPTS
# =============================================================================

SYSTEM_PROMPT_VALIDATION_TEMPLATE = PromptTemplate.from_template(
    """## Task
Evaluate the system prompt for quality, structure, and marketplace readiness.

## Context
You are validating an AI assistant for publication in a marketplace. The assistant must be well-defined, professional, and useful to end users.

## Input
**Name**: {name}
**Description**: {description}
**Categories**: {categories}
**Conversation Starters**: {conversation_starters}
**System Prompt**: {system_prompt}

## Evaluation Guidelines

Assess the system prompt quality based on:

1. **Completeness**: Does it fully define the assistant's role, capabilities, and behavior?
2. **Structure**: Is it well-organized with clear sections (not necessarily rigid format)?
3. **Clarity**: Can users understand what the assistant does and how it behaves?
4. **Alignment**: Does it match the name, description, and categories?
5. **Professionalism**: Is it suitable for a professional marketplace?
6. **Actionability**: Does it provide clear instructions for the AI to follow?
7. **Constraints**: Does it define boundaries and limitations appropriately?

**Important**: Focus on quality and usability, not rigid formatting rules. Different assistants may need different structures.

## CRITICAL Rules - Variable References and Keyword Duplication

### 1. Variable References (Template Variables)
- **Variable syntax like {{ds_name}}, {{project_name}}, {{current_user}} is ACCEPTABLE and VALID**
- These are Jinja2-style template variables that get replaced at runtime
- Do NOT flag these as errors, placeholders, or incomplete content
- Do NOT suggest removing or replacing variable references
- Examples of VALID variable references:
  * `{{ds_name}}` - datasource name
  * `{{project_name}}` - project name
  * `{{current_user}}` - current user
  * `{{context_name}}` - context name
  * `{{any_custom_variable}}` - any custom variable

### 2. Keyword Duplication
- **Keyword duplication and repetition in system prompts is ACCEPTABLE and VALID**
- Repeating important keywords, concepts, or instructions for emphasis is a common and effective prompting technique
- Do NOT flag repeated keywords, phrases, or concepts as redundant or issues
- Examples of ACCEPTABLE repetition:
  * Repeating tool names multiple times for clarity
  * Repeating important constraints or rules
  * Repeating key concepts for emphasis
  * Using similar phrasing in different sections for consistency

## Severity Guidelines
Determine the severity level for system prompt issues. Use OPTIONAL for most issues - reserve CRITICAL only for severe problems.

- **OPTIONAL**: Suggestions that improve quality but prompt is functional
  - Examples: Prompt could be more detailed or structured (basic functionality is clear)
  - Examples: Could add more examples, constraints, or guidelines
  - Examples: Missing some sections but core role is defined
  - Examples: Could clarify behavior or add more actionable instructions
  - Examples: Minor structural improvements, formatting, or style suggestions
  - **Rule**: When in doubt between OPTIONAL and CRITICAL, choose OPTIONAL

- **CRITICAL**: Issues that make the assistant unusable or dangerous
  - Examples: System prompt is completely missing or empty
  - Examples: Prompt is extremely vague (e.g., "You help users") with no other context
  - Examples: Prompt contradicts the assistant's stated purpose entirely
  - Examples: Prompt contains harmful, inappropriate, or unprofessional instructions

## Output Format
{{
  "is_valid": true/false,
  "issues": ["Specific issue 1", "Specific issue 2"],
  "recommendation": "Improved prompt text..." // Only if is_valid=false
  "severity": "critical" or "optional" // REQUIRED if is_valid=false
}}

## Examples

### Example 1: Valid Prompt (Well-Structured)
**Input**:
- Name: "Python Code Reviewer"
- Description: "Reviews Python code for quality"
- System Prompt: "## Instructions\\nYou are a Python code review expert...\\n## Steps\\n1. Analyze syntax...\\n## Constraints\\n- Follow PEP8..."

**Output**:
{{
  "is_valid": true,
  "issues": [],
  "recommendation": null
}}

### Example 2: Valid Prompt with Variable References (Variables are ACCEPTABLE)
**Input**:
- Name: "Business Analyst Assistant"
- Description: "Creates and manages Jira tickets"
- System Prompt: "You are a Business Analyst Assistant. You work with {{project_name}} project. Create Jira tickets in {{project_name}} for user {{current_user}}. Always reference {{ds_name}} datasource when searching for information. Use {{context_name}} for additional context."

**Output**:
{{
  "is_valid": true,
  "issues": [],
  "recommendation": null
}}

**Reasoning**: Variable references like {{project_name}}, {{current_user}}, {{ds_name}}, {{context_name}} are VALID template variables that get replaced at runtime. Do NOT flag these as placeholders or errors.

### Example 3: Valid Prompt with Keyword Repetition (Repetition is ACCEPTABLE)
**Input**:
- Name: "Cloud Migration Assistant"
- Description: "Helps migrate to AWS"
- System Prompt: "You are an AWS migration expert. Focus on AWS services. Use AWS best practices. When discussing migration, always mention AWS-specific tools. AWS is your primary focus. AWS CloudFormation is essential. AWS is the target platform."

**Output**:
{{
  "is_valid": true,
  "issues": [],
  "recommendation": null
}}

**Reasoning**: Repeating "AWS" multiple times for emphasis and clarity is VALID prompting technique. Do NOT flag keyword repetition as redundant.

### Example 4: Invalid Prompt (Too Vague)
**Input**:
- Name: "Helper Bot"
- Description: "Helps with tasks"
- System Prompt: "You help users"

**Output**:
{{
  "is_valid": false,
  "issues": ["Extremely vague - doesn't define what tasks or how to help", "No clear role or expertise defined", "Missing behavioral guidelines"],
  "recommendation": "You are a versatile assistant designed to help users with general tasks. Your capabilities include: providing information, answering questions, offering suggestions, and helping organize thoughts. Approach each request by: 1) Understanding the user's goal, 2) Asking clarifying questions if needed, 3) Providing clear, actionable responses. Maintain a helpful, professional tone and acknowledge when tasks are outside your expertise.",
  "severity": "critical"
}}

---
Analyze the input above and provide your assessment.
"""
)

# =============================================================================
# TOOLS VALIDATION PROMPTS (RAG-BASED)
# =============================================================================

TOOLS_DECISION_TEMPLATE = PromptTemplate.from_template(
    """## Clarification Analysis (CRITICAL - Read This First!)

{clarification_summary}

**HOW TO USE CLARIFICATIONS:**
- **High Confidence** clarifications: MUST follow strictly (evidence-based from specification)
- **Medium Confidence** clarifications: Should follow unless you find strong counter-evidence
- **Low Confidence** clarifications: Use as hints only - apply strict explicit capability analysis

**Example Usage:**
- Clarification: "AWS only (high confidence)" → Include ONLY AWS tools, exclude Azure/GCP/Kubernetes
- Clarification: "Web research needed (high confidence)" → Include google_search_tool_json, web_scrapper, tavily_search_results_json
- Clarification: "DevOps NOT needed (medium confidence)" → Exclude Azure DevOps unless you find explicit evidence
- Clarification: "Cloud provider unclear (low confidence)" → Do NOT assume any cloud tools - apply strict validation

---

## Task
Make FINAL decision on which tools to include/exclude for this assistant based on EXPLICIT capabilities mentioned in metadata AND clarification analysis above.

## Context
You are making the FINAL decision on tool selection. RAG provided candidate tools, but YOU decide which ones are actually relevant based on:
1. The clarification analysis above (PRIMARY - use high-confidence decisions)
2. The assistant's EXPLICITLY stated capabilities in metadata

## Assistant Metadata (Analyze ALL fields)
**Name**: {assistant_name}
**Description**: {assistant_description}
**Categories**: {assistant_categories}
**Conversation Starters**: {conversation_starters}

**System Prompt** (FULL - analyze ALL capabilities mentioned):
{system_prompt_full}

## Current Tool Configuration
**Existing Tools**: {existing_tools}

## Configured Knowledge Base Context (IMPORTANT - provides semantic search capabilities)
{configured_context}

**🚨 CRITICAL - KB/CONTEXT PROVIDES SEARCH - DO NOT ADD REDUNDANT TOOLS 🚨**:
- **Knowledge bases (KB) and datasources provide semantic search** over indexed documentation/code files through the RAG system
- **Tool naming pattern**:
  * search_code_repo_v2 = generic code search (searches all repos)
  * search_code_repo_[context_name] = context-specific variants (e.g., search_code_repo_my_app_repo)
  * **NEVER hallucinate** tool names - ONLY use exact names from available_tools_summary
- **If assistant has ANY context configured** (shown above) **AND system prompt mentions searching/querying/reading those datasources**:
  * **DO NOT include search_code_repo_v2 OR any search_code_repo_[context_name] variants** - they are REDUNDANT
  * The KB/context already provides semantic search capabilities
  * Example: Context = "company-docs" + System prompt = "search documentation" → **NO search_code_repo_v2 or search_code_repo_company_docs needed**
- **Only include search_code_repo_v2 (or context-specific variants) if**:
  * "code", "repository", "codebase" is explicitly mentioned AND
  * Assistant has NO context configured OR context exists but system prompt does NOT mention searching it
  * **CRITICAL**: ONLY use exact tool names from available_tools_summary - do NOT create tool name variants

## RAG Candidate Tools (Suggestions from semantic search)
{rag_candidate_tools}

## Decision Guidelines

### 1. EXPLICIT Capability Analysis (CRITICAL)
Analyze ALL assistant metadata fields and identify ONLY capabilities that are **EXPLICITLY mentioned**:
- **Name**: What domain/function is stated? (e.g., "Image Generator" → image generation capability)
- **Description**: What specific tasks/technologies are mentioned? (e.g., "creates visualizations" → visualization capability)
- **Conversation Starters**: What concrete actions are demonstrated? (e.g., "Generate a chart" → visualization capability)
- **System Prompt**: What capabilities, platforms, services are stated? (e.g., "search the web" → web search capability)

**IMPORTANT**: Capabilities can be mentioned in ANY of these fields (Name, Description, Conversation Starters, OR System Prompt). Check ALL fields when determining tool relevance.

**DO NOT assume** related capabilities. Examples:
- "Azure migration" (in name/description) → **ONLY Azure tools** (NOT AWS, GCP, Kubernetes, Azure DevOps unless explicitly mentioned)
- "AWS DevOps" (in name/description) → **AWS + DevOps tools** (both explicitly mentioned)
- "Cloud migration" (generic, no specific cloud) → **NO specific cloud tools** (too vague)
- "Multi-cloud Azure and AWS" (in description) → **Azure + AWS tools** (both explicitly mentioned)
- "Data Visualizer" (in name) → **Visualization tools** (code_executor or code_interpreter for charts/graphs)

### 2. Tool Selection Rules (BE EXTREMELY STRICT)

**CRITICAL RULE - Existing Tools Evaluation**:
🔒 **Evaluate ALL tools** (both RAG candidates AND existing tools) **using the same strict relevance criteria**
🔒 **For existing tools**: Include them in `tools_to_include` ONLY if they align with the assistant's explicitly stated capabilities
🔒 **Exclude existing tools if**:
   - They are for a different domain/function than stated in metadata (e.g., image generation tool for cloud migration assistant)
   - The capability they provide is NOT explicitly mentioned in metadata
   - They are explicitly forbidden in system prompt
🔒 **DO NOT use lenient "no evidence of harm" logic** - require positive evidence of relevance
🔒 **Example**: Cloud Migration Advisor (AWS) with `generate_image_tool` → Exclude it (image generation not mentioned in migration capabilities)

**Include a tool ONLY if**:
✅ The tool directly enables a capability EXPLICITLY mentioned in metadata
✅ The technology/platform is specifically named (e.g., "Azure", "AWS", "email", "web search")
✅ The tool is essential for the assistant's stated purpose
✅ **For existing tools**: Same rule applies - must be relevant to stated capabilities

**Exclude a tool if**:
❌ The capability is NOT explicitly mentioned (even if "related")
❌ The technology/platform is NOT named in metadata
❌ The tool is for a different domain/function than the assistant's purpose
❌ **For existing tools**: Apply same strict criteria - no lenience for irrelevant tools

### 3. Missing Tools Check
- Identify if essential tools are MISSING from RAG candidates
- **Special case - Research**: If assistant mentions "research", "web search", "internet search" capabilities, MUST include ALL research tools: google_search_tool_json, web_scrapper, tavily_search_results_json, wikipedia
- **Special case - Visualization**: If assistant mentions "visualization", "charts", "graphs", "plots" but NO code execution tool, add code_executor or code_interpreter
- If missing, add them to `tools_to_include` and set `should_retry=false` (you found them)
- Only set `should_retry=true` if you can't find essential tools in available tools list

## Output Format
{{
  "tools_to_include": ["tool1", "tool2"],  // Final approved list
  "tools_to_exclude": ["tool3", "tool4"],  // Rejected from RAG candidates
  "reasoning": "Analyzed metadata: [capabilities found]. Including [tools] for [reasons]. Excluding [tools] because [not mentioned]. **If you need excluded tools, explicitly describe their use cases in the system prompt.**"
}}

**IMPORTANT - Tool Deletion Reasoning**:
When excluding tools (especially from Existing Tools), your reasoning MUST explain:
1. WHY the tool is being removed (capability not mentioned, irrelevant, etc.)
2. HOW the user can keep it (e.g., "If you need web search, add 'research' or 'search the internet' to the system prompt")
3. This helps users understand what to fix in their assistant configuration
**CRITICAL RULE**: Evaluate ALL tools (existing and RAG candidates) using the same strict relevance criteria - exclude tools that don't align with explicitly stated capabilities!

---
Analyze the assistant metadata carefully. Only include tools for EXPLICITLY mentioned capabilities. Reject everything else.

**Special attention - MANDATORY RULES**:
1. **Research Assistants (CRITICAL)**:
   - **If assistant ALREADY HAS a research tool** (google_search_tool_json, web_scrapper, tavily_search_results_json, or wikipedia):
     * **DO NOT suggest other research tool variants**
     * One research tool is sufficient - user has chosen their preferred method
     * Keep the existing research tool if relevant, exclude RAG candidates for other research tools
   - **If assistant has NO research tool yet** AND "research", "web search", "internet search", "competitive analysis", "find information" is mentioned:
     * You MUST include ALL research toolkit tools:
       - google_search_tool_json
       - web_scrapper
       - tavily_search_results_json
       - wikipedia
     * **Reason**: Research assistants need comprehensive search capabilities - provide all options for user to choose

2. **Visualization**: If visualization/charts/graphs/plots are mentioned, ALWAYS include code_executor or code_interpreter.

3. **KB Context vs Code Search**: If KB context is configured AND system prompt mentions searching/reading those documents, then search_code_repo_v2 may NOT be needed (KB provides search).
"""
)

# =============================================================================
# CONTEXT VALIDATION PROMPTS
# =============================================================================

CONTEXT_VALIDATION_TEMPLATE = PromptTemplate.from_template(
    """## Clarification Analysis (CRITICAL - Use This First!)

{clarification_kb_summary}

**HOW TO USE CLARIFICATIONS:**
- **High Confidence** clarifications: MUST follow strictly (evidence-based from specification)
- **Medium Confidence** clarifications: Should follow unless you find strong counter-evidence
- **Low Confidence** clarifications: Use as hints only - apply strict validation
- Pay special attention to knowledge base/context/datasource mentions in clarifications

**Example Usage:**
- Clarification: "Uses company documentation KB (high confidence)" → Context is REQUIRED
- Clarification: "No specific knowledge base mentioned (medium confidence)" → Validate against system prompt
- Clarification: "May need documentation access (low confidence)" → Apply strict validation rules

---

## Task
Validate the assistant's context (datasources/knowledge bases) configuration based on:
1. **PRIMARY**: Clarification analysis above (if available with high/medium confidence)
   - High confidence clarifications = Evidence-based, MUST follow strictly
   - Medium confidence = Strong indicator, use unless contradicted
   - Low confidence = Hints only
2. **SECONDARY**: System prompt for:
   - Exact datasource names mentioned (e.g., "search my-app-repo" → verify "my-app-repo" is configured)
   - Fallback validation when clarifications are unavailable or low confidence
   - Direct quotes to support validation decisions
3. **CRITICAL**: Validate if configured context is the CORRECT data source for the stated purpose

**Decision Priority**: Clarification (high/medium confidence) > System Prompt (exact names/explicit statements) > Strict validation rules

## Context
You are validating whether the assistant has the RIGHT context configured. Context includes code repositories, documentation, knowledge bases, and other datasources that the assistant needs to function properly.

## Assistant Profile
**Name**: {assistant_name}
**Description**: {assistant_description}
**System Prompt** (FULL - analyze for datasource requirements): {system_prompt}

## Current Context Configuration
**Configured Context**: {configured_context}

## Available Context for User
{available_context}

## Validation Guidelines

### 1. Invalid Context Check (CRITICAL)
- **Are all configured context names valid?**
- Check if each configured context exists in the available context list
- Flag any context that doesn't exist as `invalid_context`

### 2. Correct Data Source Validation (CRITICAL - NEW REQUIREMENT)
**🚨 STRICT QUESTION: Is the configured KB/context the RIGHT data source for this assistant's purpose? 🚨**

Analyze if the configured context actually matches what the assistant needs:

**Check Context Type vs. Assistant Purpose:**
- **Code Analysis Assistant** (e.g., "code review", "repository analysis", "codebase search"):
  * ✅ REQUIRES: code_repository type context
  * ❌ WRONG: knowledge_base_file, confluence, google_docs (documentation contexts)
  * Example: "Code Review Assistant" with "company-policies.pdf" KB → Mark as `invalid_context` (wrong type)

- **Documentation/Knowledge Assistant** (e.g., "policy helper", "documentation search", "FAQ bot"):
  * ✅ REQUIRES: knowledge_base_file, confluence, google_docs type context
  * ❌ WRONG: code_repository (code contexts)
  * Example: "Policy Assistant" with "my-app-repo" code context → Mark as `invalid_context` (wrong type)

- **Multi-Purpose Assistant** (mentions BOTH code AND documentation):
  * ✅ REQUIRES: BOTH types (code_repository for code + knowledge_base for docs)
  * ❌ WRONG: Only one type when both are needed

**Check Context Content vs. System Prompt:**
- Does the context description/name match what the system prompt describes?
- If system prompt says "search AWS documentation" but context is "azure-policies.pdf" → `invalid_context`
- If system prompt says "analyze my-app-repo code" but context is "other-app-repo" → `invalid_context`
- Use clarification analysis to identify mismatches (high-confidence evidence)

**Key Rules:**
- Context exists in database (passes validation check) BUT is the WRONG type/content for the purpose → Mark as `invalid_context`
- Be STRICT: Wrong data source type = invalid, even if it exists
- Clarification analysis should guide you on what type of context is needed

### 3. System Prompt Context Requirements (SECONDARY VALIDATION)
**IMPORTANT**: Context is OPTIONAL for most assistants. Only flag missing context if EXPLICITLY required by the system prompt.

Analyze the system prompt for explicit context needs:
- Does it mention specific repositories, documentation, or knowledge bases by name?
- Does it require access to "company docs", "internal knowledge base", "codebase", specific data sources?
- Does it explicitly state it needs to "search" or "query" or "read from" datasources?

**Knowledge-Based Assistants**:
- **ONLY if** system prompt **EXPLICITLY mentions** "knowledge base", "documentation", "company policies", "internal docs", "company knowledge", etc.
- Require: Relevant documentation context (knowledge_base_file, confluence, google_docs)
- Flag as missing if no documentation context is configured
- **CRITICAL**: If knowledge base context is configured BUT system prompt does NOT mention knowledge base/documentation, mark as **unnecessary**

**General Rule**:
- Context is OPTIONAL unless system prompt EXPLICITLY mentions needing specific datasources
- When in doubt, mark as valid (don't be overly strict)
- Focus on what the system prompt SAYS, not what tools might theoretically benefit from

### 4. Missing Context Detection
**CRITICAL**: Only recommend context from the "Available Context for User" list. NEVER suggest context names that don't exist.

- Does the system prompt EXPLICITLY mention needing specific datasources that aren't configured?
- **IMPORTANT**: Look at the available context list and recommend ONLY from those exact names
- If no suitable context is available, set `missing_context` to empty array `[]`
- DO NOT create generic names like "code repository" or "documentation context"
- DO NOT hallucinate context names

### 5. Unnecessary Context Detection (CRITICAL - Check Description Quality First)
**IMPORTANT**: Before marking context as "unnecessary", evaluate the datasource description quality:

- **If description is EXTREMELY UNCLEAR/VAGUE** (completely meaningless or identical to the name):
  - **Extremely Vague Examples** (mark for update):
    * Description exactly same as name"
    * Single word"
    * Placeholder text: "description x", "TODO", "temp"
  - **BE LENIENT**: If description provides ANY hint about what it is, accept it

- **If description is WELL-DESCRIBED** (clear, specific, you understand what it contains):
  - Examples of clear: "AWS migration best practices documentation", "Customer support knowledge base", "Python codebase"
  - Check if it aligns with assistant's stated purpose

**Key Rule**: Be lenient with descriptions. Only flag as needing update if they're completely meaningless (same as name or pure placeholder). Brief descriptions are acceptable if they give any indication of content/purpose.

## Decision Logic (BE LENIENT - Default to VALID)

**VALID** (is_valid=true) when:
- All configured context exists (no invalid_context)
- Context is appropriate for the assistant's purpose (no unnecessary_context)
- **Default to VALID** - context is optional for most assistants

**INVALID** (is_valid=false) when:
- **Invalid context exists** - configured context names that don't exist in database (invalid_context)
- **Unnecessary context** - context is configured BUT system prompt does NOT mention needing datasources/knowledge base (unnecessary_context)
  - Example: Knowledge base context configured but system prompt makes no mention of searching docs or using knowledge base
  - Example: Code repository context configured but system prompt doesn't mention analyzing code or repositories
- **RARELY**: System prompt EXPLICITLY requires specific datasources that exist but aren't configured (missing_context)

**Key Rules**:
- ✅ Missing context is OK - most tools work without context
- ✅ Empty context is OK - most assistants don't need datasources
- ✅ When in doubt, mark as VALID
- ❌ Only mark invalid if there's clear evidence of a problem

## Output Format
{{
  "is_valid": true/false,
  "context_to_update": ["context-name-1", "context-name-2"],  // All context that needs UPDATE/attention (invalid/wrong type/wrong content/unnecessary)
  "available_context": ["all", "available", "context", "names"],  // From input (do not modify)
  "reasoning": "Detailed explanation of context validation results"
}}

**CRITICAL RULES for context_to_update**:
- ✅ Include context that doesn't exist in database (invalid - needs to be removed or datasource created)
- ✅ Include context with WRONG data source type (e.g., code repo for documentation assistant - needs update)
- ✅ Include context with WRONG data source content (e.g., AWS docs for Azure assistant - needs update)
- ✅ Include context that's unnecessary (not mentioned in system prompt - needs removal or system prompt update)
- ✅ Include context with unclear/vague descriptions (cannot determine if needed - needs description update)
- ✅ Use exact names from "Configured Context" list
- ❌ Do NOT include context that is valid and matches the assistant's purpose

---
Analyze the assistant's context configuration and provide your assessment based on system prompt requirements.
**REMEMBER**:
- Use `context_to_update` for ALL contexts that need attention (invalid/wrong type/wrong content/unnecessary/unclear)
- Mark context for update if:
  * It doesn't exist in the database
  * Wrong data source TYPE (code repo for docs assistant, docs for code assistant)
  * Wrong data source CONTENT (AWS docs for Azure assistant)
  * Unnecessary (not mentioned in system prompt)
  * Unclear description (cannot determine if needed)
- Empty `context_to_update` array means all configured contexts are valid
"""
)

# =============================================================================
# FRIENDLY MESSAGE GENERATION PROMPT (Used by MakeDecisionNode)
# =============================================================================


def _format_field_recommendations(field_recommendations: list) -> str:
    """Format field recommendations for prompt.

    Args:
        field_recommendations: List of FieldRecommendation objects

    Returns:
        Formatted string for field recommendations section
    """
    if not field_recommendations:
        return ""

    parts = ["\n## Field Recommendations"]
    for i, field_rec in enumerate(field_recommendations):
        parts.append(f"\n### Field {i + 1}: {field_rec.name}")
        parts.append(f"- Action: {field_rec.action.value}")
        if field_rec.recommended:
            if isinstance(field_rec.recommended, list):
                parts.append(f"- Recommended value: {', '.join(field_rec.recommended)}")
            else:
                rec_str = str(field_rec.recommended)
                if len(rec_str) > 200:
                    parts.append(f"- Recommended value: {rec_str[:200]}...")
                else:
                    parts.append(f"- Recommended value: {rec_str}")
        parts.append(f"- Technical reason: {field_rec.reason}")

    return "\n".join(parts)


def _format_toolkit_recommendations(toolkit_recommendations: list) -> str:
    """Format toolkit recommendations for prompt.

    Args:
        toolkit_recommendations: List of ToolkitRecommendation objects

    Returns:
        Formatted string for toolkit recommendations section
    """
    if not toolkit_recommendations:
        return ""

    parts = ["\n## Tool Recommendations"]
    for toolkit_rec in toolkit_recommendations:
        parts.append(f"\n### Toolkit: {toolkit_rec.toolkit}")
        for i, tool_rec in enumerate(toolkit_rec.tools):
            parts.append(f"  - Tool {i + 1}: $[{tool_rec.name}]$")
            parts.append(f"    - Action: {tool_rec.action.value}")
            parts.append(f"    - Technical reason: {tool_rec.reason}")

    return "\n".join(parts)


def _format_context_recommendations(context_recommendations: list) -> str:
    """Format context recommendations for prompt.

    Args:
        context_recommendations: List of ContextRecommendation objects

    Returns:
        Formatted string for context recommendations section
    """
    if not context_recommendations:
        return ""

    parts = ["\n## Knowledge Base Context Recommendations"]
    for i, ctx_rec in enumerate(context_recommendations):
        parts.append(f"\n### Context {i + 1}: $[{ctx_rec.name}]$")
        parts.append(f"- Action: {ctx_rec.action.value}")
        parts.append(f"- Technical reason: {ctx_rec.reason}")

    return "\n".join(parts)


FRIENDLY_MESSAGE_GENERATION_TEMPLATE = """Generate clear, user-friendly explanatory messages for the following assistant validation recommendations.

## Assistant Information
- Name: {assistant_name}
- Description: {assistant_description}
- Categories: {assistant_categories}

## Validation Results Context (Use for enriching messages)
{validation_results_context}

{field_recommendations_section}{toolkit_recommendations_section}{context_recommendations_section}

## Your Task
For EACH recommendation, generate a SHORT, SIMPLE, CLEAR message (1 sentence) that:
1. States the specific action needed (add/remove/update)
2. Briefly explains why (in simple terms)
3. **MUST be personalized for this specific assistant's purpose** (use assistant name, description, and categories to tailor the message)

**CRITICAL - Message Personalization**:
- Messages MUST be tailored to THIS specific assistant's purpose, not generic
- Reference the assistant's specific domain/purpose when relevant (e.g., "for Jira ticket management", "for your test case generation workflow")
- Use the assistant's name, description, and categories to provide context-specific guidance
- Example: Instead of "Add tool for managing tickets" → "Add essential tool for creating and tracking Jira tickets in your project management workflow"

**IMPORTANT - Using Technical Reason as Context**:
- Each recommendation has a "Technical reason" field provided for your INTERNAL CONTEXT ONLY
- Use the technical reason to UNDERSTAND the issue and generate better messages
- Extract ALL key details and specific problems mentioned in the technical reason
- Transform the technical reason into simple, user-friendly language while preserving the SPECIFIC ISSUES mentioned
- Your message should be ENRICHED by the technical reason but NOT repeat it verbatim
- **CRITICAL**: If the technical reason mentions specific problematic content (like "Delete jira" or other inappropriate instructions), your message MUST reference this specific problem in user-friendly terms

**ENRICHMENT FROM VALIDATION REASONING**:
- Validation results include reasoning/explanation fields that explain the validation logic
- **Available reasoning sources by validation type**:
  - **Metadata (name, description, categories)**: Use `overall_reasoning` from MetadataValidationResult
  - **System Prompt**: Use `issues` list from ValidationResult (no reasoning field - extract key problems from issues)
  - **Tools**: Use `reasoning` from ToolsValidationResult
  - **Context/Datasources**: Use `reasoning` from ContextValidationResult
- Use this reasoning to understand the full context of WHY recommendations were made
- The reasoning may include:
  - **Clarification insights**: If clarification questions were used, reasoning includes those insights
  - **Tool matching analysis**: Why specific tools match or don't match the assistant's purpose
  - **Context analysis**: Why datasources are needed, unnecessary, or have issues
  - **Metadata analysis**: Why name/description/categories need changes
- Transform this reasoning into user-friendly, actionable messages
- Example: If reasoning says "Based on clarification: Assistant needs GitHub integration but has Jira tools", your message should reference this mismatch
- **CRITICAL**: If you cannot find clear, useful enrichment from the validation reasoning, generate the message based ONLY on the technical reason field
- **Priority order for each recommendation**:
  1. Use specific issues from "Technical reason" if clear and specific
  2. If technical reason is vague/unclear, use insights from validation reasoning field (overall_reasoning for metadata, reasoning for tools/context, issues for system prompt)
  3. Combine both when they provide complementary information
  4. If NEITHER provides clear enrichment, create a generic but helpful message based on the action type (add/remove/update) and field/tool/context name

🚨 CRITICAL RULES - MUST FOLLOW 🚨
- Keep messages SHORT and SIMPLE. ONE sentence only. No additional formatting or explanation.
- ⛔ NEVER INCLUDE TOOL NAMES in tool messages
  * Tool names are marked with $[tool_name]$ in the recommendations below
  * Examples: $[generic_jira_tool]$, $[wikipedia]$, $[web_scrapper]$
  * DO NOT include these marked names in your messages
- ⛔ NEVER INCLUDE CONTEXT/DATASOURCE NAMES in context messages
  * Context names are marked with $[context_name]$ in the recommendations below
  * DO NOT include these marked names in your messages
- The tool/context name is already shown in the UI - your message should ONLY explain WHY the action is needed
- ✅ CORRECT: "Add the essential tool for managing test cases in project workflows."
- ✅ "Tool could be removed as it serves no purpose for Jira ticket operations."

SPECIAL RULES:
- For 'name' field: Explain it should clearly describe the assistant's purpose
- For 'description' field: Explain it must be clear, and describe the assistant's purpose for marketplace publishing - it shows as the title/summary to help users understand what this assistant does
- For 'system_prompt' field: **MUST mention specific problems from technical reason** (e.g., if technical reason says "Delete jira" is inappropriate, your message must mention this specific issue). Be specific about what's wrong, not generic.
- For tools to ADD: Explain WHY it's needed WITHOUT mentioning the tool name (name is already in UI). Focus on the capability/purpose.
- For tools to REMOVE: Use soft, suggestive language WITHOUT mentioning the tool name. Say "Could be removed" or "May not be needed". Explain gently what capability/purpose it doesn't serve (e.g., "Could be removed as it serves no purpose for Jira ticket operations")
- For context (datasources - CRITICAL STRICT RULES): Evaluate the datasource description quality from the technical reason:
  * If description is UNCLEAR/UNHELPFUL/VAGUE (like 'some data', 'second', 'description x', or cannot understand what it contains):
    - MUST tell user to UPDATE the datasource description to be clear and helpful
    - CANNOT suggest delete (we don't know what it is)
    - Also remind: system prompt must explain this exact datasource name, why it exists, and its purpose
  * If description is WELL-DESCRIBED (clear, specific, we understand what it contains) BUT not mentioned in system prompt:
    - Acknowledge it's well-described
    - Suggest: Either DELETE it (since not required) OR update system prompt to explain why this datasource exists and its purpose in the assistant
  * Key principle: Only suggest DELETE for well-described datasources. Unclear datasources need description updates first.

Examples of Using Technical Reason as Context:

**Example 1: Field Recommendation (Generic Issue)**
Technical reason: "Name is too generic - 'Bot' doesn't describe what the assistant does; Too short - not descriptive enough"
❌ BAD: "Name is too generic - 'Bot' doesn't describe what the assistant does" (copying technical reason)
✅ GOOD: "Choose a descriptive name that clearly indicates the assistant's specific purpose and capabilities."

**Example 1b: Field Recommendation (System Prompt with Specific Problem)**
Technical reason: "System prompt is inappropriate and unclear: 'Delete jira' is not a valid or professional instruction for a Jira ticket management assistant."
❌ BAD: "Update the system prompt to clearly define the assistant's role" (too generic - misses the specific problem)
❌ BAD: "System prompt is inappropriate and unclear: 'Delete jira' is not a valid instruction" (copying technical reason)
✅ GOOD: "Remove inappropriate instructions like 'Delete jira' from the system prompt and provide clear, professional guidance for Jira ticket management."

**Example 2: Tool Addition (Personalized)**
Technical reason: "Essential tool for assistant's stated capabilities"
Context: Assistant name = "Jira Ticket Manager", Categories = ["project-management"]
❌ BAD: "Essential tool for assistant's stated capabilities" (copying technical reason)
❌ BAD: "Essential for creating and managing project tickets" (not personalized, missing "Add")
❌ BAD: "Add the essential tool for creating and managing project tickets in your workflow." (generic - not personalized)
✅ GOOD: "Add the essential tool for creating and managing Jira tickets in your project management workflow." (personalized to assistant's purpose)

**Example 3: Tool Deletion (Personalized)**
Technical reason: "Suggests the wikipedia could be deleted as it may not be necessary"
Context: Assistant name = "Test Case Generator", Categories = ["testing"], Description = "Generates test cases for software projects"
❌ BAD: "Could be deleted as it may not be necessary" (copying technical reason)
❌ BAD: "Tool could be removed since test case generation doesn't require general knowledge lookup." (not specific to this assistant)
✅ GOOD: "Tool could be removed as general knowledge lookup isn't needed for generating software test cases in your testing workflow." (personalized to assistant's specific purpose)

**Example 4: Enriched with Clarification Insights**
Technical reason: "Based on clarification: Assistant is for GitHub issue management but configured with Jira tools instead of GitHub tools"
Context: Assistant name = "GitHub Issue Manager"
❌ BAD: "Tool doesn't match assistant purpose" (misses clarification insight)
✅ GOOD: "Tool could be removed as your GitHub issue management assistant needs GitHub integration tools, not Jira tools." (enriched with clarification reasoning)

**Example 4: Context Update**
Technical reason: "Context 'second' needs attention. Description is unclear (identical to name). Update description to explain what it contains."
❌ BAD: "Context needs attention. Description is unclear" (copying technical reason)
✅ GOOD: "Update the datasource description to clearly explain what information it contains, and reference it in your system prompt."

Examples of GOOD messages (Personalized):

Field Messages (Personalized to assistant purpose):
- ✅ "Update description to be short and clearly describe your Jira ticket management assistant's purpose for marketplace users."
- ✅ "Choose a descriptive name that clearly indicates your test case generation assistant's specific capabilities."

Tool Messages (⛔ NO TOOL NAMES - marked with $[name]$ below, MUST be personalized):
- ✅ "Add the essential tool for managing and tracking test cases in your software testing workflow." (ADD - personalized, uses "Add" prefix, NO name)
- ✅ "Tool could be removed as it serves no purpose for your Jira ticket management operations." (DELETE - personalized to assistant purpose)
- ✅ "Add the essential tool for creating and managing GitHub issues in your development workflow." (ADD - personalized to GitHub assistant)
- ❌ "Add the essential tool for managing test cases." (generic - not personalized)
- ❌ "Add $[generic_jira_tool]$ as it is essential for managing test cases." (WRONG - includes marked tool name)
- ❌ "$[Wikipedia]$." (WRONG - includes marked tool name, must delete tool name $[tool_name]$)

Context Messages (⛔ NO CONTEXT NAMES - marked with $[name]$ below):
- ✅ "Update the datasource description to be clear and helpful, and ensure your system prompt explains its purpose." (NO name)
- ❌ "Update the $[product-docs]$ description to be clear." (WRONG - includes marked context name)
- ❌ "Update the 'product-docs' description to be clear." (WRONG - includes context name)

Generate ONLY the message text, no additional formatting or explanation.
Return the messages in the same order and structure as the recommendations above.
For fields, return: field_messages (list of strings)
For tools, return: tool_messages (list of strings, one per tool across all toolkits)
For context, return: context_messages (list of strings)

🚨 FINAL REMINDER: ⛔ NEVER INCLUDE TOOL OR CONTEXT NAMES IN THE MESSAGES ⛔
"""


def build_friendly_message_generation_prompt(
    assistant_name: str,
    assistant_description: str,
    assistant_categories: str,
    field_recommendations: list,
    toolkit_recommendations: list,
    context_recommendations: list,
    metadata_result=None,
    system_prompt_result=None,
    tools_result=None,
    context_result=None,
) -> str:
    """Build prompt for generating user-friendly messages for recommendations.

    Args:
        assistant_name: Name of the assistant
        assistant_description: Description of the assistant
        assistant_categories: Categories of the assistant
        field_recommendations: List of field recommendations
        toolkit_recommendations: List of toolkit recommendations
        context_recommendations: List of context recommendations
        metadata_result: MetadataValidationResult with overall_reasoning (optional)
        system_prompt_result: ValidationResult with issues list (optional)
        tools_result: ToolsValidationResult with reasoning (optional)
        context_result: ContextValidationResult with reasoning (optional)

    Returns:
        Formatted prompt string
    """
    # Build validation results context section
    validation_context_parts = []

    if metadata_result and hasattr(metadata_result, "overall_reasoning"):
        validation_context_parts.append(f"**Metadata Validation Reasoning**: {metadata_result.overall_reasoning}")

    if system_prompt_result and hasattr(system_prompt_result, "issues") and system_prompt_result.issues:
        validation_context_parts.append(f"**System Prompt Issues**: {'; '.join(system_prompt_result.issues)}")

    if tools_result and hasattr(tools_result, "reasoning"):
        validation_context_parts.append(f"**Tools Validation Reasoning**: {tools_result.reasoning}")

    if context_result and hasattr(context_result, "reasoning"):
        validation_context_parts.append(f"**Context Validation Reasoning**: {context_result.reasoning}")

    validation_results_context = (
        "\n".join(validation_context_parts)
        if validation_context_parts
        else "No additional validation context available."
    )

    return FRIENDLY_MESSAGE_GENERATION_TEMPLATE.format(
        assistant_name=assistant_name,
        assistant_description=assistant_description,
        assistant_categories=assistant_categories,
        validation_results_context=validation_results_context,
        field_recommendations_section=_format_field_recommendations(field_recommendations),
        toolkit_recommendations_section=_format_toolkit_recommendations(toolkit_recommendations),
        context_recommendations_section=_format_context_recommendations(context_recommendations),
    )


# Export all templates
__all__ = [
    "METADATA_VALIDATION_TEMPLATE",
    "SYSTEM_PROMPT_VALIDATION_TEMPLATE",
    "TOOLS_DECISION_TEMPLATE",
    "CONTEXT_VALIDATION_TEMPLATE",
    "build_friendly_message_generation_prompt",
]
