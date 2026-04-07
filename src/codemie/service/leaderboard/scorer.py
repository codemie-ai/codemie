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

"""Leaderboard V2 6-dimension scoring engine.

Scores users across 6 dimensions using the V2 component model:
  D1: Core Platform Usage (20%) — active_days, assistants_used,
      web_conversations, conversation_depth, advanced_platform_usage
  D2: Core Platform Creation (20%) — asset_volume, assistant_maturity,
      skill_maturity, datasource_creation
  D3: Workflow Usage (10%) — execution_volume, success_rate, repeat_usage
  D4: Workflow Creation (10%) — workflow_count, workflow_complexity,
      workflow_sophistication, workflow_originality
  D5: CLI & Agentic Engineering (30%) — cli_sessions, repo_breadth,
      code_output, files_changed, delivery_efficiency, tool_sophistication
  D6: Impact & Knowledge (10%) — assistant_reuse, workflow_reuse,
      knowledge_sharing, learning

Aligned with the prototype at users-leaderboard/v2/.
"""

from __future__ import annotations

import bisect
import logging
from dataclasses import dataclass, field

from codemie.service.leaderboard.collector import RawUserMetrics
from codemie.service.leaderboard.config import DimensionDefinition, LeaderboardSettings, leaderboard_settings

logger = logging.getLogger(__name__)

_Cohort = dict[str, list[float]]
_DimDef = DimensionDefinition


@dataclass
class DimensionScore:
    """Score for a single dimension with component breakdown."""

    id: str
    label: str
    weight: float
    score: float  # 0.0 – 1.0
    components: list[dict] = field(default_factory=list)


@dataclass
class ScoredEntry:
    """Fully scored user entry ready for persistence."""

    user_id: str
    user_name: str
    user_email: str | None
    projects: list[str]
    rank: int = 0
    total_score: float = 0.0
    tier_name: str = "newcomer"
    tier_level: int = 1
    usage_intent: str = "platform_focused"
    dimensions: list[DimensionScore] = field(default_factory=list)
    summary_metrics: dict = field(default_factory=dict)


def _tiered(value: float, thresholds: list[float]) -> float:
    """Normalize a value using tiered thresholds (0.0 to 1.0)."""
    if not thresholds:
        return 0.0
    idx = bisect.bisect_right(thresholds, value)
    return min(idx / len(thresholds), 1.0)


def _percentile_rank(value: float, sorted_values: list[float]) -> float:
    """Return percentile rank of value within a sorted list (0.0 to 1.0)."""
    if value <= 0 or not sorted_values:
        return 0.0
    idx = bisect.bisect_right(sorted_values, value)
    return idx / len(sorted_values)


class LeaderboardScorer:
    """Scores all users across 6 dimensions, assigns tiers and ranks."""

    def __init__(self, settings: LeaderboardSettings | None = None) -> None:
        self._settings = settings or leaderboard_settings

    def score_all(self, raw_metrics: list[RawUserMetrics]) -> list[ScoredEntry]:
        """Score all users, assign tiers and ranks. Returns sorted by score desc."""
        cohort = self._build_cohort_stats(raw_metrics)

        entries: list[ScoredEntry] = []
        for user in raw_metrics:
            dimensions = self._score_dimensions(user, cohort)
            total_score = sum(d.score * d.weight for d in dimensions) * 100
            total_score = round(min(total_score, 100.0), 2)

            tier_name, tier_level, _ = self._settings.get_tier(total_score)
            intent = self._classify_intent(dimensions)
            summary = self._build_summary_metrics(user)

            entries.append(
                ScoredEntry(
                    user_id=user.user_id,
                    user_name=user.user_name,
                    user_email=user.user_email,
                    projects=user.projects,
                    total_score=total_score,
                    tier_name=tier_name,
                    tier_level=tier_level,
                    usage_intent=intent,
                    dimensions=dimensions,
                    summary_metrics=summary,
                )
            )

        # Filter zero-score users
        entries = [e for e in entries if e.total_score > 0]

        # Sort and assign ranks
        entries.sort(key=lambda e: e.total_score, reverse=True)
        for rank, entry in enumerate(entries, 1):
            entry.rank = rank

        logger.info(
            f"Leaderboard scorer: scored {len(entries)} users, top score={entries[0].total_score if entries else 0}"
        )
        return entries

    # ── Cohort statistics ────────────────────────────────────────────

    def _build_cohort_stats(self, users: list[RawUserMetrics]) -> dict[str, list[float]]:
        """Pre-compute sorted value lists for percentile ranking."""
        stats: dict[str, list[float]] = {
            "web_conversations": [],
            "avg_messages_per_conversation": [],
            "workflow_executions": [],
            "shared_conversations": [],
            "shared_conversation_access": [],
            "assistant_adopters": [],
            "workflow_external_users": [],
            "cli_sessions": [],
            "cli_lines_added": [],
            "cli_files_changed": [],
            "cli_repos": [],
            "cli_total_spend": [],
        }

        for u in users:
            self._append_cohort_values(stats, u)

        for values in stats.values():
            values.sort()

        return stats

    @staticmethod
    def _append_cohort_values(stats: dict[str, list[float]], user: RawUserMetrics) -> None:
        stats["web_conversations"].append(float(user.usage.get("web_conversations", 0) or 0))
        stats["avg_messages_per_conversation"].append(float(user.usage.get("avg_messages_per_conversation", 0) or 0))
        stats["workflow_executions"].append(float(user.workflow_usage.get("workflow_executions", 0) or 0))
        stats["shared_conversations"].append(float(user.impact.get("shared_conversations", 0) or 0))
        stats["shared_conversation_access"].append(float(user.impact.get("shared_conversation_access", 0) or 0))
        stats["assistant_adopters"].append(float(user.creation.get("assistant_adopters", 0) or 0))
        stats["workflow_external_users"].append(float(user.workflow_creation.get("workflow_external_users", 0) or 0))

        cli_sessions = float(user.cli.get("cli_sessions", 0) or 0)
        if cli_sessions <= 0:
            return

        stats["cli_sessions"].append(cli_sessions)
        stats["cli_lines_added"].append(float(user.cli.get("cli_lines_added", 0) or 0))
        stats["cli_files_changed"].append(float(user.cli.get("cli_files_changed", 0) or 0))
        stats["cli_repos"].append(float(user.cli.get("cli_repos", 0) or 0))
        stats["cli_total_spend"].append(float(user.cli.get("cli_total_spend", 0) or 0))

    # ── Dimension scoring ────────────────────────────────────────────

    def _score_dimensions(self, user: RawUserMetrics, cohort: dict[str, list[float]]) -> list[DimensionScore]:
        dims = self._settings.dimensions
        return [
            self._score_d1(user, cohort, dims[0]),
            self._score_d2(user, dims[1]),
            self._score_d3(user, cohort, dims[2]),
            self._score_d4(user, dims[3]),
            self._score_d5(user, cohort, dims[4]),
            self._score_d6(user, cohort, dims[5]),
        ]

    def _score_d1(self, user: RawUserMetrics, cohort: _Cohort, dim: _DimDef) -> DimensionScore:
        """D1: Core Platform Usage (20%) — V2 components."""
        active_days = float(user.usage.get("active_days", 0) or 0)
        platform_active_days = float(user.usage.get("platform_active_days", 0) or 0)
        assistants_used = float(user.usage.get("assistants_used", 0) or 0)
        web_conversations = float(user.usage.get("web_conversations", 0) or 0)
        avg_msg = float(user.usage.get("avg_messages_per_conversation", 0) or 0)
        unique_mcps = float(user.usage.get("unique_mcps_used", 0) or 0)
        datasource_types = float(
            user.usage.get("datasource_types_touched", 0) or user.creation.get("datasource_types_created", 0) or 0
        )
        skill_events = float(user.usage.get("skill_usage_events", 0) or 0)

        # advanced_platform_usage: MCP * 1.5 + datasource types + skill bonus, / 6
        adv_raw = unique_mcps * 1.5 + datasource_types + (1.0 if skill_events > 0 else 0.0)
        adv_score = min(adv_raw / 6.0, 1.0)

        days_val = int(max(active_days, platform_active_days))
        components = [
            self._component(
                "active_days",
                "Active Days",
                0.20,
                days_val,
                _tiered(days_val, [1, 3, 6, 10, 15, 20]),
                display_value=f"{days_val} days",
            ),
            self._component(
                "assistants_used",
                "Assistants Used",
                0.15,
                assistants_used,
                _tiered(assistants_used, [1, 3, 5, 8]),
                display_value=f"{int(assistants_used)} assistants",
            ),
            self._component(
                "web_conversations",
                "Web Conversations",
                0.25,
                web_conversations,
                _percentile_rank(web_conversations, cohort.get("web_conversations", [])),
                display_value=f"{int(web_conversations)} conversations",
            ),
            self._component(
                "conversation_depth",
                "Conversation Depth",
                0.25,
                avg_msg,
                _percentile_rank(avg_msg, cohort.get("avg_messages_per_conversation", [])),
                display_value=f"{avg_msg:.1f} avg msgs/conversation",
            ),
            self._component(
                "advanced_platform_usage",
                "Skill/MCP/Data Breadth",
                0.15,
                adv_raw,
                adv_score,
                display_value=(
                    f"{int(unique_mcps)} MCPs, {int(datasource_types)} datasource types,"
                    f" {int(skill_events)} skill events"
                ),
            ),
        ]
        score = sum(c["weight"] * c["normalized"] for c in components)
        return DimensionScore(
            id=dim.id, label=dim.name, weight=dim.weight, score=min(score, 1.0), components=components
        )

    def _score_d2(self, user: RawUserMetrics, dim: _DimDef) -> DimensionScore:
        """D2: Core Platform Creation (20%) — V2 components."""
        assistants, skills, datasources = self._creation_asset_counts(user)
        total_assets = assistants + skills + datasources
        asset_vol = _tiered(total_assets, [1, 3, 6, 10])
        maturity = self._assistant_maturity_score(user, assistants)
        skill_mat = self._skill_maturity_score(user, skills)
        ds_completed, ds_types, ds_score = self._datasource_creation_metrics(user, datasources)

        components = [
            self._component(
                "asset_volume",
                "Asset Volume",
                0.22,
                total_assets,
                asset_vol,
                display_value=(f"{int(assistants)} assistants, {int(skills)} skills, {int(datasources)} datasources"),
            ),
            self._component(
                "assistant_maturity",
                "Assistant Maturity",
                0.40,
                round(maturity, 2),
                maturity,
                display_value=f"{int(assistants)} assistants, maturity {maturity:.0%}",
            ),
            self._component(
                "skill_maturity",
                "Skill Maturity",
                0.18,
                round(skill_mat, 2),
                skill_mat,
                display_value=f"{int(skills)} skills, maturity {skill_mat:.0%}",
            ),
            self._component(
                "datasource_creation",
                "Datasource Creation",
                0.20,
                round(ds_score, 2),
                ds_score,
                display_value=(f"{int(datasources)} datasources, {int(ds_completed)} completed, {int(ds_types)} types"),
            ),
        ]
        score = sum(c["weight"] * c["normalized"] for c in components)
        return DimensionScore(
            id=dim.id, label=dim.name, weight=dim.weight, score=min(score, 1.0), components=components
        )

    def _score_d3(self, user: RawUserMetrics, cohort: _Cohort, dim: _DimDef) -> DimensionScore:
        """D3: Workflow Usage (10%) — V2 components."""
        executions = float(user.workflow_usage.get("workflow_executions", 0) or 0)
        successes = float(user.workflow_usage.get("workflow_successes", 0) or 0)
        distinct = float(user.workflow_usage.get("workflows_executed_distinct", 0) or 0)

        if executions == 0:
            components = [
                self._component("execution_volume", "Execution Volume", 0.50, 0, 0.0, display_value="0 executions"),
                self._component("success_rate", "Success Rate", 0.25, 0, 0.0, display_value="0%"),
                self._component("repeat_usage", "Repeat Usage", 0.25, 0, 0.0, display_value="0 repeat ratio"),
            ]
            return DimensionScore(id=dim.id, label=dim.name, weight=dim.weight, score=0.0, components=components)

        success_rate = successes / executions
        repeat_ratio = (executions - distinct) / max(distinct, 1) if distinct > 0 else 0.0

        components = [
            self._component(
                "execution_volume",
                "Execution Volume",
                0.50,
                executions,
                _percentile_rank(executions, cohort.get("workflow_executions", [])),
                display_value=f"{int(executions)} executions",
            ),
            self._component(
                "success_rate",
                "Success Rate",
                0.25,
                round(success_rate * 100, 1),
                min(success_rate, 1.0),
                display_value=f"{success_rate:.0%} ({int(successes)}/{int(executions)})",
            ),
            self._component(
                "repeat_usage",
                "Repeat Usage",
                0.25,
                round(repeat_ratio, 2),
                min(repeat_ratio, 1.0),
                display_value=f"{repeat_ratio:.2f} repeat ratio, {int(distinct)} distinct workflows",
            ),
        ]
        score = sum(c["weight"] * c["normalized"] for c in components)
        return DimensionScore(
            id=dim.id, label=dim.name, weight=dim.weight, score=min(score, 1.0), components=components
        )

    def _score_d4(self, user: RawUserMetrics, dim: _DimDef) -> DimensionScore:
        """D4: Workflow Creation (10%) — V2 components."""
        created = float(user.workflow_creation.get("workflows_created", 0) or 0)
        avg_states = float(user.workflow_creation.get("avg_workflow_states", 0) or 0)
        avg_tools = float(user.workflow_creation.get("avg_workflow_tools", 0) or 0)
        avg_assistants = float(user.workflow_creation.get("avg_workflow_assistants", 0) or 0)
        avg_custom = float(user.workflow_creation.get("avg_workflow_custom_nodes", 0) or 0)
        avg_yaml = float(user.workflow_creation.get("avg_workflow_yaml_length", 0) or 0)
        with_tools = float(user.workflow_creation.get("workflows_with_tools", 0) or 0)
        with_custom = float(user.workflow_creation.get("workflows_with_custom_nodes", 0) or 0)
        with_assistants = float(user.workflow_creation.get("workflows_with_assistants", 0) or 0)
        with_summarization = float(user.workflow_creation.get("workflows_with_summarization", 0) or 0)
        with_long_prompt = float(user.workflow_creation.get("workflows_with_long_supervisor_prompt", 0) or 0)

        # workflow_count: tiered
        wf_count = _tiered(created, [1, 2, 4, 6])

        complexity = self._workflow_complexity_score(
            created,
            avg_states,
            avg_tools,
            avg_assistants,
            avg_custom,
            avg_yaml,
        )
        sophistication = self._workflow_sophistication_score(
            created, with_tools, with_custom, with_assistants, with_summarization, with_long_prompt
        )
        originality = self._workflow_originality_score(created, avg_states, avg_tools, avg_custom, complexity)

        components = [
            self._component(
                "workflow_count",
                "Workflow Count",
                0.20,
                created,
                wf_count,
                display_value=f"{int(created)} workflows",
            ),
            self._component(
                "workflow_complexity",
                "Structural Complexity",
                0.30,
                round(avg_states, 1),
                complexity,
                display_value=(f"avg {avg_states:.1f} states, {avg_tools:.1f} tools, {avg_assistants:.1f} assistants"),
            ),
            self._component(
                "workflow_sophistication",
                "Configuration Sophistication",
                0.25,
                round(sophistication, 2),
                sophistication,
                display_value=(
                    f"{int(with_tools)} with tools, {int(with_custom)} custom nodes,"
                    f" {int(with_summarization)} summarization"
                ),
            ),
            self._component(
                "workflow_originality",
                "Originality",
                0.25,
                round(originality, 2) if created > 0 else 0,
                originality if created > 0 else 0.0,
                display_value=f"{originality:.0%}" if created > 0 else "N/A",
            ),
        ]
        score = sum(c["weight"] * c["normalized"] for c in components)
        return DimensionScore(
            id=dim.id, label=dim.name, weight=dim.weight, score=min(score, 1.0), components=components
        )

    def _score_d5(self, user: RawUserMetrics, cohort: _Cohort, dim: _DimDef) -> DimensionScore:
        """D5: CLI & Agentic Engineering (30%) — V2 components."""
        sessions = float(user.cli.get("cli_sessions", 0) or 0)
        repos = float(user.cli.get("cli_repos", 0) or 0)
        lines_added = float(user.cli.get("cli_lines_added", 0) or 0)
        files_changed = float(user.cli.get("cli_files_changed", 0) or 0)
        total_tokens = float(user.cli.get("cli_total_tokens", 0) or 0)
        cli_spend = float(user.cli.get("cli_total_spend", 0) or 0)

        if sessions == 0:
            components = [
                self._component("cli_sessions", "CLI Sessions", 0.18, 0, 0.0, display_value="0"),
                self._component("repo_breadth", "Repository Breadth", 0.14, 0, 0.0, display_value="0"),
                self._component("code_output", "Code Output", 0.18, 0, 0.0, display_value="0 lines"),
                self._component("files_changed", "Files Changed", 0.10, 0, 0.0, display_value="0"),
                self._component("delivery_efficiency", "Delivery Efficiency", 0.18, 0, 0.0, display_value="N/A"),
                self._component(
                    "tool_sophistication", "Agent/Skill/MCP Sophistication", 0.22, 0, 0.0, display_value="N/A"
                ),
            ]
            return DimensionScore(id=dim.id, label=dim.name, weight=dim.weight, score=0.0, components=components)

        cli_sessions_sorted = cohort.get("cli_sessions", [])
        cli_lines_sorted = cohort.get("cli_lines_added", [])
        cli_files_sorted = cohort.get("cli_files_changed", [])
        efficiency, eff_display = self._cli_efficiency_metrics(lines_added, files_changed, total_tokens, cli_spend)

        unique_tools = float(user.usage.get("unique_tools", 0) or 0)
        unique_mcps = float(user.usage.get("unique_mcps_used", 0) or 0)
        skill_events = float(user.usage.get("skill_usage_events", 0) or 0)
        advanced_calls, tool_soph = self._tool_sophistication_metrics(unique_tools, unique_mcps, skill_events)

        components = [
            self._component(
                "cli_sessions",
                "CLI Sessions",
                0.18,
                sessions,
                _percentile_rank(sessions, cli_sessions_sorted),
                display_value=str(int(sessions)),
            ),
            self._component(
                "repo_breadth",
                "Repository Breadth",
                0.14,
                repos,
                _tiered(repos, [1, 2, 3, 5]),
                display_value=str(int(repos)),
            ),
            self._component(
                "code_output",
                "Code Output",
                0.18,
                lines_added,
                _percentile_rank(lines_added, cli_lines_sorted),
                display_value=f"{int(lines_added)} lines",
            ),
            self._component(
                "files_changed",
                "Files Changed",
                0.10,
                files_changed,
                _percentile_rank(files_changed, cli_files_sorted),
                display_value=str(int(files_changed)),
            ),
            self._component(
                "delivery_efficiency",
                "Delivery Efficiency",
                0.18,
                round(efficiency, 2),
                efficiency,
                display_value=eff_display,
            ),
            self._component(
                "tool_sophistication",
                "Agent/Skill/MCP Sophistication",
                0.22,
                round(tool_soph, 2),
                tool_soph,
                display_value=(f"{int(advanced_calls)}/{int(unique_tools)} advanced calls, {int(unique_mcps)} MCP"),
            ),
        ]
        score = sum(c["weight"] * c["normalized"] for c in components)
        return DimensionScore(
            id=dim.id, label=dim.name, weight=dim.weight, score=min(score, 1.0), components=components
        )

    def _score_d6(self, user: RawUserMetrics, cohort: _Cohort, dim: _DimDef) -> DimensionScore:
        """D6: Impact & Knowledge (10%) — V2 components."""
        adopters = float(user.creation.get("assistant_adopters", 0) or 0)
        wf_external = float(user.workflow_creation.get("workflow_external_users", 0) or 0)
        shared = float(user.impact.get("shared_conversations", 0) or 0)
        shared_access = float(user.impact.get("shared_conversation_access", 0) or 0)
        kata = float(user.impact.get("kata_completed", 0) or 0)

        # Filter system creators from adoption counts
        if user.user_name and user.user_name.lower() in self._settings.system_creators:
            adopters = 0.0
            wf_external = 0.0

        # assistant_reuse: percentile (only if above zero)
        ast_reuse = 0.0
        if adopters > 0:
            ast_reuse = _percentile_rank(adopters, cohort.get("assistant_adopters", []))

        # workflow_reuse: percentile (only if above zero)
        wf_reuse = 0.0
        if wf_external > 0:
            wf_reuse = _percentile_rank(wf_external, cohort.get("workflow_external_users", []))

        # knowledge_sharing: blended shared count + access
        sharing_score = _tiered(shared, [1, 3, 8, 15]) * 0.55
        access_score = _percentile_rank(shared_access, cohort.get("shared_conversation_access", [])) * 0.45
        knowledge = sharing_score + access_score

        components = [
            self._component(
                "assistant_reuse",
                "Assistant Reuse",
                0.30,
                adopters,
                ast_reuse,
                display_value=f"{int(adopters)} adopters",
            ),
            self._component(
                "workflow_reuse",
                "Workflow Reuse",
                0.25,
                wf_external,
                wf_reuse,
                display_value=f"{int(wf_external)} external users",
            ),
            self._component(
                "knowledge_sharing",
                "Knowledge Sharing",
                0.30,
                shared,
                knowledge,
                display_value=f"{int(shared)} shared, {int(shared_access)} accesses",
            ),
            self._component(
                "learning",
                "Kata Completion",
                0.15,
                kata,
                _tiered(kata, [1, 3, 6]),
                display_value=f"{int(kata)} completed",
            ),
        ]
        score = sum(c["weight"] * c["normalized"] for c in components)
        return DimensionScore(
            id=dim.id, label=dim.name, weight=dim.weight, score=min(score, 1.0), components=components
        )

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _component(
        key: str,
        label: str,
        weight: float,
        raw_value: float,
        normalized: float,
        display_value: str | None = None,
    ) -> dict:
        # Auto-format display_value if not explicitly provided
        if display_value is None:
            if isinstance(raw_value, float) and raw_value == int(raw_value) and raw_value < 1_000_000:
                display_value = str(int(raw_value))
            elif isinstance(raw_value, float):
                display_value = f"{raw_value:.2f}"
            else:
                display_value = str(raw_value)
        return {
            "key": key,
            "label": label,
            "weight": weight,
            "raw_value": raw_value,
            "normalized": round(min(max(normalized, 0.0), 1.0), 4),
            "display_value": display_value,
        }

    @staticmethod
    def _classify_intent(dimensions: list[DimensionScore]) -> str:
        """Classify user's primary usage intent based on dimension scores.

        Returns one of: sdlc_unicorn, developer, workflow_architect,
        workflow_user, platform_builder, ai_user, explorer.
        """
        scores: dict[str, float] = {}
        for d in dimensions:
            scores[d.id] = d.score

        d1 = scores.get("d1", 0.0)  # Platform Usage
        d2 = scores.get("d2", 0.0)  # Platform Creation
        d3 = scores.get("d3", 0.0)  # Workflow Usage
        d4 = scores.get("d4", 0.0)  # Workflow Creation
        d5 = scores.get("d5", 0.0)  # CLI & Agentic
        total = sum(scores.values())
        if total < 0.05:
            return "explorer"

        # SDLC Unicorn: strong across CLI + platform creation + workflow usage + impact
        strong_dims = sum(1 for s in [d1, d2, d3, d5] if s >= 0.3)
        if strong_dims >= 3 and d5 >= 0.3 and (d2 >= 0.3 or d4 >= 0.3):
            return "sdlc_unicorn"

        # Developer: CLI-dominant
        if d5 >= 0.3 and d5 >= max(d1, d2, d3, d4) * 0.8:
            return "developer"

        # Workflow Architect: creates sophisticated workflows
        if d4 >= 0.3 and d4 >= max(d1, d2, d5):
            return "workflow_architect"

        # Workflow User: executes workflows heavily
        if d3 >= 0.3 and d3 >= max(d1, d2, d5):
            return "workflow_user"

        # Platform Builder: creates assets (assistants, skills, datasources)
        if d2 >= 0.3 and d2 >= max(d1, d5):
            return "platform_builder"

        # AI User: consistent platform consumer
        if d1 >= 0.2:
            return "ai_user"

        return "explorer"

    @staticmethod
    def _build_summary_metrics(user: RawUserMetrics) -> dict:
        """Build summary metrics dict for the entry."""
        cli = user.cli or {}
        summary = {
            **LeaderboardScorer._summary_int_metrics(
                user.usage,
                (
                    ("active_days", "active_days"),
                    ("platform_active_days", "platform_active_days"),
                    ("web_conversations", "web_conversations"),
                    ("assistants_used", "assistants_used"),
                ),
            ),
            **LeaderboardScorer._summary_int_metrics(
                user.creation,
                (
                    ("assistants_created", "assistants_created"),
                    ("skills_created", "skills_created"),
                    ("datasources_created", "datasources_created"),
                    ("assistant_adopters", "assistant_adopters"),
                ),
            ),
            **LeaderboardScorer._summary_int_metrics(
                user.workflow_creation,
                (
                    ("workflows_created", "workflows_created"),
                    ("workflow_external_users", "workflow_external_users"),
                ),
            ),
            **LeaderboardScorer._summary_int_metrics(
                user.workflow_usage,
                (("workflow_executions", "workflow_executions"),),
            ),
            **LeaderboardScorer._summary_int_metrics(
                cli,
                (
                    ("cli_sessions", "cli_sessions"),
                    ("cli_repos", "cli_repos"),
                    ("total_lines_added", "cli_lines_added"),
                    ("files_changed", "cli_files_changed"),
                    ("unique_repos", "cli_repos"),
                ),
            ),
            **LeaderboardScorer._summary_int_metrics(
                user.impact,
                (
                    ("shared_conversations", "shared_conversations"),
                    ("shared_conversation_access", "shared_conversation_access"),
                    ("kata_completed", "kata_completed"),
                ),
            ),
        }
        summary["total_spend"] = float(user.litellm_spend.get("total_spend", 0) or 0) + float(
            user.usage.get("web_money_spent", 0) or 0
        )
        summary["cli_spend"] = float(user.litellm_spend.get("cli_spend", 0) or 0)
        return summary

    @staticmethod
    def _creation_asset_counts(user: RawUserMetrics) -> tuple[float, float, float]:
        return (
            float(user.creation.get("assistants_created", 0) or 0),
            float(user.creation.get("skills_created", 0) or 0),
            float(user.creation.get("datasources_created", 0) or 0),
        )

    def _assistant_maturity_score(self, user: RawUserMetrics, assistants: float) -> float:
        return self._weighted_ratio_score(
            assistants,
            [
                (float(user.creation.get("assistants_with_tools", 0) or 0), 0.20),
                (float(user.creation.get("assistants_with_mcps", 0) or 0), 0.20),
                (float(user.creation.get("assistants_with_skills", 0) or 0), 0.15),
                (float(user.creation.get("assistants_with_nested", 0) or 0), 0.10),
                (float(user.creation.get("assistants_with_context", 0) or 0), 0.15),
                (float(user.creation.get("assistants_smart_tools", 0) or 0), 0.10),
            ],
            [(float(user.creation.get("avg_assistant_prompt_length", 0) or 0), 1500.0, 0.10)],
        )

    def _skill_maturity_score(self, user: RawUserMetrics, skills: float) -> float:
        return self._weighted_ratio_score(
            skills,
            [
                (float(user.creation.get("skills_with_toolkits", 0) or 0), 0.35),
                (float(user.creation.get("skill_likes_sum", 0) or 0), 0.20, 3.0),
            ],
            [(float(user.creation.get("avg_skill_content_length", 0) or 0), 2500.0, 0.45)],
        )

    def _datasource_creation_metrics(self, user: RawUserMetrics, datasources: float) -> tuple[float, float, float]:
        ds_completed = float(user.creation.get("datasources_completed", 0) or 0)
        ds_types = float(user.creation.get("datasource_types_created", 0) or 0)
        ds_score = self._weighted_ratio_score(
            datasources,
            [(ds_completed, 0.35)],
            [
                (ds_types, 4.0, 0.25),
                (float(user.creation.get("datasource_documents_total", 0) or 0), 500.0, 0.40),
            ],
        )
        return ds_completed, ds_types, ds_score

    @staticmethod
    def _summary_int_metrics(source: dict, fields: tuple[tuple[str, str], ...]) -> dict[str, int]:
        return {target: int(source.get(source_key, 0) or 0) for target, source_key in fields}

    @staticmethod
    def _weighted_ratio_score(
        total: float,
        ratio_components: list[tuple[float, float] | tuple[float, float, float]],
        absolute_components: list[tuple[float, float, float]],
    ) -> float:
        if total <= 0:
            return 0.0

        score = 0.0
        for component in ratio_components:
            if len(component) == 2:
                value, weight = component
                divisor = total
            else:
                value, weight, multiplier = component
                divisor = max(total, 1) * multiplier
            score += min(value / divisor, 1.0) * weight

        for value, scale, weight in absolute_components:
            score += min(value / scale, 1.0) * weight

        return min(score, 1.0)

    @staticmethod
    def _workflow_complexity_score(
        created: float,
        avg_states: float,
        avg_tools: float,
        avg_assistants: float,
        avg_custom: float,
        avg_yaml: float,
    ) -> float:
        if created <= 0:
            return 0.0
        return min(
            min(avg_states / 8.0, 1.0) * 0.35
            + min(avg_tools / 4.0, 1.0) * 0.20
            + min(avg_assistants / 4.0, 1.0) * 0.20
            + min(avg_custom / 3.0, 1.0) * 0.10
            + min(avg_yaml / 5000.0, 1.0) * 0.15,
            1.0,
        )

    @staticmethod
    def _workflow_sophistication_score(
        created: float,
        with_tools: float,
        with_custom: float,
        with_assistants: float,
        with_summarization: float,
        with_long_prompt: float,
    ) -> float:
        if created <= 0:
            return 0.0
        return min(
            min(with_tools / created, 1.0) * 0.25
            + min(with_custom / created, 1.0) * 0.20
            + min(with_assistants / created, 1.0) * 0.20
            + min(with_summarization / created, 1.0) * 0.15
            + min(with_long_prompt / created, 1.0) * 0.20,
            1.0,
        )

    @staticmethod
    def _workflow_originality_score(
        created: float,
        avg_states: float,
        avg_tools: float,
        avg_custom: float,
        complexity: float,
    ) -> float:
        if created <= 0:
            return 1.0
        if avg_states < 3 and avg_tools == 0 and avg_custom == 0:
            return 0.40
        if avg_states < 4 and complexity < 0.2:
            return 0.65
        return 1.0

    def _cli_efficiency_metrics(
        self, lines_added: float, files_changed: float, total_tokens: float, cli_spend: float
    ) -> tuple[float, str]:
        efficiency = 0.0
        eff_parts: list[float] = []
        if (
            lines_added >= self._settings.cli_min_lines_for_efficiency
            and files_changed >= self._settings.cli_min_files_for_efficiency
        ):
            if total_tokens > 0:
                eff_parts.append(min(lines_added / max(total_tokens / 1000, 1) / 10.0, 1.0))
            if cli_spend > 0:
                eff_parts.append(min(lines_added / max(cli_spend, 1) / 100.0, 1.0))
        if eff_parts:
            efficiency = sum(eff_parts) / len(eff_parts)

        lines_per_token = lines_added / max(total_tokens, 1) if total_tokens > 0 else 0.0
        lines_per_dollar = lines_added / max(cli_spend, 0.01) if cli_spend > 0 else 0.0
        return efficiency, f"{lines_per_token:.5f} lines/token · {lines_per_dollar:.2f} lines/$"

    @staticmethod
    def _tool_sophistication_metrics(
        unique_tools: float,
        unique_mcps: float,
        skill_events: float,
    ) -> tuple[float, float]:
        advanced_calls = unique_mcps + (1.0 if skill_events > 0 else 0.0)
        advanced_ratio = min(advanced_calls / max(unique_tools, 1), 1.0)
        mcp_ratio = min(unique_mcps / 3.0, 1.0)
        return advanced_calls, advanced_ratio * 0.75 + mcp_ratio * 0.25
