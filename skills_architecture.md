# Skills Architecture - Comprehensive Report

## Executive Summary

This document analyzes the Skills system implementation in the Claude Code project to inform the development of similar functionality for a LangGraph-based AI agents platform. The Skills system enables modular, reusable knowledge injection into agents through markdown files that agents can load on-demand.

**Key Finding**: Skills are implemented as a **specialized tool** (not as prompt replacements or base prompt modifications) that agents can invoke to inject domain-specific instructions into their context when needed.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [File Format and Structure](#2-file-format-and-structure)
3. [Discovery and Loading](#3-discovery-and-loading)
4. [The Skill Tool Implementation](#4-the-skill-tool-implementation)
5. [Integration with Agent System](#5-integration-with-agent-system)
6. [Data Flow Architecture](#6-data-flow-architecture)
7. [Permission System](#7-permission-system)
8. [Configuration and Extensibility](#8-configuration-and-extensibility)
9. [Implementation Recommendations](#9-implementation-recommendations-for-langgraph-platform)
10. [Code Examples](#10-code-examples)
11. [Conclusion](#11-conclusion)

---

## 1. System Overview

### 1.1 What Are Skills?

Skills are **modular knowledge units** stored as markdown files that contain:
- Domain-specific instructions
- Best practices and patterns
- API documentation
- Code examples
- File references

### 1.2 Core Design Principles

```
┌─────────────────────────────────────────────────────────────┐
│                    SKILLS ARCHITECTURE                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. SEPARATION OF CONCERNS                                   │
│     Skills ≠ System Prompt                                   │
│     Skills ≠ Tool Definitions                                │
│     Skills = On-demand Context Injection                     │
│                                                              │
│  2. AGENT AUTONOMY                                           │
│     Agent decides WHEN to load skills                        │
│     Not pre-loaded into every conversation                   │
│                                                              │
│  3. DISCOVERABILITY                                          │
│     Skill tool description lists available skills            │
│     Agent can see what's available before loading            │
│                                                              │
│  4. HIERARCHICAL ORGANIZATION                                │
│     Global (~/.claude/skills/)                               │
│     Project-level (.opencode/skill/)                         │
│     Custom paths (configurable)                              │
│                                                              │
│  5. PERMISSION-AWARE                                         │
│     Skills filtered by agent permissions                     │
│     Skill directories auto-whitelisted for file access       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 1.3 Why Not Alternatives?

| Approach | Why Not Used |
|----------|--------------|
| **Inject into base prompt** | Would bloat every conversation; Skills can be large (multiple KB); Most skills not needed for most tasks |
| **Replace system prompt** | System prompt contains critical operational instructions; Skills are additive, not replacements |
| **Pre-load all skills** | Wastes context window; Increases cost; Slower inference; Most skills unused in given conversation |
| **Hard-code in agent** | Not extensible; Requires code changes; Can't be shared/versioned independently |

**Chosen Approach**: Skills as a specialized tool that agents invoke contextually.

---

## 2. File Format and Structure

### 2.1 Skill Definition Format

Skills use **YAML frontmatter + Markdown** format:

```yaml
---
name: bun-file-io
description: Use this when you are working on file operations like reading, writing, scanning, or deleting files in the Bun runtime.
---

# Skill: Bun File I/O

## Use this when
- Editing file I/O or scans in `packages/opencode`
- Handling directory operations or external tools
- Working with Bun-specific APIs

## Bun file APIs (from Bun docs)
- `Bun.file(path)` is lazy; call `text`, `json`, `stream`, `arrayBuffer`, `bytes`, `exists` to read.
- Use `Bun.write(path, data)` for atomic writes
- `new Bun.Glob(pattern)` for file scanning

## When to use node:fs
- Use `node:fs/promises` for directories: `mkdir`, `readdir`, `rm`, `rmdir`, etc.
- Bun's file APIs don't cover all directory operations

## Quick checklist
- Use Bun APIs first for file operations
- Fall back to node:fs for directory operations
- Prefer async/await over callbacks
```

### 2.2 File System Organization

```
Project Structure:
.
├── .opencode/                    # Project-level (highest priority)
│   └── skill/
│       ├── bun-file-io/
│       │   ├── SKILL.md         # Required: Skill definition
│       │   ├── examples/        # Optional: Supporting files
│       │   └── reference/       # Optional: Documentation
│       └── another-skill/
│           └── SKILL.md
│
├── .claude/                      # Alternative project-level
│   └── skills/
│       └── my-skill/
│           └── SKILL.md
│
└── ~/.claude/                    # Global user-level
    └── skills/
        └── shared-skill/
            └── SKILL.md

Custom Paths (via config):
/absolute/path/to/skills/
~/my-custom-skills/
./relative/path/
```

### 2.3 Data Structure

**Parsed Skill Object**:
```typescript
interface Skill.Info {
  name: string;           // Unique identifier from YAML
  description: string;    // What the skill is for (from YAML)
  location: string;       // Absolute path to SKILL.md
  content: string;        // Parsed markdown body (no frontmatter)
}
```

**Validation**:
- Uses Zod schema for frontmatter validation
- Required fields: `name`, `description`
- Optional: Custom metadata fields (ignored)
- Invalid YAML falls back to sanitization
- Duplicate names logged as warnings

---

## 3. Discovery and Loading

### 3.1 Discovery Process

**File**: `/packages/opencode/src/skill/skill.ts`

**Discovery Algorithm**:
```typescript
async function discover(): Promise<Skill.Info[]> {
  const skills: Skill.Info[] = []
  const seenNames = new Set<string>()

  // 1. Scan project-level (.opencode/skill/)
  for (const skillPath of glob('.opencode/{skill,skills}/**/SKILL.md')) {
    const parsed = await parseSkill(skillPath)
    if (seenNames.has(parsed.name)) {
      console.warn(`Duplicate skill: ${parsed.name}`)
    } else {
      skills.push(parsed)
      seenNames.add(parsed.name)
    }
  }

  // 2. Scan project-level (.claude/skills/)
  if (!process.env.OPENCODE_DISABLE_CLAUDE_CODE_SKILLS) {
    for (const skillPath of glob('.claude/skills/**/SKILL.md')) {
      const parsed = await parseSkill(skillPath)
      if (!seenNames.has(parsed.name)) {
        skills.push(parsed)
        seenNames.add(parsed.name)
      }
    }
  }

  // 3. Scan global (~/.claude/skills/)
  for (const skillPath of glob('~/.claude/skills/**/SKILL.md')) {
    const parsed = await parseSkill(skillPath)
    if (!seenNames.has(parsed.name)) {
      skills.push(parsed)
      seenNames.add(parsed.name)
    }
  }

  // 4. Scan custom paths from config
  for (const customPath of config.skills?.paths || []) {
    for (const skillPath of glob(`${customPath}/**/SKILL.md`)) {
      const parsed = await parseSkill(skillPath)
      if (!seenNames.has(parsed.name)) {
        skills.push(parsed)
        seenNames.add(parsed.name)
      }
    }
  }

  return skills
}
```

**Implementation Details**:
- Uses `Bun.Glob` for efficient filesystem scanning
- Glob pattern: `{skill,skills}/**/SKILL.md` (supports nested directories)
- Scans are performed once at startup and cached
- Priority order: Project (.opencode) > Project (.claude) > Global > Custom

### 3.2 Parsing Process

**Parser Implementation**:
```typescript
import matter from 'gray-matter'
import { z } from 'zod'

const SkillSchema = z.object({
  name: z.string().min(1),
  description: z.string().min(1),
})

async function parseSkill(filePath: string): Promise<Skill.Info> {
  const content = await Bun.file(filePath).text()

  try {
    // Parse YAML frontmatter
    const { data, content: body } = matter(content)

    // Validate schema
    const validated = SkillSchema.parse(data)

    return {
      name: validated.name,
      description: validated.description,
      location: path.resolve(filePath),
      content: body.trim(),
    }
  } catch (error) {
    // Fallback for invalid YAML
    const sanitized = fallbackSanitization(content)
    throw new Error(`Failed to parse skill at ${filePath}: ${error}`)
  }
}
```

### 3.3 Caching Strategy

**Caching Approach**:
```
Startup
  ↓
Skill.state() called
  ↓
Discovery runs once
  ↓
Results cached in memory
  ↓
All future Skill.all() calls return cached data
  ↓
Cache valid for instance lifetime
```

**Rationale**:
- Skills don't change during runtime
- Filesystem scanning is expensive
- Reduces latency for skill lookups
- No need for invalidation (process restart required for new skills)

---

## 4. The Skill Tool Implementation

### 4.1 Tool Architecture

**Critical Insight**: Skills are exposed to agents as a **specialized tool**, not through prompt modification.

```
┌────────────────────────────────────────────────────────┐
│                    TOOL REGISTRY                        │
├────────────────────────────────────────────────────────┤
│  - BashTool                                             │
│  - ReadTool                                             │
│  - WriteTool                                            │
│  - EditTool                                             │
│  - GlobTool                                             │
│  - GrepTool                                             │
│  - WebFetchTool                                         │
│  - SkillTool  ← Skills implemented as a tool            │
│  - ... other tools                                      │
│  - MCP Tools (if configured)                            │
└────────────────────────────────────────────────────────┘
         ↓
    Passed to LLM as available tools
         ↓
    Agent decides when to invoke SkillTool
         ↓
    Skill content injected into conversation context
```

### 4.2 SkillTool Definition

**File**: `/packages/opencode/src/tool/skill.ts`

```typescript
export const SkillTool = Tool.define({
  id: "skill",
  description: "Execute a skill within the main conversation",

  parameters: {
    skill: {
      type: "string",
      description: "The skill name. E.g., 'commit', 'review-pr', or 'pdf'",
      required: true,
    }
  },

  init: async ({ agent }) => {
    // Load all available skills
    const allSkills = await Skill.all()

    // Filter by agent permissions
    const accessibleSkills = agent
      ? allSkills.filter((skill) => {
          const rule = PermissionNext.evaluate("skill", skill.name, agent.permission)
          return rule.action !== "deny"
        })
      : allSkills

    // Generate rich description with available skills
    const description = [
      "Execute a skill within the main conversation",
      "",
      "When users ask you to perform tasks, check if any of the available skills match.",
      "Skills provide specialized capabilities and domain knowledge.",
      "",
      "Available skills:",
      "",
      "<available_skills>",
      ...accessibleSkills.map(s =>
        `<skill>\n<name>${s.name}</name>\n<description>${s.description}</description>\n<location>${pathToFileURL(s.location)}</location>\n</skill>`
      ),
      "</available_skills>",
      "",
      "How to invoke:",
      "- Use this tool with the skill name",
      "- Example: skill: 'pdf'",
      "- Example: skill: 'commit'",
    ].join("\n")

    return { description }
  },

  execute: async (ctx, { skill }) => {
    // 1. Permission check
    await ctx.ask({
      permission: "skill",
      patterns: [skill],
      always: [skill],  // Always ask for explicit permission
      metadata: {},
    })

    // 2. Load skill
    const skillInfo = await Skill.get(skill)
    if (!skillInfo) {
      throw new Error(`Skill "${skill}" not found`)
    }

    // 3. Discover supporting files
    const skillDir = path.dirname(skillInfo.location)
    const supportingFiles = await Ripgrep.files({
      path: skillDir,
      limit: 10,
      exclude: ["SKILL.md"]
    })

    // 4. Format output
    const output = [
      `<skill_content name="${skill}">`,
      `# Skill: ${skill}`,
      "",
      skillInfo.content,
      "",
      `Base directory for this skill: ${pathToFileURL(skillDir)}`,
      "Relative paths in this skill (e.g., scripts/, reference/) are relative to this base directory.",
      "",
      "<skill_files>",
      ...supportingFiles.map(f => pathToFileURL(path.join(skillDir, f))),
      "</skill_files>",
      "</skill_content>",
    ].join("\n")

    // 5. Return with metadata
    return {
      output,
      metadata: {
        name: skill,
        dir: skillDir,
      }
    }
  },
})
```

### 4.3 Tool Output Format

**What the Agent Sees**:
```xml
<skill_content name="bun-file-io">
# Skill: bun-file-io

## Use this when
- Editing file I/O or scans in `packages/opencode`
- Handling directory operations or external tools

## Bun file APIs (from Bun docs)
- `Bun.file(path)` is lazy; call `text`, `json`, `stream`, `arrayBuffer`, `bytes`, `exists` to read.
...

Base directory for this skill: file:///Users/.../opencode/.opencode/skill/bun-file-io
Relative paths in this skill (e.g., scripts/, reference/) are relative to this base directory.

<skill_files>
file:///Users/.../opencode/.opencode/skill/bun-file-io/examples/basic.ts
file:///Users/.../opencode/.opencode/skill/bun-file-io/reference/api.md
</skill_files>
</skill_content>
```

**Key Features**:
- XML structure for clear parsing
- Full markdown content preserved
- Base directory with file:// URL
- Supporting files listed for reference
- Agent can read referenced files using ReadTool

---

## 5. Integration with Agent System

### 5.1 Agent Initialization

**File**: `/packages/opencode/src/agent/agent.ts`

**Agent Permission Setup**:
```typescript
async function initializeAgent(agentConfig: AgentConfig): Promise<Agent> {
  // Get all skill directories
  const skillDirs = await Skill.dirs()

  // Create default permissions
  const defaults = PermissionNext.fromConfig({
    "*": "allow",              // Allow most operations
    doom_loop: "ask",          // Prevent infinite loops

    // External directory permissions
    external_directory: {
      "*": "ask",              // Ask for external directories
      ...Object.fromEntries(
        skillDirs.map((dir) => [
          path.join(dir, "*"),
          "allow"               // Auto-allow skill directories
        ])
      ),
    },

    // File read permissions
    read: {
      "*": "allow",
      "*.env": "ask",          // Ask for sensitive files
      "*.env.*": "ask",
    },

    // Other permissions...
  })

  return {
    name: agentConfig.name,
    permission: PermissionNext.merge(defaults, agentConfig.permissions),
    tools: ToolRegistry.tools(),
  }
}
```

**Why Skill Directories Are Whitelisted**:
- Skills may reference supporting files (scripts, examples, docs)
- Agent needs to read these files without permission prompts
- Improves UX (no repeated permission requests)
- Skill directories are trusted (under project/user control)

### 5.2 System Prompt Construction

**File**: `/packages/opencode/src/session/prompt.ts`

**Prompt Structure**:
```typescript
async function buildPrompt(agent: Agent, model: Model): Promise<Prompt> {
  // 1. System prompt (operational instructions)
  const systemMessages = [
    ...(await SystemPrompt.environment(model)),  // Model info, env vars
    ...(await InstructionPrompt.system()),       // Additional instructions
  ]

  // 2. Tools (including SkillTool)
  const tools = await resolveTools(agent, model)

  // 3. Return structured prompt
  return {
    system: systemMessages,
    tools: tools,
    // Skills NOT included in system prompt
    // Skills loaded on-demand via SkillTool
  }
}

async function resolveTools(agent: Agent, model: Model): Promise<Tool[]> {
  const allTools = ToolRegistry.tools()

  return Promise.all(
    allTools.map(async (tool) => {
      // Initialize with agent context
      const initialized = await tool.init({ agent })

      // Wrap execution with hooks and permissions
      return {
        id: tool.id,
        description: initialized.description,
        parameters: ProviderTransform.schema(tool.parameters),
        execute: async (ctx, params) => {
          // Permission check
          await ctx.ask(...)

          // Execute
          return tool.execute(ctx, params)
        },
      }
    })
  )
}
```

**Key Points**:
- System prompt is separate from tools
- SkillTool description includes list of available skills
- Tools passed to LLM as structured tool definitions
- Agent autonomously decides when to invoke SkillTool

### 5.3 Agent Decision Flow

```
User Request: "Help me optimize file operations"
    ↓
Agent receives:
  - System prompt (operational instructions)
  - Available tools (including SkillTool)
  - SkillTool description lists "bun-file-io" skill
    ↓
Agent reasoning:
  "The task is about file operations.
   I see a skill called 'bun-file-io' that describes
   file operations. Let me load that skill."
    ↓
Agent invokes: SkillTool { skill: "bun-file-io" }
    ↓
System:
  - Checks permissions
  - Loads skill content
  - Injects into conversation
    ↓
Agent sees:
  - Skill instructions about Bun file APIs
  - Best practices for file operations
  - Examples and patterns
    ↓
Agent applies skill knowledge to solve the task
```

---

## 6. Data Flow Architecture

### 6.1 Complete Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 1: STARTUP & DISCOVERY                                    │
└─────────────────────────────────────────────────────────────────┘
                              │
    1. Application Starts     │
           ↓                  │
    2. Skill.state() called   │
           ↓                  │
    3. Filesystem scan        │
       - .opencode/skill/     │
       - .claude/skills/      │
       - ~/.claude/skills/    │
       - Custom paths         │
           ↓                  │
    4. Parse SKILL.md files   │
       (YAML + Markdown)      │
           ↓                  │
    5. Validate with Zod      │
           ↓                  │
    6. Cache in memory        │
           ↓                  │
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 2: AGENT INITIALIZATION                                   │
└─────────────────────────────────────────────────────────────────┘
                              │
    7. Agent created          │
           ↓                  │
    8. Load skill directories │
           ↓                  │
    9. Configure permissions  │
       (whitelist skill dirs) │
           ↓                  │
   10. Initialize tools       │
           ↓                  │
   11. SkillTool.init() called│
           ↓                  │
   12. Filter skills by perms │
           ↓                  │
   13. Generate description   │
       with available skills  │
           ↓                  │
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 3: CONVERSATION START                                     │
└─────────────────────────────────────────────────────────────────┘
                              │
   14. User sends message     │
           ↓                  │
   15. Build prompt:          │
       - System messages      │
       - Tool definitions     │
           ↓                  │
   16. Send to LLM            │
           ↓                  │
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 4: SKILL INVOCATION (if agent decides)                    │
└─────────────────────────────────────────────────────────────────┘
                              │
   17. Agent sees SkillTool   │
       in available tools     │
           ↓                  │
   18. Agent reads description│
       listing skills         │
           ↓                  │
   19. Agent decides to invoke│
       SkillTool              │
           ↓                  │
   20. Permission check       │
       ctx.ask()              │
           ↓                  │
   21. Load skill from cache  │
           ↓                  │
   22. Scan for supporting    │
       files in skill dir     │
           ↓                  │
   23. Format as XML output   │
           ↓                  │
   24. Return to agent        │
           ↓                  │
   25. Skill content injected │
       into conversation      │
           ↓                  │
   26. Agent applies skill    │
       knowledge to task      │
           ↓                  │
```

### 6.2 Interaction with Other Tools

```
┌─────────────────────────────────────────────────────────────────┐
│                     TOOL ECOSYSTEM                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  SkillTool                                                       │
│    ↓                                                             │
│  Injects instructions                                            │
│    ↓                                                             │
│  Agent follows instructions                                      │
│    ↓                                                             │
│  May use other tools mentioned in skill:                         │
│    - ReadTool (read referenced files)                            │
│    - WriteTool (create files per instructions)                   │
│    - BashTool (run commands as specified)                        │
│    - GrepTool (search patterns mentioned)                        │
│    - Custom tools (skill may reference)                          │
│                                                                  │
│  Skills coordinate with MCP tools:                               │
│    - MCP tools loaded separately                                 │
│    - Same permission model                                       │
│    - Skills can reference MCP tools                              │
│    - No namespace conflicts                                      │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**Example Workflow**:
```
1. User: "Optimize file operations in this project"
2. Agent invokes SkillTool("bun-file-io")
3. Skill content loaded with instruction: "Use Bun.file() instead of fs.readFile()"
4. Agent uses GrepTool to find fs.readFile() calls
5. Agent uses ReadTool to read files
6. Agent uses EditTool to replace with Bun.file()
7. Agent reports completion with skill-guided changes
```

---

## 7. Permission System

### 7.1 Permission Model

**Three-Level Permission System**:

```typescript
type PermissionAction = "allow" | "ask" | "deny"

interface PermissionRule {
  action: PermissionAction
  patterns: string[]
}

interface AgentPermissions {
  skill: {
    [skillName: string]: PermissionAction
  },
  external_directory: {
    [path: string]: PermissionAction
  },
  read: {
    [pattern: string]: PermissionAction
  },
  // ... other permissions
}
```

### 7.2 Skill Permission Evaluation

**At Tool Initialization** (`SkillTool.init()`):
```typescript
const accessibleSkills = agent
  ? skills.filter((skill) => {
      const rule = PermissionNext.evaluate("skill", skill.name, agent.permission)
      return rule.action !== "deny"
    })
  : skills
```

**At Execution** (`SkillTool.execute()`):
```typescript
await ctx.ask({
  permission: "skill",
  patterns: [skillName],
  always: [skillName],  // Always prompt user (not silently allowed)
  metadata: {},
})
```

### 7.3 Automatic Skill Directory Whitelisting

**Why It's Needed**:
- Skills reference supporting files (scripts, examples, docs)
- Agent must read these without permission interruptions
- Improves UX

**Implementation**:
```typescript
const skillDirs = await Skill.dirs()  // Returns all skill directories

const permissions = {
  external_directory: {
    "*": "ask",  // Default: ask for external directories
    ...Object.fromEntries(
      skillDirs.map((dir) => [
        path.join(dir, "*"),  // Whitelist pattern
        "allow"               // Auto-allow
      ])
    ),
  }
}
```

**Result**:
```javascript
{
  external_directory: {
    "*": "ask",
    "/Users/.../opencode/.opencode/skill/*": "allow",
    "/Users/.../.claude/skills/*": "allow",
  }
}
```

---

## 8. Configuration and Extensibility

### 8.1 Configuration Schema

**File**: `/packages/opencode/src/config/config.ts`

```typescript
// Skills configuration
export const Skills = z.object({
  paths: z.array(z.string()).optional()
    .describe("Additional paths to skill folders")
})

// Main config
export const Config = z.object({
  // ... other config
  skills: Skills.optional()
    .describe("Additional skill folder paths")
})
```

**Example Configuration** (`opencode.json`):
```json
{
  "skills": {
    "paths": [
      "~/my-team-skills",
      "./project-specific-skills",
      "/shared/skills/library"
    ]
  }
}
```

### 8.2 Skill Sharing and Distribution

**Approaches**:

1. **Git Submodules**:
```bash
git submodule add https://github.com/team/shared-skills .opencode/skill/shared
```

2. **NPM Packages**:
```json
{
  "scripts": {
    "postinstall": "cp -r node_modules/@team/skills .opencode/skill/"
  }
}
```

3. **Symlinks**:
```bash
ln -s ~/shared-skills ~/.claude/skills/team-skills
```

4. **Configuration Reference**:
```json
{
  "skills": {
    "paths": [
      "~/shared-skills",
      "/mnt/team-skills"
    ]
  }
}
```

### 8.3 Extensibility Points

**Custom Skill Parsers**:
- Current: YAML frontmatter + Markdown
- Extensible to: JSON, TOML, custom DSL
- Parser interface:
```typescript
interface SkillParser {
  parse(content: string): Skill.Info
  validate(data: unknown): boolean
}
```

**Custom Skill Loaders**:
- Current: Filesystem-based
- Extensible to: Database, API, Git, S3
- Loader interface:
```typescript
interface SkillLoader {
  discover(): Promise<SkillSource[]>
  load(source: SkillSource): Promise<Skill.Info>
}
```

**Skill Metadata Extensions**:
- Current: name, description
- Extensible to: tags, version, author, dependencies
```yaml
---
name: my-skill
description: Does something
version: 1.2.0
author: team@example.com
tags: [ai, automation]
requires: [other-skill]
---
```

---

## 9. Implementation Recommendations for LangGraph Platform

### 9.1 Architecture Recommendations

Based on the analysis, here's how to implement Skills in your LangGraph platform:

#### **Recommendation 1: Implement Skills as a Tool (Not Prompt Modification)**

```
✅ DO: Create a SkillTool that agents can invoke
❌ DON'T: Inject skills into base prompt
❌ DON'T: Replace system prompt with skills
❌ DON'T: Pre-load all skills into context
```

**Rationale**:
- Preserves context window for actual work
- Gives agents autonomy to load skills when needed
- Enables better token efficiency
- Allows skill-specific permission checks

#### **Recommendation 2: Use YAML Frontmatter + Markdown Format**

```markdown
---
name: skill-name
description: When to use this skill
tags: [tag1, tag2]
version: 1.0.0
---

# Skill Content

Your markdown instructions here...
```

**Rationale**:
- Human-readable and editable
- Git-friendly (easy diffs, versioning)
- Supports rich formatting (code blocks, lists, tables)
- Standard format (gray-matter parser)

#### **Recommendation 3: Hierarchical Skill Discovery**

```
Priority Order:
1. Project-level (.agents/skills/)
2. User-level (~/.ai-platform/skills/)
3. Team-level (custom paths in config)
4. Marketplace (optional: remote skill registry)
```

**Rationale**:
- Projects can override team/global skills
- Users can have personal skill libraries
- Teams can share common skills
- Future: marketplace for community skills

### 9.2 LangGraph-Specific Implementation

#### **Integration with LangGraph Nodes**

```python
from langgraph.graph import Graph, Node
from typing import Dict, Any

class SkillNode(Node):
    """LangGraph node that loads and applies skills"""

    def __init__(self, skill_registry: SkillRegistry):
        self.skills = skill_registry

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        # Agent decided to use a skill
        skill_name = state.get("requested_skill")

        if not skill_name:
            return state

        # Load skill
        skill = await self.skills.get(skill_name)

        # Inject into state
        state["context"].append({
            "role": "system",
            "content": f"<skill>{skill.content}</skill>"
        })

        return state

# Graph definition
workflow = Graph()
workflow.add_node("decide_skill", agent_node)
workflow.add_node("load_skill", SkillNode(skill_registry))
workflow.add_node("execute_task", agent_node)
workflow.add_edge("decide_skill", "load_skill", condition=lambda s: s.get("requested_skill"))
workflow.add_edge("load_skill", "execute_task")
```

#### **Skill Discovery in LangGraph**

```python
class SkillRegistry:
    """Manages skill discovery and loading"""

    def __init__(self, paths: List[str]):
        self.paths = paths
        self._cache: Dict[str, Skill] = {}

    async def discover(self) -> List[Skill]:
        """Scan filesystem for SKILL.md files"""
        skills = []

        for base_path in self.paths:
            pattern = os.path.join(base_path, "**/SKILL.md")
            for skill_path in glob.glob(pattern, recursive=True):
                try:
                    skill = await self.parse_skill(skill_path)
                    skills.append(skill)
                    self._cache[skill.name] = skill
                except Exception as e:
                    logger.error(f"Failed to parse skill at {skill_path}: {e}")

        return skills

    async def parse_skill(self, path: str) -> Skill:
        """Parse YAML frontmatter + markdown"""
        with open(path, 'r') as f:
            content = f.read()

        parsed = frontmatter.loads(content)

        return Skill(
            name=parsed['name'],
            description=parsed['description'],
            content=parsed.content,
            location=path,
            metadata=parsed.metadata
        )

    async def get(self, name: str) -> Optional[Skill]:
        """Get skill by name from cache"""
        return self._cache.get(name)

    def list_for_agent(self, agent_id: str) -> List[Skill]:
        """List skills accessible to agent (filtered by permissions)"""
        # Apply permission filtering
        return [
            skill for skill in self._cache.values()
            if self._check_permission(agent_id, skill.name)
        ]
```

#### **Tool Definition for LangGraph**

```python
from langchain.tools import Tool
from pydantic import BaseModel, Field

class SkillInput(BaseModel):
    skill: str = Field(description="The name of the skill to load")

class SkillTool(Tool):
    """Tool that loads skills into agent context"""

    name = "skill"
    description = """
    Load a skill to get specialized instructions and knowledge.

    Available skills:
    {skill_list}

    Use this tool when you need domain-specific guidance.
    """
    args_schema = SkillInput

    def __init__(self, registry: SkillRegistry, agent_id: str):
        super().__init__()
        self.registry = registry
        self.agent_id = agent_id

        # Update description with available skills
        skills = self.registry.list_for_agent(agent_id)
        skill_list = "\n".join([
            f"- {s.name}: {s.description}" for s in skills
        ])
        self.description = self.description.format(skill_list=skill_list)

    async def _arun(self, skill: str) -> str:
        """Load skill asynchronously"""
        skill_obj = await self.registry.get(skill)

        if not skill_obj:
            return f"Error: Skill '{skill}' not found"

        # Check permissions
        if not self._check_permission(skill):
            return f"Error: Permission denied for skill '{skill}'"

        # Return formatted skill content
        return f"""
<skill_content name="{skill}">
# Skill: {skill}

{skill_obj.content}

Base directory: file://{os.path.dirname(skill_obj.location)}
</skill_content>
"""

    def _run(self, skill: str) -> str:
        """Sync wrapper"""
        import asyncio
        return asyncio.run(self._arun(skill))
```

### 9.3 Database Schema for Web Platform

Since your platform is web-based, you'll likely store skills in a database:

```sql
-- Skills table
CREATE TABLE skills (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) UNIQUE NOT NULL,
    description TEXT NOT NULL,
    content TEXT NOT NULL,  -- Markdown content
    metadata JSONB,         -- Version, tags, etc.
    author_id UUID REFERENCES users(id),
    visibility VARCHAR(50) DEFAULT 'private',  -- private, team, public
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Skill files (supporting files)
CREATE TABLE skill_files (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    skill_id UUID REFERENCES skills(id) ON DELETE CASCADE,
    path VARCHAR(255) NOT NULL,  -- relative path
    content BYTEA NOT NULL,
    mime_type VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Agent-skill associations
CREATE TABLE agent_skills (
    agent_id UUID REFERENCES agents(id) ON DELETE CASCADE,
    skill_id UUID REFERENCES skills(id) ON DELETE CASCADE,
    enabled BOOLEAN DEFAULT true,
    PRIMARY KEY (agent_id, skill_id)
);

-- Skill permissions
CREATE TABLE skill_permissions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    skill_id UUID REFERENCES skills(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id),
    team_id UUID REFERENCES teams(id),
    permission VARCHAR(50) NOT NULL,  -- read, write, execute
    CHECK (user_id IS NOT NULL OR team_id IS NOT NULL)
);

-- Skill marketplace (optional)
CREATE TABLE skill_marketplace (
    skill_id UUID REFERENCES skills(id),
    downloads INTEGER DEFAULT 0,
    rating DECIMAL(3,2),
    is_featured BOOLEAN DEFAULT false,
    PRIMARY KEY (skill_id)
);

-- Indexes
CREATE INDEX idx_skills_author ON skills(author_id);
CREATE INDEX idx_skills_visibility ON skills(visibility);
CREATE INDEX idx_agent_skills_agent ON agent_skills(agent_id);
CREATE INDEX idx_skill_permissions_skill ON skill_permissions(skill_id);
```

### 9.4 API Endpoints for Skill Management

```typescript
// GET /api/skills - List available skills
app.get('/api/skills', async (req, res) => {
  const { userId, agentId, visibility } = req.query

  const skills = await db.skills.findMany({
    where: {
      OR: [
        { visibility: 'public' },
        { authorId: userId },
        { permissions: { some: { userId } } }
      ]
    },
    include: {
      author: { select: { id: true, name: true } },
      _count: { select: { downloads: true } }
    }
  })

  res.json(skills)
})

// POST /api/skills - Create new skill
app.post('/api/skills', async (req, res) => {
  const { name, description, content, metadata } = req.body
  const userId = req.user.id

  // Validate YAML frontmatter
  const parsed = parseFrontmatter(content)

  const skill = await db.skills.create({
    data: {
      name,
      description,
      content: parsed.content,
      metadata: parsed.data,
      authorId: userId
    }
  })

  res.json(skill)
})

// PUT /api/skills/:id - Update skill
app.put('/api/skills/:id', async (req, res) => {
  const { id } = req.params
  const { content } = req.body

  const skill = await db.skills.update({
    where: { id },
    data: { content, updatedAt: new Date() }
  })

  res.json(skill)
})

// POST /api/agents/:id/skills - Attach skill to agent
app.post('/api/agents/:id/skills', async (req, res) => {
  const { id: agentId } = req.params
  const { skillId } = req.body

  await db.agentSkills.create({
    data: { agentId, skillId, enabled: true }
  })

  res.json({ success: true })
})

// GET /api/agents/:id/skills - Get agent's skills
app.get('/api/agents/:id/skills', async (req, res) => {
  const { id: agentId } = req.params

  const skills = await db.agentSkills.findMany({
    where: { agentId, enabled: true },
    include: { skill: true }
  })

  res.json(skills.map(as => as.skill))
})
```

### 9.5 Frontend Components

```typescript
// SkillEditor.tsx - Skill creation/editing UI
import { useState } from 'react'
import { Monaco } from '@monaco-editor/react'

export function SkillEditor({ skillId }: { skillId?: string }) {
  const [content, setContent] = useState(`---
name: my-skill
description: What this skill does
tags: [tag1, tag2]
---

# Skill Instructions

## Use this when
- Situation 1
- Situation 2

## Instructions
1. Step 1
2. Step 2
`)

  const handleSave = async () => {
    const response = await fetch('/api/skills', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content })
    })
    // Handle response
  }

  return (
    <div>
      <Monaco
        language="markdown"
        value={content}
        onChange={setContent}
        options={{
          minimap: { enabled: false },
          lineNumbers: 'on'
        }}
      />
      <button onClick={handleSave}>Save Skill</button>
    </div>
  )
}

// SkillSelector.tsx - Attach skills to agents
export function SkillSelector({ agentId }: { agentId: string }) {
  const [availableSkills, setAvailableSkills] = useState([])
  const [selectedSkills, setSelectedSkills] = useState([])

  useEffect(() => {
    fetch('/api/skills').then(r => r.json()).then(setAvailableSkills)
    fetch(`/api/agents/${agentId}/skills`).then(r => r.json()).then(setSelectedSkills)
  }, [agentId])

  const handleToggle = async (skillId: string) => {
    await fetch(`/api/agents/${agentId}/skills`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ skillId })
    })
    // Update state
  }

  return (
    <div>
      <h3>Available Skills</h3>
      {availableSkills.map(skill => (
        <label key={skill.id}>
          <input
            type="checkbox"
            checked={selectedSkills.some(s => s.id === skill.id)}
            onChange={() => handleToggle(skill.id)}
          />
          {skill.name} - {skill.description}
        </label>
      ))}
    </div>
  )
}
```

### 9.6 MCP Integration

To support MCP servers in skills (as you mentioned):

```yaml
---
name: database-skill
description: Database operations using MCP tools
mcp_servers:
  - postgres-mcp
  - redis-mcp
---

# Database Skill

This skill uses the following MCP tools:
- `postgres_query` from postgres-mcp
- `redis_get` from redis-mcp

## Instructions

When working with databases:
1. Use `postgres_query` for SQL operations
2. Use `redis_get` for cache lookups
3. Always validate input before queries
```

**Implementation**:
```python
class SkillTool(Tool):
    async def _arun(self, skill: str) -> str:
        skill_obj = await self.registry.get(skill)

        # Check if skill requires MCP servers
        required_mcps = skill_obj.metadata.get('mcp_servers', [])

        # Verify MCP servers are available
        for mcp in required_mcps:
            if mcp not in self.mcp_registry:
                return f"Error: Required MCP server '{mcp}' not available"

        # Return skill with MCP context
        return f"""
<skill_content name="{skill}">
# Skill: {skill}

{skill_obj.content}

Available MCP servers for this skill:
{', '.join(required_mcps)}
</skill_content>
"""
```

---

## 10. Code Examples

### 10.1 Complete Skill Example

**File**: `.agents/skills/api-testing/SKILL.md`

```yaml
---
name: api-testing
description: Use this when testing REST APIs, writing integration tests, or validating API responses
tags: [testing, api, rest, http]
version: 1.2.0
author: team@example.com
mcp_servers: [http-client-mcp]
---

# API Testing Skill

## Use this when
- Writing integration tests for REST APIs
- Validating API responses
- Testing authentication flows
- Debugging API issues

## Testing Framework

We use `pytest` with `requests` library:

```python
import pytest
import requests

@pytest.fixture
def api_client():
    return requests.Session()

def test_api_endpoint(api_client):
    response = api_client.get("https://api.example.com/users")
    assert response.status_code == 200
    assert "users" in response.json()
```

## Best Practices

### 1. Always test status codes
```python
assert response.status_code == 200  # or 201, 404, etc.
```

### 2. Validate response schema
```python
from jsonschema import validate

schema = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"}
    },
    "required": ["id", "name"]
}

validate(instance=response.json(), schema=schema)
```

### 3. Test authentication
```python
# Test with valid token
headers = {"Authorization": f"Bearer {valid_token}"}
response = api_client.get(url, headers=headers)
assert response.status_code == 200

# Test without token
response = api_client.get(url)
assert response.status_code == 401
```

### 4. Use environment variables for config
```python
import os

API_BASE_URL = os.getenv("API_BASE_URL", "https://api.example.com")
API_KEY = os.getenv("API_KEY")
```

## Common Patterns

### Parametrized tests
```python
@pytest.mark.parametrize("endpoint,expected_status", [
    ("/users", 200),
    ("/users/123", 200),
    ("/users/999999", 404),
])
def test_endpoints(api_client, endpoint, expected_status):
    response = api_client.get(f"{API_BASE_URL}{endpoint}")
    assert response.status_code == expected_status
```

### Testing error responses
```python
def test_invalid_input(api_client):
    payload = {"email": "invalid-email"}  # missing @ symbol
    response = api_client.post(f"{API_BASE_URL}/users", json=payload)

    assert response.status_code == 400
    assert "error" in response.json()
    assert "email" in response.json()["error"]
```

## Reference Files

- See `examples/basic_test.py` for a complete example
- See `examples/auth_test.py` for authentication testing
- See `reference/api_schema.json` for API schema definitions

## Quick Checklist

Before submitting tests:
- [ ] All status codes tested (200, 201, 400, 401, 404, 500)
- [ ] Request/response schemas validated
- [ ] Authentication scenarios covered
- [ ] Error cases tested
- [ ] Environment variables used for configuration
- [ ] Tests are idempotent (can run multiple times)
```

**Supporting Files**:
- `.agents/skills/api-testing/examples/basic_test.py`
- `.agents/skills/api-testing/examples/auth_test.py`
- `.agents/skills/api-testing/reference/api_schema.json`

### 10.2 Agent Configuration with Skills

```typescript
// Agent configuration in your platform
interface AgentConfig {
  id: string
  name: string
  systemPrompt: string
  tools: string[]         // Tool IDs from your platform
  skills: string[]        // Skill names to load
  mcpServers: string[]    // MCP server IDs
  permissions: PermissionConfig
}

// Example agent
const testingAgent: AgentConfig = {
  id: "agent-123",
  name: "API Testing Agent",
  systemPrompt: "You are an expert at writing API integration tests.",
  tools: [
    "bash",
    "read",
    "write",
    "skill"  // SkillTool enabled
  ],
  skills: [
    "api-testing",
    "pytest-patterns"
  ],
  mcpServers: [
    "http-client-mcp"
  ],
  permissions: {
    skill: {
      "api-testing": "allow",
      "pytest-patterns": "allow",
      "*": "ask"
    }
  }
}
```

### 10.3 LangGraph Workflow with Skills

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, List

class AgentState(TypedDict):
    messages: List[dict]
    requested_skill: Optional[str]
    skill_content: Optional[str]
    current_task: str

# Define nodes
async def decide_action(state: AgentState) -> AgentState:
    """Agent decides what to do next"""
    # LLM call with available tools (including SkillTool)
    response = await llm.ainvoke(state["messages"])

    # Check if agent wants to use a skill
    if response.tool_calls:
        for tool_call in response.tool_calls:
            if tool_call["name"] == "skill":
                state["requested_skill"] = tool_call["args"]["skill"]
                return state

    return state

async def load_skill(state: AgentState) -> AgentState:
    """Load requested skill"""
    skill_name = state["requested_skill"]
    skill = await skill_registry.get(skill_name)

    # Inject skill into messages
    state["messages"].append({
        "role": "system",
        "content": f"<skill>{skill.content}</skill>"
    })

    state["skill_content"] = skill.content
    state["requested_skill"] = None

    return state

async def execute_task(state: AgentState) -> AgentState:
    """Agent executes task (may use other tools)"""
    response = await llm.ainvoke(state["messages"])

    state["messages"].append({
        "role": "assistant",
        "content": response.content
    })

    return state

def should_load_skill(state: AgentState) -> str:
    """Route to skill loading if requested"""
    if state.get("requested_skill"):
        return "load_skill"
    return "execute_task"

# Build graph
workflow = StateGraph(AgentState)

workflow.add_node("decide_action", decide_action)
workflow.add_node("load_skill", load_skill)
workflow.add_node("execute_task", execute_task)

workflow.set_entry_point("decide_action")
workflow.add_conditional_edges(
    "decide_action",
    should_load_skill,
    {
        "load_skill": "load_skill",
        "execute_task": "execute_task"
    }
)
workflow.add_edge("load_skill", "execute_task")
workflow.add_edge("execute_task", END)

app = workflow.compile()
```

---

## 11. Conclusion

### 11.1 Key Takeaways

1. **Skills Are Tools, Not Prompts**
    - Implement SkillTool as a regular tool
    - Don't inject skills into base prompts
    - Let agents decide when to load skills

2. **Use Standard Formats**
    - YAML frontmatter + Markdown content
    - Git-friendly, human-readable
    - Easy to edit and version

3. **Hierarchical Discovery**
    - Project > User > Team > Global
    - Support custom paths
    - Cache for performance

4. **Permission-Aware**
    - Filter skills by agent permissions
    - Always require explicit permission to load
    - Whitelist skill directories for file access

5. **Integrate with Existing Tools**
    - Skills coordinate with other tools
    - Work alongside MCP servers
    - No namespace conflicts

### 11.2 Advantages of This Approach

| Advantage | Description |
|-----------|-------------|
| **Context Efficiency** | Skills loaded on-demand, not pre-loaded |
| **Agent Autonomy** | Agent decides when skills are relevant |
| **Modularity** | Skills are independent, reusable units |
| **Discoverability** | Agent sees available skills in tool description |
| **Maintainability** | Skills versioned separately from code |
| **Shareability** | Easy to distribute via Git, NPM, marketplace |
| **Extensibility** | New skills without code changes |
| **Permission Control** | Fine-grained access control per skill |

### 11.3 Next Steps for Your Platform

**Phase 1: Core Implementation**
1. Implement SkillRegistry for discovery and caching
2. Create SkillTool as a LangGraph tool
3. Build YAML frontmatter parser
4. Add permission system for skills

**Phase 2: Web UI**
1. Build skill editor with Monaco
2. Create skill marketplace UI
3. Add skill selector for agents
4. Implement search and filtering

**Phase 3: Advanced Features**
1. Version control for skills
2. Skill dependencies
3. MCP server integration
4. Collaborative editing
5. Usage analytics

**Phase 4: Marketplace**
1. Public skill repository
2. Rating and reviews
3. Featured skills
4. Skill templates

### 11.4 Critical Implementation Details

**For your LangGraph platform**, focus on:

1. **Database-backed skill storage** (not just filesystem)
2. **Web-based skill editor** (Monaco with markdown preview)
3. **Permission system** integrated with your platform's auth
4. **Skill versioning** (track changes, allow rollbacks)
5. **Team collaboration** (sharing, forking, pull requests)
6. **MCP integration** (skills can declare required MCP servers)
7. **Analytics** (track skill usage, effectiveness)

### 11.5 Questions to Consider

Before implementing, consider:

- **Skill Distribution**: How will users share skills? (marketplace, git, export/import)
- **Versioning**: How to handle skill updates? (semantic versioning, deprecation)
- **Dependencies**: Can skills depend on other skills? (transitive loading)
- **Size Limits**: Max skill size? (prevent context overflow)
- **Security**: Sandboxing skill code? (if skills include executable scripts)
- **Testing**: How to test skills? (validation framework)
- **Documentation**: Skill documentation standards? (required sections)

---

## Appendix A: File Locations Reference

### Key Implementation Files

| File | Purpose |
|------|---------|
| `/packages/opencode/src/skill/skill.ts` | Skill discovery, parsing, caching |
| `/packages/opencode/src/tool/skill.ts` | SkillTool implementation |
| `/packages/opencode/src/tool/registry.ts` | Tool registration |
| `/packages/opencode/src/agent/agent.ts` | Agent initialization, permissions |
| `/packages/opencode/src/session/prompt.ts` | Prompt construction, tool resolution |
| `/packages/opencode/src/config/config.ts` | Configuration schema |
| `/packages/opencode/test/skill/skill.test.ts` | Skill system tests |

### Skill Locations

| Path | Priority | Scope |
|------|----------|-------|
| `.opencode/skill/*/SKILL.md` | Highest | Project |
| `.claude/skills/*/SKILL.md` | High | Project |
| `~/.claude/skills/*/SKILL.md` | Medium | User |
| Custom paths (config) | Low | Configurable |

---

## Appendix B: Comparison with Alternative Approaches

### Approach 1: Skills as System Prompt Injection

```typescript
// ❌ BAD: Pre-load all skills into system prompt
const systemPrompt = `
You are an AI assistant.

${allSkills.map(s => s.content).join('\n\n')}

Now help the user with their task.
`
```

**Problems**:
- Wastes 10-100KB of context on every request
- Increases cost (tokens * requests)
- Slower inference (longer prompts)
- Most skills unused in any given conversation

### Approach 2: Skills as Separate Agents

```typescript
// ❌ BAD: Create separate agents for each skill
const apiTestingAgent = new Agent({ systemPrompt: apiTestingSkill.content })
const databaseAgent = new Agent({ systemPrompt: databaseSkill.content })

// Route to different agents
if (task.includes("API")) return apiTestingAgent.run(task)
if (task.includes("database")) return databaseAgent.run(task)
```

**Problems**:
- No skill composition (can't use multiple skills)
- Routing logic becomes complex
- State management difficult across agents
- Doesn't scale to many skills

### Approach 3: Skills as RAG Documents

```typescript
// ❌ SUBOPTIMAL: Store skills in vector DB, retrieve via RAG
const relevantSkills = await vectorDB.search(userQuery, k=3)
const context = relevantSkills.map(s => s.content).join('\n\n')
```

**Problems**:
- Retrieval may miss relevant skills (semantic search limitations)
- Agent can't explicitly choose skills
- No skill discovery mechanism
- Overhead of vector search on every request

### Approach 4: Skills as Tool (✅ RECOMMENDED)

```typescript
// ✅ GOOD: Skills as explicit tool
const skillTool = {
  name: "skill",
  description: "Load skill: api-testing, database, pytest-patterns...",
  execute: async (skillName) => {
    return await loadSkill(skillName)
  }
}
```

**Advantages**:
- Agent decides when to load skills
- On-demand loading (context efficient)
- Explicit, transparent skill usage
- Composes with other tools
- Scales to many skills

---

**Document Version**: 1.0
**Date**: 2026-02-03
**Author**: Architecture Analysis for LangGraph Platform Implementation

