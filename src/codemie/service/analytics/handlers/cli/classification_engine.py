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

from codemie.service.analytics.handlers.money_coverage import read_money_coverage
from codemie.service.spend.rendering import RenderedSpend, apply_spend_cells, combine_rendered_spend

from .constants import (
    AGENT_TOOL_MATCHERS,
    CODE_CHANGE_TOOL_MATCHERS,
    COWORK_VALUE,
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
        spend: RenderedSpend,
    ) -> tuple[str, float]:
        """Apply a lightweight deterministic classification for CLI insight widgets.

        **`spend` is a qualified figure and not a float, because the result of this function is a
        bare string.** The classification ships on four tables and on the "Primary Category" card,
        none of which carry a currency format hint, so nothing on those surfaces can tell a reader
        that the label was decided by money nobody measured. The qualification has to be spent
        *here*, inside the scoring, since it cannot be published beside the answer.
        """
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
        CLIClassificationEngine._score_cli_cost(scores, spend)
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
    def _score_cli_cost(scores: dict[str, float], spend: RenderedSpend) -> None:
        """Apply spend-based classification signals, and only where the figure can carry the branch.

        **No figure means no cost signal at all - neither branch fires.** The absent figure used to
        arrive here as a fabricated `0.0`, which satisfies `< 5` and pushed `experimental`, where the
        same traffic with its costs settled would have added `1.5` toward `production`: an unmeasured
        cost was quietly deciding a person's label. Omitting the component instead leaves the
        classification standing on its repository, branch and project signals - the same remedy
        `scorer._cli_efficiency_metrics` applies when a person's CLI money is unknown, and for the
        same reason: a manufactured operand is worse than a missing one.

        **A subtotal is a lower bound, so it may fire only the branch a lower bound proves.** That the
        settled requests already total >= 100 settles that the whole window does, whatever the
        unsettled ones hold, so that branch is sound on a partial figure. The other two need an upper
        bound a subtotal cannot give: a partial `3.00` may really be `300`, and reading it as `< 5`
        would brand heavy production use `experimental` on the strength of the part that happened to
        settle first.

        A **measured** zero is a figure and keeps its branch. It says the traffic cost nothing, which
        is exactly what the `< 5` signal is for.
        """
        if spend.figure is None:
            return
        if spend.figure >= 100:
            scores["production"] += 1.5
            return
        if spend.partial:
            return
        if spend.figure >= 20:
            scores["production"] += 0.5
            scores["learning"] += 0.5
        elif spend.figure < 5:
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
        spend: RenderedSpend,
        total_sessions: int,
        active_days: int,
        net_lines: int,
    ) -> list[str]:
        """Build human-readable deterministic signals for the detail modal.

        A reason string is a surface with no annotation slot at all, so the qualification has to be
        inside the sentence. An absent figure says it is absent rather than being skipped by the old
        `> 0` guard - silence there reads as *cost was not a signal*, when the truth is that cost is
        not known - and a subtotal says it is one, so `cost: $3.00` never stands in for a window
        whose settled part is $3.00 and whose remainder nobody has.
        """
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
        if spend.figure is None:
            reasons.append("cost: unknown (no request in this window settled a cost)")
        elif spend.figure > 0:
            reasons.append(f"cost: ${spend.figure:.2f}{' (subtotal)' if spend.partial else ''}")
        return reasons

    # Client types that all represent the CLI client label.
    _CLI_CLIENT_KEYS = frozenset({"CLI", "codemie-daemon", "codemie-claude", "codemie-claude-acp", "codemie-code"})

    @staticmethod
    def _normalize_client(raw: str) -> str:
        """Map all CLI-variant client keys to the canonical 'CLI' label."""
        return "CLI" if raw in CLIClassificationEngine._CLI_CLIENT_KEYS else raw

    @staticmethod
    def _normalize_branch(raw: str) -> str:
        """Treat 'HEAD' as empty — it means no real tracked branch (e.g. CLI from ~)."""
        return "" if raw == "HEAD" else raw

    @staticmethod
    def _extract_cli_client_usage(client_bucket: dict) -> dict:
        """Extract usage/cost/session metrics for a single (repository, branch, client) bucket.

        `cost` is a `RenderedSpend` rather than a float. The `proxy` sub-bucket's `sum` skips every
        request whose cost never settled, so `... or 0` published a repository row reading `$0.00`
        for a repository whose spend is simply unknown - and that row is also what the category
        breakdown and the classification are built from, so the fabricated zero travelled.

        Sessions and line counts stay integers: a zero there is a measurement, not an absence.
        """
        usage_aggs = client_bucket.get("usage", {})
        project_buckets = usage_aggs.get("projects", {}).get("buckets", [])
        return {
            "project_name": project_buckets[0]["key"] if project_buckets else None,
            "cost": read_money_coverage(client_bucket.get("proxy", {}), "total_cost"),
            "lines_added": int(usage_aggs.get("lines_added", {}).get("value", 0) or 0),
            "lines_removed": int(usage_aggs.get("lines_removed", {}).get("value", 0) or 0),
            "sessions": int(client_bucket.get("sessions", {}).get("count", {}).get("value", 0) or 0),
        }

    @staticmethod
    def _merge_cli_repository_row(row: dict, raw_branch: str, usage: dict) -> None:
        """Fold the countable usage from a duplicate (repository, branch, client) bucket into a row.

        Money is deliberately **not** folded here: two qualified figures combine by
        `combine_rendered_spend`, not by `+`, so that a row short by one unsettled bucket says it is
        short instead of presenting the other bucket's subtotal as the pair's total (AD-29). The
        caller holds the figures and combines them there.
        """
        row["sessions"] += usage["sessions"]
        row["net_lines"] += usage["lines_added"] - usage["lines_removed"]
        if raw_branch and not row["branch"]:
            row["branch"] = raw_branch

    @staticmethod
    def _new_cli_repository_row(repository: str, raw_branch: str, branch: str, client: str, usage: dict) -> dict:
        """Build a new output row, classifying the (repository, branch) pair.

        The money cell is written by `apply_spend_cells` once the row's buckets are all folded in,
        so it is absent from the literal here: a figure and the three values that qualify it are
        written in one pass, and a row cannot carry one without the others.
        """
        classification, _confidence = CLIClassificationEngine._classify_cli_entity(
            repositories=[repository],
            branches=[branch] if branch else [],
            project_name=usage["project_name"],
            spend=usage["cost"],
        )
        return {
            "repository": repository,
            "branch": raw_branch,
            "client": client,
            "sessions": usage["sessions"],
            "classification": classification,
            "net_lines": usage["lines_added"] - usage["lines_removed"],
        }

    @staticmethod
    def _build_cli_repository_classifications(
        repository_buckets: list[dict],
    ) -> tuple[list[dict], list[RenderedSpend]]:
        """Build repository rows for CLI user detail, one row per (repository, branch, client).

        ES produces multiple buckets for the same logical row when different event types
        carry inconsistent branch/client values (proxy cost events have no branch/client,
        tool-usage events do). Normalise both dimensions before merging so sessions and
        cost always land in the same output row.

        The rows and their qualified figures are returned side by side, in the same order, because
        the category breakdown regroups these rows by classification and has to combine the figures
        again: a triple already written into a row cannot be combined, only re-read, and a total over
        re-read figures cannot tell one shared hole from several (AD-29).
        """
        merged: dict[tuple[str, str, str], dict] = {}
        spend_by_key: dict[tuple[str, str, str], RenderedSpend] = {}
        for repo_bucket in repository_buckets:
            # Fall back to Cowork rather than dropping the bucket — an empty repository value
            # still carries real cost (proxy events without attribution), and silently
            # skipping it here caused the row's cost to vanish from this table while still
            # counting toward the flat, unfiltered top-level total_cost sum.
            repository = str(repo_bucket.get("key", "")).strip() or COWORK_VALUE
            for branch_bucket in repo_bucket.get("branches", {}).get("buckets", []):
                raw_branch = str(branch_bucket.get("key", "")).strip()
                branch = CLIClassificationEngine._normalize_branch(raw_branch)
                for client_bucket in branch_bucket.get("clients", {}).get("buckets", []):
                    raw_client = str(client_bucket.get("key", "")).strip() or "CLI"
                    client = CLIClassificationEngine._normalize_client(raw_client)
                    usage = CLIClassificationEngine._extract_cli_client_usage(client_bucket)
                    key = (repository, branch, client)
                    if key in merged:
                        CLIClassificationEngine._merge_cli_repository_row(merged[key], raw_branch, usage)
                        spend_by_key[key] = combine_rendered_spend([spend_by_key[key], usage["cost"]])
                    else:
                        merged[key] = CLIClassificationEngine._new_cli_repository_row(
                            repository, raw_branch, branch, client, usage
                        )
                        spend_by_key[key] = usage["cost"]

        # A bucket with no session document has nothing to say in a table of per-repository sessions
        # and is still dropped - but never while it holds a measured figure. Proxy cost events carry
        # no session, so a repository can settle real spend and open no session at all; dropping that
        # row subtracted its money from this table *and* from the category rollup built out of these
        # rows, while the modal's Total Cost card above them - a separate aggregation over the same
        # window - still counted it. That is the same leak the repository fallback above fixes, in the
        # other dimension: `$500` in the card, in no row, and nothing anywhere saying it was short.
        # `figure is not None` is exactly the money that card counts (an unsettled bucket contributes
        # to neither), so keeping these rows makes the two agree. A *measured* zero is a figure like
        # any other and keeps its row, which says the work was free rather than omitting it.
        kept = [
            (row, spend_by_key[key])
            for key, row in merged.items()
            if row["sessions"] > 0 or spend_by_key[key].figure is not None
        ]
        # Cost still decides the order, and an absent figure sorts last rather than as a zero: the
        # first key separates the two outright, so no repository whose spend is unknown is ever
        # ranked among the ones whose spend is known.
        kept.sort(
            key=lambda pair: (pair[1].figure is not None, pair[1].figure or 0.0, pair[0]["sessions"]),
            reverse=True,
        )
        rows = [row for row, _ in kept]
        rendered = [spend for _, spend in kept]
        apply_spend_cells(rows, rendered, "cost")
        return rows, rendered

    @staticmethod
    def _build_cli_category_breakdown(
        repository_rows: list[dict],
        repository_spend: list[RenderedSpend],
    ) -> tuple[list[dict], list[RenderedSpend]]:
        """Build category breakdown from classified repositories.

        A category's cost is `combine_rendered_spend` over its rows rather than `sum(...)` of their
        cells: a category holding one repository whose cost never settled is short by an unknown
        amount and has to say so, and a category holding nothing but such repositories has no figure
        at all - `$0.00` there would claim a whole category of work was free.

        `sessions`, `repositories` and the `percentage` derived from sessions are counts, so a zero
        in them is a fact and they need no qualification.
        """
        grouped: dict[str, dict] = defaultdict(
            lambda: {"category": "", "sessions": 0, "repositories": 0, "percentage": 0.0}
        )
        parts: dict[str, list[RenderedSpend]] = defaultdict(list)
        total_sessions = sum(int(row["sessions"]) for row in repository_rows)
        for row, spend in zip(repository_rows, repository_spend, strict=True):
            bucket = grouped[row["classification"]]
            bucket["category"] = row["classification"]
            bucket["sessions"] += int(row["sessions"])
            bucket["repositories"] += 1
            parts[row["classification"]].append(spend)
        rows = list(grouped.values())
        for row in rows:
            row["percentage"] = round((row["sessions"] / total_sessions) * 100, 1) if total_sessions else 0.0
        ordered = sorted(
            zip(rows, [combine_rendered_spend(parts[row["category"]]) for row in rows], strict=True),
            # Sessions lead as before; cost only breaks their ties, and an absent figure breaks them
            # last rather than as a zero.
            key=lambda pair: (pair[0]["sessions"], pair[1].figure is not None, pair[1].figure or 0.0),
            reverse=True,
        )
        rows = [row for row, _ in ordered]
        rendered = [spend for _, spend in ordered]
        apply_spend_cells(rows, rendered, "cost")
        return rows, rendered
