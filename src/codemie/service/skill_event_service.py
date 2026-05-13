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

"""Service layer for `codemie skill *` lifecycle events.

Responsibilities:
- Derive `(skill_slug, skill_id)` pair from whichever fields the client sent
  (mirrors upstream `skills.sh` `toSkillSlug` so the canonical form is
  identical to what the upstream tooling produces).
- INSERT the row into Postgres (authoritative).
- Mirror as a `codemie_cli_skill_command_total` metric so the existing
  Elastic-backed dashboards keep working during the transition. The mirror
  is best-effort; persistence is what matters.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Tuple

from codemie.configs import logger
from codemie.repository.skill_event_repository import (
    SkillEventRepository,
    SkillEventRepositoryImpl,
)
from codemie.rest_api.models.skill_event import SkillEvent, SkillEventRequest
from codemie.rest_api.security.user import User
from codemie.service.monitoring.base_monitoring_service import send_log_metric


# Mirror metric name (must match `MetricsSender.METRIC_SKILL_COMMAND_TOTAL` on
# the CLI side and the new entry in `service.analytics.metric_names.MetricName`).
SKILL_COMMAND_METRIC = "codemie_cli_skill_command_total"


# Slug normalization mirrors upstream's `toSkillSlug` exactly:
#   name.toLowerCase()
#       .replace(/[\s_]+/g, "-")
#       .replace(/[^a-z0-9-]/g, "")
#       .replace(/-+/g, "-")
#       .replace(/^-|-$/g, "")


def to_skill_slug(name: str) -> str:
    """Mirror of upstream `skills` CLI `toSkillSlug`.

    Identical algorithm — keeps our canonical id in sync with whatever the
    upstream tooling stores (so cross-system reconciliation works without
    surprise).
    """
    if not name:
        return ""

    slug_parts: list[str] = []
    previous_dash = False
    for char in name.lower():
        if char.isspace() or char in {"_", "-"}:
            if not previous_dash:
                slug_parts.append("-")
                previous_dash = True
        elif "a" <= char <= "z" or "0" <= char <= "9":
            slug_parts.append(char)
            previous_dash = False

    return "".join(slug_parts).strip("-")


def derive_skill_identity(
    source: Optional[str],
    skill_name: Optional[str],
    skill_slug: Optional[str],
    skill_id: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """Fill the `(slug, id)` pair from whatever the client provided.

    Resolution order:
    - if `skill_slug` is missing, derive it from `skill_name`
    - if `skill_id` is missing and we have a slug, derive
      `<source>/<slug>` if `source` is known, else just `<slug>`

    When both are provided and disagree, we trust the explicit `skill_id`
    but log a warning so the caller side can be fixed.
    """
    derived_slug = skill_slug or (to_skill_slug(skill_name) if skill_name else None) or None
    derived_id = skill_id
    if derived_id is None and derived_slug:
        derived_id = f"{source}/{derived_slug}" if source else derived_slug

    # If both were supplied, sanity-check they match the derived form.
    if skill_id and derived_slug:
        expected = f"{source}/{derived_slug}" if source else derived_slug
        if skill_id != expected:
            logger.warning(
                f"[skill_events] skill_id={skill_id!r} disagrees with "
                f"(source={source!r}, slug={derived_slug!r})->{expected!r}; "
                "trusting client-supplied id"
            )

    return derived_slug, derived_id


class SkillEventService:
    """Persist + mirror `codemie skill *` lifecycle events."""

    def __init__(self, repository: Optional[SkillEventRepository] = None):
        self._repository: SkillEventRepository = repository or SkillEventRepositoryImpl()

    def record(
        self,
        request: SkillEventRequest,
        user: User,
        x_codemie_cli: Optional[str] = None,
        x_codemie_client: Optional[str] = None,
    ) -> SkillEvent:
        """Persist the event and mirror it to the legacy metrics path.

        Postgres failures bubble up to the router (returns 500). Mirror
        failures are logged and swallowed — Postgres is the authoritative
        store; transient Elastic-shipper hiccups must not lose data the
        client already trusted us to keep.
        """
        slug, skill_id = derive_skill_identity(
            request.source,
            request.skill_name,
            request.skill_slug,
            request.skill_id,
        )

        event = SkillEvent(
            user_id=user.id,
            user_email=user.email or None,
            session_id=request.session_id,
            agent=request.agent or "codemie-skills",
            agent_version=request.agent_version,
            client_type=x_codemie_client,
            cli_version=x_codemie_cli,
            repository=request.repository,
            branch=request.branch,
            project=request.project,
            command=request.command,
            status=request.status,
            scope=request.scope,
            error_code=request.error_code,
            agent_selection_mode=request.agent_selection_mode,
            target_agents=list(request.target_agents or []),
            source=request.source,
            skill_slug=slug,
            skill_id=skill_id,
            attributes=request.attributes,
        )

        persisted = self._repository.insert(event)

        # Mirror to existing Elastic-backed metrics path. Wrapped in a
        # try/except because durable persistence already succeeded — a
        # downstream telemetry hiccup shouldn't surface as an API error.
        try:
            self._mirror_to_metrics(persisted)
        except Exception as exc:  # noqa: BLE001 — mirror is best-effort
            logger.warning(f"[skill_events] mirror to legacy metrics failed (id={persisted.id!r}): {exc}")

        return persisted

    @staticmethod
    def _mirror_to_metrics(event: SkillEvent) -> None:
        """Emit a flat-attribute mirror so existing Elastic consumers keep working.

        Flattens scalar fields directly; arrays / dicts pass through to the
        log line as JSON. This MUST stay in sync with the metric name added
        to `service.analytics.metric_names.MetricName`.
        """
        attrs = {
            "agent": event.agent,
            "agent_version": event.agent_version,
            "session_id": event.session_id,
            "command": event.command,
            "status": event.status,
            "scope": event.scope,
            "error_code": event.error_code,
            "agent_selection_mode": event.agent_selection_mode,
            "target_agents": event.target_agents or None,
            "source": event.source,
            "skill_slug": event.skill_slug,
            "skill_id": event.skill_id,
            "repository": event.repository,
            "branch": event.branch,
            "project": event.project,
            "user_id": event.user_id,
            "user_email": event.user_email,
        }
        # Drop empty values so log lines stay tight; downstream parsers in
        # the codebase already treat missing keys as "unknown".
        attrs = {k: v for k, v in attrs.items() if v is not None}
        send_log_metric(SKILL_COMMAND_METRIC, attrs)

    def get_event_log(
        self,
        *,
        user: User,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[SkillEvent], int]:
        """Return paginated raw install/remove events visible to *user*.

        Admins receive events for all users; regular users see only their own.
        """
        resolved_user_id: str | None = None if user.is_admin else user.id
        return self._repository.get_events(
            user_id=resolved_user_id,
            from_dt=from_dt,
            to_dt=to_dt,
            limit=limit,
            offset=offset,
        )

    def get_all_skills_stats(
        self,
        *,
        user: User,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Return paginated per-skill aggregated install/remove stats.

        All authenticated users see stats across all users.
        """
        return self._repository.get_all_skills_aggregated_stats(
            user_id=None,
            from_dt=from_dt,
            to_dt=to_dt,
            limit=limit,
            offset=offset,
        )

    def get_skill_stats(self, *, skill_slug: str) -> dict | None:
        """Return aggregated install/removal stats for *skill_slug*.

        Delegates directly to the repository. Returns ``None`` when no
        matching rows exist — the caller is expected to raise a 404.
        """
        return self._repository.get_skill_aggregated_stats(skill_slug=skill_slug)


# Module-level singleton mirrors `skill_user_interaction_service` style.
skill_event_service = SkillEventService()
