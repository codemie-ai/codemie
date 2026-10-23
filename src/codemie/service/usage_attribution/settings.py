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

"""Settings of the Usage Attribution analysis.

Defaults come from the code below, overridden by the ``features:usageAttribution`` component of the customer
configuration. Admins and maintainers may override the tunable settings for their own view: the UI sends the changed
values with each request (``?overrides=`` base64url JSON of ``{dotted.key: value}``), they are validated against the
schema below and the result is cached under its own hashes, so the default analysis is never affected.

Each tunable setting belongs to a group, and each group to a cost tier:
- ``view``: applied when a response is built, nothing is recomputed;
- ``analysis``: the analysis reruns on the Elasticsearch data already cached for the period;
- ``fetch``: Elasticsearch is read again.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from codemie.service.usage_attribution.constants import CATEGORIES, FAMILIES, UNCLASSIFIED

SCHEMA_VERSION = 1

TIER_VIEW = "view"
TIER_ANALYSIS = "analysis"
TIER_FETCH = "fetch"
TIER_RANK = {TIER_VIEW: 0, TIER_ANALYSIS: 1, TIER_FETCH: 2}
TIERS = {
    TIER_VIEW: {"label": "Instant", "description": "Applied to the cached analysis. Nothing is recomputed."},
    TIER_ANALYSIS: {
        "label": "Re-analysis, about 4 s",
        "description": "Reuses the Elasticsearch data cached for this period and reruns identity, grouping, "
        "classification and allocation.",
    },
    TIER_FETCH: {
        "label": "New read, about 10 s",
        "description": "Reads Elasticsearch again, then reruns the analysis. Use it for troubleshooting.",
    },
}

GROUPS = (
    {"id": "view", "label": "Display", "tier": TIER_VIEW},
    {"id": "classify", "label": "Classification", "tier": TIER_ANALYSIS},
    {"id": "alloc", "label": "Allocation", "tier": TIER_ANALYSIS},
    {"id": "identity", "label": "Identity and Grouping", "tier": TIER_ANALYSIS},
    {"id": "fetch", "label": "Retrieval", "tier": TIER_FETCH},
)
GROUP_TIER = {g["id"]: g["tier"] for g in GROUPS}

# Component settings that are not analysis settings.
_COMPONENT_META_KEYS = {"enabled", "name", "description", "availableForExternal", "url", "created_by", "icon_url"}

LIST_MAX_ITEMS = 50
LIST_ITEM_MAX_LENGTH = 100
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z$")

# ---------------------------------------------------------------------------------------------------------------------
# Code defaults. classification-model.md §11 and architecture.md §14; calibrated on production data on 2026-10-13.
# ---------------------------------------------------------------------------------------------------------------------
CODE_DEFAULTS: dict[str, Any] = {
    # Display
    "sharedProjectThresholdPer30Days": 1000,
    "repositoryGroupThresholdPer30Days": 1000,
    "experimentsHighlightPer30Days": 500,
    "platformPeopleFilterPer30Days": 300,
    # A chargeback candidate does production work from a personal budget on this many days per 30 days of the
    # evidence window (People).
    "candidateProductionDaysPer30Days": 5,
    # Classification
    "minimumConfidence": 50,
    # Production against the repository's name (learning, testing, experiment, pet project) needs more.
    "contradictedProductionMinConfidence": 60,
    # Unclassified repositories where Production leads with at least this confidence are shown as likely production.
    "likelyProductionMinConfidence": 40,
    "confidenceConstant": 6,
    "productionMinFamilies": 2,
    "familyWeights": {"where": 1, "who": 1, "how": 1, "names": 1},
    "namesCap": 12,
    "teamSignalPeople": 3,
    "sustainedWork": {
        "enabled": True,
        "all": ["how.code_changes", "how.active_days"],
        "anyOf": ["how.work_tools", "how.framework", "how.mcp_project_work", "names.ticket_keys"],
    },
    "thresholds": {
        "minToolSessions": 3,
        "editShareProduction": 0.5,
        "readOnlyShareLearning": 0.8,
        "activeDaysProduction": 5,
        "oneOffMaxDays": 1,
        "oneOffMaxSessions": 2,
        "workToolMinSessions": 1,
        "budgetedReuseMinSpend": 10,
        "budgetedReuseMinShare": 0.05,
        "ownerSharedMinPeople": 2,
        "sharedProjectMinShare": 0.5,
    },
    # A weak Who signal for a person whose other classified work is mostly Production.
    "personHistory": {"enabled": True, "minProductionShare": 0.7, "minClassifiedSpend": 20},
    # Allocation
    "allocateByPerson": {"enabled": True, "minClassifiedShare": 0.1, "minClassifiedSpend": 5},
    # Unclassified spend inside a shared project, of people without enough classified work, by the project's work.
    "allocateByProject": {"enabled": True, "minClassifiedShare": 0.3, "minClassifiedSpend": 50},
    "notChargedBack": {"excludeSharedExperiments": True},
    # Identity and grouping
    "groupMinSignals": 2,
    "maxGroupSize": 40,
    "maxProjectRepositoriesForGrouping": 50,
    "maxTicketPrefixRepositories": 50,
    "maxOwnerRepositoriesForGrouping": 200,
    "minProjectShareForGrouping": 0.1,
    "folderOwnerMinPeople": 5,
    "folderOwnerMinRepos": 10,
    "folderOwnerMaxSharedShare": 0.1,
    "sessionlessClients": [
        "claude-desktop",
        "codemie-daemon",
        "vscode-byok",
        "codex-desktop",
        "t3-code",
        "codemie-cli",
        "unknown",
        "claude-code-otlp",
    ],
    "learningSources": ["codemie-katas/*", "epam-learn/*"],
    "excludedRepositories": ["epam/*"],
    "ownerProjectRules": [],
    "ignoredValues": ["", "n/a", "na", "none", "null", "unknown", "undefined", "-", "/", ".", "cowork"],
    "ignoredValuePrefixes": ["cowork"],
    # A CLI started in C:\Windows\System32 reports WINDOWS/system32: not a repository. Nor is a Cowork folder,
    # wherever it is in the path.
    "ignoredSegments": ["temp", "tmp", "scratchpad", "scratch", "system32", "syswow64", "cowork"],
    "ignoredSegmentPrefixes": ["tmp.", "tmp-", "tmp_"],
    "localFolderOwners": [
        "users",
        "home",
        "projects",
        "project",
        "repos",
        "repo",
        "git",
        "src",
        "code",
        "dev",
        "work",
        "workspace",
        "workspaces",
        "documents",
        "desktop",
        "source",
        "sources",
        "tmp",
        "temp",
        "downloads",
        "ideaprojects",
        "pycharmprojects",
        "webstormprojects",
        "github",
        "gitlab",
        "codebase",
        "dev-projects",
        "repositories",
        "git-repos",
        "gitrepos",
        "my-projects",
        "myprojects",
        "coding",
        "development",
        "devel",
    ],
    "genericAzureNames": [
        "api",
        "app",
        "apps",
        "backend",
        "frontend",
        "front",
        "web",
        "ui",
        "core",
        "common",
        "infra",
        "infrastructure",
        "docs",
        "doc",
        "service",
        "services",
        "server",
        "client",
        "tests",
        "test",
        "main",
        "platform",
        "portal",
        "mobile",
        "admin",
        "shared",
        "utils",
        "tools",
        "scripts",
        "config",
        "deploy",
        "deployment",
        "pipelines",
        "pipeline",
        "terraform",
        "helm",
        "data",
        "etl",
        "ml",
        "automation",
        "src",
        "lib",
        "libs",
        "sdk",
        "database",
        "db",
        "integration",
        "integrations",
        "gateway",
        "auth",
    ],  # fmt: skip
    "genericTicketPrefixes": [
        "FEATURE",
        "FEATURES",
        "FEAT",
        "FIX",
        "FIXES",
        "BUGFIX",
        "HOTFIX",
        "HF",
        "TEST",
        "TESTS",
        "RELEASE",
        "TASK",
        "TASKS",
        "STORY",
        "STORIES",
        "US",
        "BUG",
        "BUGS",
        "DEV",
        "ISSUE",
        "ISSUES",
        "CHORE",
        "REFACTOR",
        "DOCS",
        "DOC",
        "CI",
        "CD",
        "BUILD",
        "PR",
        "MR",
        "WIP",
        "VER",
        "VERSION",
        "RC",
        "SPRINT",
        "PHASE",
        "STEP",
        "PART",
        "TEMP",
        "TMP",
        "COPY",
        "BACKUP",
        "BRANCH",
        "MAIN",
        "MASTER",
        "DEVELOP",
        "UPDATE",
        "UPGRADE",
        "ADD",
        "REMOVE",
        "SUPPORT",
        "PYTHON",
        "PY",
        "NODE",
        "JAVA",
        "JDK",
        "DOTNET",
        "NET",
        "ANGULAR",
        "REACT",
        "VUE",
        "SPRING",
        "CLAUDE",
        "CODEX",
        "COPILOT",
        "GPT",
        "AGENT",
        "SESSION",
        "WORKTREE",
        "MIGRATION",
        "IMPROVEMENT",
        "ENHANCEMENT",
        "EPIC",
        "SUBTASK",
        "ITEM",
        "REQ",
        "CR",
        "POC",
        "DEMO",
        "SPIKE",
        "DRAFT",
        "EXPERIMENT",
        "NEW",
        "OLD",
        "E2E",
        "QA",
        "UAT",
        "SIT",
        "PROD",
        "STAGE",
        "STAGING",
        "LOCAL",
        "DAY",
        "WEEK",
        "MONTH",
        "PLAN",
        "ITERATION",
        "TICKET",
        "CHANGE",
        "CHANGES",
    ],  # fmt: skip
    "namePatterns": {
        "learning": "tutorials?|katas?|courses?|learn|learning|trainings?|workshops?|lessons?|exercises?|bootcamp|"
        "homework|study|studies|udemy|coursera|leetcode|hackerrank|codewars|practice|academy",
        "testing": "tests?|testing|qa|e2e|autotests?|load-tests?|perf-tests?|selenium|cypress|jmeter|k6",
        "experimental": "demos?|pocs?|sandbox|playground|experiments?|try|tryout|spikes?|prototypes?|hello-world|"
        "helloworld|samples?|examples?|scratch|draft|hackathon|research|mvp",
        "pet_project": "dotfiles|personal|blog|portfolio|homelab|home-lab|resume|cv|notes|diary|hobby|"
        "side-projects?|sideproject|pet-projects?",
    },
    # Signal points: [category, points]. Configuration only, never tunable in the UI.
    "weights": {
        "where.learning_source": ["learning", 100],
        "where.budgeted_reuse": ["production", 10],
        "where.datasource": ["production", 8],
        "where.shared_project": ["production", 6],
        "where.owner_shared": ["production", 6],
        "where.personal_key": ["pet_project", 10],
        "where.local_folder": ["experimental", 4],
        "who.team": ["production", 10],
        "who.team_group": ["production", 5],
        "who.person_history": ["production", 5],
        "how.code_changes": ["production", 8],
        "how.active_days": ["production", 8],
        "how.work_tools": ["production", 10],
        "how.framework": ["production", 8],
        "how.mcp_project_work": ["production", 4],
        "how.read_only": ["learning", 12],
        "how.one_off": ["experimental", 12],
        "names.ticket_keys": ["production", 4],
        "names.learning": ["learning", 10],
        "names.testing": ["testing", 12],
        "names.experimental": ["experimental", 8],
        "names.pet_project": ["pet_project", 8],
    },
    # Retrieval
    "repositoriesPerPerson": 10,
    "repositoriesPerSharedProject": 50,
    "minimumEvidenceDays": 30,
    "elasticsearchConcurrency": 8,
    "partitionBucketShare": 0.6,
    # Not tunable
    # When this environment started to record the budget fallback field (informational, shown in How It Works).
    "fallbackFieldSince": "2026-10-09T12:00:00Z",
    "maxRangeDays": 90,
    "analysisCacheMinutes": 15,
    "analysisTimeoutSeconds": 120,
    # A computation process that goes above this much memory is stopped (keep it below the pod's memory limit,
    # with room for the API process and a second computation).
    "workerMemoryLimitMb": 3072,
    "tuning": {
        "enabled": True,
        "roles": ["admin", "maintainer"],
        "maxOverrideBytes": 4096,
        "maxCustomComputationsPerHour": 10,
        # Every computation a user starts (a period or settings not in the cache yet) counts, default settings too.
        "maxComputationsPerHour": 30,
    },
}

# Settings that are neither part of the analysis nor of the retrieval: changing them never changes results.
_NON_ANALYSIS_KEYS = {
    "maxRangeDays",
    "analysisCacheMinutes",
    "analysisTimeoutSeconds",
    "workerMemoryLimitMb",
    "tuning",
    # Informational: the queries tell records with the budget fallback field by the field itself.
    "fallbackFieldSince",
}


@dataclass(frozen=True)
class SettingSpec:
    """One tunable setting of the Analysis Settings dialog."""

    key: str
    group: str
    type: str  # money | int | float | share | bool | list | rules | datetime
    label: str
    description: str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    unit: str | None = None

    @property
    def tier(self) -> str:
        return GROUP_TIER[self.group]


SCHEMA: tuple[SettingSpec, ...] = (
    # Display: applied when a response is built
    SettingSpec(
        "sharedProjectThresholdPer30Days",
        "view",
        "money",
        "Shared project candidate",
        "Chargeback Potential per 30 days, scaled to the period. The default view of Shared Projects Without a Budget.",
        0,
        100000,
        100,
    ),
    SettingSpec(
        "repositoryGroupThresholdPer30Days",
        "view",
        "money",
        "Repository group candidate",
        "Production CLI spend in personal projects per 30 days, per repository group, scaled to the period.",
        0,
        100000,
        100,
    ),
    SettingSpec(
        "experimentsHighlightPer30Days",
        "view",
        "money",
        "Experiments highlight",
        "Per person per 30 days, scaled to the period. Highlights and filters People.",
        0,
        10000,
        50,
    ),
    SettingSpec(
        "platformPeopleFilterPer30Days",
        "view",
        "money",
        "Web Platform people filter",
        "Per person per 30 days, scaled to the period.",
        0,
        10000,
        50,
    ),
    SettingSpec(
        "candidateProductionDaysPer30Days",
        "view",
        "int",
        "Chargeback candidate",
        "Days with production work from a personal budget, per 30 days of the evidence window. Marks chargeback "
        "candidates on People.",
        1,
        30,
        1,
    ),
    # Classification
    SettingSpec(
        "minimumConfidence",
        "classify",
        "int",
        "Minimum confidence",
        "Repositories below it are Unclassified.",
        30,
        95,
        5,
    ),
    SettingSpec(
        "contradictedProductionMinConfidence",
        "classify",
        "int",
        "Confidence for Production against the name",
        "When the repository name points to learning, testing, an experiment or a pet project.",
        30,
        95,
        5,
    ),
    SettingSpec(
        "likelyProductionMinConfidence",
        "classify",
        "int",
        "Likely production from",
        "Unclassified repositories where Production leads with at least this confidence are shown as likely "
        "production, apart from Production.",
        0,
        95,
        5,
    ),
    SettingSpec(
        "personHistory.enabled",
        "classify",
        "bool",
        "Person's history as evidence",
        "A weak Who signal for repositories of a person whose other classified work is mostly Production.",
    ),
    SettingSpec(
        "personHistory.minProductionShare",
        "classify",
        "share",
        "Production share of the person's work",
        "Of the person's classified spend on other repositories.",
        0.5,
        1,
        0.05,
    ),
    SettingSpec(
        "confidenceConstant",
        "classify",
        "int",
        "Confidence constant",
        "Confidence = 100 × top ÷ (top + runner-up + constant) × coverage. A lower constant lets thin evidence "
        "count more.",
        0,
        20,
        1,
    ),
    SettingSpec(
        "productionMinFamilies",
        "classify",
        "int",
        "Evidence families for Production",
        "Where, Who, How and Names. Names alone never make Production.",
        1,
        4,
        1,
    ),
    SettingSpec(
        "sustainedWork.enabled",
        "classify",
        "bool",
        "Sustained work counts as two families",
        "Code changes on several days, plus work tracking, a delivery framework or ticket keys.",
    ),
    SettingSpec(
        "thresholds.activeDaysProduction",
        "classify",
        "int",
        "Active days",
        "Days of activity for the active-days signal and for sustained work.",
        2,
        30,
        1,
        "days",
    ),
    SettingSpec(
        "teamSignalPeople",
        "classify",
        "int",
        "Team signal",
        "People on the same repository or repository group.",
        2,
        20,
        1,
        "people",
    ),
    SettingSpec(
        "thresholds.readOnlyShareLearning",
        "classify",
        "share",
        "Read-only share for Learning",
        "Share of sessions without code changes.",
        0.5,
        1,
        0.05,
    ),
    SettingSpec(
        "namesCap",
        "classify",
        "int",
        "Names cap",
        "Most points that repository and branch names add to one category.",
        0,
        40,
        1,
        "points",
    ),
    # Allocation
    SettingSpec(
        "allocateByPerson.enabled",
        "alloc",
        "bool",
        "Allocation by person",
        "Splits a person's unclassified CLI spend by their classified work. Always shown apart.",
    ),
    SettingSpec(
        "allocateByPerson.minClassifiedShare",
        "alloc",
        "share",
        "Minimum classified share",
        "Of the person's CLI spend, before anything is allocated.",
        0.1,
        0.9,
        0.05,
    ),
    SettingSpec(
        "allocateByPerson.minClassifiedSpend",
        "alloc",
        "money",
        "Minimum classified spend",
        "Per person in the period.",
        0,
        1000,
        5,
    ),
    SettingSpec(
        "allocateByProject.enabled",
        "alloc",
        "bool",
        "Allocation by shared project",
        "Splits unclassified CLI spend inside a shared project by the project's classified work, for people without "
        "enough classified work of their own. Always shown apart.",
    ),
    SettingSpec(
        "allocateByProject.minClassifiedShare",
        "alloc",
        "share",
        "Minimum classified share of the project",
        "Of the project's CLI spend.",
        0.1,
        0.9,
        0.05,
    ),
    SettingSpec(
        "notChargedBack.excludeSharedExperiments",
        "alloc",
        "bool",
        "Leave experiments in shared projects out of Chargeback Potential",
        "CLI work classified as an experiment inside a shared project does not count toward Chargeback Potential.",
    ),
    # Identity and grouping
    SettingSpec(
        "groupMinSignals",
        "identity",
        "int",
        "Signals to link two repositories",
        "Same owner, ticket key, people in common, shared project, Git datasource or owner rule.",
        2,
        4,
        1,
    ),
    SettingSpec(
        "maxGroupSize",
        "identity",
        "int",
        "Largest group",
        "Bigger groups are split by owner.",
        5,
        200,
        5,
        "repositories",
    ),
    SettingSpec(
        "folderOwnerMinPeople",
        "identity",
        "int",
        "Folder-like owner: people",
        "An owner used as a folder name by this many people…",
        2,
        50,
        1,
        "people",
    ),
    SettingSpec(
        "folderOwnerMinRepos",
        "identity",
        "int",
        "Folder-like owner: folders",
        "…with this many different folders is treated as a local folder.",
        2,
        100,
        1,
        "folders",
    ),
    SettingSpec(
        "localFolderOwners",
        "identity",
        "list",
        "Local folder names",
        "Owners that are always local folders. Windows drive letters are always local.",
    ),
    SettingSpec(
        "ignoredSegments",
        "identity",
        "list",
        "Ignored folders",
        "Paths with these folders count as no repository.",
    ),
    SettingSpec(
        "learningSources",
        "identity",
        "list",
        "Learning sources",
        "Repository patterns that are always Learning.",
    ),
    SettingSpec(
        "excludedRepositories",
        "identity",
        "list",
        "Excluded repositories",
        "Public open-source work, left out of the candidates and groups.",
    ),
    SettingSpec(
        "ownerProjectRules",
        "identity",
        "rules",
        "Owner → shared project",
        "Shown as the related shared project when no other rule finds one.",
    ),
    SettingSpec(
        "sessionlessClients",
        "identity",
        "list",
        "Clients without session ids",
        "They get a new session id for every request; their sessions come from CLI tool metrics.",
    ),
    # Retrieval
    SettingSpec(
        "repositoriesPerPerson",
        "fetch",
        "int",
        "Repositories per person",
        "Read for each person. The rest counts as outside the limits.",
        5,
        25,
        1,
    ),
    SettingSpec(
        "repositoriesPerSharedProject",
        "fetch",
        "int",
        "Repositories per shared project",
        "Shown in the project popup and used for the production share of a shared project.",
        10,
        200,
        10,
    ),
    SettingSpec(
        "minimumEvidenceDays",
        "fetch",
        "int",
        "Minimum evidence window",
        "Shorter periods use this many days, ending at the period end, as evidence. Money always covers the "
        "selected period.",
        7,
        90,
        1,
        "days",
    ),
    SettingSpec(
        "elasticsearchConcurrency",
        "fetch",
        "int",
        "Parallel Elasticsearch requests",
        "Lower it when Elasticsearch is under load.",
        1,
        12,
        1,
    ),
    SettingSpec(
        "partitionBucketShare",
        "fetch",
        "float",
        "Partition size",
        "Share of search.max_buckets per request. Lower means more, smaller requests.",
        0.2,
        0.8,
        0.05,
    ),
)
SCHEMA_BY_KEY = {spec.key: spec for spec in SCHEMA}
FETCH_KEYS = tuple(spec.key for spec in SCHEMA if spec.tier == TIER_FETCH)
VIEW_KEYS = tuple(spec.key for spec in SCHEMA if spec.tier == TIER_VIEW)

# Bounds of configuration-only settings, checked when the defaults load.
_CONFIG_BOUNDS = {
    "maxRangeDays": (1, 366),
    "analysisCacheMinutes": (1, 1440),
    "analysisTimeoutSeconds": (30, 900),
    "workerMemoryLimitMb": (512, 65536),
    "tuning.maxOverrideBytes": (256, 16384),
    "tuning.maxCustomComputationsPerHour": (1, 1000),
    "tuning.maxComputationsPerHour": (1, 1000),
    "maxProjectRepositoriesForGrouping": (2, 1000),
    "maxTicketPrefixRepositories": (2, 1000),
    "maxOwnerRepositoriesForGrouping": (2, 5000),
    "minProjectShareForGrouping": (0, 1),
    "folderOwnerMaxSharedShare": (0, 1),
}


class SettingsError(ValueError):
    """A setting failed validation. ``key`` names the setting (None when the payload itself is invalid)."""

    def __init__(self, key: str | None, message: str):
        self.key = key
        self.message = message
        super().__init__(f"{key}: {message}" if key else message)


class SettingsConfigError(RuntimeError):
    """The customer configuration holds a value the schema rejects."""


# ---------------------------------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------------------------------
def stable_hash(obj: Any, length: int = 32) -> str:
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def get_path(data: dict, dotted: str, default: Any = None) -> Any:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def set_path(data: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def deep_merge(base: dict, override: dict) -> dict:
    """Merge configuration onto the code defaults: nested dicts merge, lists and scalars replace."""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict) and key != "namePatterns":
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _fmt_bound(spec: SettingSpec, value: float) -> str:
    if spec.type == "money":
        return f"${value:,.0f}"
    if spec.type == "share":
        return f"{value * 100:.0f}%"
    return f"{value:g}"


def normalize_value(spec: SettingSpec, value: Any) -> Any:
    """Validate one value against its spec and return its normalized form. Raises SettingsError."""
    if spec.type == "bool":
        if not isinstance(value, bool):
            raise SettingsError(spec.key, "must be true or false")
        return value
    if spec.type in ("money", "int", "float", "share"):
        return _normalize_number(spec, value)
    if spec.type == "list":
        return _normalize_list(spec, value)
    if spec.type == "rules":
        return normalize_rules(value, spec.key)
    raise SettingsError(spec.key, f"unsupported type {spec.type}")  # pragma: no cover - schema is static


def _normalize_number(spec: SettingSpec, value: Any) -> int | float:
    if not _is_number(value):
        raise SettingsError(spec.key, "must be a number")
    if spec.type == "int":
        if float(value) != int(value):
            raise SettingsError(spec.key, "must be a whole number")
        value = int(value)
    if spec.minimum is not None and value < spec.minimum - 1e-9:
        raise SettingsError(
            spec.key, f"must be between {_fmt_bound(spec, spec.minimum)} and {_fmt_bound(spec, spec.maximum)}"
        )
    if spec.maximum is not None and value > spec.maximum + 1e-9:
        raise SettingsError(
            spec.key, f"must be between {_fmt_bound(spec, spec.minimum)} and {_fmt_bound(spec, spec.maximum)}"
        )
    return value


def _normalize_list(spec: SettingSpec, value: Any) -> list[str]:
    if not isinstance(value, list):
        raise SettingsError(spec.key, "must be a list of values")
    if len(value) > LIST_MAX_ITEMS:
        raise SettingsError(spec.key, f"at most {LIST_MAX_ITEMS} values")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise SettingsError(spec.key, "values must be text")
        text = item.strip()
        if len(text) > LIST_ITEM_MAX_LENGTH:
            raise SettingsError(spec.key, f"values must be at most {LIST_ITEM_MAX_LENGTH} characters")
        # An empty value is allowed only in the configuration's own lists (ignoredValues); the UI lists reject it.
        if not text:
            raise SettingsError(spec.key, "values must not be empty")
        if text not in out:
            out.append(text)
    return out


def _normalize_rule_entry(rule: Any, key: str) -> tuple[str, str]:
    """Validate and normalize a single owner/project rule, returning the cleaned owner and project."""
    if not isinstance(rule, dict) or set(rule) != {"owner", "project"}:
        raise SettingsError(key, "each rule needs an owner and a project")
    owner, project = rule.get("owner"), rule.get("project")
    if not isinstance(owner, str) or not isinstance(project, str):
        raise SettingsError(key, "each rule needs an owner and a project")
    owner = re.sub(r"/\*?$", "", owner.strip()).lower()
    project = project.strip()
    if not owner or not project or "/" in owner:
        raise SettingsError(key, "each rule needs an owner (without a slash) and a project")
    if len(re.sub(r"[*?\[\]!]", "", owner)) < 2:
        raise SettingsError(key, "an owner needs at least two characters besides wildcards")
    if len(owner) > LIST_ITEM_MAX_LENGTH or len(project) > LIST_ITEM_MAX_LENGTH:
        raise SettingsError(key, f"owners and projects must be at most {LIST_ITEM_MAX_LENGTH} characters")
    return owner, project


def normalize_rules(value: Any, key: str = "ownerProjectRules") -> list[dict[str, str]]:
    """Owner -> shared project rules: a list of ``{"owner": ..., "project": ...}``."""
    if not isinstance(value, list):
        raise SettingsError(key, "must be a list of owner and project rules")
    if len(value) > LIST_MAX_ITEMS:
        raise SettingsError(key, f"at most {LIST_MAX_ITEMS} rules")
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for rule in value:
        owner, project = _normalize_rule_entry(rule, key)
        if owner in seen:
            continue
        seen.add(owner)
        out.append({"owner": owner, "project": project})
    return out


def _normalize_datetime(key: str, value: Any) -> str:
    if not isinstance(value, str) or not _DATETIME_RE.match(value):
        raise SettingsError(key, "must be a UTC time such as 2026-10-09T12:00:00Z")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SettingsError(key, "must be a UTC time such as 2026-10-09T12:00:00Z") from exc
    return value if len(value) == 20 else value[:-1] + ":00Z"


# ---------------------------------------------------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class DefaultSettings:
    values: dict[str, Any]
    defaults_hash: str


def _validate_schema_values(values: dict[str, Any]) -> None:
    for spec in SCHEMA:
        current = get_path(values, spec.key)
        try:
            normalized = normalize_value(spec, current)
        except SettingsError as exc:
            raise SettingsConfigError(
                f"features:usageAttribution setting {spec.key} is invalid: {exc.message}"
            ) from exc
        set_path(values, spec.key, normalized)


def _validate_config_bounds(values: dict[str, Any]) -> None:
    for key, (low, high) in _CONFIG_BOUNDS.items():
        current = get_path(values, key)
        if not _is_number(current) or not low <= current <= high:
            raise SettingsConfigError(f"features:usageAttribution setting {key} must be between {low} and {high}")


def _is_valid_weight_pair(pair: Any) -> bool:
    return (
        isinstance(pair, (list, tuple))
        and len(pair) == 2
        and pair[0] in CATEGORIES
        and pair[0] != UNCLASSIFIED
        and _is_number(pair[1])
        and pair[1] >= 0
    )


def _validate_weights(values: dict[str, Any]) -> None:
    weights = values.get("weights")
    if not isinstance(weights, dict) or not weights:
        raise SettingsConfigError("features:usageAttribution setting weights must map signals to [category, points]")
    for signal, pair in weights.items():
        if not _is_valid_weight_pair(pair) or signal.split(".")[0] not in FAMILIES:
            raise SettingsConfigError(f"features:usageAttribution weight {signal} must be [category, points]")


def _validate_fallback_field_since(values: dict[str, Any]) -> None:
    try:
        values["fallbackFieldSince"] = _normalize_datetime("fallbackFieldSince", values.get("fallbackFieldSince"))
    except SettingsError as exc:
        raise SettingsConfigError(f"features:usageAttribution setting fallbackFieldSince {exc.message}") from exc


def _validate_tuning_roles(values: dict[str, Any]) -> None:
    roles = get_path(values, "tuning.roles")
    if not isinstance(roles, list) or not all(r in ("admin", "maintainer", "auditor") for r in roles):
        raise SettingsConfigError("features:usageAttribution tuning.roles may hold admin, maintainer and auditor")


def _validate_name_patterns(values: dict[str, Any]) -> None:
    patterns = values.get("namePatterns")
    if not isinstance(patterns, dict):
        raise SettingsConfigError("features:usageAttribution namePatterns must map categories to patterns")
    for category, pattern in patterns.items():
        try:
            re.compile(pattern)
        except (re.error, TypeError) as exc:
            raise SettingsConfigError(f"features:usageAttribution namePatterns.{category} is invalid") from exc


def _validate_config_values(values: dict[str, Any]) -> None:
    """A configuration value outside its bounds fails here, so a default can never be one the UI would reject."""
    _validate_schema_values(values)
    _validate_config_bounds(values)
    _validate_weights(values)
    _validate_fallback_field_since(values)
    _validate_tuning_roles(values)
    _validate_name_patterns(values)


def build_defaults(component_settings: dict[str, Any] | None) -> DefaultSettings:
    """Code defaults merged with the customer configuration, validated."""
    configured = {k: v for k, v in (component_settings or {}).items() if k not in _COMPONENT_META_KEYS}
    values = deep_merge(CODE_DEFAULTS, configured)
    # Rules written in the YAML as a mapping {owner: project} are accepted too.
    if isinstance(values.get("ownerProjectRules"), dict):
        values["ownerProjectRules"] = [{"owner": k, "project": v} for k, v in values["ownerProjectRules"].items()]
    _validate_config_values(values)
    return DefaultSettings(values=values, defaults_hash=stable_hash({"schema": SCHEMA_VERSION, "values": values}))


# ---------------------------------------------------------------------------------------------------------------------
# Overrides
# ---------------------------------------------------------------------------------------------------------------------
def decode_overrides(raw: str | None, max_bytes: int) -> dict[str, Any]:
    """Decode ``?overrides=``: base64url JSON of ``{dotted.key: value}``. Raises SettingsError."""
    if raw is None or raw.strip() == "":
        return {}
    raw = raw.strip()
    if len(raw) > max_bytes:
        raise SettingsError(None, f"Setting overrides are larger than {max_bytes // 1024} KB")
    if not re.fullmatch(r"[A-Za-z0-9_-]+=*", raw):
        raise SettingsError(None, "Setting overrides must be base64url-encoded JSON")
    try:
        text = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
        payload = json.loads(text)
    except ValueError as exc:
        raise SettingsError(None, "Setting overrides must be base64url-encoded JSON") from exc
    if not isinstance(payload, dict):
        raise SettingsError(None, "Setting overrides must be a JSON object of setting keys and values")
    return payload


def encode_overrides(overrides: dict[str, Any]) -> str:
    text = json.dumps(overrides, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def validate_overrides(payload: dict[str, Any], defaults: DefaultSettings) -> dict[str, Any]:
    """Known keys, types and bounds. Values equal to the default are dropped. Raises SettingsError."""
    cleaned: dict[str, Any] = {}
    for key in sorted(payload):
        spec = SCHEMA_BY_KEY.get(key)
        if spec is None:
            raise SettingsError(key, "is not a tunable setting")
        value = normalize_value(spec, payload[key])
        if stable_hash(value) != stable_hash(get_path(defaults.values, key)):
            cleaned[key] = value
    return cleaned


# ---------------------------------------------------------------------------------------------------------------------
# Effective settings
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class EffectiveSettings:
    values: dict[str, Any]
    overrides: dict[str, Any]
    defaults_hash: str
    fetch_hash: str
    analysis_hash: str
    settings_hash: str
    tier: str | None
    extra_fetch: dict[str, Any] = field(default_factory=dict)

    @property
    def is_custom(self) -> bool:
        """Overrides that change the computation (display-only overrides do not)."""
        return self.tier in (TIER_ANALYSIS, TIER_FETCH)

    def get(self, dotted: str, default: Any = None) -> Any:
        return get_path(self.values, dotted, default)

    def info(self) -> dict[str, Any]:
        """The ``analysis.settings`` block of every analysis-backed response."""
        return {
            "hash": self.settings_hash,
            "defaults_hash": self.defaults_hash,
            "schema_version": SCHEMA_VERSION,
            "overridden": sorted(self.overrides),
            "tier": self.tier,
        }


def _analysis_part(values: dict[str, Any]) -> dict[str, Any]:
    part = copy.deepcopy(values)
    for key in VIEW_KEYS + FETCH_KEYS:
        parts = key.split(".")
        node = part
        for p in parts[:-1]:
            node = node.get(p, {})
        node.pop(parts[-1], None)
    for key in _NON_ANALYSIS_KEYS:
        part.pop(key, None)
    return part


def build_effective(
    defaults: DefaultSettings,
    overrides: dict[str, Any],
    extra_fetch: dict[str, Any] | None = None,
) -> EffectiveSettings:
    """Defaults with the (validated) overrides applied, and the hashes that key the caches."""
    from codemie.service.usage_attribution.constants import CLASSIFIER_VERSION, INPUTS_VERSION

    values = copy.deepcopy(defaults.values)
    for key, value in overrides.items():
        set_path(values, key, copy.deepcopy(value))
    extra_fetch = dict(extra_fetch or {})
    fetch_part = {key: get_path(values, key) for key in FETCH_KEYS}
    fetch_hash = stable_hash({"v": INPUTS_VERSION, "fetch": fetch_part, "extra": extra_fetch})
    analysis_hash = stable_hash({"v": CLASSIFIER_VERSION, "analysis": _analysis_part(values)})
    tier = None
    for key in overrides:
        spec_tier = SCHEMA_BY_KEY[key].tier
        if tier is None or TIER_RANK[spec_tier] > TIER_RANK[tier]:
            tier = spec_tier
    settings_hash = stable_hash({"defaults": defaults.defaults_hash, "overrides": overrides})
    return EffectiveSettings(
        values=values,
        overrides=dict(overrides),
        defaults_hash=defaults.defaults_hash,
        fetch_hash=fetch_hash,
        analysis_hash=analysis_hash,
        settings_hash=settings_hash,
        tier=tier,
        extra_fetch=extra_fetch,
    )


def schema_payload(
    defaults: DefaultSettings, can_tune: bool, can_refresh: bool = False, enrichment: bool = True
) -> dict[str, Any]:
    """``GET /settings``: the defaults, the tunable schema, and what the caller may do."""
    tuning = defaults.values["tuning"]
    return {
        "schema_version": SCHEMA_VERSION,
        "defaults_hash": defaults.defaults_hash,
        "can_tune": can_tune,
        "can_refresh": can_refresh,
        # Country, city and job title group come from user enrichment (features:userEnrichmentEnabled).
        "enrichment": enrichment,
        "tuning": {
            "enabled": bool(tuning.get("enabled")),
            "roles": list(tuning.get("roles") or []),
            "max_override_bytes": int(tuning["maxOverrideBytes"]),
            "max_custom_computations_per_hour": int(tuning["maxCustomComputationsPerHour"]),
            "max_computations_per_hour": int(tuning["maxComputationsPerHour"]),
        },
        "tiers": [{"id": tier, **TIERS[tier]} for tier in (TIER_VIEW, TIER_ANALYSIS, TIER_FETCH)],
        "groups": [dict(g) for g in GROUPS],
        "settings": [
            {
                "key": spec.key,
                "group": spec.group,
                "tier": spec.tier,
                "type": spec.type,
                "label": spec.label,
                "description": spec.description,
                "default": copy.deepcopy(get_path(defaults.values, spec.key)),
                "min": spec.minimum,
                "max": spec.maximum,
                "step": spec.step,
                "unit": spec.unit,
                "max_items": LIST_MAX_ITEMS if spec.type in ("list", "rules") else None,
                "max_item_length": LIST_ITEM_MAX_LENGTH if spec.type in ("list", "rules") else None,
            }
            for spec in SCHEMA
        ],
        "fixed": {
            "signal_points": {k: list(v) for k, v in defaults.values["weights"].items()},
            "max_range_days": defaults.values["maxRangeDays"],
            "analysis_cache_minutes": defaults.values["analysisCacheMinutes"],
            "analysis_timeout_seconds": defaults.values["analysisTimeoutSeconds"],
            "fallback_field_since": defaults.values["fallbackFieldSince"],
        },
    }


def user_can_tune(user: Any, defaults: DefaultSettings) -> bool:
    """Only the roles in ``tuning.roles`` (admins and maintainers by default) may send overrides."""
    tuning = defaults.values.get("tuning") or {}
    if not tuning.get("enabled"):
        return False
    roles = set(tuning.get("roles") or [])
    is_maintainer = bool(getattr(user, "is_maintainer", False))
    is_auditor = bool(getattr(user, "is_auditor", False))
    # Maintainers are also flagged is_admin; a pure auditor is neither.
    is_admin = bool(getattr(user, "is_admin", False))
    return (
        ("admin" in roles and is_admin)
        or ("maintainer" in roles and is_maintainer)
        or ("auditor" in roles and is_auditor)
    )
