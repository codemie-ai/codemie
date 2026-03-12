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

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import Column, Index, String
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field as SQLModelField

from codemie.rest_api.models.base import BaseModelWithSQLSupport, PydanticListType, PydanticType


class AnalysisStatus(str, Enum):
    """Status of conversation analysis in queue"""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TopicCategory(str, Enum):
    """Category classification for conversation topics.

    Each category represents a distinct type of work or activity in software development
    and business contexts, helping to classify conversations for analytics purposes.
    """

    # Writing, debugging, refactoring application code; implementing features, fixing bugs
    CODE_DEVELOPMENT = "code_development"
    # Data analysis, ETL pipelines, database queries, data modeling, analytics
    DATA_WORK = "data_work"
    # Cloud resources, containers, CI/CD, deployment, DevOps, monitoring
    INFRASTRUCTURE = "infrastructure"
    # Unit tests, integration tests, test automation, QA processes, test strategies
    TESTING = "testing"
    # System design, technical decisions, design patterns, scalability, reviews
    ARCHITECTURE = "architecture"
    # Technical docs, API documentation, README files, user guides, comments
    DOCUMENTATION = "documentation"
    # Requirements, specifications, user stories, business logic, domain modeling
    BUSINESS_CONTENT = "business_content"
    # Debugging issues, troubleshooting errors, root cause analysis, investigations
    PROBLEM_SOLVING = "problem_solving"
    # Learning new technologies, understanding concepts, exploring frameworks
    LEARNING = "learning"
    # Workflow automation, scripting, build scripts, task automation tools
    PROCESS_AUTOMATION = "process_automation"
    # Project planning, sprint planning, task breakdown, roadmap, estimation
    PLANNING = "planning"
    # Writing emails, messages, reports, presentations, team communication
    COMMUNICATION = "communication"
    # Proof of concepts, prototypes, trying new tools, experimental features, R&D
    EXPERIMENTS = "experiments"
    # Topics that don't fit clearly into any other category
    OTHER = "other"


class AnswerQuality(str, Enum):
    """Quality assessment of AI-generated answers in the conversation.

    Evaluates how well the AI responses met the user's needs and requirements.
    """

    # AI provided exactly what was needed; answer ready to use with no modifications
    EXCELLENT = "excellent"
    # AI response was helpful and mostly correct; minor adjustments or tweaks needed
    GOOD = "good"
    # AI provided partial help; significant user effort required to achieve the goal
    FAIR = "fair"
    # AI struggled to understand the request or provide meaningful help
    POOR = "poor"


class IterationEfficiency(str, Enum):
    """Efficiency measured by the number of exchanges needed to reach resolution.

    Indicates how quickly the conversation reached a satisfactory outcome.
    """

    # Task resolved in 1-2 exchanges; quick and direct path to solution
    OPTIMAL = "optimal"
    # Task resolved in 3-4 exchanges; reasonable back-and-forth to clarify and solve
    EFFICIENT = "efficient"
    # Task resolved in 5-7 exchanges; moderate effort with some trial and error
    MODERATE = "moderate"
    # Required 8+ exchanges with no clear resolution; significant struggle
    STRUGGLING = "struggling"


class ConversationFocus(str, Enum):
    """Level of focus and coherence maintained throughout the conversation.

    Assesses whether the conversation stayed on track or jumped between topics.
    """

    # Sequential topic completion; no unplanned switches; clear progression
    FOCUSED = "focused"
    # Mostly on track with 1-2 intentional pivots to related topics
    MOSTLY_FOCUSED = "mostly_focused"
    # Multiple topic switches (3-5); some unrelated tangents; reduced coherence
    SCATTERED = "scattered"
    # Chaotic with 6+ jumps between unrelated topics; no clear direction
    LOST = "lost"


class OverallScore(int, Enum):
    """Overall satisfaction score on a 1-5 scale.

    Combines answer quality, efficiency, and user sentiment into a single metric.
    """

    # Very unsatisfied: Complete failure to achieve goals; frustration evident
    VERY_UNSATISFIED = 1
    # Unsatisfied: Struggled significantly; mostly unmet goals; poor experience
    UNSATISFIED = 2
    # Neutral: Mixed results; some goals met, some not; acceptable but not great
    NEUTRAL = 3
    # Satisfied: Goals achieved within reasonable effort; positive experience overall
    SATISFIED = 4
    # Highly satisfied: Quick resolution; exceeded expectations; clear positive feedback
    HIGHLY_SATISFIED = 5


class MaturityLevel(str, Enum):
    """AI maturity level classification based on user proficiency and usage patterns.

    Represents the user's sophistication in working with AI tools, from beginner
    to advanced power user. Used to assess training needs and feature adoption.
    """

    # Beginner: Simple questions, minimal context, sporadic usage, trial-and-error approach
    L1 = "L1"
    # Intermediate: Provides context and details, regular usage, 2-4 turn solutions
    L2 = "L2"
    # Advanced: Custom assistants, complex multi-domain problems, AI in all workflows
    L3 = "L3"


class PromptQuality(str, Enum):
    """Quality level of user prompts in terms of clarity, context, and specificity.

    Assesses how well users formulate their requests to the AI system.
    """

    # Simple questions with minimal context; vague or unclear requests
    BASIC = "basic"
    # Provides adequate context and details; clear problem statements
    INTERMEDIATE = "intermediate"
    # Expert-level prompts with rich context, constraints, and clear success criteria
    ADVANCED = "advanced"


class TaskComplexity(str, Enum):
    """Complexity level of tasks requested from the AI.

    Evaluates the sophistication and difficulty of problems users are solving with AI.
    """

    # Basic questions; simple information retrieval; straightforward tasks
    SIMPLE = "simple"
    # Work tasks with clear requirements; standard development or analysis work
    MODERATE = "moderate"
    # Multi-domain problems; custom assistants; architectural decisions; research
    COMPLEX = "complex"


class UsagePattern(str, Enum):
    """Frequency and integration level of AI usage in the user's workflow.

    Indicates how deeply AI is embedded in the user's daily work practices.
    """

    # Occasional usage; trial-and-error exploration; not part of regular workflow
    SPORADIC = "sporadic"
    # Integrated into daily work; consistent usage for routine tasks
    REGULAR = "regular"
    # AI native; uses AI in all workflows; teaches others; champions adoption
    NATIVE = "native"


class AntiPatternCode(str, Enum):
    """Anti-pattern identification codes categorized by root cause.

    Categories: tool_* (wrong tool usage), prompt_* (poor prompting),
    context_* (context management issues), platform_* (underutilized features).
    """

    # TOOL CATEGORY - Using wrong or inefficient tools
    # Using AI for simple calculations instead of calculator
    TOOL_MATH_CALCULATION = "tool_math_calculation"
    # Using AI to search web when web search tool available
    TOOL_WEB_SEARCH_MANUAL = "tool_web_search_manual"
    # Asking AI to remember things instead of using knowledge base
    TOOL_MEMORY_MANUAL = "tool_memory_manual"
    # Using AI for code execution instead of running code directly
    TOOL_CODE_EXECUTION = "tool_code_execution"

    # PROMPT CATEGORY - Poor prompting techniques
    # Vague or unclear requests without specific requirements
    PROMPT_VAGUE_REQUEST = "prompt_vague_request"
    # Insufficient context provided for the task
    PROMPT_INSUFFICIENT_CONTEXT = "prompt_insufficient_context"
    # Too much irrelevant information cluttering the request
    PROMPT_EXCESSIVE_DETAIL = "prompt_excessive_detail"
    # Not breaking down complex tasks into manageable steps
    PROMPT_NO_DECOMPOSITION = "prompt_no_decomposition"
    # Asking multiple unrelated questions in one prompt
    PROMPT_MULTIPLE_UNRELATED = "prompt_multiple_unrelated"

    # CONTEXT CATEGORY - Context management issues
    # Too much context causing confusion or token limits
    CONTEXT_OVERLOAD = "context_overload"
    # Switching topics without clear transitions
    CONTEXT_TOPIC_JUMPING = "context_topic_jumping"
    # Not providing enough background information
    CONTEXT_INSUFFICIENT = "context_insufficient"
    # Repeating information already in conversation
    CONTEXT_REDUNDANT = "context_redundant"

    # PLATFORM CATEGORY - Not leveraging platform features
    # Not creating custom assistants for repetitive tasks
    PLATFORM_NO_CUSTOM_ASSISTANT = "platform_no_custom_assistant"
    # Not using knowledge base for project-specific information
    PLATFORM_NO_KNOWLEDGE_BASE = "platform_no_knowledge_base"
    # Not using available integrations (Jira, Confluence, etc.)
    PLATFORM_NO_INTEGRATIONS = "platform_no_integrations"
    # Not leveraging multi-turn conversations effectively
    PLATFORM_NO_CONVERSATION_FLOW = "platform_no_conversation_flow"
    # Not using templates or reusable prompts
    PLATFORM_NO_TEMPLATES = "platform_no_templates"
    ASSISTANT_WRONG_PURPOSE = "assistant_wrong_purpose"
    # Using assistant without required tools when explicitly trying to perform tool actions
    ASSISTANT_NO_TOOLS = "assistant_no_tools"
    # Using assistant without project datasources for project-specific questions (NOT general questions)
    ASSISTANT_NO_DATASOURCES = "assistant_no_datasources"

    # OTHER - Patterns not fitting other categories
    OTHER = "other"


class Severity(str, Enum):
    """Impact severity level of an identified anti-pattern.

    Indicates how significantly the anti-pattern affects user productivity and outcomes.
    """

    # Minor inefficiency with minimal impact; slightly suboptimal but functional
    LOW = "low"
    # Notable inefficiency that affects productivity; wastes time or effort
    MEDIUM = "medium"
    # Significant issue that wastes substantial effort or blocks progress
    HIGH = "high"
    # Severe problem causing total failure or fundamentally wrong approach
    CRITICAL = "critical"


# Sub-models matching JSON schema for conversation analytics


class AssistantUsed(BaseModel):
    """Assistant information from conversation"""

    class Config:
        extra = "forbid"  # Ensures additionalProperties: false for Azure structured output

    id: str = Field(description="Unique identifier of the AI assistant")
    name: str = Field(description="Display name of the AI assistant")
    description: Optional[str] = Field(
        default=None, description="Description of the assistant's purpose and capabilities"
    )
    categories: List[str] = Field(
        default_factory=list,
        description="Categories this assistant belongs to (e.g., 'Development', 'Business Analysis')",
    )
    author: Optional[str] = Field(default=None, description="Name of the user who created this assistant")
    tool_names: List[str] = Field(
        default_factory=list,
        description="Names of tools available to this assistant (e.g., 'Jira', 'GitLab', 'Web Search', 'Calculator')",
    )
    datasource_names: List[str] = Field(
        default_factory=list,
        description="Names of datasources/knowledge bases this assistant has access to (e.g., project repos)",
    )


class TopicAnalysis(BaseModel):
    """Topic identified in conversation"""

    class Config:
        extra = "forbid"  # Ensures additionalProperties: false for Azure structured output

    topic: str = Field(description="Brief name of the topic (max 100 chars, e.g., 'Python API Integration')")
    category: TopicCategory = Field(
        description=(
            "Category classification for the conversation topic. Choose the most appropriate category:\n"
            "- code_development: Writing, debugging, refactoring code; implementing features, fixing bugs\n"
            "- data_work: Data analysis, ETL pipelines, database queries, data modeling, analytics\n"
            "- infrastructure: Cloud resources, containers, CI/CD, deployment, DevOps, monitoring\n"
            "- testing: Unit tests, integration tests, test automation, QA processes, test strategies\n"
            "- architecture: System design, technical decisions, design patterns, scalability, reviews\n"
            "- documentation: Technical docs, API documentation, README files, user guides, comments\n"
            "- business_content: Requirements, specifications, user stories, business logic, modeling\n"
            "- problem_solving: Debugging issues, troubleshooting, root cause analysis, investigations\n"
            "- learning: Learning new technologies, understanding concepts, exploring frameworks\n"
            "- process_automation: Workflow automation, scripting, build scripts, task automation tools\n"
            "- planning: Project planning, sprint planning, task breakdown, roadmap, estimation\n"
            "- communication: Writing emails, messages, reports, presentations, team communication\n"
            "- experiments: Proof of concepts, prototypes, trying new tools, features, R&D\n"
            "- other: Topics that don't fit clearly into any other category (MUST provide other_category)"
        )
    )
    other_category: Optional[str] = Field(
        default=None,
        description=(
            "REQUIRED when category='other'. Suggest a new category name that would fit this topic. "
            "Leave empty/null ONLY if category is NOT 'other'. "
            "Example: 'security_operations', 'api_design', 'database_administration'"
        ),
    )
    usage_intent: str = Field(
        description="Usage intent: 'production' (day-to-day tasks impacting real projects), "
        "'experimentation' (testing AI capabilities), or 'personal' (non-SDLC, non-business personal cases)"
    )
    user_goal: str = Field(description="What the user wanted to achieve (max 100 chars)")
    summary: str = Field(description="Summary of what was discussed in this topic (max 200 chars)")


class SatisfactionMetrics(BaseModel):
    """User satisfaction metrics"""

    class Config:
        extra = "forbid"  # Ensures additionalProperties: false for Azure structured output

    answer_quality: AnswerQuality = Field(
        description=(
            "Quality assessment of AI-generated answers. Choose the most appropriate level:\n"
            "- excellent: AI provided exactly what was needed; answer ready to use with no modifications\n"
            "- good: AI response was helpful and mostly correct; minor adjustments or tweaks needed\n"
            "- fair: AI provided partial help; significant user effort required to achieve the goal\n"
            "- poor: AI struggled to understand the request or provide meaningful help"
        )
    )
    iteration_efficiency: IterationEfficiency = Field(
        description=(
            "Efficiency measured by the number of exchanges needed to reach resolution:\n"
            "- optimal: Task resolved in 1-2 exchanges; quick and direct path to solution\n"
            "- efficient: Task resolved in 3-4 exchanges; reasonable back-and-forth to clarify and solve\n"
            "- moderate: Task resolved in 5-7 exchanges; moderate effort with some trial and error\n"
            "- struggling: Required 8+ exchanges with no clear resolution; significant struggle"
        )
    )
    conversation_focus: ConversationFocus = Field(
        description=(
            "Level of focus and coherence maintained throughout the conversation:\n"
            "- focused: Sequential topic completion; no unplanned switches; clear progression\n"
            "- mostly_focused: Mostly on track with 1-2 intentional pivots to related topics\n"
            "- scattered: Multiple topic switches (3-5); some unrelated tangents; reduced coherence\n"
            "- lost: Chaotic with 6+ jumps between unrelated topics; no clear direction"
        )
    )
    overall_score: OverallScore = Field(
        description=(
            "Overall satisfaction score on a 1-5 scale combining answer quality, efficiency, and sentiment:\n"
            "- 5 (highly_satisfied): Quick resolution; exceeded expectations; clear positive feedback\n"
            "- 4 (satisfied): Goals achieved within reasonable effort; positive experience overall\n"
            "- 3 (neutral): Mixed results; some goals met, some not; acceptable but not great\n"
            "- 2 (unsatisfied): Struggled significantly; mostly unmet goals; poor experience\n"
            "- 1 (very_unsatisfied): Complete failure to achieve goals; frustration evident"
        )
    )
    evidence: str = Field(description="Evidence from the conversation supporting the satisfaction assessment")


class MaturityIndicators(BaseModel):
    """Maturity level indicators"""

    class Config:
        extra = "forbid"  # Ensures additionalProperties: false for Azure structured output

    prompt_quality: PromptQuality = Field(
        description=(
            "Quality level of user prompts in terms of clarity, context, and specificity:\n"
            "- basic: Simple questions with minimal context; vague or unclear requests\n"
            "- intermediate: Provides adequate context and details; clear problem statements\n"
            "- advanced: Expert-level prompts with rich context, constraints, and clear success criteria"
        )
    )
    task_complexity: TaskComplexity = Field(
        description=(
            "Complexity level of tasks requested from the AI:\n"
            "- simple: Basic questions; simple information retrieval; straightforward tasks\n"
            "- moderate: Work tasks with clear requirements; standard development or analysis work\n"
            "- complex: Multi-domain problems; custom assistants; architectural decisions; research"
        )
    )
    usage_pattern: UsagePattern = Field(
        description=(
            "Frequency and integration level of AI usage in the user's workflow:\n"
            "- sporadic: Occasional usage; trial-and-error exploration; not part of regular workflow\n"
            "- regular: Integrated into daily work; consistent usage for routine tasks\n"
            "- native: AI native; uses AI in all workflows; teaches others; champions adoption"
        )
    )


class MaturityAnalysis(BaseModel):
    """AI maturity level analysis"""

    class Config:
        extra = "forbid"  # Ensures additionalProperties: false for Azure structured output

    level: MaturityLevel = Field(
        description=(
            "AI maturity level classification. Choose the most appropriate level:\n"
            "- L1 (beginner): Simple questions, minimal context provided, sporadic usage, trial-and-error\n"
            "- L2 (intermediate): Provides context and details, regular daily usage, 2-4 turn solutions\n"
            "- L3 (advanced): Custom assistants, complex multi-domain problems, AI integrated in all workflows"
        )
    )
    indicators: MaturityIndicators = Field(description="Specific indicators supporting the maturity level assessment")
    justification: str = Field(
        description="Explanation of why this maturity level was assigned based on the conversation"
    )


class AntiPattern(BaseModel):
    """Identified anti-pattern that actually occurred in the conversation.

    CRITICAL: Only include anti-patterns that were ACTUALLY OBSERVED (occurrences > 0).
    Do NOT include anti-patterns as suggestions or recommendations if they did not occur.
    If no anti-patterns are found, return an empty list.
    """

    class Config:
        extra = "forbid"  # Ensures additionalProperties: false for Azure structured output

    pattern: AntiPatternCode = Field(description="Anti-pattern identification code")
    severity: Severity = Field(
        description=(
            "Impact severity level. Choose the most appropriate level:\n"
            "- low: Minor inefficiency with minimal impact; slightly suboptimal but functional\n"
            "- medium: Notable inefficiency that affects productivity; wastes time or effort\n"
            "- high: Significant issue that wastes substantial effort or blocks progress\n"
            "- critical: Severe problem causing total failure or fundamentally wrong approach"
        )
    )
    occurrences: int = Field(
        gt=0,
        description="Number of times this anti-pattern appeared in the conversation. MUST be at least 1. "
        "If an issue did not occur, do NOT include it in the list.",
    )
    example: str = Field(
        min_length=1,
        description="Specific instance from the conversation demonstrating this anti-pattern (max 150 chars). "
        "REQUIRED - must be a real quote or description from the conversation, cannot be empty.",
    )
    recommendation: str = Field(description="Actionable advice for improving this behavior (max 200 chars)")
    potential_improvement: str = Field(description="Expected benefit if this anti-pattern is addressed (max 100 chars)")


class ConversationAnalysisQueue(BaseModelWithSQLSupport, table=True):
    """Intermediate table for tracking conversations pending analysis"""

    __tablename__ = "conversation_analysis_queue"

    conversation_id: str = SQLModelField(sa_column=Column(String, nullable=False, index=True))
    status: AnalysisStatus = SQLModelField(
        sa_column=Column(SAEnum(AnalysisStatus), nullable=False, index=True, default=AnalysisStatus.PENDING)
    )
    retry_count: int = SQLModelField(default=0)
    error_message: Optional[str] = SQLModelField(default=None, max_length=500)
    claimed_by_pod: Optional[str] = SQLModelField(default=None)  # K8s pod name that claimed this record
    claimed_at: Optional[datetime] = SQLModelField(default=None)

    __table_args__ = (
        Index("idx_queue_status_date", "status", "date"),  # Query efficiency
        # NOTE: No unique constraint on conversation_id - same conversation can be queued multiple times
        # (for reprocessing after updates), but only one pending/processing record at a time
        Index("idx_queue_conversation_status", "conversation_id", "status"),  # Reprocessing detection
    )


class ConversationAnalytics(BaseModelWithSQLSupport, table=True):
    """Final results table storing LLM analysis results"""

    __tablename__ = "conversation_analytics"

    conversation_id: str = SQLModelField(sa_column=Column(String, nullable=False, index=True, unique=True))

    # User context
    user_id: str = SQLModelField(index=True)
    user_name: str
    project: Optional[str] = None

    # Assistants used
    assistants_used: List[AssistantUsed] = SQLModelField(
        default_factory=list, sa_column=Column(PydanticListType(AssistantUsed))
    )

    # Analysis results
    topics: List[TopicAnalysis] = SQLModelField(default_factory=list, sa_column=Column(PydanticListType(TopicAnalysis)))
    satisfaction: Optional[SatisfactionMetrics] = SQLModelField(
        default=None, sa_column=Column(PydanticType(SatisfactionMetrics))
    )
    maturity: Optional[MaturityAnalysis] = SQLModelField(default=None, sa_column=Column(PydanticType(MaturityAnalysis)))
    anti_patterns: List[AntiPattern] = SQLModelField(
        default_factory=list, sa_column=Column(PydanticListType(AntiPattern))
    )

    # Reprocessing tracking fields
    last_analysis_date: datetime = SQLModelField(default_factory=datetime.utcnow, index=True)
    message_count_at_analysis: int = SQLModelField(default=0)

    # Metadata
    analyzed_at: datetime = SQLModelField(default_factory=datetime.utcnow)  # Deprecated: use last_analysis_date
    llm_model_used: str  # Track which model performed analysis
    analysis_duration_seconds: float  # Performance metric

    __table_args__ = (
        Index("idx_analytics_user_id", "user_id"),
        Index("idx_analytics_last_analysis", "last_analysis_date"),
        Index("idx_analytics_conversation_id", "conversation_id", unique=True),  # Enforce one record per conversation
    )
