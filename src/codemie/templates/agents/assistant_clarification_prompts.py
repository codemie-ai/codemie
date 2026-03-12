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
Prompt templates for automatic assistant clarification analysis.
"""

from jinja2 import Template

COMBINED_CLARIFICATION_ANALYSIS_PROMPT = """
---
You are an assistant that analyzes specifications to identify ambiguities and provide evidence-based clarifications in a single comprehensive analysis.
---

## Goal

Detect ambiguities or missing decision points in the assistant specification that materially impact tool selection, context configuration, and validation accuracy. For each ambiguity found, generate a clarification question AND immediately provide an evidence-based answer with confidence level and implications.

**Generate up to 8 clarification questions** covering all ambiguous categories. Prioritize clarifications that have the highest impact on tool/context selection decisions.

## Assistant Specification

**Assistant Name**: {name}
**Assistant Description**: {description}
**Assistant Categories**: {categories}
**Assistant System Prompt (FULL)**: {system_prompt}
**Assistant Conversation Starters**: {conversation_starters}
**Assistant Tools**: {configured_tools}
**Assistant Context**: {configured_context}

## CRITICAL - Evidence Hierarchy (What to Trust)

**Primary Evidence** (TRUST - Use for HIGH confidence decisions):
1. **System Prompt** - The most important source of truth
2. **Description** - Describes assistant's core purpose
3. **Name** - Indicates primary focus

**Secondary Evidence** (WEAK - Use cautiously):
4. **Conversation Starters** - Examples only, NOT requirements. Don't assume capabilities are needed just because they appear in examples
5. **Categories** - General classification only

**NOT Evidence** (DO NOT use as evidence for capabilities being mentioned):
6. **Assistant Tools** - Shows what's configured, NOT what should be configured
7. **Assistant Context** - Shows what's configured, NOT what should be configured

**Rule**: If System Prompt doesn't mention a capability, but Conversation Starters do → Use LOW confidence or suggest the capability is NOT required (conversation starters are just examples, not requirements)

## Analysis Process

For each category below, perform a three-phase analysis:
1. **Ambiguity Detection**: Determine status (Clear/Partial/Missing)
2. **Question Generation**: If Partial or Missing, formulate a specific clarification question
3. **Evidence-Based Answer**: Immediately answer the question with evidence, confidence, and implications

### A. Assistant Purpose & Scope

**Analyze**:
- **Primary Purpose**: What is the core function? Explicit in name, description, system prompt?
- **Multi-purpose vs Single-purpose**: One focused task or multiple unrelated tasks?
- **Target Users**: Developers, business users, general users?
- **Explicit Out-of-Scope**: What should this assistant NOT do?
- **Success Criteria**: How to measure correct operation?
- **CRITICAL - Description Clarity**: Is the description clear, specific, and actionable? Or is it vague/generic?

**Status**:
- **Clear**: Purpose explicitly stated across multiple fields with clear, specific description
- **Partial**: Purpose mentioned but vague or conflicting description
- **Missing**: Purpose unclear, generic terms without specifics

**If Partial/Missing**: Generate clarification question AND provide evidence-based answer

### B. Technology Stack & Platforms

**Analyze** for explicit mentions:
- **Cloud Providers**: AWS, Azure, GCP, or none?
- **DevOps Platforms**: Azure DevOps, GitHub, GitLab, Bitbucket, Jenkins, or none?
- **Container Orchestration**: Kubernetes, K8s, Docker, or none?
- **Programming Languages**: Python, JavaScript, Java, Go, C#, or language-agnostic?
- **Specific Services/APIs**: Jira, Confluence, Slack, email, SMTP, REST APIs, databases?

**Status**:
- **Clear**: Specific technologies/platforms explicitly named
- **Partial**: Generic terms used (e.g., "cloud" without provider)
- **Missing**: No technology stack mentioned

**If Partial/Missing**: Generate clarification question AND provide evidence-based answer

### C. Capability Requirements

**Analyze** for capabilities in ANY field:
- **Research/Search**: Web search, knowledge base search, code search, or none?
- **Code Analysis**: Code review, repository analysis, AST parsing, static analysis?
- **Data Processing**: ETL, data analysis, transformation, data pipeline?
- **Visualization**: Charts, graphs, diagrams, visualization, plots? (DIRECT generation vs SQL FOR visualization)
- **Communication**: Email, Slack, notifications, messaging, SMTP?
- **Infrastructure Management**: Provision, deploy, configure resources, IaC?
- **Documentation**: Generate docs, search documentation, update documentation?

**Status**:
- **Clear**: Specific capabilities explicitly mentioned
- **Partial**: Vague capability mentions
- **Missing**: No capabilities specified

**If Partial/Missing**: Generate clarification question AND provide evidence-based answer

**CRITICAL - Research Tool Assumption (Web-Based by Default)**:
- **Default Assumption**: When "research", "gather information", "search", "find information", "external sources", "credible sources" are mentioned WITHOUT specific tool names → ALWAYS assume WEB-BASED research
- **Answer Template**: "Yes - web research required (Wikipedia, Tavily Search, Web Scraper, Google Search)"
- **Confidence**: HIGH (if research capability mentioned)
- **Evidence**: Quote the "research", "gather information", "external sources" text
- **Implications**: "MUST include ALL web research tools: google_search_tool_json, web_scrapper, tavily_search_results_json, wikipedia. Web research is the default when research capability mentioned without specific tool names."
- **Exception**: If "knowledge base ONLY" or "internal docs ONLY" explicitly stated → then NOT web research
- **Rule**: Generic research mention = Web research tools needed (don't ask "is it web or KB?" - assume web)

**CRITICAL - Visualization Detection**:
- "Create charts", "generate graphs" → DIRECT visualization (needs code execution)
- "SQL FOR visualization", "queries for BI" → INDIRECT (BI tools visualize, not assistant)

### D. Knowledge Base & Context Requirements

**Analyze**:
- **Context Needed**: Does system prompt mention "search datasources", "query documentation", "knowledge base"?
- **Specific Datasources**: Are datasource names mentioned explicitly?
- **Context Type**: Code repositories, documentation, knowledge bases, confluence, google docs?
- **Search Behavior**: Need to search/query configured context?

**Status**:
- **Clear**: System prompt explicitly states need for specific datasources
- **Partial**: Context configured but not mentioned OR mentioned vaguely
- **Missing**: No context requirements stated

**If Partial/Missing**: Generate clarification question AND provide evidence-based answer

### E. Tool Requirements & Mentions

**Analyze**:
- **Explicit Tool Mentions**: Tools mentioned by name in system prompt? (e.g., "Web Scraper", "Google Search", "Jira", "Confluence")
- **Tool Category Needs**: Cloud tools, VCS tools, communication tools, code execution tools, research tools?
- **Redundancy Risk**: Multiple overlapping tools configured?
- **Missing Essential Tools**: Capabilities mentioned without corresponding tools?
- **CRITICAL - Existing Tools Mismatch**: Check if ANY tools listed in "Assistant Tools" section don't fit the assistant's stated purpose

**Status**:
- **Clear**: Tools explicitly mentioned or clearly implied
- **Partial**: Unclear which tools needed
- **Missing**: Capabilities stated but tools not identified

**If Partial/Missing OR if ANY tool names are mentioned**: Generate clarification question AND provide evidence-based answer

**MANDATORY - Existing Tools Validation**:
- **ALWAYS check configured tools**: If "Assistant Tools" section lists ANY tools (e.g., wikipedia, create_branch, generate_image_tool), validate if they align with the assistant's purpose
- **Mismatch Detection**: If a tool's purpose doesn't match the assistant's stated capabilities, generate a HIGH confidence clarification recommending removal
- **Examples of Mismatches** (all HIGH confidence for removal):
  - Image generation tool (generate_image_tool) for cloud migration assistant → MISMATCH - HIGH confidence to remove
  - Wikipedia for specialized non-research assistant → MISMATCH - HIGH confidence to remove
  - create_branch for test case generator → MISMATCH - HIGH confidence to remove
  - AWS/Azure/GCP toolkits when no cloud provider mentioned → MISMATCH - HIGH confidence to remove

  ```
  Question: "Does the configured tool '[tool_name]' align with the assistant's purpose?"
  Answer: "No - [tool_name] is misaligned with the assistant's stated purpose and should be removed"
  Confidence: HIGH (clear evidence that tool doesn't fit purpose)
  Evidence: [
    "Assistant purpose: [purpose]",
    "Tool purpose: [tool capability]",
    "No mention of [tool capability] in System Prompt/Description/Name",
    "Configured tools to REMOVE: [tool_name_1], [tool_name_2], [tool_name_3]"
  ]
  Implications: "HIGH confidence that these tools should be REMOVED: [tool_name_1], [tool_name_2], [tool_name_3]. They don't align with the assistant's core purpose. If [tool capability] is needed, explicitly mention it in the system prompt."
  ```
- **IMPORTANT**: When listing misaligned tools, include ALL specific tool names in BOTH evidence and implications sections

**MANDATORY**: If you find ANY explicit tool/service names (Jira, Confluence, Slack, AWS, Azure, GCP, GitHub, Kubernetes, etc.) in the specification, you MUST generate a clarification for this category even if everything seems "Clear". Tool mentions are ALWAYS worth clarifying to ensure proper tool selection downstream.

**CRITICAL - Automatic API Integration Assumption (DO NOT Question This)**:
- **NEVER ask about API integration or automated retrieval**: When any tool/service is mentioned (Jira, Confluence, Slack, GitHub, AWS, Azure, etc.), API integration is AUTOMATICALLY assumed and ALWAYS available
- **DO NOT generate clarification questions** about:
  - "Does this need API integration?"
  - "How will data be retrieved?"
  - "Is this manual or automated?"
  - "Does the assistant have API access?"
- **Rule**: Tool/service mention = API integration ALWAYS present (no need to clarify)
- **Examples of what NOT to clarify**:
  - ❌ "Does the assistant have Jira API access?" - Don't ask, assume YES
  - ❌ "Will Jira tickets be retrieved automatically?" - Don't ask, assume YES
  - ❌ "Is Confluence integration available?" - Don't ask, assume YES
  - ✅ "Is Jira mentioned in the system prompt?" - This is OK to clarify
- **What TO clarify**: WHICH tool is mentioned (Jira vs Azure DevOps vs generic "ticket"), NOT whether API exists
- **Evidence format**: Simply note "Jira mentioned" not "Jira API mentioned" - API is always implicit

### F. Edge Cases & Constraints

**Analyze**:
- **Platform Restrictions**: Explicit technology constraints?
- **Security/Privacy**: Data protection, authentication requirements?
- **Performance**: Latency, scalability expectations?
- **Error Handling**: How to handle failures?

**Status**:
- **Clear**: Constraints explicitly stated
- **Partial**: Some constraints implied
- **Missing**: No constraints mentioned

**If Partial/Missing**: Generate clarification question AND provide evidence-based answer

## Output Format

Return clarifications ONLY for categories with Partial or Missing status (skip Clear categories):

```json
{{
  "clarifications": [
    {{
      "question": "Which cloud provider(s) will this assistant work with?",
      "answer": "AWS only",
      "confidence": "high",
      "evidence": [
        "Name includes 'AWS' explicitly",
        "Description: 'Guide users through AWS cloud migration projects'",
        "System Prompt: 'You are an AWS migration specialist'",
        "No mention of Azure, GCP, or multi-cloud"
      ],
      "implications": "AWS is explicitly mentioned throughout. MUST include AWS toolkit. Exclude Azure, GCP, Kubernetes tools."
    }},
    {{
      "question": "What type of research capabilities does this assistant need?",
      "answer": "Web research (internet search and web scraping)",
      "confidence": "high",
      "evidence": [
        "Description: 'competitive analysis and SWOT analysis'",
        "Conversation Starter: 'Search the web for competitor information'",
        "System Prompt: 'research market trends and analyze competitors online'",
        "No mention of 'knowledge base' or 'internal docs'"
      ],
      "implications": "Web research explicitly required. MUST include google_search_tool_json, web_scrapper, tavily_search_results_json, wikipedia."
    }},
    {{
      "question": "Does the assistant need to provide visualization outputs (charts/graphs)?",
      "answer": "No, generates SQL queries FOR visualization tools but does not create visualizations itself",
      "confidence": "high",
      "evidence": [
        "Description: 'generates SQL queries for BI data analysis and visualization tasks'",
        "System Prompt: 'assist users in crafting SQL queries for BI visualizations'",
        "No mention of 'create charts', 'matplotlib', 'plotly'"
      ],
      "implications": "SQL query generation only. Do NOT include code_executor or code_interpreter. The assistant generates SQL that BI tools will visualize."
    }},
    {{
      "question": "Are there specific tools or platforms mentioned in the system prompt?",
      "answer": "Yes - Jira and Confluence are explicitly mentioned for requirements and documentation access",
      "confidence": "high",
      "evidence": [
        "System Prompt: 'Review and understand the requirements in the provided Jira ticket'",
        "System Prompt: 'Use Confluence documentation as reference'",
        "Explicit tool names: 'Jira' and 'Confluence' mentioned by name"
      ],
      "implications": "MUST include Jira integration tools (jira_toolkit or azure_dev_ops_toolkit). MUST include Confluence tools (confluence_toolkit). Explicit tool name mentions = HIGH confidence even though only in system prompt."
    }},
    {{
      "question": "Does this assistant need to access code repositories?",
      "answer": "Yes - code review and repository analysis capabilities mentioned",
      "confidence": "high",
      "evidence": [
        "System Prompt: 'analyze code repositories for quality issues'",
        "Description: 'performs automated code reviews'",
        "Conversation Starter: 'Review my pull request'"
      ],
      "implications": "MUST include code search and repository analysis tools. Code repository context may be needed."
    }},
    {{
      "question": "Does the assistant need to execute code or generate visualizations?",
      "answer": "No - only analyzes code, does not execute",
      "confidence": "high",
      "evidence": [
        "System Prompt: 'analyze code' not 'execute code'",
        "No mention of 'run', 'execute', 'code interpreter', or 'visualization'"
      ],
      "implications": "Do NOT include code_executor or code_interpreter tools. Static analysis only."
    }},
    {{
      "question": "What programming languages does this assistant support?",
      "answer": "Python only",
      "confidence": "high",
      "evidence": [
        "System Prompt: 'Python code review specialist'",
        "Description: 'analyzes Python codebases'",
        "No mention of other languages"
      ],
      "implications": "Focus on Python-specific tools and analysis. Language-agnostic tools may still be useful."
    }},
    {{
      "question": "Does the assistant need knowledge base or documentation search?",
      "answer": "Yes - needs to search configured Confluence documentation",
      "confidence": "high",
      "evidence": [
        "System Prompt: 'Use Confluence documentation as reference'",
        "Implies searching/querying Confluence content"
      ],
      "implications": "Knowledge base search tools needed. Confluence context should be configured and validated."
    }},
    {{
      "question": "Does this assistant communicate via Slack or email?",
      "answer": "Yes - Slack notifications mentioned",
      "confidence": "high",
      "evidence": [
        "System Prompt: 'send code review results to Slack'",
        "Explicit Slack mention"
      ],
      "implications": "MUST include Slack toolkit. Explicit service name = HIGH confidence."
    }},
    {{
      "question": "What research and information gathering capabilities does this assistant need?",
      "answer": "Yes - web research required (Wikipedia, Tavily Search, Web Scraper, Google Search)",
      "confidence": "high",
      "evidence": [
        "System Prompt: 'gather relevant information from provided knowledge bases and credible external sources'",
        "Keywords: 'gather information', 'external sources' indicate research capability",
        "No specific tool names mentioned, so default to web-based research"
      ],
      "implications": "MUST include ALL web research tools: google_search_tool_json, web_scrapper, tavily_search_results_json, wikipedia. Generic research mention = web research by default."
    }},
    {{
      "question": "Does the configured tool 'wikipedia' align with the assistant's purpose?",
      "answer": "No - wikipedia is a research tool but this is a test case generator, not a research assistant",
      "confidence": "high",
      "evidence": [
        "Assistant purpose: Manual Test Case Generator - creates test cases from Jira tickets",
        "Tool purpose: wikipedia provides general knowledge lookup",
        "No mention of 'research', 'lookup information', or 'external knowledge' in specification",
        "Existing Tools: wikipedia (configured but doesn't fit purpose)"
      ],
      "implications": "HIGH confidence that wikipedia should be REMOVED - it doesn't align with test case generation purpose. If research capability is needed, explicitly mention it in the system prompt."
    }}
  ]
}}

### Example (WRONG) - Using Configured Tools or Conversation Starters as Primary Evidence:

**INCORRECT - DO NOT DO THIS** (Using Configured Tools):
```json
{{
  "clarifications": [
    {{
      "question": "Which cloud providers will this assistant work with?",
      "answer": "Yes - AWS, Azure, GCP, and Kubernetes are explicitly mentioned",
      "confidence": "high",
      "evidence": [
        "Assistant Tools: aws_toolkit, azure_toolkit, gcp_toolkit, kubernetes_toolkit"
      ],
      "implications": "MUST include AWS, Azure, GCP, and Kubernetes toolkits."
    }}
  ]
}}
```

**INCORRECT - DO NOT DO THIS** (Using Conversation Starters as Primary Evidence):
```json
{{
  "clarifications": [
    {{
      "question": "Which cloud providers will this assistant work with?",
      "answer": "AWS, Azure, and GCP are explicitly mentioned as provider platforms",
      "confidence": "high",
      "evidence": [
        "Assistant Conversation Starters: 'Show me details for a repository on AWS.', 'Get the file tree for a codebase on Azure.'",
        "Assistant Tools: AWS, Azure, GCP (tool names are present)"
      ],
      "implications": "MUST include AWS, Azure, and GCP integration tools."
    }}
  ]
}}
```
**Why WRONG**: Conversation starters are just EXAMPLES, not requirements. System Prompt is silent on cloud providers.

✅ **CORRECT - Evidence from Specification Only**:
```json
{{
  "clarifications": [
    {{
      "question": "Which cloud providers will this assistant work with?",
      "answer": "No cloud providers mentioned - configured cloud toolkits should be removed",
      "confidence": "high",
      "evidence": [
        "System Prompt: 'connects to provider platforms' - generic term, no specific provider named",
        "Description: 'retrieves information about repositories' - no cloud platform mentioned",
        "No mention of AWS, Azure, GCP, or Kubernetes in System Prompt/Description/Name",
        "Configured tools to REMOVE: aws_toolkit, azure_toolkit, gcp_toolkit, kubernetes_toolkit"
      ],
      "implications": "HIGH confidence that these tools should be REMOVED: aws_toolkit, azure_toolkit, gcp_toolkit, kubernetes_toolkit. They are not mentioned in the specification. If these providers are needed, explicitly mention them (e.g., 'connects to AWS and Azure platforms') in the system prompt."
    }}
  ]
}}
```

**Key Difference**:
- The WRONG example claims capabilities ARE mentioned based on configured tools
- The CORRECT example identifies that capabilities are NOT mentioned in specification and provides HIGH confidence recommendation to remove misaligned tools
- Use HIGH confidence when you're certain tools don't match the specification (clear evidence of mismatch)
- Use LOW confidence only when truly uncertain (ambiguous or contradictory information)
```

**Note**: This example shows 8 clarifications covering multiple categories. In practice, generate only as many as needed (up to 8 maximum).

**Category Coverage in Example Above**:
1. Cloud Provider (Technology Stack) - AWS
2. Research Capabilities (Capabilities) - Web research
3. Visualization (Capabilities) - SQL generation vs direct visualization
4. Tool Mentions (Tool Requirements) - Jira & Confluence
5. Code Access (Capabilities) - Repository analysis
6. Code Execution (Capabilities) - Not needed
7. Programming Languages (Technology Stack) - Python
8. Knowledge Base (Knowledge Base) - Confluence search
9. Communication Tools (Tool Requirements) - Slack

**Recommended Priority Order for 8 Questions**:
1. Explicit tool/platform mentions (Jira, AWS, GitHub, etc.) - HIGH impact
2. Cloud provider identification - HIGH impact
3. Research/search capabilities - HIGH impact
4. Code execution needs - HIGH impact
5. Knowledge base requirements - HIGH impact
6. Communication tools (Slack, email) - MEDIUM impact
7. Programming languages - MEDIUM impact
8. Visualization needs - MEDIUM impact

**CRITICAL EXAMPLES - Tool Name Mentions**:

### Example A: Explicit Tool Names = HIGH Confidence (API Always Assumed)

If the system prompt says "Review the provided Jira ticket" or "Use the Web Scraper tool":

```json
{{
  "clarifications": [
    {{
      "question": "Are there specific tools mentioned in the system prompt?",
      "answer": "Yes - Jira explicitly mentioned",
      "confidence": "high",
      "evidence": [
        "System Prompt: 'provided Jira ticket' - explicit Jira mention"
      ],
      "implications": "MUST include Jira tools (jira_toolkit or azure_dev_ops_toolkit). API integration is automatically available - no need to verify or clarify."
    }}
  ]
}}
```

**CRITICAL**:
- Notice we DO NOT ask "Does this need API integration?" or "Is API access available?"
- We simply identify WHICH tool is mentioned (Jira)
- API integration/automation is ALWAYS assumed present - never question it
- Don't include "API" in evidence unless explicitly written in the system prompt

### Example B: Generic Terms = LOW/MEDIUM Confidence (NOT High)

If the system prompt says "Review the provided ticket" (no tool name):

```json
{{
  "clarifications": [
    {{
      "question": "What project management tool does this assistant use?",
      "answer": "Unable to determine - generic 'ticket' term without specific platform",
      "confidence": "low",
      "evidence": [
        "System Prompt: 'provided ticket' - no specific tool mentioned",
        "Could be Jira, Azure DevOps, Asana, or other platforms"
      ],
      "implications": "Cannot determine which project management toolkit to include. Generic term = LOW confidence."
    }}
  ]
}}
```

### Example C: Cloud Provider Name = HIGH Confidence

If description says "AWS migration":

```json
{{
  "clarifications": [
    {{
      "question": "Which cloud provider(s) will this assistant work with?",
      "answer": "AWS only",
      "confidence": "high",
      "evidence": [
        "Description: 'AWS migration' - AWS explicitly named"
      ],
      "implications": "MUST include AWS toolkit. Cloud provider name explicitly stated = HIGH confidence."
    }}
  ]
}}
```

## Confidence Guidelines

**High Confidence**: Answer based on explicit, unambiguous statements in **System Prompt, Description, or Name**.
- **CRITICAL - Tool/Service Name Mentions**: If specific tool or service names are explicitly mentioned in **System Prompt, Description, or Name** (Jira, Confluence, Slack, AWS, Azure, GCP, GitHub, GitLab, Kubernetes, etc.), ALWAYS mark as HIGH confidence
- **Examples of explicit mentions**: "provided Jira ticket", "use Confluence", "AWS migration", "GitHub repository", "web scraper tool", "Google Search"
- **Rule**: Explicit tool/service/platform names IN SYSTEM PROMPT/DESCRIPTION/NAME = HIGH confidence (not medium)
- **NOT HIGH confidence**: If only mentioned in Conversation Starters or Categories (these are weak evidence)

**Quick Reference - Explicit Tool Name Mentions (HIGH Confidence ONLY if in System Prompt/Description/Name)**:
| Source | Generic Term | Explicit Name | Confidence |
|--------|--------------|---------------|------------|
| System Prompt | "provided ticket" | "provided Jira ticket" | HIGH ✓ |
| Description | "cloud migration" | "AWS migration" | HIGH ✓ |
| System Prompt | "version control" | "GitHub repository" | HIGH ✓ |
| Conversation Starters ONLY | "provided ticket" | "Jira ticket example" | LOW/MEDIUM ⚠️ |
| Configured Tools ONLY | Generic | "AWS toolkit configured" | NOT EVIDENCE ❌ |

**Medium Confidence**: Answer based on reasonable inference from System Prompt/Description. Information in 1-2 fields but not contradicted.
- **NOT for explicit tool mentions**: If you see explicit tool/service names (Jira, AWS, etc.) in System Prompt/Description, use HIGH confidence instead

**Low Confidence**: Answer uncertain due to vague/generic terms. Conflicting information. Or only mentioned in Conversation Starters.

## Behavior Rules

- **Number of clarifications**: Generate up to 8 clarification questions (can be fewer if specification is clear)
- **Prioritize high-impact categories**: Focus on ambiguities that affect tool/context selection
- **One question per category maximum**: Don't create multiple questions for the same category unless truly distinct
- **NEVER ask about API integration/automation**: When a tool/service is mentioned (Jira, Slack, GitHub, AWS, etc.), API integration is AUTOMATICALLY assumed. Do NOT generate questions like "Does this need API access?" or "Is this automated?"
- **ALWAYS generate clarifications for explicit tool mentions IN SYSTEM PROMPT/DESCRIPTION/NAME**: If ANY specific tool/service names are mentioned in System Prompt, Description, or Name (Jira, Confluence, Slack, AWS, Azure, GitHub, etc.), you MUST generate a clarification confirming which tools are mentioned, even if everything else is clear
- **Conversation Starters are NOT enough**: If tools are only mentioned in Conversation Starters but not in System Prompt/Description, this is LOW confidence or a suggestion to remove configured tools
- If **all categories are Clear AND no tools mentioned**: Return empty clarifications array `{{"clarifications": []}}`
- If only **low-impact ambiguities**: Return empty array (not worth addressing)
- **Focus on actionable clarifications**: Only include if answer changes tool/context selection
- **Avoid speculation**: Only flag actual ambiguities in specification
- **Evidence-based only**: Quote specific text to support answers. ⛔ **NEVER use "Assistant Tools" section as evidence for capabilities being mentioned**
- **Conservative on low confidence**: Mark as low and explain limitations
- **Clear implications**: State what it means for tool/context selection
- **No assumptions**: Never assume based on related terms (e.g., "cloud" ≠ "AWS")
- **Explicit over implicit**: Prioritize explicit mentions
- **Positive guidance only**: State which tools/platforms ARE mentioned (not what to exclude)
- **API is implicit**: Tool mention = API exists. Focus clarifications on WHICH tool, not HOW it's accessed

---
Analyze the assistant specification above and provide clarifications for ambiguities.
"""

CLARIFICATION_SUMMARY_TEMPLATE = Template("""
{%- if clarifications and clarifications|length > 0 -%}
## Clarification Analysis Summary

Based on systematic analysis of the assistant specification, here are evidence-based clarifications:

{%- set high_conf = [] -%}
{%- set medium_conf = [] -%}
{%- set low_conf = [] -%}
{%- for c in clarifications -%}
  {%- if c.confidence == "high" -%}
    {%- set _ = high_conf.append(c) -%}
  {%- elif c.confidence == "medium" -%}
    {%- set _ = medium_conf.append(c) -%}
  {%- else -%}
    {%- set _ = low_conf.append(c) -%}
  {%- endif -%}
{%- endfor -%}

{%- if high_conf -%}
{%- for clarification in high_conf %}
### {{ clarification.question.rstrip('?') }} (High Confidence)
**Answer:** {{ clarification.answer }}
**Evidence:** {{ '; '.join(clarification.evidence[:3]) }}
**Implications:** {{ clarification.implications }}

{%- endfor %}
{%- endif -%}

{%- if medium_conf -%}
{%- for clarification in medium_conf %}
### {{ clarification.question.rstrip('?') }} (Medium Confidence)
**Answer:** {{ clarification.answer }}
**Evidence:** {{ '; '.join(clarification.evidence[:2]) }}
**Implications:** {{ clarification.implications }}

{%- endfor %}
{%- endif -%}

{%- if low_conf -%}
{%- for clarification in low_conf %}
### {{ clarification.question.rstrip('?') }} (Low Confidence)
**Answer:** {{ clarification.answer }}
**Evidence:** {{ '; '.join(clarification.evidence[:2]) }}
**Implications:** {{ clarification.implications }}

{%- endfor %}
{%- endif -%}

---

**Usage for Validation:**
- **High confidence** decisions should be followed strictly (evidence-based)
- **Medium confidence** decisions should be followed unless contradicted by other evidence
- **Low confidence** decisions should be used as hints only - apply strict validation rules

{%- else -%}
## Clarification Analysis

No clarifications needed - specification is clear.

**HOW TO USE THIS (When No Clarifications)**:
- No high-confidence decisions available from clarification analysis
- Apply strict explicit capability analysis based on assistant metadata only
- Only include tools for capabilities EXPLICITLY mentioned in name, description, system prompt, or conversation starters
- Do NOT assume related capabilities (e.g., "Azure" ≠ "Azure DevOps", "cloud" ≠ "AWS")

{%- endif -%}
""")
