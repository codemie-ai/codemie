# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Classification engine for CLI analytics handlers."""

from __future__ import annotations

import re
from collections import defaultdict
from enum import Enum

from .constants import (
    AGENT_TOOL_MATCHERS,
    CODE_CHANGE_TOOL_MATCHERS,
    EXPERIMENTAL_REPO_PATTERNS,
    LEARNING_REPO_PATTERNS,
    LOCAL_PATH_PATTERNS,
    NON_PRODUCTION_BRANCH_PATTERNS,
    PET_PROJECT_REPO_PATTERNS,
    PERSONAL_PROJECT_DOMAINS,
    PLANNING_TOOL_MATCHERS,
    PRODUCTION_BRANCH_PATTERNS,
    PROJECT_TYPE_PERSONAL,
    PROJECT_TYPE_TEAM,
    READ_SEARCH_TOOL_MATCHERS,
    TERMINAL_TOOL_MATCHERS,
    TESTING_REPO_PATTERNS,
    TOOL_COUNTS_ATTR_KEY,
    TOOL_NAMES_ATTR_KEY,
)


class EnrichedUserScope(str, Enum):
    COUNTRY = "country"
    CITY = "city"
    JOB_TITLE = "job_title"
    JOB_TITLE_GROUP = "job_title_group"
    PRIMARY_SKILL = "primary_skill"


class CLIClassificationEngine:
    """Classification engine for CLI analytics. All methods are static."""

    NO_HR_DATA_LABEL = "No Data"
    CODEMIE_CLI_EMAIL_SUFFIX = "_codemie_cli"
    ENRICHED_SCOPE_LABELS: dict[EnrichedUserScope, str] = {
        EnrichedUserScope.COUNTRY: "Country",
        EnrichedUserScope.CITY: "City",
        EnrichedUserScope.JOB_TITLE: "Job Title",
        EnrichedUserScope.JOB_TITLE_GROUP: "Job Title Group",
        EnrichedUserScope.PRIMARY_SKILL: "Primary Skill",
    }

    @staticmethod
    def _classify_cli_entity(
        repositories: list[str],
        branches: list[str],
        project_name: str | None,
        total_cost: float,
    ) -> tuple[str, float]:
        """Apply a lightweight deterministic classification for CLI insight widgets."""
        scores = {
            "production": 0.0,
            "learning": 0.0,
            "testing": 0.0,
            "experimental": 0.0,
            "pet_project": 0.0,
        }
        CLIClassificationEngine._score_cli_repositories(scores, repositories)
        CLIClassificationEngine._score_cli_branches(scores, branches)
        CLIClassificationEngine._score_cli_project(scores, project_name)
        CLIClassificationEngine._score_cli_cost(scores, total_cost)
        if not repositories and not branches:
            scores["experimental"] += 1.0

        classification = max(scores, key=scores.get)
        total_score = sum(scores.values())
        confidence = round(scores[classification] / total_score, 2) if total_score else 0.0
        return classification, confidence

    @staticmethod
    def _score_cli_repositories(scores: dict[str, float], repositories: list[str]) -> None:
        """Apply repository-name based classification signals."""
        for repository in repositories:
            repo_lower = repository.lower()
            if any(re.search(pattern, repo_lower) for pattern in LEARNING_REPO_PATTERNS):
                scores["learning"] += 2.0
            if any(re.search(pattern, repo_lower) for pattern in TESTING_REPO_PATTERNS):
                scores["testing"] += 2.0
            if any(re.search(pattern, repo_lower) for pattern in EXPERIMENTAL_REPO_PATTERNS):
                scores["experimental"] += 2.0
            if any(re.search(pattern, repo_lower) for pattern in PET_PROJECT_REPO_PATTERNS):
                scores["pet_project"] += 1.5
            if any(re.search(pattern, repository) for pattern in LOCAL_PATH_PATTERNS):
                scores["experimental"] += 1.5
            if repository.count("/") >= 2 or re.search(r"[A-Z][a-z]+/[a-z0-9._-]+", repository):
                scores["production"] += 1.5

    @staticmethod
    def _score_cli_branches(scores: dict[str, float], branches: list[str]) -> None:
        """Apply branch-name based classification signals."""
        for branch in branches:
            if any(re.search(pattern, branch, re.IGNORECASE) for pattern in PRODUCTION_BRANCH_PATTERNS):
                scores["production"] += 2.0
                continue
            if any(re.search(pattern, branch, re.IGNORECASE) for pattern in NON_PRODUCTION_BRANCH_PATTERNS):
                scores["testing"] += 1.5
                scores["experimental"] += 0.5
                continue
            if branch in {"main", "master", "develop"}:
                scores["production"] += 0.5

    @staticmethod
    def _score_cli_project(scores: dict[str, float], project_name: str | None) -> None:
        """Apply project-name based classification signals."""
        if project_name and CLIClassificationEngine._infer_project_type(project_name) == PROJECT_TYPE_PERSONAL:
            scores["pet_project"] += 1.0

    @staticmethod
    def _score_cli_cost(scores: dict[str, float], total_cost: float) -> None:
        """Apply spend-based classification signals."""
        if total_cost >= 100:
            scores["production"] += 1.5
        elif total_cost >= 20:
            scores["production"] += 0.5
            scores["learning"] += 0.5
        elif total_cost < 5:
            scores["experimental"] += 0.5

    @staticmethod
    def _infer_project_type(project_name: str) -> str:
        """Infer personal vs team project type."""
        if project_name.lower().endswith(PERSONAL_PROJECT_DOMAINS) or "@" in project_name:
            return PROJECT_TYPE_PERSONAL
        return PROJECT_TYPE_TEAM

    @staticmethod
    def _calculate_cli_category_diversity_score(category_breakdown: list[dict]) -> float:
        """Calculate a simple diversity score from category percentages."""
        if not category_breakdown:
            return 0.0
        total_share = sum((item["percentage"] / 100) ** 2 for item in category_breakdown)
        if total_share <= 0:
            return 0.0
        return round(1 - total_share, 2)

    @staticmethod
    def _extract_cli_tool_counts(tool_docs_result: dict) -> list[tuple[str, int]]:
        """Aggregate tool counts from raw CLI tool usage documents."""
        counts: dict[str, int] = defaultdict(int)
        for hit in tool_docs_result.get("hits", {}).get("hits", []):
            CLIClassificationEngine._merge_cli_tool_counts_from_hit(counts, hit)
        return sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))

    @staticmethod
    def _merge_cli_tool_counts_from_hit(counts: dict[str, int], hit: dict) -> None:
        """Merge tool counts from a single raw tool usage hit."""
        attributes = hit.get("_source", {}).get("attributes", {})
        tool_names = attributes.get(TOOL_NAMES_ATTR_KEY) or []
        tool_counts = attributes.get(TOOL_COUNTS_ATTR_KEY) or []

        if isinstance(tool_counts, dict):
            CLIClassificationEngine._merge_cli_dict_tool_counts(counts, tool_names, tool_counts)
            return

        for index, tool_name in enumerate(tool_names):
            normalized_tool_name = CLIClassificationEngine._normalize_cli_tool_name(tool_name)
            if not normalized_tool_name:
                continue
            counts[normalized_tool_name] += CLIClassificationEngine._resolve_cli_tool_count(tool_counts, index)

    @staticmethod
    def _merge_cli_dict_tool_counts(
        counts: dict[str, int],
        tool_names: list,
        tool_counts: dict,
    ) -> None:
        """Merge tool counts when a hit stores counts as a dict keyed by tool name."""
        for tool_name in tool_names:
            normalized_tool_name = CLIClassificationEngine._normalize_cli_tool_name(tool_name)
            if not normalized_tool_name:
                continue
            counts[normalized_tool_name] += int(tool_counts.get(normalized_tool_name, 0) or 0)

    @staticmethod
    def _normalize_cli_tool_name(tool_name: str | None) -> str | None:
        """Normalize a tool name for aggregation."""
        if not tool_name:
            return None
        return str(tool_name)

    @staticmethod
    def _resolve_cli_tool_count(tool_counts: dict | list | int | float, index: int) -> int:
        """Resolve tool count from list/scalar fallback formats."""
        if isinstance(tool_counts, list):
            return int((tool_counts[index] if index < len(tool_counts) else 1) or 0)
        if isinstance(tool_counts, int | float):
            return int(tool_counts or 0)
        return 1

    @staticmethod
    def _build_cli_tool_profile(tool_counts: list[tuple[str, int]]) -> dict:
        """Build lightweight tool profile for user detail modal."""
        if not tool_counts:
            return {
                "primary_intent_label": "Unknown",
                "rationale": "No scoped tool usage available for this entity.",
                "top_tools": [],
                "intent_scores": {},
            }

        category_totals = {
            "terminal": 0,
            "read_search": 0,
            "code_changes": 0,
            "planning": 0,
            "agents": 0,
            "other": 0,
        }
        top_tools = [{"name": name, "count": count} for name, count in tool_counts[:8]]

        for tool_name, count in tool_counts:
            normalized = tool_name.strip().lower()
            if any(matcher in normalized for matcher in CODE_CHANGE_TOOL_MATCHERS):
                category_totals["code_changes"] += count
            elif any(matcher in normalized for matcher in TERMINAL_TOOL_MATCHERS):
                category_totals["terminal"] += count
            elif any(matcher in normalized for matcher in READ_SEARCH_TOOL_MATCHERS):
                category_totals["read_search"] += count
            elif any(matcher in normalized for matcher in PLANNING_TOOL_MATCHERS):
                category_totals["planning"] += count
            elif any(matcher in normalized for matcher in AGENT_TOOL_MATCHERS):
                category_totals["agents"] += count
            else:
                category_totals["other"] += count

        intent_scores = {
            "active_development": round(category_totals["code_changes"] * 1.4 + category_totals["terminal"] * 0.8, 2),
            "code_exploration": round(category_totals["read_search"] * 1.2 + category_totals["other"] * 0.2, 2),
            "planning_architecture": round(category_totals["planning"] * 1.5 + category_totals["agents"] * 0.5, 2),
            "advanced_integrations": round(category_totals["agents"] * 1.3 + category_totals["terminal"] * 0.3, 2),
            "debugging_loops": round(category_totals["terminal"] * 0.9 + category_totals["read_search"] * 0.6, 2),
        }
        primary_intent = max(intent_scores, key=intent_scores.get)

        rationale_parts = []
        if category_totals["code_changes"]:
            rationale_parts.append("code changes are present")
        if category_totals["terminal"]:
            rationale_parts.append("terminal activity is substantial")
        if category_totals["read_search"]:
            rationale_parts.append("read/search activity is frequent")
        if category_totals["planning"]:
            rationale_parts.append("planning/task tools are used")
        if category_totals["agents"]:
            rationale_parts.append("agent/skill tools are used")
        rationale = (
            f"Primary signal suggests {primary_intent.replace('_', ' ')} because " + ", ".join(rationale_parts) + "."
            if rationale_parts
            else "Scoped tool usage is limited, so the intent remains uncertain."
        )

        return {
            "primary_intent_label": primary_intent.replace("_", " ").title(),
            "rationale": rationale,
            "top_tools": top_tools,
            "intent_scores": intent_scores,
        }

    @staticmethod
    def _build_cli_rule_reasons(
        repositories: list[str],
        branches: list[str],
        total_cost: float,
        total_sessions: int,
        active_days: int,
        net_lines: int,
    ) -> list[str]:
        """Build human-readable deterministic signals for the detail modal."""
        reasons = []
        production_branch_count = sum(
            1
            for branch in branches
            if any(re.search(pattern, branch, re.IGNORECASE) for pattern in PRODUCTION_BRANCH_PATTERNS)
        )
        if total_sessions and active_days:
            reasons.append(f"frequency: {round(total_sessions / max(active_days, 1), 2)} sessions/day")
        if production_branch_count:
            reasons.append(f"production_branches: {production_branch_count}")
        if any("/" in repository for repository in repositories):
            reasons.append(f"multi_repo: {len(repositories)} repos")
        if net_lines > 0:
            reasons.append(f"productivity: +{net_lines} lines")
        if total_cost > 0:
            reasons.append(f"cost: ${total_cost:.2f}")
        return reasons

    @staticmethod
    def _build_cli_repository_classifications(repository_buckets: list[dict]) -> list[dict]:
        """Build repository rows for CLI user detail."""
        rows = []
        for bucket in repository_buckets:
            repository = str(bucket.get("key", "")).strip()
            if not repository:
                continue
            usage_aggs = bucket.get("usage", {})
            branch_buckets = usage_aggs.get("branches", {}).get("buckets", [])
            branches = [branch["key"] for branch in branch_buckets if branch["key"]]
            project_buckets = usage_aggs.get("projects", {}).get("buckets", [])
            project_name = project_buckets[0]["key"] if project_buckets else None
            repository_cost = round(bucket.get("proxy", {}).get("total_cost", {}).get("value", 0) or 0, 2)
            total_lines_added = int(usage_aggs.get("lines_added", {}).get("value", 0) or 0)
            total_lines_removed = int(usage_aggs.get("lines_removed", {}).get("value", 0) or 0)
            classification, _confidence = CLIClassificationEngine._classify_cli_entity(
                repositories=[repository],
                branches=branches,
                project_name=project_name,
                total_cost=repository_cost,
            )
            rows.append(
                {
                    "repository": repository,
                    "sessions": int(bucket.get("sessions", {}).get("count", {}).get("value", 0) or 0),
                    "cost": repository_cost,
                    "classification": classification,
                    "net_lines": total_lines_added - total_lines_removed,
                    "branches": branches,
                }
            )
        rows.sort(key=lambda row: (row["cost"], row["sessions"]), reverse=True)
        return rows

    @staticmethod
    def _build_cli_category_breakdown(repository_rows: list[dict]) -> list[dict]:
        """Build category breakdown from classified repositories."""
        grouped: dict[str, dict] = defaultdict(
            lambda: {"category": "", "sessions": 0, "cost": 0.0, "repositories": 0, "percentage": 0.0}
        )
        total_sessions = sum(int(row["sessions"]) for row in repository_rows)
        for row in repository_rows:
            bucket = grouped[row["classification"]]
            bucket["category"] = row["classification"]
            bucket["sessions"] += int(row["sessions"])
            bucket["cost"] += float(row["cost"])
            bucket["repositories"] += 1
        rows = list(grouped.values())
        for row in rows:
            row["cost"] = round(row["cost"], 2)
            row["percentage"] = round((row["sessions"] / total_sessions) * 100, 1) if total_sessions else 0.0
        rows.sort(key=lambda row: (row["sessions"], row["cost"]), reverse=True)
        return rows
