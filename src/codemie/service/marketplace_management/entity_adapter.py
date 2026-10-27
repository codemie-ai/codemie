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

"""Entity adapters that bridge the codemie DB models to the PoC's AssistantRecord dataclass.

Protocol:  EntityAdapter   — structural interface
           (list_records / get_record / list_clone_edges / mark_status / kind)
Concrete:  AssistantAdapter — backed by the assistants table via raw SQLModel query
Stub:      WorkflowAdapter  — future work, every method raises NotImplementedError
Factory:   get_adapter(kind) -> EntityAdapter

Validator imports are deferred to first use so this module loads cleanly and
cheaply; the validator itself no longer carries any ML dependency (the
dense-vector path it used to reach for was deleted in EXECUTION-PLAN 9.1).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional
from typing import Protocol, runtime_checkable

from sqlalchemy import or_
from sqlmodel import select

from codemie.clients.postgres import get_session
from codemie.rest_api.models.assistant import Assistant
from codemie.service.marketplace_management.models import EntityKind, MarketplaceStatus

if TYPE_CHECKING:
    # Import only for type checkers; not evaluated at runtime.
    from codemie.service.marketplace_management.validator.models import AssistantRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers — field-for-field mapping following loader.py::_to_record
# ---------------------------------------------------------------------------


def _as_dict(obj) -> dict:
    """Pydantic model -> dict; anything dict-like is copied."""
    return obj.model_dump() if hasattr(obj, "model_dump") else dict(obj)


def _tool_name(tool) -> str:
    """Name of one entry of a toolkit's tool list (a plain string or a dict)."""
    if isinstance(tool, str):
        return tool
    if isinstance(tool, dict):
        return tool.get("name") or tool.get("id") or ""
    return ""


def _toolkit_tool_ids(toolkit) -> list[str]:
    """'toolkit_name:tool_name' identifiers for a single toolkit."""
    raw_tk = _as_dict(toolkit)
    base = raw_tk.get("toolkit_name") or raw_tk.get("name") or raw_tk.get("id") or ""
    tools = raw_tk.get("tools") or raw_tk.get("selected_tools") or []
    if not tools:
        return [str(base)] if base else []
    if not isinstance(tools, list):
        return []
    names = (_tool_name(t) for t in tools)
    return [f"{base}:{n}" if base else n for n in names if n]


def _extract_tool_ids(toolkits) -> list[str]:
    """Flatten ToolKitDetails objects into 'toolkit_name:tool_name' identifiers."""
    return [tool_id for tk in toolkits or [] for tool_id in _toolkit_tool_ids(tk)]


def _extract_mcp_ids(mcp_servers) -> list[str]:
    """Extract string identifiers from MCPServerDetails objects."""
    out: list[str] = []
    for m in mcp_servers or []:
        raw_m = _as_dict(m)
        v = raw_m.get("id") or raw_m.get("name") or raw_m.get("server_name") or raw_m.get("url")
        if v:
            out.append(str(v))
    return out


def _extract_datasource_ids(context) -> list[str]:
    """Extract datasource identifiers from Context objects."""
    out: list[str] = []
    for c in context or []:
        raw_c = _as_dict(c)
        n = raw_c.get("name") or raw_c.get("id")
        if n:
            out.append(str(n))
    return out


def _author_fields(a: Assistant) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """(creator_id, author_name, author_username) from created_by (real human) vs creator (service-account UUID)."""
    creator_id: Optional[str] = a.creator or None
    cb = a.created_by
    if cb is None:
        return creator_id, None, None
    return (
        creator_id or getattr(cb, "id", None),
        getattr(cb, "name", None),
        getattr(cb, "username", None),
    )


def _stripped(value) -> str:
    return str(value or "").strip()


def _str_list(values) -> list[str]:
    return [str(v) for v in (values or []) if v]


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


def _assistant_to_record(a: Assistant) -> AssistantRecord:
    """Convert a codemie Assistant row to a PoC AssistantRecord.

    Follows the mapping in assistants-triage-poc/app/loader.py::_to_record
    field-for-field. `raw` is left as an empty dict per spec.

    Validator import is deferred here (inside the function) so that this
    module can be imported without pulling in the audit engine.
    """
    # Deferred import: keeps `validator.models` off this module's import path
    # until a record is actually built.
    from codemie.service.marketplace_management.validator.models import AssistantRecord  # noqa: F811

    creator_id, author_name, author_username = _author_fields(a)

    return AssistantRecord(
        id=str(a.id or ""),
        name=_stripped(a.name),
        description=_stripped(a.description),
        system_prompt=_stripped(a.system_prompt),
        categories=_str_list(a.categories),
        tools=_extract_tool_ids(a.toolkits),
        mcp_servers=_extract_mcp_ids(a.mcp_servers),
        skills=_str_list(a.skill_ids),
        datasources=_extract_datasource_ids(a.context),
        icon_url=a.icon_url or None,
        is_global=bool(a.is_global),
        shared=bool(a.shared),
        creator=creator_id,
        unique_users_count=int(a.unique_users_count or 0),
        unique_likes_count=int(a.unique_likes_count or 0),
        unique_dislikes_count=int(a.unique_dislikes_count or 0),
        clone_count=int(a.clone_count or 0),
        version_count=0,  # not tracked per-row in codemie
        # Datetime fields as ISO strings (or None).
        updated_date=_iso(a.updated_date),
        created_date=_iso(a.created_date),
        author_name=author_name,
        author_username=author_username,
        project=a.project or None,
        slug=a.slug or None,
        llm_model_type=a.llm_model_type or None,
        # agent_mode is an Enum in codemie; the PoC stores it as a plain string.
        agent_mode=a.agent_mode.value if a.agent_mode else None,
        is_react=(a.is_react if isinstance(a.is_react, bool) else None),
        conversation_starters=[s for s in (a.conversation_starters or []) if isinstance(s, str)],
        plan_prompt=(str(a.plan_prompt).strip() if a.plan_prompt else None),
        # nested_assistants and prompt_variables are Pydantic models — convert to plain dicts.
        nested_assistants=[_as_dict(n) for n in (a.nested_assistants or [])],
        prompt_variables=[_as_dict(p) for p in (a.prompt_variables or [])],
        raw={},  # leave empty — raw payload not needed for the audit engine
    )


def assistant_to_record(a: Assistant) -> "AssistantRecord":
    """Public entry point for `_assistant_to_record`.

    The publish-time gate (EXECUTION-PLAN 7.6) needs exactly the record shape
    the batch auditor validates, built by exactly the same mapping — if the two
    diverge, an assistant can pass at publish and fail in the audit for reasons
    nobody can reproduce. Exported rather than reached for through the private
    name so that stays a contract instead of an accident.
    """
    return _assistant_to_record(a)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class EntityAdapter(Protocol):
    """Structural interface for fetching and mutating marketplace entity rows."""

    def list_records(self) -> list[AssistantRecord]:
        """Return all records for the managed entity kind."""
        ...

    def get_record(self, id: str) -> Optional[AssistantRecord]:
        """Return a single record by its ID, or None if not found."""
        ...

    def list_clone_edges(self) -> list[tuple[str, str]]:
        """Return recorded (source_id, clone_id) duplicate-provenance edges."""
        ...

    def mark_status(self, id: str, status: MarketplaceStatus, actor: str) -> None:
        """Persist a marketplace_status change on the given record."""
        ...

    def kind(self) -> EntityKind:
        """Return the EntityKind this adapter manages."""
        ...


# ---------------------------------------------------------------------------
# Concrete — AssistantAdapter
# ---------------------------------------------------------------------------


class AssistantAdapter:
    """EntityAdapter backed by the assistants table.

    Uses get_session() / SQLModel rather than AssistantRepository so that
    the service layer can list ALL assistants independently of any user
    scope or Elasticsearch.
    """

    def list_records(self) -> list[AssistantRecord]:
        # Marketplace management operates on marketplace assistants only —
        # i.e. those actually published (is_global=True) OR still moving
        # through the approval workflow (PENDING_REVIEW / REJECTED). Draft
        # private assistants stay out of the queue entirely so admins never
        # see workspace-only work in the cleanup surface.
        #
        # APPROVED is deliberately *not* in the second list (EXECUTION-PLAN
        # 7.2, F-20). `marketplace_status` carries a server default of
        # 'APPROVED' from `5df1d2e53fbf`, so every row the platform has ever
        # created claims a decision nobody made; while APPROVED alone was
        # enough to list a row, all 282 assistants — 63 of them private —
        # appeared on the moderation surface. An approval is only meaningful
        # for something that is actually public, and `is_global` already says
        # so. PENDING_REVIEW and REJECTED stay because both are mid-workflow
        # by definition and a reviewer has to be able to reach them.
        #
        # `audit_read_repository._CATALOGUE_SCOPE` is the SQL twin of this
        # predicate; the two must move together.
        with get_session() as session:
            rows = session.exec(
                select(Assistant).where(
                    or_(
                        Assistant.is_global.is_(True),
                        Assistant.marketplace_status.in_(
                            [
                                MarketplaceStatus.PENDING_REVIEW.value,
                                MarketplaceStatus.REJECTED.value,
                            ]
                        ),
                    )
                )
            ).all()
        return [_assistant_to_record(row) for row in rows]

    def get_record(self, id: str) -> Optional[AssistantRecord]:
        with get_session() as session:
            row = session.exec(select(Assistant).where(Assistant.id == id)).first()
        return _assistant_to_record(row) if row is not None else None

    def list_clone_edges(self) -> list[tuple[str, str]]:
        """Recorded clone provenance, the third v1 similarity mechanism (D3b).

        `assistant_clone_event` already knew that someone created assistant B
        by cloning assistant A; nothing read it (F-70) while the similarity
        approximation it would have corroborated was structurally disabled
        (F-13). One query for the whole corpus, because the audit scores every
        pair and needs the full edge set before it starts.

        Failure is non-fatal and returns no edges: the clone signal is
        additive, so losing it costs recall on duplicates that other signals
        may still catch, whereas failing the audit costs the operator every
        verdict. The failure is logged at WARNING, not swallowed silently.
        """
        try:
            from codemie.repository.assistants.assistant_clone_event_repository import (
                AssistantCloneEventRepository,
            )

            return AssistantCloneEventRepository().list_clone_edges()
        except Exception:
            logger.warning(
                "Could not read clone provenance edges; the clone similarity signal will be inert for this run",
                exc_info=True,
            )
            return []

    def mark_status(self, id: str, status: MarketplaceStatus, actor: str) -> None:
        with get_session() as session:
            row = session.exec(select(Assistant).where(Assistant.id == id)).first()
            if row is None:
                raise ValueError(f"Assistant {id!r} not found")
            row.marketplace_status = status.value
            session.add(row)
            session.commit()
        logger.info(f"Marketplace status of assistant {id} set to {status.value} by {actor}")

    def kind(self) -> EntityKind:
        return EntityKind.ASSISTANT


# ---------------------------------------------------------------------------
# Stub — WorkflowAdapter (future work)
# ---------------------------------------------------------------------------


_WORKFLOW_NOT_IMPLEMENTED = "WorkflowAdapter is not yet implemented"


class WorkflowAdapter:
    """Placeholder adapter for workflow entities — not yet implemented.

    Future work: wire this to whichever Workflow model/table codemie
    introduces when WORKFLOW marketplace support lands.
    """

    def list_records(self) -> list[AssistantRecord]:  # type: ignore[return]
        raise NotImplementedError(_WORKFLOW_NOT_IMPLEMENTED)

    def get_record(self, id: str) -> Optional[AssistantRecord]:  # type: ignore[return]
        raise NotImplementedError(_WORKFLOW_NOT_IMPLEMENTED)

    def list_clone_edges(self) -> list[tuple[str, str]]:
        """No clone provenance exists for workflows; an empty edge set is the
        honest answer, not a NotImplementedError.

        The other stubs raise because a caller reaching them has asked for
        something that cannot be served. This one can be served exactly: there
        is no workflow clone event table, so there are no edges. Raising here
        would take down `run_audit` for a kind whose audit is otherwise fine.
        """
        return []

    def mark_status(self, id: str, status: MarketplaceStatus, actor: str) -> None:
        raise NotImplementedError(_WORKFLOW_NOT_IMPLEMENTED)

    def kind(self) -> EntityKind:
        raise NotImplementedError(_WORKFLOW_NOT_IMPLEMENTED)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_ADAPTERS: dict[EntityKind, object] = {
    EntityKind.ASSISTANT: AssistantAdapter(),
    EntityKind.WORKFLOW: WorkflowAdapter(),
}


def get_adapter(kind: EntityKind) -> EntityAdapter:
    """Return the EntityAdapter for *kind*.

    Raises ValueError when *kind* is not registered.
    """
    try:
        return _ADAPTERS[kind]  # type: ignore[return-value]
    except KeyError:
        raise ValueError(f"No adapter registered for EntityKind {kind!r}")
