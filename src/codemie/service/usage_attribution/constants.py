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

"""Constants of the Usage Attribution analysis: Elasticsearch fields, categories, signal and rule labels."""

from __future__ import annotations

from codemie.service.budget.attribution_resolver import FallbackKind

FEATURE_KEY = "usageAttribution"
USAGE_ATTRIBUTION_COMPONENT_ID = f"features:{FEATURE_KEY}"

# Bump when the analysis logic changes in a way that changes results: it is part of every analysis cache key
# (2: Web Platform who pays follows the dated funding of each member, funding.py).
CLASSIFIER_VERSION = "2"
# Bump when the shape or the meaning of the cached Elasticsearch/app DB inputs changes (2: records without a
# repository field join the empty value; 3: Web Platform funding, A9; 4: A5 framework sessions are also counted in
# the CLI analytics Postgres).
INPUTS_VERSION = "4"

# ---------------------------------------------------------------------------------------------------------------------
# Elasticsearch (codemie_metrics_logs*). Field paths follow service/analytics/handlers/cli/constants.py.
# ---------------------------------------------------------------------------------------------------------------------
F_METRIC = "metric_name.keyword"
F_TS = "@timestamp"
F_USER_ID = "attributes.user_id.keyword"
F_USER_NAME = "attributes.user_name.keyword"
F_PROJECT = "attributes.project.keyword"
F_REPOSITORY = "attributes.repository.keyword"
F_BRANCH = "attributes.branch.keyword"
F_SESSION = "attributes.session_id.keyword"
F_CLIENT = "attributes.codemie_client.keyword"
F_MODEL = "attributes.llm_model.keyword"
F_MONEY = "attributes.money_spent"
F_CLI_REQUEST = "attributes.cli_request"
F_FALLBACK = "attributes.budget_fallback_from.keyword"
# "default_project" or "personal"; absent on records written before EPMCDME-16625 (all of those were personal).
F_FALLBACK_KIND = "attributes.budget_fallback_kind.keyword"
FALLBACK_KIND_DEFAULT_PROJECT = FallbackKind.DEFAULT_PROJECT.value
F_CUSTOMER = "attributes.litellm_customer_id.keyword"
F_TOOL_NAMES = "attributes.tool_names.keyword"
F_LINES_ADDED = "attributes.total_lines_added"
F_LINES_REMOVED = "attributes.total_lines_removed"
F_PROMPTS = "attributes.total_user_prompts"
F_MCP_PROJECT = "attributes.mcp_project_server_names.keyword"
F_SKILLS_PROJECT = "attributes.skills_project_names.keyword"
F_ASSISTANT = "attributes.assistant_name.keyword"
F_WORKFLOW = "attributes.workflow_name.keyword"
F_DATASOURCE = "attributes.repo_name.keyword"

METRIC_CLI_PROXY = "codemie_litellm_proxy_usage"  # CLI when attributes.cli_request is true
METRIC_CLI_TOOLS = "codemie_cli_tool_usage_total"
METRIC_CLI_SESSION = "codemie_cli_session_total"
METRIC_WEB_ASSISTANT = "conversation_assistant_usage"
METRIC_WEB_WORKFLOW = "workflow_execution_total"
METRIC_WEB_DATASOURCE = "datasource_tokens_usage"
WEB_METRICS = {
    "assistant": METRIC_WEB_ASSISTANT,
    "workflow": METRIC_WEB_WORKFLOW,
    "datasource": METRIC_WEB_DATASOURCE,
}
WEB_KIND_BY_METRIC = {metric: kind for kind, metric in WEB_METRICS.items()}

PROJECT_CUSTOMER_PREFIX = "codemie:project:"

EDIT_TOOL_PATTERNS = ("*edit*", "*write*", "*replace*")
WORK_TRACKING_PATTERNS = ("*jira*", "*gitlab*", "*github*", "*azure*devops*", "*azuredevops*", "*confluence*")
MR_TOOL_PATTERNS = (
    "*create_merge_request*",
    "*create_pull_request*",
    "*createmergerequest*",
    "*createpullrequest*",
)
# Used when features:cliAnalytics.deliveryFramework has no usable rules (Lucene regexp syntax, anchored).
DEFAULT_FRAMEWORK_SKILL_REGEX = "(sdlc-factory:|stlc-factory:|stlc-feature|superpowers:|bmad-|opsx:|speckit\\.).*"

MAX_BUCKETS_DEFAULT = 65536
# Members per project in the Web Platform funding aggregation (A9 and /totals).
FUNDED_MEMBERS_MAX = 10000
ES_REQUEST_TIMEOUT_S = 60

# ---------------------------------------------------------------------------------------------------------------------
# Categories and states
# ---------------------------------------------------------------------------------------------------------------------
PRODUCTION = "production"
LEARNING = "learning"
TESTING = "testing"
EXPERIMENTAL = "experimental"
PET_PROJECT = "pet_project"
UNCLASSIFIED = "unclassified"
CATEGORIES = (PRODUCTION, LEARNING, TESTING, EXPERIMENTAL, PET_PROJECT, UNCLASSIFIED)
EXPERIMENTS = (LEARNING, TESTING, EXPERIMENTAL, PET_PROJECT)
FAMILIES = ("where", "who", "how", "names")
CATEGORY_LABELS = {
    PRODUCTION: "Production",
    LEARNING: "Learning",
    TESTING: "Testing",
    EXPERIMENTAL: "Experimental",
    PET_PROJECT: "Pet Project",
    UNCLASSIFIED: "Unclassified",
}

# Who pays
CHARGED = "charged"
SHARED = "shared"
PERSONAL = "personal"

BUDGET_CATEGORIES = ("platform", "cli", "premium_models")

SIGNAL_LABELS = {
    "where.learning_source": "Learning source (settings)",
    "where.budgeted_reuse": "Used in a shared project with a budget",
    "where.datasource": "Git datasource of a shared project",
    "where.shared_project": "Used inside shared CodeMie projects",
    "where.owner_shared": "Owner shared by other people",
    "where.personal_key": "Personal repository (own name)",
    "where.local_folder": "Local folder (no remote)",
    "who.team": "Team on the repository",
    "who.team_group": "Team on the repository group",
    "who.person_history": "The person's other repositories are production",
    "how.code_changes": "Most sessions change code",
    "how.active_days": "Active on many days",
    "how.work_tools": "Work-tracking or merge-request tools called",
    "how.framework": "Delivery-framework skills",
    "how.mcp_project_work": "Project-scoped work-tracking MCP servers",
    "how.read_only": "Mostly read-only sessions",
    "how.one_off": "One-off use",
    "names.ticket_keys": "Ticket keys in branch names",
    "names.learning": "Learning word in the name",
    "names.testing": "Testing word in the name",
    "names.experimental": "Experiment word in the name",
    "names.pet_project": "Personal-project word in the name",
}

# Identity rule id -> (number in architecture.md §7.4, label)
RULES = {
    "azure": (1, "Azure DevOps remote, reported as _git/<repo>"),
    "azure_generic": (1, "Azure DevOps remote with a generic name, kept per person"),
    "ignored": (2, "Ignored: temporary, scratch or Cowork folder, or no value"),
    "subfolder": (3, "Subfolder of a repository the same person uses"),
    "local_folder": (4, "Local folder: common folder name"),
    "folder_like_owner": (4, "Local folder: folder-like owner detected from the data"),
    "no_slash": (4, "No remote: a single folder name"),
    "own_name": (5, "Personal: the owner is the person's own name"),
    "folder_like_remote": (6, "Local folder named like a remote the same person uses"),
    "personal_like_remote": (6, "Own-name repository named like a remote the same person uses"),
    "remote": (7, "Git remote owner/repo"),
}

# Unclassified reasons
NO_REPOSITORY = "no_repository"
OUTSIDE_LIMITS = "outside_limits"
LOW_CONFIDENCE = "low_confidence"
PRODUCTION_ONE_FAMILY = "production_one_family"
NO_EVIDENCE = "no_evidence"
TIE = "tie"
LIKELY_PRODUCTION = "likely_production"
REASONS = {
    NO_REPOSITORY: "No repository: desktop and IDE clients, ignored folders or no value",
    OUTSIDE_LIMITS: "Outside the analysis limits (beyond each person's top repositories)",
    LIKELY_PRODUCTION: "Likely production: production leads, but on one kind of evidence or below the confidence",
    LOW_CONFIDENCE: "Below the minimum confidence",
    PRODUCTION_ONE_FAMILY: "Production evidence from fewer than two families",
    NO_EVIDENCE: "No evidence",
    TIE: "Tie between two categories",
}
# Reasons grouped as the tile and How It Works show them; likely production is shown on its own.
TOO_LITTLE_EVIDENCE_REASONS = (LOW_CONFIDENCE, PRODUCTION_ONE_FAMILY, NO_EVIDENCE, TIE)

# ---------------------------------------------------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------------------------------------------------
# v2: A2 reads budget_fallback_from per person and the analysis adds the production work section (People).
# v3: a fallback charged to the user's default project is charged back there, not paid personally (EPMCDME-16625).
CACHE_NAMESPACE = "codemie:ua:v3"
DETAIL_SHARDS = 16
# The last good analysis of a period and settings is kept a day, so a first visit in the morning shows yesterday's
# analysis at once, marked with its time, while the new one is computed.
LAST_GOOD_TTL_S = 24 * 3600
# The slow read, the evidence over at least 30 days, changes little within an hour: the first computation of the
# hour reads it, and the later ones reuse it and read only the spend of their period.
SLOW_READ_REUSE_S = 3600
REFRESH_MIN_INTERVAL_S = 60
WAIT_FOR_OTHER_POD_S = 60
CUSTOM_COMPUTATION_WINDOW_S = 3600
# After a failed computation, requests for the same key get the error (or the last good analysis) for this long
# instead of starting another full read, so a struggling Elasticsearch is not hit again on every page load.
FAILURE_BACKOFF_S = 60
# The A5 framework-session read in the CLI analytics Postgres is bounded; past it A5 keeps the Elasticsearch counts.
PG_FRAMEWORK_TIMEOUT_S = 10.0
# An analysis computed without the Postgres framework sessions (the read or the merge failed) is served for this long
# only, instead of ``analysisCacheMinutes``, so the next requests for the same range read Postgres again.
PG_DEGRADED_TTL_S = 60
# The skill-name read of the person view is bounded too; past it the view classifies by the Elasticsearch keys alone.
PG_SKILL_NAMES_TIMEOUT_S = 5.0
# Computations of one kind (default or custom settings) that may wait for the running one, per API process; more
# are refused at once (503) instead of queueing.
MAX_WAITING_PER_KIND = 3
# Exit code of a computation process stopped by its memory watchdog (worker.py).
WORKER_MEMORY_EXIT_CODE = 75

# Sections of one cached analysis
SECTION_SUMMARY = "summary"
SECTION_SHARED = "shared"
SECTION_GROUPS = "groups"
SECTION_PEOPLE = "people"
# Production work paid personally, per person and repository, with the conditions people share (people_work.py)
SECTION_PEOPLE_WORK = "pwork"
# How People can group chargeback candidates (people_groups.py)
PEOPLE_GROUP_BY = ("common", "project", "repositories", "organization")
SECTION_BREAKDOWN = "breakdown"
SECTION_SHARED_DETAIL = "sdetail"
SECTION_GROUP_DETAIL = "gdetail"
SECTION_PERSON_DETAIL = "pdetail"
LIST_SECTIONS = (
    SECTION_SUMMARY,
    SECTION_SHARED,
    SECTION_GROUPS,
    SECTION_PEOPLE,
    SECTION_PEOPLE_WORK,
    SECTION_BREAKDOWN,
)
SHARDED_SECTIONS = (SECTION_SHARED_DETAIL, SECTION_GROUP_DETAIL, SECTION_PERSON_DETAIL)
