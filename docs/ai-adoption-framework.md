# AI Adoption Measurement Framework

---

## Table of Contents

1. [Overview](#overview)
2. [Core Concepts](#core-concepts)
   - [Projects](#projects)
   - [Assistants](#assistants)
   - [Workflows](#workflows)
   - [Datasources](#datasources)
   - [Tools](#tools)
   - [Conversations](#conversations)
   - [Interactions](#interactions)
   - [Users](#users)
3. [Maturity Levels](#maturity-levels)
   - [Level 1: ASSISTED (Score 0-33)](#level-1-assisted-score-0-33)
   - [Level 2: AUGMENTED (Score 34-66)](#level-2-augmented-score-34-66)
   - [Level 3: AGENTIC (Score 67-100)](#level-3-agentic-score-67-100)
4. [Measurement Dimensions](#measurement-dimensions)
5. [Dimension 1: Daily Active Users (30%)](#dimension-1-daily-active-users-30-of-score)
6. [Dimension 2: Reusability (30%)](#dimension-2-reusability-30-of-score)
7. [Dimension 3: AI Champions (20%)](#dimension-3-ai-champions-20-of-score)
   - [Understanding "Top 20%" (Power Users / Champions)](#understanding-top-20-power-users--champions)
   - [Why Champion Distribution Matters](#why-champion-distribution-matters)
   - [Key Metrics](#key-metrics)
8. [Dimension 4: AI Capabilities (20%)](#dimension-4-ai-capabilities-20-of-score)
9. [Composite Scores](#composite-scores)
10. [Diagnostic Indicators](#diagnostic-indicators)
11. [Improvement Roadmap](#improvement-roadmap)
12. [Project Case Studies](#project-case-studies)
13. [Glossary](#glossary)

**Appendices:**
- [Appendix A: Configurable Thresholds](#appendix-a-configurable-thresholds)
- [Appendix B: Statistical Validity & Cross-Project Comparison](#appendix-b-statistical-validity--cross-project-comparison)

---

## Overview

This framework measures AI adoption maturity within projects, helping leadership understand how effectively teams are utilizing AI tools. The framework provides a structured approach to assess current adoption levels, identify areas for improvement, and track progress over time.

**Key Outputs:**

- **[Adoption Index](#composite-scores)** (0-100): Overall maturity score indicating how well AI is integrated into daily work
- **[Maturity Level](#maturity-levels)**: Classification into one of three progressive stages (Assisted, Augmented, Agentic)

---

## Core Concepts

Before diving into metrics, it's important to understand the key entities being measured. See the [Glossary](#glossary) for complete term definitions.

### Projects

A **Project** is the fundamental organizational unit in the framework. All metrics are calculated at the project level.

- **Scope**: A project contains all assistants, workflows, datasources, and users associated with a team or organizational unit
- **Minimum Size**: Projects with fewer than 5 users are excluded from analytics (see [Configurable Thresholds](#appendix-a-configurable-thresholds))
- **Isolation**: Metrics are calculated independently per project; cross-project comparison requires [normalization](#cross-project-comparison-and-normalization)

### Assistants

An **Assistant** is a configured AI tool that users interact with through [conversations](#conversations). Each assistant has:

- **Instructions (System Prompt)**: Custom prompts that define the assistant's behavior, expertise, and response style
- **Toolkits**: Collections of [tools](#tools) that enable the assistant to perform actions (search, create tickets, query databases, etc.)
- **Context**: Connections to [datasources](#datasources) that provide knowledge for the assistant to reference
- **Configuration**: Settings like temperature, model selection, and conversation starters

**Assistant Classifications:**

| Classification | Definition | Metric Impact |
|----------------|------------|---------------|
| **Personal** | Created and used by a single user | Does not contribute to [Assistants Reuse Rate](#assistants-reuse-rate) |
| **Reused** | Used by 2+ users (configurable) | Contributes to [Reusability](#dimension-2-reusability-30-of-score) score |
| **Active** | Had interactions in last 30 days | Contributes to [Assistant Utilization Rate](#assistant-utilization-rate) |
| **Inactive** | No interactions in last 30 days | May indicate "assistant graveyard" (see [Diagnostic Indicators](#diagnostic-indicators)) |

### Workflows

A **Workflow** is an automated multi-step AI process that orchestrates multiple assistants and tools to complete complex tasks. Workflows are separate entities from assistants.

- **States**: Sequential or parallel steps that the workflow executes
- **Assistants**: Workflows can reference and coordinate multiple assistants
- **Tools**: Direct tool integrations for actions within workflow steps
- **Mode**: Execution pattern (sequential, parallel, or supervisor-coordinated)

**Why Workflows Matter for Adoption:**
- Workflows indicate advanced AI maturity (see [AI Capabilities](#dimension-4-ai-capabilities-20-of-score))
- Workflow count contributes 30% to D4 capability scoring
- Workflow complexity (orchestration sophistication) contributes to Feature Utilization scoring

### Datasources

A **Datasource** (also called Index or Knowledge Base) is a searchable collection of content that assistants can reference. Datasources are independent entities that can be connected to multiple assistants.

**Datasource Types:**

| Type | Description | Examples |
|------|-------------|----------|
| **Code** | Source code repositories | Git repos with code analysis |
| **Knowledge Base - Confluence** | Wiki/documentation from Confluence | Team wikis, documentation |
| **Knowledge Base - Jira** | Issue tracking data | Tickets, project history |
| **Knowledge Base - File** | Uploaded documents | PDFs, Word docs, spreadsheets |
| **Knowledge Base - Azure DevOps** | Azure DevOps wikis | Team documentation |

**Datasource Metrics:**
- [Datasource Reuse Rate](#datasource-reuse-rate): Measures how many datasources are connected to multiple assistants
- Higher reuse indicates efficient knowledge sharing across tools

### Tools

**Tools** (organized into **Toolkits**) are integrations that enable assistants to perform actions beyond text generation.

- **Search Tools**: Query datasources, web search, code search
- **Action Tools**: Create tickets, send notifications, update records
- **MCP Servers**: Model Context Protocol integrations for external services

Tools are measured through [Feature Utilization Rate](#dimension-4-ai-capabilities-20-of-score) in the AI Capabilities dimension.

### Conversations

A **Conversation** is a session between a user and an assistant (or workflow). Conversations contain the message history and are the source of [interaction](#interactions) counts.

- **Messages**: Individual exchanges within a conversation (user prompts and AI responses)
- **Metrics Tracked**: Message count, tokens used, response time, user feedback
- **Workflow Conversations**: Conversations that execute workflow processes (flagged separately)

**Conversation Depth**: The number of messages in a conversation indicates engagement complexity. See [Average Conversation Depth](#average-conversation-depth) in AI Capabilities.

### Interactions

An **Interaction** is counted when a user starts a new [conversation](#conversations) with an assistant. This is the primary unit for measuring user engagement.

- **Counting**: Each new conversation = 1 interaction (not each message)
- **Activation Threshold**: Default 20 interactions to be considered "activated" (see [Configurable Thresholds](#appendix-a-configurable-thresholds))
- **Time-based**: Interactions can be measured all-time or within time windows (7 days, 30 days)

**Why 20 interactions?** This threshold represents approximately 2-3 days of regular use with typical usage patterns (7-10 conversations per working day), indicating the user has moved beyond initial experimentation and is incorporating AI into their workflow. Lower than traditional thresholds to better support small teams.

### Users

Users are categorized by their engagement level:

- **Activated Users**: Users who have reached meaningful usage (≥20 total interactions). See [User Activation Rate](#user-activation-rate).
- **Monthly Active Users (MAU)**: Users who had at least one interaction in the last 30 days. See [MAU Ratio](#monthly-active-user-ratio-mau-ratio).
- **Power Users / Champions**: Users in the top 20% by total usage. This concept is critical for measuring usage concentration and adoption health. For detailed explanation of how "top 20%" is calculated and interpreted, see [Understanding "Top 20%"](#understanding-top-20-power-users--champions) in the AI Champions dimension.
- **Creators (Unique Creators)**: Users who have created at least one assistant within the creator window (default: 90 days). Used to calculate [Creator Diversity](#creator-diversity-used-in-champions-score---25-weight) for the [Champions dimension](#dimension-3-ai-champions-20-of-score).

---

## Maturity Levels

Projects are classified into three maturity levels based on their [Adoption Index](#composite-scores) score. For guidance on advancing between levels, see [Improvement Roadmap](#improvement-roadmap). For real-world examples, see [Project Case Studies](#project-case-studies).

### Level 1: ASSISTED (Score 0-33)

Teams at this level are beginning their AI journey with basic usage patterns.

| Characteristic | Description |
|----------------|-------------|
| **Usage Pattern** | Occasional, individual use of AI tools |
| **Knowledge Sharing** | Personal prompts and scripts, limited sharing |
| **Champions** | A few enthusiasts experimenting without formal mandate |
| **Tracking** | Anecdotal evidence, no standardized measurement |

### Level 2: AUGMENTED (Score 34-66)

Teams have established AI as a regular part of their workflow with growing collaboration.

| Characteristic | Description |
|----------------|-------------|
| **Usage Pattern** | Majority of team actively uses AI (>70% monthly active) |
| **Knowledge Sharing** | Assistants and prompts shared across team members |
| **Champions** | Designated AI champions helping drive adoption |
| **Tracking** | Metrics defined and consistently monitored |

### Level 3: AGENTIC (Score 67-100)

Teams have fully embedded AI into their processes with advanced capabilities.

| Characteristic | Description |
|----------------|-------------|
| **Usage Pattern** | AI usage is prevalent (>80% monthly active) |
| **Knowledge Sharing** | Assets maintained as code with continuous improvement |
| **Champions** | Champions embedded across all roles and functions |
| **Tracking** | Full governance with regular optimization reviews |

---

## Measurement Dimensions

The framework evaluates four key dimensions, each contributing to the overall [Adoption Index](#composite-scores):

- [Daily Active Users (30%)](#dimension-1-daily-active-users-30-of-score) - User engagement breadth and depth
- [Reusability (30%)](#dimension-2-reusability-30-of-score) - Shared assets and knowledge
- [AI Champions (20%)](#dimension-3-ai-champions-20-of-score) - Distribution of expertise
- [AI Capabilities (20%)](#dimension-4-ai-capabilities-20-of-score) - Sophistication of usage

```
┌──────────────────────────────────────────────────────┐
│                    AI ADOPTION INDEX                 │
│                      (0-100 Score)                   │
├──────────────────────────────────────────────────────┤
│                                                      │
│  ┌──────────────────────┐  ┌──────────────────────┐  │
│  │  Daily Active Users  │  │      Reusability     │  │
│  │                      │  │                      │  │
│  │        (30%)         │  │         (30%)        │  │
│  └──────────────────────┘  └──────────────────────┘  │
│                                                      │
│  ┌──────────────────────┐  ┌──────────────────────┐  │
│  │     AI Champions     │  │    AI Capabilities   │  │
│  │                      │  │                      │  │
│  │        (30%)         │  │         (30%)        │  │
│  └──────────────────────┘  └──────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

---

## Dimension 1: Daily Active Users (30% of Score)

**What it measures:** The proportion of users actively engaging with AI tools and the depth of their engagement.

### Key Metrics

#### User Activation Rate

The percentage of users who have reached meaningful AI usage. A user is considered "activated" when they exceed the [activation threshold](#activation-threshold-20-interactions) (default: 20 interactions).

**Why 20 interactions?** This threshold represents approximately 2-3 days of regular use with typical usage patterns (7-10 conversations per working day), indicating the user has moved beyond initial experimentation and is incorporating AI into their workflow. Lower than traditional thresholds to better support small teams. See [Configurable Thresholds](#appendix-a-configurable-thresholds) for adjustment guidance.

**Formula:**

```
User Activation Rate = Activated Users / Total Users × 100%

Where: Activated Users = users with ≥ 20 total interactions (conversations started)
```

**Interpretation:**

| Rate | Meaning | Action |
|------|---------|--------|
| < 30% | Low adoption - most users haven't integrated AI into daily work | Launch onboarding initiatives |
| 30-50% | Growing adoption - room for improvement | Identify and support inactive users |
| 50-70% | Strong adoption - majority of team has meaningful usage | Maintain momentum |
| > 70% | Excellent adoption - AI is embedded in team culture | Focus on advanced use cases |

#### Daily Active User Ratio (DAU Ratio)

The percentage of total users who used AI tools in the last 24 hours. This measures immediate daily engagement, providing a real-time pulse of platform activity.

**Formula:**

```
DAU Ratio = Users Active in Last 24 Hours / Total Users × 100%
```

**Interpretation:**

| Ratio | Meaning | Action |
|-------|---------|--------|
| > 50% | Excellent daily engagement - AI is integral to daily work | Maintain momentum, focus on advanced features |
| 30-50% | Strong daily usage - Regular integration into workflows | Good baseline, identify opportunities to increase |
| 10-30% | Moderate daily activity - Periodic but not daily use | Encourage more frequent usage patterns |
| < 10% | Low daily engagement - Usage is sporadic or declining | Investigate barriers, re-engagement needed |

**Why this matters:** DAU Ratio provides immediate feedback on platform health. Unlike MAU (30-day window) or activation (all-time), DAU shows what's happening today. It's useful for detecting trends quickly but is more volatile than longer-term metrics.

#### Monthly Active User Ratio (MAU Ratio)

The percentage of total users who used AI tools in the last 30 days. This measures recent engagement regardless of historical usage depth.

**Formula:**

```
MAU Ratio = Users Active in Last 30 Days / Total Users × 100%
```

**Interpretation:**

| Ratio | Level | Meaning |
|-------|-------|---------|
| 0-33% | L1 | Occasional usage - AI is not part of regular workflow |
| 34-66% | L2 | Majority actively using - AI is becoming standard practice |
| 67-100% | L3 | Prevalent usage - AI is embedded in daily operations |

#### Multi-Assistant Rate

The percentage of users who interact with 2 or more distinct assistants. This measures exploration breadth and indicates deeper AI tool adoption across different use cases.

**Formula:**

```
Multi-Assistant Rate = Users Using 2+ Assistants / Total Users × 100%
```

**Interpretation:**

| Rate | Meaning | Action |
|------|---------|--------|
| > 50% | Strong exploration - Users leverage multiple tools | Maintain diverse assistant portfolio |
| 30-50% | Growing exploration - Users branching beyond primary assistant | Encourage discovery of additional tools |
| 10-30% | Limited exploration - Most users stick to one assistant | Promote cross-functional assistants |
| < 10% | Narrow usage - Single-tool dependency | Showcase assistant variety, improve discoverability |

**Why this matters:** Users who interact with multiple assistants demonstrate broader engagement with the AI platform. This indicates they're finding value across different use cases rather than treating AI as a single-purpose tool.

#### Engagement Distribution

Measures how evenly AI usage is spread across the user base, identifying whether engagement is broadly distributed or concentrated among few users.

**Formula:**

```
Engagement Distribution = 1 - (STDDEV(LN(interactions + 1)) / MEAN(LN(interactions + 1)))

Normalized to 0.0-1.0 range where:
- 1.0 = Perfectly even distribution (all users have similar interaction counts)
- 0.5 = Neutral/unknown (users exist but no measurable activity)
- 0.0 = Highly skewed distribution (few users dominate activity) OR no users exist
```

**Why log-normalized:** AI usage follows a power-law distribution — a few power users naturally accumulate far more interactions than typical users. Applying `LN(x+1)` to interaction counts before computing the coefficient of variation compresses these outliers, ensuring the metric produces meaningful differentiation across teams rather than collapsing to 0 for all power-law distributions.

**Special Cases:**
- When total_users = 0: Returns 0.0 (no users = no distribution)
- When mean log-interactions = 0 but users exist: Returns 0.5 (neutral - cannot measure)
- When standard deviation = 0: Returns 1.0 (perfect distribution - all users equal)

**Interpretation:**

| Score | Meaning | Action |
|-------|---------|--------|
| > 0.7 | Healthy distribution | Maintain current approach |
| 0.4-0.7 | Moderate concentration | Monitor and encourage broader usage |
| < 0.4 | High concentration | Investigate barriers for non-power users |

### Dimension 1 Score Calculation

The User Engagement dimension score combines six metrics weighted to prioritize adoption depth and retention:

```
D1 Score = (
    User Activation Rate × 25% +
    MAU Ratio × 25% +
    Returning User Rate × 20% +
    Engagement Distribution × 15% +
    Multi-Assistant Rate × 10% +
    DAU Ratio × 5%
)

Result: Score from 0.0 to 1.0 (capped)
```

| Component | Weight | Timeframe | What It Measures |
|-----------|--------|-----------|------------------|
| User Activation Rate | 25% | Configurable window | Depth of engagement (users exceeding activation threshold) |
| MAU Ratio | 25% | 30 days | Stable monthly engagement breadth |
| Returning User Rate | 20% | Configurable window | Retention — users who returned after first use |
| Engagement Distribution | 15% | All-time | Evenness of usage across user base (log-normalized CV) |
| Multi-Assistant Rate | 10% | All-time | Exploration breadth (users using 2+ assistants) |
| DAU Ratio | 5% | 24 hours | Real-time daily pulse (minimal weight due to point-in-time volatility) |

**Component Rationale:**

- **User Activation (25%)**: Primary indicator of meaningful AI integration — users who reached the engagement threshold
- **MAU Ratio (25%)**: Stable signal for consistent monthly usage; more reliable than point-in-time DAU
- **Returning User Rate (20%)**: Strong retention signal — measures whether users find ongoing value
- **Engagement Distribution (15%)**: Usage balance ensuring sustainable team-wide adoption
- **Multi-Assistant Rate (10%)**: Exploration breadth indicating deeper platform adoption
- **DAU Ratio (5%)**: Real-time pulse retained for visibility but low-weighted due to volatility (affected by time-of-day and weekends)

---

## Dimension 2: Reusability (30% of Score)

**What it measures:** Whether AI assets (assistants, knowledge sources) are shared and reused across the organization rather than remaining siloed with individual users.

### Why Reusability Matters

When AI tools remain personal, the organization loses:

- **Knowledge**: Effective prompts and configurations aren't shared
- **Efficiency**: Multiple people solve the same problems independently
- **Quality**: No opportunity for collective improvement

High reusability indicates a mature AI practice where best practices are codified and shared.

### Key Metrics

#### Assistants Reuse Rate

The percentage of assistants being used by multiple team members. An assistant is considered "reused" when 2 or more users have interacted with it.

**Formula:**

```
Assistants Reuse Rate = Assistants Used by 2+ Users / Total Assistants × 100%
```

**Why this matters:** Reused assistants indicate knowledge sharing. If most assistants are used by only their creator, the organization has siloed AI usage. High reuse means effective prompts and configurations are being shared across the team.

**Interpretation:**

| Rate | Meaning | Implication |
|------|---------|-------------|
| < 30% | Most assistants are personal tools | Opportunity to consolidate and share |
| 30-50% | Growing collaboration | Encourage more sharing |
| > 50% | Strong reuse | Assets are being leveraged across the team |

#### Assistant Utilization Rate

The percentage of created assistants that are actively being used (had interactions in the last 30 days).

**Formula:**

```
Assistant Utilization Rate = Active Assistants / Total Assistants × 100%

Where: Active Assistants = assistants with at least one interaction in the last 30 days
```

**Why this matters:** Low utilization indicates an "assistant graveyard" - many tools were created but aren't providing value. This suggests lack of governance or tools that don't meet user needs.

**Interpretation:**

| Rate | Meaning | Implication |
|------|---------|-------------|
| < 30% | Many unused assistants | Audit needed - archive or improve unused tools |
| 30-50% | Moderate utilization | Review which assistants aren't being used and why |
| > 50% | Good utilization | Assets are well-maintained and relevant |

#### Workflow Reuse Rate

The percentage of workflows that are used by multiple team members. A workflow is considered "multi-user" when 2 or more distinct users have executed it in the last 30 days.

**Formula:**

```
Workflow Reuse Rate = Workflows Used by 2+ Users / Total Workflows × 100%

Where: Multi-user workflows = workflows with 2+ distinct users executing them (30-day window)
```

**Why this matters:** Workflows represent automation of multi-step processes. When workflows are reused across team members, it indicates valuable automation patterns are being shared. Low reuse suggests workflows are personal automation that hasn't been generalized for team use.

**Interpretation:**

| Rate | Meaning | Implication |
|------|---------|-------------|
| < 30% | Most workflows are personal automation | Opportunity to generalize and share automation patterns |
| 30-50% | Growing workflow collaboration | Encourage sharing of effective automation |
| > 50% | Strong workflow reuse | Automation patterns are team assets |

#### Workflow Utilization Rate

The percentage of workflows that are actively executed. A workflow is considered "active" when it has 10 or more executions in the last 30 days.

**Formula:**

```
Workflow Utilization Rate = Active Workflows / Total Workflows × 100%

Where: Active Workflows = workflows with ≥5 executions in the last 30 days
```

**Why this matters:** High utilization indicates workflows are providing consistent value through regular execution. Low utilization suggests a "workflow graveyard" where automation was created but isn't being used, possibly due to unreliable execution or changing needs.

**Interpretation:**

| Rate | Meaning | Implication |
|------|---------|-------------|
| < 30% | Many unused workflows | Audit needed - fix or archive unused automation |
| 30-50% | Moderate workflow activity | Some workflows provide consistent value |
| > 50% | Strong workflow adoption | Automation is actively delivering value |

#### Datasource Reuse Rate

The percentage of knowledge sources (datasources) that are connected to multiple assistants.

**Formula:**

```
Datasource Reuse Rate = Datasources Used by 2+ Assistants / Total Datasources × 100%
```

**Why this matters:** Datasources represent indexed knowledge (documents, code, wikis). When they're reused across assistants, knowledge is being effectively leveraged. When each datasource is only used once, there may be duplication or missed opportunities.

**Interpretation:**

- **High Rate (>50%)**: Knowledge is being effectively shared across tools
- **Low Rate (<30%)**: Opportunity to better leverage existing knowledge assets

### Dimension 2 Score Calculation

The Reusability dimension score combines metrics across three asset types: assistants, workflows, and datasources.

```
D2 Score = (
    Assistants Reuse Rate × 30% +
    Assistant Utilization Rate × 25% +
    Workflow Reuse Rate × 25% +
    Workflow Utilization Rate × 10% +
    Datasource Reuse Rate × 10%
)

Result: Score from 0.0 to 1.0 (capped)
```

| Component | Weight | Asset Type | What It Measures |
|-----------|--------|-----------|------------------|
| Assistants Reuse Rate | 30% | Assistants | Assistants used by 2+ people (sharing) |
| Assistant Utilization Rate | 25% | Assistants | Assistants active in last 30 days (quality) |
| Workflow Reuse Rate | 25% | Workflows | Workflows executed by 2+ users (sharing) |
| Workflow Utilization Rate | 10% | Workflows | Workflows with ≥5 executions (quality) |
| Datasource Reuse Rate | 10% | Datasources | Datasources used by 2+ assistants (sharing) |

**Weight Distribution by Asset Type:**

| Asset Type | Reuse Weight | Utilization Weight | Total | Rationale |
|------------|-------------|-------------------|-------|-----------|
| Assistants | 30% | 25% | **55%** | Primary user-facing assets |
| Workflows | 25% | 10% | **35%** | Automation sophistication |
| Datasources | 10% | - | **10%** | Supporting infrastructure |

**Dual-Aspect Measurement:**
- **Sharing (65% total)**: Team adoption, workflow reuse, datasource reuse
- **Quality (35% total)**: Assistant utilization, workflow utilization

**Note on D2 vs. D4 Workflow Metrics:** Workflows appear in both dimensions but measure different aspects:
- **D2 (Reusability)**: Measures workflow *sharing* (reuse rate) and *execution frequency* (utilization)
- **D4 (AI Capabilities)**: Measures workflow *count* and *complexity* (sophistication)

These are complementary measurements, not double-counting. A project can have many complex workflows (high D4) that aren't shared or actively used (low D2), or vice versa.

---

## Dimension 3: AI Champions (20% of Score)

**What it measures:** How AI expertise and usage is distributed across the team. This dimension identifies whether adoption is sustainable (spread across many users) or fragile (concentrated in a few power users).

### Understanding "Top 20%" (Power Users / Champions)

The "top 20%" is a key concept for measuring usage concentration in this dimension. Here's exactly how it works:

**Step-by-step calculation:**

1. **List all users** who have ever interacted with assistants in the project
2. **Count total interactions** for each user (all-time, not just recent)
3. **Rank users** from highest to lowest by their interaction count
4. **Calculate 20% of total users**: Number of Power Users = Total Users × 0.20
5. **Select the top users**: The users with the highest interaction counts up to that number

**Concrete Examples:**

| Total Users | Top 20% Count | Who Are Power Users? |
|-------------|---------------|----------------------|
| 10 users | 2 users | The 2 users with the most interactions |
| 25 users | 5 users | The 5 users with the most interactions |
| 50 users | 10 users | The 10 users with the most interactions |
| 100 users | 20 users | The 20 users with the most interactions |

**Detailed Example with Numbers:**

Consider a project with **10 users** and their interaction counts:

| User | Total Interactions | Rank | Is Power User? |
|------|-------------------|------|----------------|
| User A | 450 | 1 | Yes (top 20%) |
| User B | 320 | 2 | Yes (top 20%) |
| User C | 180 | 3 | No |
| User D | 95 | 4 | No |
| User E | 72 | 5 | No |
| User F | 45 | 6 | No |
| User G | 30 | 7 | No |
| User H | 18 | 8 | No |
| User I | 12 | 9 | No |
| User J | 8 | 10 | No |
| **Total** | **1,230** | - | **2 users** |

In this example:
- Total users = 10
- Top 20% = 10 × 0.20 = **2 users**
- Power Users = User A and User B (the top 2 by interactions)
- Power User interactions = 450 + 320 = **770**
- **Concentration = 770 / 1,230 = 62.6%** (WARNING status)

This means the top 20% of users (2 people) generate 62.6% of all AI activity.

**Why 20%?**

The 20% threshold is based on the Pareto principle observation that a minority of users typically drive the majority of activity. Using 20% allows us to:

- Identify the core group driving adoption
- Measure if this group is doing a disproportionate amount (concentration risk)
- Track whether adoption is spreading beyond the initial champions

**Interpreting Concentration:**

| If Top 20% Generate... | Status | What It Means |
|------------------------|--------|---------------|
| > 80% of activity | CRITICAL | 2 out of 10 users do almost everything |
| 60-80% of activity | WARNING | Power users dominate, others lag behind |
| 40-60% of activity | HEALTHY | Power users lead, but others contribute |
| 20-40% of activity | HEALTHY | Well-distributed, broad engagement |
| < 20% of activity | FLAT | Everyone does a little, no one goes deep |

**Note:** For detailed threshold configuration and adjustment guidance, see [Appendix A](#appendix-a-configurable-thresholds). For statistical validity considerations with small teams, see [Appendix B](#appendix-b-statistical-validity--cross-project-comparison).

### Why Champion Distribution Matters

AI adoption can be unhealthy even with high total usage if:

- **Few power users do most of the work**: If those users leave, knowledge and capability is lost
- **Most users aren't engaging**: The benefits of AI aren't reaching the whole team
- **Only a few people create tools**: Innovation and customization is limited

Healthy adoption shows usage and creation spread across the team.

### Key Metrics

#### Champion Health Assessment

This metric evaluates how AI usage is distributed across the user base by measuring **concentration** - what percentage of total activity comes from the top 20% of users.

**How it's calculated:**

1. All users are ranked by their total interactions (conversations started)
2. The top 20% of users (by activity) are identified as "power users" or "champions"
3. The percentage of total interactions from these power users is calculated

**Example:** If a project has 10 users and 1,000 total interactions:
- Top 2 users (20%) have 700 interactions combined
- Concentration = 70% (the top 20% generate 70% of activity)

**Health Status Ranges:**

| Concentration | Status | Meaning | Score |
|---------------|--------|---------|-------|
| > 80% | **CRITICAL** | Severe over-reliance on very few users. High risk if power users leave. | 0.2 |
| 60-80% | **WARNING** | Unbalanced adoption. Usage is concentrated but not critical. | 0.5 |
| 40-60% | **HEALTHY** | Good distribution. Power users lead but others contribute significantly. | 1.0 |
| 20-40% | **HEALTHY** | Well-distributed. Broad engagement across the team. | 0.8 |
| < 20% | **FLAT** | Very even distribution. May indicate low engagement depth overall. | 0.6 |

**Ideal Range:** 40-60% concentration is optimal. It indicates that power users drive adoption (natural in any tool adoption) while others contribute meaningfully.

**Red Flags:**

- **CRITICAL (>80%)**: Immediate action needed. Document power user workflows and train others.
- **FLAT (<20%)**: May indicate everyone is doing light usage but no one is going deep.

#### Creator Diversity (Used in Champions Score - 25% weight)

The percentage of users who have created at least one assistant within the creator window.

**Thresholds Applied:**

- **Creator Window:** 90 days (default) - only assistants created in the last 90 days are counted

**How it's calculated:**

1. **Count Unique Creators**: Count distinct users who created at least one assistant in the last 90 days
2. **Get Total Users**: Count all users who have ever interacted with assistants in the project
3. **Calculate Diversity**: Unique Creators / Total Users × 100%

**Formula (Display):**

```
Unique Creators = Count of users who created ≥1 assistant in last 90 days
Creator Diversity = Unique Creators / Total Users × 100%
```

**Example:**

| Project | Total Users | Unique Creators (90 days) | Creator Diversity |
|---------|-------------|---------------------------|-------------------|
| Project A | 20 users | 8 users created assistants | 8/20 = 40% |
| Project B | 50 users | 5 users created assistants | 5/50 = 10% |
| Project C | 15 users | 1 user created assistants | 1/15 = 6.7% |

**Why this matters:** When only a few people create assistants, innovation is limited and the team depends on those creators for customization. High creator diversity indicates broad ownership and experimentation.

**Workflow Creator Bonus (Scoring Only):**

To encourage creation of sophisticated automation, users who create workflows receive additional weight in the Champions Score calculation:

- **Displayed Metric:** Shows base creator diversity (simple percentage)
- **Scoring Formula:** `(Unique Creators + Workflow Creators × 0.5) / Total Users`
- **Impact:** Workflow creators count as 1.5× regular creators in scoring

**Example with Workflow Bonus:**

Project with 20 users:
- 2 users created assistants only
- 1 user created workflows (also counts as assistant creator)

**Displayed:** 2/20 = **10% creator diversity**

**Scoring calculation:**
- Base creators: 2
- Workflow creators: 1
- Weighted: (2 + 1 × 0.5) / 20 = 2.5 / 20 = **12.5%**
- This 12.5% is used to determine the score tier (below)

**Why the bonus?** Workflow creation demonstrates deeper engagement with AI automation and advanced platform capabilities. The 50% bonus incentivizes teams to move beyond basic assistants to sophisticated multi-step automation.

**Scoring (converts to Creator Diversity Score for Champions calculation):**

| Weighted Creator Diversity | Score | Meaning |
|-------------------|-------|---------|
| ≥ 15% | 1.0 | Strong distributed ownership - many people contribute tools |
| 5% - 14.9% | 0.6 | Growing creator base - some experimentation happening |
| < 5% | 0.2 | Low diversity - few people creating, most only consuming |

**Interpretation:**

- **> 15%**: Healthy - multiple team members are creating and customizing AI tools
- **5-15%**: Growing - encourage more users to create assistants and workflows
- **< 5%**: Concentrated - consider training programs to enable more creators

**Note:** This metric directly contributes to the Champions Score with **25% weight**. A project with low creator diversity will have a lower Champions Score even if other metrics are healthy. The workflow creator bonus makes it easier to reach the 15% threshold by rewarding creation of advanced automation.

### Dimension 3 Score Calculation

The AI Champions dimension score combines multiple metrics:

```
Champions Score = (
    Champion Concentration Score × 35% +
    Non-Champion Activity Score × 40% +
    Creator Diversity Score × 25%
)

Result: Score from 0.0 to 1.0 (capped)
```

| Component | Weight | What It Measures |
|-----------|--------|------------------|
| Champion Concentration Score | 35% | How balanced usage is (see Health Status table above) |
| Non-Champion Activity Score | 40% | Engagement level of bottom 50% of users |
| Creator Diversity Score | 25% | Percentage of users creating assistants |

**Non-Champion Activity Scoring:**

This measures whether users outside the power user group are still meaningfully engaged.

| Bottom 50% Median Usage | Score | Meaning |
|------------------------|-------|---------|
| ≥ 20 interactions (activation threshold) | 1.0 | Bottom half of users are activated |
| ≥ 10 interactions (50% of threshold) | 0.7 | Moderate engagement from non-champions |
| ≥ 4 interactions (20% of threshold) | 0.4 | Low engagement from non-champions |
| < 10 interactions | 0.2 | Very low engagement from most users |

### Dimension Score Thresholds

| Score | Level | Characteristic |
|-------|-------|----------------|
| 0.0-0.33 | L1 | Sporadic - Enthusiasts acting without mandate, high concentration risk |
| 0.34-0.66 | L2 | Designated - At least one AI champion per team, moderate distribution |
| 0.67-1.0 | L3 | Embedded - All core roles represented, healthy champion network |

---

## Dimension 4: AI Capabilities (20% of Score)

**What it measures:** The sophistication of AI usage through complexity-based assessment of assistants, workflows, and conversation depth.

### Progression of AI Capabilities

Teams typically progress through complexity stages:

1. **Simple (0%)**: Basic Q&A assistants without features; minimal workflows
2. **Basic (33%)**: Single-feature assistants (tools OR datasources OR MCP); simple workflows (1-5 states)
3. **Advanced (67%)**: Two-feature assistants (tools + datasources, etc.); orchestrated workflows (6-10 states)
4. **Complex (100%)**: Full-featured assistants (tools + datasources + MCP); sophisticated workflows (10+ states, multiple assistants)

### Key Metrics

#### Workflow Count Score (30% weight)

Whether the project uses automated multi-step AI workflows.

**What is a Workflow?** A workflow is an automated process where AI handles multiple sequential steps without human intervention between steps. Examples:

- Automatically reviewing code, creating a summary, and posting to a ticket
- Processing a document, extracting data, and updating a spreadsheet
- Analyzing customer feedback, categorizing issues, and routing to teams

**Workflow Count Scoring:**

| Workflow Count | Score | Maturity Level |
|----------------|-------|----------------|
| 0 workflows | 0.2 | Basic AI assistance only - users must manually orchestrate steps |
| 1-2 workflows | 0.4 | Some automation implemented - team is moving beyond basic usage |
| 3-5 workflows | 0.6 | Growing automation - multiple processes automated |
| 6-10 workflows | 0.8 | Advanced automation - complex processes are automated |
| 10+ workflows | 1.0 | Mature automation - comprehensive workflow coverage |

#### Feature Utilization Rate (50% weight)

**Complexity-based assessment of assistant and workflow sophistication.**

This metric evaluates the complexity of both assistants and workflows, recognizing that sophistication comes from feature combinations and orchestration complexity.

**Formula:**

```
Feature Utilization Rate = (
    Assistant Complexity Score × 60% +
    Workflow Complexity Score × 40%
) × 100%
```

##### Assistant Complexity Levels

Assistants are categorized by feature combinations:

| Level | Features | Weight | Examples |
|-------|----------|--------|----------|
| **Simple** | No features | 0% | Chat-only assistants without tools, datasources, or MCP |
| **Basic** | 1 feature | 33% | Assistant with ONLY tools, OR ONLY datasources, OR ONLY MCP |
| **Advanced** | 2 features | 67% | Tools + datasources, tools + MCP, or datasources + MCP |
| **Complex** | 3 features | 100% | Tools + datasources + MCP (full-featured assistant) |
| **Bonus** | Multi-datasource types | +15% | Assistants using multiple datasource types (code + Confluence + Jira) |

**Assistant Complexity Score Calculation:**

```
Assistant Complexity = (
    simple_assistants / total_assistants × 0.0 +
    basic_assistants / total_assistants × 0.33 +
    advanced_assistants / total_assistants × 0.67 +
    complex_assistants / total_assistants × 1.0 +
    multi_datasource_assistants / total_assistants × 0.15
)
```

**Example:**
- Project has 10 assistants:
  - 2 simple (no features)
  - 3 basic (1 feature each)
  - 3 advanced (2 features each)
  - 2 complex (3 features each)
  - 1 with multiple datasource types
- Assistant Complexity = (2×0 + 3×0.33 + 3×0.67 + 2×1.0 + 1×0.15) / 10 = 0.415 (41.5%)

##### Workflow Complexity Levels

Workflows are categorized by orchestration sophistication:

| Level | Criteria | Weight | Examples |
|-------|----------|--------|----------|
| **Simple** | 1-2 states, no tools/custom nodes | 0% | Linear 2-step workflows without tooling |
| **Basic** | 3-5 states OR has tools/custom nodes | 33% | Multi-step workflow with basic orchestration |
| **Advanced** | 6-10 states OR many tools/nodes (>5) | 67% | Complex orchestration with multiple decision points |
| **Complex** | 10+ states AND 3+ tools/nodes | 100% | Sophisticated multi-step workflows with extensive tooling |
| **Bonus** | 3+ assistants | +15% | Workflows coordinating multiple specialized assistants |

**Workflow Complexity Score Calculation:**

```
Workflow Complexity = (
    simple_workflows / total_workflows × 0.0 +
    basic_workflows / total_workflows × 0.33 +
    advanced_workflows / total_workflows × 0.67 +
    complex_workflows / total_workflows × 1.0 +
    multi_assistant_workflows / total_workflows × 0.15
)
```

**Example:**
- Project has 8 workflows:
  - 1 simple (2 states, no tools)
  - 2 basic (4 states each)
  - 3 advanced (8 states each)
  - 2 complex (12 states + tooling)
  - 1 with 3+ assistants
- Workflow Complexity = (1×0 + 2×0.33 + 3×0.67 + 2×1.0 + 1×0.15) / 8 = 0.395 (39.5%)

**Combined Feature Utilization:**

```
Feature Utilization = (0.415 × 60%) + (0.395 × 40%) = 0.249 + 0.158 = 40.7%
```

**Why 60/40 weighting?** Assistants receive higher weight (60%) as they are the primary interaction point for users. Workflows (40%) represent automation sophistication but are typically fewer in number.

#### Conversation Depth Score (20% weight)

How deeply users engage with AI in conversations, measured by the median number of messages per conversation.

**Threshold Applied:** Message Depth Cap = **10 messages** (default)

Conversations are scored relative to this cap. A conversation with 10+ messages receives the maximum depth score.

**How it's calculated:**

```
Conversation Depth Score = MIN(Median Messages per Conversation / 10, 1.0)

Examples:
- Median = 4 messages → Score = 4/10 = 0.4
- Median = 8 messages → Score = 8/10 = 0.8
- Median = 12 messages → Score = 10/10 = 1.0 (capped)
```

**Why this matters:** Short conversations (1-2 messages) indicate simple lookups. Longer conversations indicate iterative problem-solving where users and AI work together on complex tasks.

**Interpretation:**

| Messages | Pattern | Implication | Approx. Score |
|----------|---------|-------------|---------------|
| < 3 | Quick queries | Basic usage - simple questions and answers | 0.1 - 0.3 |
| 3-6 | Moderate depth | Iterative work - users refining requests | 0.3 - 0.6 |
| 6-10 | Deep engagement | Complex problem-solving with AI | 0.6 - 1.0 |
| > 10 | Very deep | Capped at maximum score | 1.0 |

**Note:** Optimal depth depends on use case. Quick queries aren't bad if that's the intended use. The goal is matching depth to the complexity of problems being solved.

### Dimension 4 Score Calculation

The AI Capabilities dimension score combines three complexity-weighted metrics:

```
D4 Score = (
    Workflow Count Score × 30% +
    Feature Utilization Rate × 50% +
    Conversation Depth Score × 20%
)

Result: Score from 0.0 to 1.0 (capped)
```

| Component | Weight | What It Measures |
|-----------|--------|------------------|
| Workflow Count Score | 30% | Presence and quantity of automated workflows |
| Feature Utilization Rate | 50% | Complexity-based sophistication of assistants (60%) and workflows (40%) |
| Conversation Depth Score | 20% | Depth of AI conversations (capped at 10 messages) |

**Complete Example Calculation:**

**Project Stats:**
- 10 assistants: 2 simple, 3 basic, 3 advanced, 2 complex, 1 multi-datasource
- 8 workflows total: 1 simple, 2 basic, 3 advanced, 2 complex, 1 multi-assistant
- Median conversation depth: 6 messages

**Step 1: Workflow Count Score**
- 8 workflows = 0.8 score (6-10 workflows tier)

**Step 2: Feature Utilization Rate**
- Assistant Complexity = (2×0 + 3×0.33 + 3×0.67 + 2×1.0 + 1×0.15) / 10 = 0.415
- Workflow Complexity = (1×0 + 2×0.33 + 3×0.67 + 2×1.0 + 1×0.15) / 8 = 0.395
- Feature Utilization = (0.415 × 60%) + (0.395 × 40%) = 40.7%

**Step 3: Conversation Depth Score**
- 6 messages / 10 cap = 0.6

**Step 4: D4 Score**
```
D4 = (0.8 × 30%) + (0.407 × 50%) + (0.6 × 20%)
   = 0.24 + 0.204 + 0.12
   = 0.564 (56.4%)
```

### Dimension Score Thresholds

| Score | Level | Characteristic |
|-------|-------|----------------|
| 0.0-0.33 | L1 | Assisted - Simple assistants and workflows with minimal sophistication |
| 0.34-0.66 | L2 | Augmented - Growing complexity with basic-to-advanced assistants and workflows |
| 0.67-1.0 | L3 | Agentic - Highly sophisticated assistants and workflows with complex orchestration |

---

## Composite Scores

### Adoption Index Calculation

The Adoption Index combines all four [measurement dimensions](#measurement-dimensions) with their respective weights:

```
Adoption Index = (
    Daily Active Users Score × 30% +
    Reusability Score × 30% +
    AI Champions Score × 20% +
    AI Capabilities Score × 20%
) × 100

Result: Score from 0 to 100
```

### Maturity Level Classification

| Score | Level | Name | Summary |
|-------|-------|------|---------|
| 0-33 | **L1** | **ASSISTED** | AI assistance with varying results. Siloed usage by sporadic enthusiasts. |
| 34-66 | **L2** | **AUGMENTED** | Shared assets, designated champions. Majority of team actively using. |
| 67-100 | **L3** | **AGENTIC** | Agents handling sub-tasks. Codified assets, embedded champions network. |

---

## Diagnostic Indicators

Use these indicators for quick health checks. For detailed metric definitions, see the relevant dimension sections. For improvement strategies, see [Improvement Roadmap](#improvement-roadmap).

### Red Flags to Monitor

| Indicator | Condition | Risk | Action |
|-----------|-----------|------|--------|
| **Ghost Town** | MAU Ratio < 10% | Users abandoning AI | Re-engagement campaign |
| **Low Activation** | Activation < 30% | Low meaningful usage | Onboarding improvements |
| **Champion Dependency** | CRITICAL (>80%) | Over-reliance on few | Knowledge transfer |
| **Assistant Graveyard** | Utilization < 20% | Many unused assets | Audit and archive |
| **Churn Risk** | Return Rate < 30% | Users not finding value | Quality improvements |
| **Single Creator** | Creator Diversity < 5% | 1-2 people building | Creator training |

### Healthy Indicators

| Indicator | Condition | Status |
|-----------|-----------|--------|
| **Broad Adoption** | Activation Rate > 50% | Healthy |
| **Active Ecosystem** | Assistants Reuse Rate > 50% | Healthy |
| **High Retention** | Return Rate > 70% | Healthy |
| **Distributed Creation** | Creator Diversity > 15% | Healthy |
| **Balanced Champions** | Concentration 40-60% | Healthy |

---

## Improvement Roadmap

This roadmap provides actionable guidance for advancing between [Maturity Levels](#maturity-levels). For real-world examples of these transitions, see [Project Case Studies](#project-case-studies), particularly [Case Study 4 (L1 → L2)](#case-study-4-project-delta---l1-extrapolation-early-stage) and [Case Study 5 (L2 → L3)](#case-study-5-project-epsilon---l3-extrapolation-target-state).

### Moving from Level 1 to Level 2

**Focus Area: User Activation (Highest Impact)**

| Current State | Target Action | Expected Impact |
|---------------|---------------|-----------------|
| Activation < 30% | 1:1 onboarding sessions for inactive users | +5-10 index points |
| MAU Ratio < 50% | Weekly AI tips and re-engagement campaigns | +3-5 index points |

**Focus Area: Reusability**

| Current State | Target Action | Expected Impact |
|---------------|---------------|-----------------|
| Assistants Reuse Rate < 30% | Consolidate personal assistants into team tools | +2-4 index points |
| Utilization < 30% | Audit and archive unused assistants | +2-3 index points |

### Moving from Level 2 to Level 3

**Focus Area: Advanced Capabilities**

| Current State | Target Action | Expected Impact |
|---------------|---------------|-----------------|
| No workflows | Create first workflow for recurring multi-step task | +3-5 index points |
| Low conversation depth | Train users on iterative problem-solving with AI | +2-3 index points |

**Focus Area: Champion Network**

| Current State | Target Action | Expected Impact |
|---------------|---------------|-----------------|
| CRITICAL/WARNING health | Document power user workflows, pair with others | +3-4 index points |
| Creator Diversity < 10% | Enable more users to create assistants | +2-3 index points |

---

## Project Case Studies

The following anonymized case studies demonstrate how to interpret metrics and apply recommendations across different [maturity levels](#maturity-levels). For metric definitions, see [Measurement Dimensions](#measurement-dimensions). For improvement strategies, see [Improvement Roadmap](#improvement-roadmap).

### Case Study 1: Project Alpha - High-Performing L2 (Near L3 Threshold)

**Profile:** Mid-sized development team with strong AI culture

**Scores:**

| Metric | Value | Assessment |
|--------|-------|------------|
| Adoption Index | 63.9 | Upper L2 (4 points from L3) |
| Maturity Level | L2: AUGMENTED | - |

**Key Metrics Breakdown:**

| Dimension | Metric | Value | Status |
|-----------|--------|-------|--------|
| Daily Active Users | User Activation Rate | 69.2% | Excellent |
| Daily Active Users | MAU Ratio | 46.2% | Moderate |
| Reusability | Assistants Reuse Rate | 37.5% | Below target |
| Reusability | Assistant Utilization | 25.0% | Low |
| Reusability | Datasource Reuse | 64.3% | Good |
| AI Champions | Champion Health | HEALTHY (46% concentration) | Sustainable |
| AI Champions | Creator Diversity | 92.3% | Excellent |
| AI Capabilities | Total Workflows | 2 | Limited |
| AI Capabilities | Conversation Depth | 6 messages | Deep engagement |

**Analysis:**

This project demonstrates strong user engagement with excellent creator diversity - nearly everyone on the team has created at least one assistant. The HEALTHY champion status (46% concentration) means usage is well-distributed without over-reliance on power users.

However, reusability metrics reveal an opportunity: while many assistants are created, only 25% are actively used and only 37.5% are used by multiple people. This suggests many personal experiments that never became shared team tools.

**Strengths:**

- Exceptional creator diversity (92%) indicates broad ownership of AI tools
- High user activation shows team members are engaged
- Healthy champion distribution reduces key-person dependency
- Deep conversations suggest complex, valuable AI interactions

**Areas for Improvement:**

- Low assistant utilization (25%) indicates many unused assets
- Limited workflows (only 2) restricts automation potential
- Team adoption rate below 50% threshold

**Recommendations:**

| Priority | Action | Expected Impact |
|----------|--------|-----------------|
| 1 | Audit assistants - archive unused, consolidate duplicates | +3-4 index points |
| 2 | Create 2-3 workflows for recurring team processes | +4-6 index points |
| 3 | Promote top assistants to increase team adoption | +2-3 index points |

**Path to Level 3:** Focus on workflow automation and assistant consolidation to push Adoption Index above 67.

---

### Case Study 2: Project Beta - Mid-Range L2

**Profile:** Medium team balancing multiple priorities

**Scores:**

| Metric | Value | Assessment |
|--------|-------|------------|
| Adoption Index | 58.7 | Mid L2 |
| Maturity Level | L2: AUGMENTED | - |

**Key Metrics Breakdown:**

| Dimension | Metric | Value | Status |
|-----------|--------|-------|--------|
| Daily Active Users | User Activation Rate | 50.0% | Moderate |
| Daily Active Users | MAU Ratio | 43.8% | Moderate |
| Reusability | Assistants Reuse Rate | 39.1% | Below target |
| Reusability | Assistant Utilization | 47.8% | Good |
| Reusability | Datasource Reuse | 60.9% | Good |
| AI Champions | Champion Health | HEALTHY (55% concentration) | Sustainable |
| AI Champions | Creator Diversity | 62.5% | Strong |
| AI Capabilities | Total Workflows | 13 | Excellent |
| AI Capabilities | Conversation Depth | 4 messages | Moderate |

**Analysis:**

Project Beta shows the most advanced workflow adoption (13 workflows) but only moderate user activation. This pattern suggests a sophisticated AI setup that not everyone has fully adopted. Half the team hasn't reached the 20-interaction threshold for activation.

The 55% concentration is in the healthy range - power users lead adoption but don't dominate. With 62.5% creator diversity, more than half the team has created assistants, indicating good experimentation culture.

**Strengths:**

- Most workflows of any project (13) shows advanced automation
- Balanced metrics across all dimensions
- Good assistant utilization indicates relevant tools
- Healthy distribution of AI expertise

**Areas for Improvement:**

- User activation at 50% leaves half the team behind
- Team adoption rate below 50% target
- Room to deepen conversation engagement

**Recommendations:**

| Priority | Action | Expected Impact |
|----------|--------|-----------------|
| 1 | Target inactive 50% with personalized onboarding | +5-8 index points |
| 2 | Consolidate assistants to increase team adoption | +2-3 index points |
| 3 | Train users on iterative AI problem-solving | +1-2 index points |

**Path to Level 3:** Primary focus on activating the remaining 50% of users who haven't reached meaningful usage.

---

### Case Study 3: Project Gamma - Lower L2 (Risk Indicators)

**Profile:** Larger team with concentrated AI usage

**Scores:**

| Metric | Value | Assessment |
|--------|-------|------------|
| Adoption Index | 42.3 | Lower L2 (near L1 boundary) |
| Maturity Level | L2: AUGMENTED | - |

**Key Metrics Breakdown:**

| Dimension | Metric | Value | Status |
|-----------|--------|-------|--------|
| Daily Active Users | User Activation Rate | 2.9% | Critical |
| Daily Active Users | MAU Ratio | 17.6% | Low |
| Reusability | Assistants Reuse Rate | 100% | Excellent |
| Reusability | Assistant Utilization | 50.0% | Good |
| Reusability | Datasource Reuse | 50.0% | Moderate |
| AI Champions | Champion Health | CRITICAL (81% concentration) | At-risk |
| AI Champions | Creator Diversity | 5.9% | Low |
| AI Capabilities | Total Workflows | 0 | None |
| AI Capabilities | Conversation Depth | 4 messages | Moderate |

**Analysis:**

Project Gamma presents a critical situation. With 81% concentration, the top 20% of users (approximately 7 of 34 users) generate over 80% of all AI activity. Only 1 user (2.9%) has reached the activation threshold of 20 interactions.

The paradox of 100% assistants reuse rate with 2.9% activation is explained by the assistant structure: the few assistants that exist are used by multiple people (reused), but usage across the team is extremely shallow.

Only 2 people (5.9% creator diversity) have created assistants, meaning the entire team depends on tools built by two individuals.

**Red Flags Identified:**

- **CRITICAL champion health (81% concentration)**: Top 20% of users generate 81%+ of activity
- **Very low activation (2.9%)**: Only 1 of 34 users is truly activated
- **Low MAU ratio (17.6%)**: Most team members not using AI monthly
- **No workflows**: Missing automation opportunities
- **Low creator diversity (5.9%)**: Only 2 people creating assistants

**Strengths:**

- High team adoption rate shows shared assistants are valuable when used
- Existing users engage at moderate depth
- Assets are being used (50% utilization)

**Critical Recommendations:**

| Priority | Action | Expected Impact |
|----------|--------|-----------------|
| 1 | **URGENT**: Knowledge transfer from power users to prevent single point of failure | Risk mitigation |
| 2 | Launch structured onboarding for 30+ inactive users | +10-15 index points |
| 3 | Enable more users to create assistants (training sessions) | +3-4 index points |
| 4 | Create first workflow to demonstrate automation value | +3-5 index points |

**Path to Stability:** This project risks regression to Level 1 if power users leave. Immediate priority is spreading knowledge and activating more users.

---

### Case Study 4: Project Delta - L1 Extrapolation (Early Stage)

**Profile:** Large team in early AI adoption phase (extrapolated from Project Gamma metrics)

**Baseline Reference:** Project Gamma actual scores with Adoption Index 42.3

**L1 Scenario Scores (Extrapolated):**

| Metric | Project Gamma (Actual) | Delta L1 Scenario | Change Required |
|--------|----------------------|-------------------|-----------------|
| Adoption Index | 42.3 | 27.5 | -14.8 points |
| Maturity Level | L2: AUGMENTED | L1: ASSISTED | Regression |

**How L1 Would Be Reached (Calculation Breakdown):**

| Dimension | Metric | Gamma Value | L1 Scenario | Impact on Index |
|-----------|--------|-------------|-------------|-----------------|
| **Daily Active Users (30%)** | User Activation Rate | 2.9% | 0% | -0.9 points |
| | MAU Ratio | 17.6% | 10% | -1.4 points |
| | Engagement Distribution | 0.9 | 0.3 | -1.8 points |
| **Reusability (30%)** | Assistants Reuse Rate | 100% | 50% | -3.8 points |
| | Assistant Utilization | 50% | 25% | -2.3 points |
| | Datasource Reuse | 50% | 25% | -2.3 points |
| **AI Champions (20%)** | Champion Health | CRITICAL (81%) | CRITICAL (90%) | -0.8 points |
| | Creator Diversity | 5.9% | 2% | -0.5 points |
| **AI Capabilities (20%)** | Workflows | 0 | 0 | 0 points |
| | Conversation Depth | 4 | 2 | -1.0 points |
| | | | **Total Change** | **-14.8 points** |

**L1 Characteristic Patterns:**

| Characteristic | L1 Manifestation |
|----------------|------------------|
| Usage Pattern | Sporadic, experimental use by 1-2 individuals |
| Knowledge Sharing | No shared assistants; all personal experiments |
| Champions | Single enthusiast without team support |
| Tracking | No formal metrics; anecdotal usage reports |

**What Would Prevent This Scenario:**

| Risk Factor | Mitigation | Impact |
|-------------|------------|--------|
| Zero activated users | Structured onboarding program | +3-5 points |
| No workflows | Create first team workflow | +2-3 points |
| Extreme concentration | Knowledge transfer sessions | +2-4 points |

**Key Insight:** Project Gamma is already showing L1 risk patterns (2.9% activation, CRITICAL health). Without intervention, natural attrition could push it to L1 within 2-3 months.

---

### Case Study 5: Project Epsilon - L3 Extrapolation (Target State)

**Profile:** High-performing team achieving optimal AI integration (extrapolated from Project Alpha metrics)

**Baseline Reference:** Project Alpha actual scores with Adoption Index 63.9

**L3 Scenario Scores (Extrapolated):**

| Metric | Project Alpha (Actual) | Epsilon L3 Scenario | Change Required |
|--------|----------------------|---------------------|-----------------|
| Adoption Index | 63.9 | 76.1 | +12.2 points |
| Maturity Level | L2: AUGMENTED | L3: AGENTIC | Advancement |

**How L3 Would Be Achieved (Calculation Breakdown):**

| Dimension | Metric | Alpha Value | L3 Target | Change | Impact on Index |
|-----------|--------|-------------|-----------|--------|-----------------|
| **Daily Active Users (30%)** | User Activation Rate | 69.2% | 80% | +10.8% | +1.6 points |
| | MAU Ratio | 46.2% | 75% | +28.8% | +2.6 points |
| | Engagement Distribution | 0.9 | 0.95 | +0.05 | +0.3 points |
| **Reusability (30%)** | Assistants Reuse Rate | 37.5% | 60% | +22.5% | +1.7 points |
| | Assistant Utilization | 25% | 60% | +35% | +2.6 points |
| | Datasource Reuse | 64.3% | 70% | +5.7% | +0.4 points |
| **AI Champions (20%)** | Champion Health | HEALTHY (46%) | HEALTHY (45%) | -1% | +0.1 points |
| | Creator Diversity | 92.3% | 95% | +2.7% | +0.1 points |
| **AI Capabilities (20%)** | Workflows | 2 | 8 | +6 | +2.4 points |
| | Conversation Depth | 6 | 8 | +2 | +0.4 points |
| | | | | **Total Change** | **+12.2 points** |

**L3 Characteristic Patterns:**

| Characteristic | L3 Manifestation |
|----------------|------------------|
| Usage Pattern | AI embedded in daily workflows; 80%+ monthly active |
| Knowledge Sharing | Shared assistant library with active maintenance |
| Champions | Champions in each team/role; distributed expertise |
| Tracking | Full governance with KPIs and regular optimization |

**Actions Required to Reach L3:**

| Priority | Action | Current → Target | Expected Impact |
|----------|--------|------------------|-----------------|
| 1 | **Create team workflows** | 2 → 8 workflows | +2.4 points |
| 2 | **Improve MAU Ratio** | 46% → 75% | +2.2 points |
| 3 | **Increase assistant utilization** | 25% → 60% | +2.1 points |
| 4 | **Improve team adoption** | 37.5% → 60% | +1.4 points |
| 5 | **Boost user activation** | 69% → 80% | +1.4 points |

**Implementation Roadmap:**

| Phase | Focus | Actions | Timeline Target |
|-------|-------|---------|-----------------|
| Phase 1 | Quick Wins | Archive unused assistants, promote top performers | Month 1 |
| Phase 2 | Automation | Create 4-6 new workflows for recurring tasks | Months 2-3 |
| Phase 3 | Engagement | Re-engage dormant users, increase MAU | Months 3-4 |
| Phase 4 | Optimization | Fine-tune assistants, measure impact | Month 5+ |

**Key Insight:** Project Alpha is well-positioned for L3 advancement. The primary gap is automation (workflows) and assistant utilization - addressing these two areas would contribute 4.5 of the 8.6 points needed.

---

### Case Study Comparison Summary

| Metric | Delta (L1) | Gamma | Beta | Alpha | Epsilon (L3) |
|--------|------------|-------|------|-------|--------------|
| **Adoption Index** | 27.5 | 42.3 | 58.7 | 63.9 | 76.1 |
| **Maturity Level** | L1 | L2 | L2 | L2 | L3 |
| **Champion Health** | CRITICAL | CRITICAL | HEALTHY | HEALTHY | HEALTHY |
| **User Activation** | 0% | 2.9% | 50.0% | 69.2% | 80% |
| **Creator Diversity** | 2% | 5.9% | 62.5% | 92.3% | 95% |
| **Workflows** | 0 | 0 | 13 | 2 | 8 |
| **Primary Focus** | Foundation | Risk mitigation | User activation | Automation | Optimization |

**Key Insights:**

1. **L1 → L2 Transition (Delta → Gamma):** Requires foundation building - getting first activated users and establishing shared assistants
2. **L2 Progression (Gamma → Beta → Alpha):** Focus shifts from risk mitigation to user activation to automation
3. **L2 → L3 Transition (Alpha → Epsilon):** Requires workflow maturity and consistently high engagement metrics
4. **Champion Distribution Critical:** Projects with similar Adoption Index can have very different sustainability based on champion concentration (compare CRITICAL vs HEALTHY status)

---

## Appendix A: Configurable Thresholds

This appendix details all configurable thresholds in the framework and provides guidance on when and how to adjust them.

### Threshold Reference Table

| Category | Threshold | Default Value | Description |
|----------|-----------|---------------|-------------|
| **User Activation** | Activation Threshold | **20 interactions** | Minimum conversations a user must start to be considered "activated" |
| **User Activation** | Minimum Users | **5 users** | Minimum users for a project to be included in analytics |
| **Reusability** | Reuse Threshold | **2 users** | Minimum unique users for an assistant/workflow to be considered "reused" |
| **Reusability** | Workflow Activation | **5 executions** | Minimum executions in 30 days for a workflow to be considered "actively executed" |
| **Time Windows** | Active Window (Short) | **7 days** | Window for daily/weekly activity metrics |
| **Time Windows** | Active Window (Long) | **30 days** | Window for monthly activity metrics (MAU, utilization) |
| **Time Windows** | Creator Window | **90 days** | Window for measuring assistant creator diversity |
| **Champions** | Top User Percentile | **20%** | Defines "power users" for concentration analysis |
| **Capabilities** | Message Depth Cap | **10 messages** | Maximum messages for conversation depth scoring |
| **Maturity** | Level 2 Threshold | **34 points** | Adoption Index score to reach AUGMENTED level |
| **Maturity** | Level 3 Threshold | **67 points** | Adoption Index score to reach AGENTIC level |

### How Thresholds Affect Measurements

#### Activation Threshold (20 interactions)

This threshold determines when a user is considered to have meaningfully adopted AI tools.

**How it's applied:**

- Users with **≥ 20 total interactions** are counted as "activated"
- Users with **< 20 total interactions** are counted as "not activated"
- The User Activation Rate = Activated Users / Total Users

**Example:** A project with 20 users where 8 have ≥20 interactions:
- Activated Users = 8
- User Activation Rate = 8 / 20 = 40%

**Why 20?** This represents approximately 2-3 days of regular use with typical usage patterns (7-10 conversations per working day). It filters out users who only experimented briefly while being achievable enough to support small teams.

**When to adjust:**

| Scenario | Suggested Adjustment |
|----------|---------------------|
| New projects (< 3 months old) | Lower to 10-15 |
| Periodic usage patterns (weekly reports) | Lower to 10-15 |
| Mature projects with intensive AI usage | Increase to 50-100 |
| High-frequency environments (support teams) | Increase to 50-100 |

#### Reuse Threshold (2 users)

Determines when an assistant is considered a shared team tool vs. a personal tool.

**How it's applied:**

- Assistants with **≥ 2 unique users** are counted as "reused"
- Assistants with **1 user** are counted as "personal"
- Assistants Reuse Rate = Reused Assistants / Total Assistants

**Example:** A project with 15 assistants where 6 have 2+ users:
- Reused Assistants = 6
- Assistants Reuse Rate = 6 / 15 = 40%

**When to adjust:**

| Scenario | Suggested Adjustment |
|----------|---------------------|
| Small teams (< 5 people) | Keep at 2 |
| Large teams (> 20 people) | Increase to 3-5 |
| Cross-functional assistants expected | Increase to 3-5 |

#### Active Window (30 days)

Defines the time period for measuring recent activity.

**How it's applied:**

- **Monthly Active Users (MAU)**: Users with ≥1 interaction in last 30 days
- **Active Assistants**: Assistants with ≥1 interaction in last 30 days
- **Assistant Utilization**: Active Assistants / Total Assistants

**Example:** A project with 50 users where 35 used AI in the last 30 days:
- MAU = 35
- MAU Ratio = 35 / 50 = 70%

**When to adjust:**

| Scenario | Suggested Adjustment |
|----------|---------------------|
| Fast-paced daily usage | Shorten to 7-14 days |
| Periodic usage (monthly reports) | Extend to 60-90 days |
| Standard comparison across projects | Keep at 30 days (industry standard) |

#### Top User Percentile (20%)

Defines who counts as a "power user" for concentration analysis. See [Understanding "Top 20%"](#understanding-top-20-power-users--champions) in Core Concepts for detailed examples.

**How it's applied:**

1. All users ranked by total interactions (all-time count)
2. Calculate number of power users: Total Users × 0.20
3. Select users with highest interaction counts
4. Sum their interactions
5. Concentration = Power User Interactions / Total Interactions × 100%

**Quick Reference:**

| Total Users | Power Users (20%) | Concentration Measures... |
|-------------|-------------------|---------------------------|
| 5 users | 1 user | What % of activity comes from top 1 person |
| 10 users | 2 users | What % of activity comes from top 2 people |
| 25 users | 5 users | What % of activity comes from top 5 people |
| 50 users | 10 users | What % of activity comes from top 10 people |

**When to adjust:**

| Scenario | Suggested Adjustment | Effect |
|----------|---------------------|--------|
| Focus on top performers only | Lower to 10% | Stricter - fewer users classified as power users |
| Broader "power user" definition | Increase to 30% | Looser - more users included in power user group |
| Very small teams (< 5 users) | Consider 10% or individual analysis | Prevents single user from being the entire "top 20%" |
| Large organizations | Keep at 20% | Standard for comparability |

#### Level Thresholds (34 / 67)

Determines maturity level classification.

**How it's applied:**

| Adoption Index | Maturity Level |
|----------------|----------------|
| 0-33 | Level 1: ASSISTED |
| 34-66 | Level 2: AUGMENTED |
| 67-100 | Level 3: AGENTIC |

**When to adjust:**

| Scenario | Suggested Adjustment |
|----------|---------------------|
| Stricter L3 requirements | Increase L3 threshold to 75 |
| Encourage early wins | Lower L2 threshold to 25-30 |
| Standardized comparison | Keep defaults for comparability |

### Threshold Impact on Scoring

Understanding how thresholds affect component scores:

| Metric | Threshold Applied | Impact on Score |
|--------|-------------------|-----------------|
| User Activation Rate | 20 interactions | Higher threshold → fewer activated users → lower score |
| Assistants Reuse Rate | 2 users | Higher threshold → fewer reused assistants → lower score |
| Workflow Utilization | 5 executions | Higher threshold → fewer active workflows → lower score |
| MAU Ratio | 30 days window | Longer window → more users counted as active → higher score |
| Champion Health | 20% top users | Lower percentile → stricter concentration measurement |
| Conversation Depth | 10 message cap | Scores capped at 10; conversations >10 messages score the same as 10 |

---

## Appendix B: Statistical Validity & Cross-Project Comparison

This appendix addresses statistical considerations for metric interpretation and provides normalization approaches for cross-project comparison.

### Minimum Sample Size Recommendations

When interpreting metrics, it's important to understand how sample size affects reliability.

| Population Size | Metric Reliability | Recommendation |
|-----------------|-------------------|----------------|
| **< 5 users** | Low reliability | Use individual analysis rather than aggregate metrics |
| **5-9 users** | Moderate reliability | Interpret with caution; focus on directional trends |
| **10-19 users** | Acceptable reliability | Standard interpretation applies |
| **20+ users** | High reliability | Full confidence in metric accuracy |

### Confidence Intervals for Small Populations

Metrics for smaller projects have wider confidence intervals, meaning actual values may differ significantly from calculated values.

**Example - Creator Diversity:**

| Project Size | Creators | Calculated Diversity | Confidence Range |
|--------------|----------|---------------------|------------------|
| 100 users | 15 | 15% | ±3% (12-18%) |
| 50 users | 8 | 16% | ±6% (10-22%) |
| 20 users | 3 | 15% | ±10% (5-25%) |
| 10 users | 2 | 20% | ±15% (5-35%) |

**Interpretation:** For a project with 10 users showing 20% Creator Diversity, the actual diversity could reasonably be anywhere from 5% to 35% due to sampling variance.

### Alternative Approaches for Small Teams (< 5 users)

For teams below the minimum user threshold, consider these alternative measurement approaches:

| Standard Metric | Alternative for Small Teams |
|-----------------|---------------------------|
| User Activation Rate | Track individual user progression toward 20-interaction threshold |
| Champion Concentration | Monitor if any single user exceeds 50% of total activity |
| Creator Diversity | Track whether at least 2 different people have created assistants |
| Assistants Reuse Rate | Track if any assistant has 2+ users (binary: yes/no) |

### Cross-Project Comparison and Normalization

When comparing projects of different sizes, direct score comparison can be misleading. Use these normalization approaches:

#### Size-Based Normalization

| Project Size | Normalization Approach |
|--------------|----------------------|
| < 5 users | Do not compare directly; use trend analysis only |
| 5-9 users | Apply ±15% tolerance band when comparing |
| 10-19 users | Apply ±10% tolerance band when comparing |
| 20-49 users | Apply ±5% tolerance band when comparing |
| 50+ users | Direct comparison acceptable |

**Tolerance Band Example:** Project A (15 users) scores 55. Project B (40 users) scores 52. With ±10% tolerance for Project A, its effective range is 49.5-60.5. Since Project B (52) falls within this range, the projects are considered equivalent.

#### Percentile Ranking

For portfolio-level comparison, convert scores to percentile rankings:

```
Project Percentile = (Projects Scoring Below This Project / Total Projects) × 100
```

**Example:**
- 20 projects in portfolio
- Project X scores 58, ranking 14th highest
- Percentile = ((20 - 14) / 20) × 100 = 30th percentile

#### Cohort-Based Comparison

Group projects by similar characteristics before comparison:

| Cohort Factor | Why It Matters |
|---------------|----------------|
| Project age | New projects (< 6 months) have naturally lower scores |
| Team function | Technical teams may show different patterns than business teams |
| User count range | Compare 10-20 user projects with other 10-20 user projects |

**Recommended Cohorts:**
- **By Size:** Small (10-19), Medium (20-49), Large (50+)
- **By Age:** New (< 6 months), Established (6-18 months), Mature (18+ months)
- **By Maturity Target:** L1-targeting, L2-targeting, L3-targeting

---

## Glossary

Quick reference for all terms used in this framework. For detailed explanations, see [Core Concepts](#core-concepts) and the relevant [Measurement Dimensions](#measurement-dimensions).

### Terms

| Term | Definition |
|------|------------|
| **Activated User** | A user who has reached the activation threshold (default: ≥20 interactions) |
| **Active Assistant** | An assistant with at least one interaction within the active window (default: 30 days) |
| **Active Window** | Time period for measuring recent activity (default: 30 days for monthly metrics) |
| **Active Workflow** | A workflow with at least 5 executions within the last 30 days |
| **Adoption Index** | Composite score (0-100) measuring overall AI adoption maturity |
| **Assistant** | A configured AI tool with instructions, toolkits, and datasource connections |
| **Champion Health** | Assessment of usage distribution (HEALTHY, WARNING, CRITICAL, FLAT) based on concentration |
| **Concentration** | Percentage of total activity from the top user percentile (default: top 20%) |
| **Conversation** | A session between a user and an assistant containing message history; one conversation = one interaction |
| **Creator Diversity** | Percentage of users who have created at least one assistant within the creator window (default: 90 days) |
| **Datasource** | A searchable knowledge base (code repos, Confluence, Jira, files) that assistants can reference |
| **Engagement Distribution** | Measure of how evenly AI usage is spread across users; calculated using log-normalized coefficient of variation (1 - stddev/mean of LN-transformed interaction counts) |
| **Interaction** | A single conversation started by a user with an assistant (not individual messages) |
| **MAU (Monthly Active Users)** | Users who had at least one interaction within the active window (default: 30 days) |
| **Maturity Level** | Classification based on Adoption Index: L1 (0-33), L2 (34-66), L3 (67-100) |
| **Power User / Champion** | A user in the top user percentile (default: top 20%) by total interactions |
| **Project** | The organizational unit containing all assistants, workflows, datasources, and users for a team |
| **Reused Assistant** | An assistant used by at least the reuse threshold number of users (default: ≥2 users) |
| **Tools / Toolkits** | Integrations that enable assistants to perform actions (search, create tickets, etc.) |
| **Unique Creators** | Count of distinct users who created at least one assistant within the creator window (90 days) |
| **Workflow** | An automated multi-step AI process that orchestrates assistants and tools to complete complex tasks |
| **Workflow Complexity** | Categorization of workflows by orchestration sophistication (simple, basic, advanced, complex) based on state count, tools, and assistants |
| **Assistant Complexity** | Categorization of assistants by feature sophistication (simple, basic, advanced, complex) based on tools, datasources, and MCP combinations |

### Default Thresholds Quick Reference

| Threshold | Default | Used For |
|-----------|---------|----------|
| Activation Threshold | 20 interactions | Determining if a user is "activated" |
| Minimum Users | 5 users | Project inclusion in analytics |
| Reuse Threshold | 2 users | Determining if an assistant/workflow is "reused" |
| Workflow Activation | 5 executions | Determining if a workflow is "actively executed" |
| Active Window (Long) | 30 days | MAU, Assistant Utilization |
| Creator Window | 90 days | Creator Diversity |
| Top User Percentile | 20% | Champion Health / Concentration |
| Message Depth Cap | 10 messages | Conversation Depth scoring |
| Level 2 Threshold | 34 points | ASSISTED → AUGMENTED boundary |
| Level 3 Threshold | 67 points | AUGMENTED → AGENTIC boundary |

---

*Document Version: 1.0*
*AI Adoption Measurement Framework*

[↑ Back to Table of Contents](#table-of-contents)
