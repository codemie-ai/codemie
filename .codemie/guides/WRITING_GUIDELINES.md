# AI-First Documentation Writing Guidelines

## Quick Summary

Comprehensive guidelines for writing AI-optimized documentation in CodeMie. Focus on patterns over prose, examples over description, and structured content over narrative flow.

**Target**: AI agent comprehension and token efficiency
**Constraint**: ≤400 lines per document (excludes code blocks)
**Philosophy**: Show > Tell, Structure > Narrative, Density > Verbosity

## Prerequisites

- Understanding of CodeMie project structure
- Familiarity with Markdown syntax
- Basic knowledge of documentation best practices
## Core Principles
### 1. Patterns Over Prose
**DO**: Lead with code patterns and structure
**DON'T**: Write paragraphs explaining concepts
```python
# GOOD: Pattern-first
class AgentBuilder:
    def __init__(self, config: dict):
        self.config = config
    def build(self) -> Agent:
        return Agent(**self.config)
```
```markdown
<!-- BAD: Prose-first -->
The AgentBuilder class is a utility that helps you create agents.
It takes a configuration dictionary and uses it to instantiate
an Agent object with the appropriate settings...
```
### 2. Examples Over Description
**DO**: Show 3+ concrete examples | **DON'T**: Explain in abstract terms
```python
# Example 1: Basic agent
agent = AgentBuilder({"name": "analyst"}).build()
# Example 2: With tools
agent = AgentBuilder({"name": "dev", "tools": [CodeTool(), TestTool()]}).build()
# Example 3: Advanced config
agent = AgentBuilder({"name": "architect", "tools": [DiagramTool()], "llm_provider": "openai"}).build()
```
### 3. Structured Over Narrative
**DO**: Use lists, tables, code blocks | **DON'T**: Write flowing paragraphs

| Approach | Tokens | Parse Time | Density |
|----------|--------|------------|---------|
| Structured | 50 | Fast | High |
| Narrative | 150 | Slow | Low |
## Structure Patterns for AI Parsing
### Heading Hierarchy
```markdown
# Document Title (H1 - once per doc)
## Major Section (H2 - main topics)
### Subsection (H3 - details)
```
**Rules**: Never skip levels | Use as section markers | Keep concise (≤8 words)
### Lists for Enumeration
**Unordered** (sets, features): `- Item`
**Ordered** (steps, sequences): `1. Step`
**Checklists** (requirements): `- [ ] Task`
### Tables for Comparison
Use tables when comparing ≥3 items across ≥2 dimensions:

| Pattern | Use Case | Complexity | Source |
|---------|----------|------------|--------|
| Builder | Complex objects | Medium | `src/builders/` |
| Factory | Simple objects | Low | `src/factories/` |
### Code Blocks
**Always specify language**: ` ```python ` (GOOD) vs ` ``` ` (BAD)

## ≤400 Line Strategies
### Chunking Strategy
**Target**: One focused topic per file | **Max**: 400 lines (excludes code) | **Tool**: `validate-docs.py`
**Techniques**:
1. **Split by concept**: One pattern/API/guide per file
2. **Extract to separate files**: Related topics → new file + link
3. **Use appendices**: Optional/advanced → separate file
```
# Instead of 800-line file: agent-patterns.md
# Create 3 linked files:
agent-builder-pattern.md (150 lines)
agent-factory-pattern.md (130 lines)
agent-registry-pattern.md (120 lines)
```
### Linking Strategy
**Aggressive cross-referencing** enables lazy loading:
```markdown
See [LangChain Agent Patterns](./agents/langchain-agent-patterns.md) for implementation details.
Config details: [Configuration Patterns](./development/configuration-patterns.md)
Source: `src/agents/builder.py:45-67`
```
**Rules**: Relative paths for internal docs | Always link to source | Link related patterns
### Prioritization Strategy
**Order by importance**:
1. **Essential** (30%): Core patterns, most-used APIs
2. **Common** (40%): Frequent scenarios, standard usage
3. **Advanced** (30%): Edge cases, optimization
**Mark optional**:
```markdown
### Advanced: Custom Validators (Optional)
```

## Information Density Techniques
### Code-First Approach
**Pattern**: Code → Brief explanation (≤2 sentences)
```python
def process_document(doc: str) -> dict:
    return {"chunks": doc.split("\n\n"), "metadata": {}}
# Splits document into chunks and returns structured data.
```
### Bullet Lists Over Paragraphs
```markdown
<!-- GOOD: High density -->
**Features**: Async processing | Retry logic (3x) | Error aggregation | Source tracking
<!-- BAD: Low density -->
This component provides several key features. First, it supports async...
```
### Tables for Structured Comparison
```markdown
| Component | Purpose | Lines | Tests |
|-----------|---------|-------|-------|
| Parser | Extract entities | 120 | 45 |
| Validator | Check schemas | 80 | 30 |
```
### Minimal Context Prose
**Rule**: ≤2 sentences per section intro
```markdown
## Error Handling
All services implement retry logic with exponential backoff. Errors logged.
### Implementation...
```

## Code Example Best Practices
### Size Limits
- **Max 20 lines** per example
- Split larger code across multiple examples
- Focus on specific concept
### Complete & Runnable
**GOOD**:
```python
from codemie.agents import AgentBuilder
agent = AgentBuilder({"name": "analyst", "tools": ["search"]}).build()
result = agent.run("Analyze report")
```
**BAD** (incomplete):
```python
agent.run("Analyze report")  # Where does agent come from?
```
### Always Link to Source
**Required**: `Source: src/agents/builder.py:45-67`
**External**: `Source: LangChain [AgentExecutor](https://url)`

## Inclusion/Exclusion Decisions
### INCLUDE
- ✅ **Patterns**: Implementation, anti-patterns, when to use
- ✅ **Examples**: ≥3 concrete use cases with code
- ✅ **Source references**: File paths, line numbers
- ✅ **Structure**: Tables, lists, code blocks
- ✅ **Decision rationale**: Why this pattern/approach
### EXCLUDE
- ❌ **History**: How code evolved (unless decision insight)
- ❌ **Verbose explanations**: >3 sentences without code
- ❌ **Duplicate content**: Repeat of other docs (link instead)
- ❌ **Trivial details**: Obvious from code
- ❌ **Personal opinions**: "I think", "probably", "maybe"
### Decision Framework
**Ask**: Does this...
1. Help AI understand implementation?
2. Show concrete pattern with example?
3. Add unique information not elsewhere?
4. Fit within line budget?
**Rule**: 3+ YES → Include | 2+ NO → Exclude or link

## Before/After Transformation Examples
### Example 1: Verbose Prose → Pattern-First
**BEFORE** (narrative, ~80 lines):
```markdown
# Understanding the Agent System
The codemie agent system is designed to provide a flexible framework...
[60+ lines of prose explanation]
```
**AFTER** (pattern-first, ~30 lines):
```markdown
# Agent Builder Pattern
## Implementation
```python
agent = AgentBuilder({"name": "analyst", "tools": [SearchTool()]}).build()
```
## Key Components
1. **Config dict**: Agent name, tools, LLM settings
2. **Builder**: Validates config, instantiates agent
Source: `src/agents/builder.py:12-45`
```
### Example 2: Narrative Guide → Structured Steps
**BEFORE** (conversational, ~100 lines): Prose explanations of agent setup process
**AFTER** (structured, ~40 lines):
```markdown
# How to Create an Agent
## Steps
### 1. Define Configuration
```python
config = {"name": "analyst", "tools": ["search"], "llm_provider": "openai"}
```
### 2. Build Agent
```python
agent = AgentBuilder(config).build()
```
### 3. Execute
```python
result = agent.run("Analyze Q1")
```
Source: `src/agents/builder.py`
```
### Example 3: Monolithic Doc → Chunked with Links
**BEFORE**: Single 400-line `agent-documentation.md`
**AFTER**: 6 linked files (≤150 lines each):
```
agents/README.md (hub, 80 lines)
├── agent-builder-pattern.md (120)
├── agent-factory-pattern.md (100)
├── agent-registry-pattern.md (110)
├── agent-config-reference.md (130)
└── agent-examples.md (90)
```
**Hub navigation**:
```markdown
# Agent System
- [LangChain Agent Patterns](./agents/langchain-agent-patterns.md) - Agent implementation
- [Agent Tools](./agents/agent-tools.md) - Tool integration
- [Custom Tool Creation](./agents/custom-tool-creation.md) - Building new tools
```
## Verification

### Validate Your Documentation

Run the validation script to check compliance:

```bash
python3 .codemie/guides/validate-docs.py
```

**Expected output**: 
- ✅ Line count validation passed
- ✅ Link validation passed
- ✅ Template compliance passed

### Manual Checklist

- [ ] Document is ≤400 lines
- [ ] All code examples have source references
- [ ] All internal links are working
- [ ] Examples are from actual codebase
- [ ] Follows pattern > prose approach

---

## Troubleshooting

### Issue: Validation script reports broken links

**Symptoms**: Script shows "Broken link" errors
**Cause**: Referenced files don't exist or path is incorrect
**Solution**: 
1. Verify the file exists at the referenced path
2. Update link to correct relative path
3. Or create the missing documentation file

### Issue: Document exceeds 400 lines

**Symptoms**: Validation reports line count warning
**Cause**: Too much content in single file
**Solution**:
1. Split into multiple focused documents
2. Extract examples into separate files
3. Remove redundant explanations
4. Use more concise formatting (tables vs paragraphs)

### Issue: Template compliance warnings

**Symptoms**: Missing required sections warning
**Cause**: Document doesn't follow template structure
**Solution**:
1. Check which template applies (guide/pattern/reference)
2. Add missing sections (Quick Summary, Prerequisites, etc.)
3. Or document is meta-documentation (like this file) - compliance optional

---

## References
**Successful AI-first patterns**: LangChain docs (pattern-first) | FastAPI docs (example-driven) | Anthropic Claude docs (code + brief context)
**Tools**: `validate-docs.py` (enforces ≤400 lines) | Templates: `templates/*.md`
**Related**: [Project Structure](./architecture/project-structure.md) | [Layered Architecture](./architecture/layered-architecture.md)
<!-- Version: 1.0 -->
