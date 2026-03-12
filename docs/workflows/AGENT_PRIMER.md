# CodeMie Workflows - AI Agent Primer

> **READ THIS FIRST** - This document provides essential context about CodeMie Workflows that you MUST understand before assisting users with workflow creation.

## What Are CodeMie Workflows?

CodeMie Workflows is an orchestration system built on LangGraph that enables you to create complex, multi-step AI-powered processes by coordinating multiple assistants, tools, and processing nodes. Unlike single AI assistants that handle tasks in one interaction, workflows enable you to orchestrate sequences (or parallel executions) of multiple operations, each optimized for a specific subtask.

### Core Concept

A workflow is a **directed graph** where:
- **Nodes (States)** represent specific actions (running an AI assistant, executing a tool, or processing data)
- **Edges (Transitions)** define how execution flows from one node to another
- **Context Store** shares data across all steps
- **Message History** maintains conversation context throughout execution

## Why Use Workflows?

Workflows are designed for tasks that require:

1. **Multiple Distinct Steps**: Break complex problems into manageable, specialized steps
2. **Parallel Processing**: Execute multiple operations concurrently for efficiency
3. **Conditional Logic**: Branch execution based on results (if-then-else, switch/case)
4. **Different AI Configurations**: Use different models, temperatures, or tools for different subtasks
5. **Context Preservation**: Maintain state and data across multiple steps
6. **Tool Integration**: Connect to external systems (cloud platforms, databases, APIs)

**Example Use Cases:**
- Code analysis → Identify issues → Generate fixes → Validate changes
- Fetch data → Transform → Validate → Store results
- Gather information → Analyze → Compare alternatives → Generate reports

## Core Architecture Components

### 1. States (Nodes)

Three types of states exist:

**Agent States**: Execute an AI assistant with a specific task
```yaml
- id: analyze-code
  assistant_id: code-analyzer
  task: "Analyze this code for security issues: {{code}}"
```

**Tool States**: Execute a tool directly without LLM involvement
```yaml
- id: fetch-data
  tool_id: api-call
```

**Custom Node States**: Specialized processing (aggregation, document generation, etc.)
```yaml
- id: aggregate-results
  custom_node_id: state_processor_node
```

### 2. Assistants

AI agents configured with specific capabilities:
```yaml
assistants:
  - id: code-analyzer
    model: gpt-4.1              # Choose appropriate model
    temperature: 0.3             # Control randomness
    system_prompt: |             # Define behavior
      You are an expert code security analyst...
    tools:                       # Grant tool access
      - name: read_file
    datasource_ids:              # Connect to knowledge bases
      - codebase-repo
```

### 3. State Transitions

Define execution flow between states:

- **Simple**: `state_id: next-state` (sequential execution)
- **Parallel**: `state_ids: [state-1, state-2]` (concurrent fan-out)
- **Conditional**: `condition: {expression: "status == 'success'", then: ..., otherwise: ...}` (branching)
- **Switch/Case**: Multiple conditions evaluated sequentially
- **Iterative**: `iter_key: items` (map-reduce pattern for collections)

### 4. Context Management

**Context Store**: Key-value storage accessible throughout workflow
- Automatically populated with state outputs
- Supports dynamic value resolution: `{{variable_name}}`
- Can be explicitly cleaned up to optimize performance

**Message History**: All messages exchanged during execution
- Sent to AI assistants for context
- Can be included/excluded per state
- Automatically summarized when limits exceeded

## Basic YAML Configuration Structure

Every workflow configuration has this structure:

```yaml
# 1. Workflow-Level Settings (optional)
messages_limit_before_summarization: 25
enable_summarization_node: true
recursion_limit: 50
max_concurrency: 10

# 2. Assistants (required - at least one)
assistants:
  - id: assistant-1
    model: gpt-4.1
    system_prompt: "Instructions..."
    # OR reference existing: assistant_id: existing-id

# 3. Tools (optional)
tools:
  - id: tool-1
    tool: tool-method-name
    tool_args:
      param: value

# 4. Custom Nodes (optional)
custom_nodes:
  - id: node-1
    custom_node_id: state_processor_node

# 5. States (required - at least one)
states:
  - id: state-1
    assistant_id: assistant-1
    task: "Task instructions with {{variables}}"
    next:
      state_id: state-2

  - id: state-2
    assistant_id: assistant-1
    task: "Next task"
    next:
      state_id: end
```

## Required vs Optional Fields

### Required in Every Workflow:
- **assistants**: At least one assistant definition
- **states**: At least one state definition
- Each state must have: `id`, `next`, and one of (`assistant_id` | `tool_id` | `custom_node_id`)
- Each transition must have one of: `state_id` | `state_ids` | `condition` | `switch`

### Commonly Used Optional Fields:
- Workflow-level settings (memory, concurrency limits)
- Tools and custom nodes
- Assistant properties (temperature, tools, datasources)
- State properties (output_schema, interrupt_before, retry_policy)
- Transition properties (iter_key, store_in_context, context cleanup)

## Essential Patterns to Know

### Pattern 1: Sequential Processing
```yaml
states:
  - id: step-1
    next: { state_id: step-2 }
  - id: step-2
    next: { state_id: step-3 }
  - id: step-3
    next: { state_id: end }
```

### Pattern 2: Parallel Processing
```yaml
states:
  - id: split
    next:
      state_ids: [process-a, process-b, process-c]
  # All three execute concurrently
```

### Pattern 3: Conditional Branching
```yaml
states:
  - id: validate
    next:
      condition:
        expression: "valid == true"
        then: continue
        otherwise: handle-error
```

### Pattern 4: Map-Reduce (Iteration)
```yaml
states:
  - id: list-files
    # Outputs: ["file1.txt", "file2.txt", "file3.txt"]
    next:
      state_id: process-file
      iter_key: .  # Iterate over entire list

  - id: process-file
    task: "Process {{task}}"
    # Executes 3 times in parallel, once per file
    next:
      state_id: aggregate-results
```

## Key Principles for Workflow Design

1. **Single Responsibility**: Each state does one thing well
2. **Descriptive Naming**: Use clear, action-based IDs (`analyze-security` not `state1`)
3. **Explicit Instructions**: Provide detailed task descriptions with clear input/output
4. **Error Handling**: Always plan failure paths with conditional transitions
5. **Context Hygiene**: Store only what's needed, clean up regularly
6. **Security First**: Never hardcode credentials; use `integration_alias`

## Common Mistakes to Avoid

❌ **Circular Dependencies**: States referencing each other in a loop
❌ **Vague Tasks**: "Check the code" (be specific about what to check)
❌ **Generic IDs**: `state1`, `helper`, `temp` (use descriptive names)
❌ **Context Bloat**: Storing large datasets in context
❌ **Missing Error Paths**: No `otherwise` or `default` branches
❌ **Hardcoded Values**: Use context variables instead of literals

## Documentation Quick Reference

When assisting users, refer to these documentation sections:

| Topic | Documentation | Key Sections |
|-------|--------------|--------------|
| Getting started concepts | `01_introduction.md` | 1.1 What are workflows, 1.2 Architecture |
| Full configuration syntax | `02_configuration_reference.md` | 3.1-3.6 All configuration options |
| State types and properties | `03_workflow_states.md` | 4.1-4.4 Agent/Tool/Custom states |
| Transition types and routing | `04_state_transitions.md` | 5.1-5.5 Simple/Parallel/Conditional/Iterative |
| Context and memory | `05_context_management.md` | Context store, dynamic values, cleanup |
| Advanced features | `06_advanced_features.md` | Map-reduce, memory, retry, interruption |
| Specialized nodes | `07_specialized_nodes.md` | State processor, Bedrock, document tree |
| Tool integration | `08_integration_capabilities.md` | Data sources, tools, MCP servers |
| Design best practices | `09_best_practices.md` | All sections - critical reference |
| Complete examples | `10_examples.md` | Working workflow examples |
| Debugging issues | `11_troubleshooting.md` | Common problems and solutions |

## Your Approach to Helping Users

### Step 1: Understand Requirements
Ask clarifying questions BEFORE generating any YAML:
- What is the workflow's purpose and goal?
- What are the inputs and expected outputs?
- What processing steps are needed?
- Should any steps run in parallel?
- How should errors be handled?
- What external systems need integration?

### Step 2: Design Architecture
Plan the workflow structure:
- Break into focused, single-purpose states
- Identify parallelization opportunities
- Design conditional branches for error handling
- Plan context flow and data management

### Step 3: Generate Configuration
Create complete, valid YAML following the structure above:
- Start with workflow-level settings
- Define assistants with clear purposes
- Create states in logical execution order
- Configure transitions with proper error handling
- Add tools and custom nodes as needed

### Step 4: Validate and Document
Ensure quality:
- Validate YAML syntax and schema compliance
- Check for circular dependencies
- Verify all ID references exist
- Add inline comments for complex logic
- Provide usage examples

## When to Search Documentation

Search the documentation when you need:
- Exact syntax for specific features
- Examples of complex patterns (map-reduce, conditionals)
- Schema requirements for configuration fields
- Best practices for specific use cases
- Clarification on advanced features (MCP servers, custom nodes)

**IMPORTANT**: Always search documentation when uncertain. Never guess syntax or schema requirements.

---

## Quick Start Example

Here's a minimal workflow to illustrate the basics:

```yaml
# Simple two-step sequential workflow
assistants:
  - id: analyzer
    model: gpt-4.1-mini
    temperature: 0.3
    system_prompt: "You are a data analyst"

states:
  - id: analyze-data
    assistant_id: analyzer
    task: "Analyze this data: {{input_data}}"
    next:
      state_id: generate-summary
      store_in_context: true

  - id: generate-summary
    assistant_id: analyzer
    task: "Create a summary from: {{task}}"
    next:
      state_id: end
```

**Usage**: Provide `{"input_data": "..."}` when executing

---

## You Are Ready

With this foundation, you can now assist users in creating CodeMie Workflows. Remember:

1. **Ask questions first** - understand before generating
2. **Design thoughtfully** - plan the architecture
3. **Generate completely** - provide full, valid configurations
4. **Validate rigorously** - ensure correctness
5. **Document clearly** - make workflows maintainable
6. **Reference docs** - search when uncertain

Refer to the detailed documentation sections above for specific syntax, advanced features, and best practices as you help users build their workflows.

