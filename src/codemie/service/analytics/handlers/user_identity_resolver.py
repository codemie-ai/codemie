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

"""Centralized user identity resolution for analytics handlers."""

from __future__ import annotations

import logging
from typing import Literal

from codemie.clients.postgres import get_async_session
from codemie.repository.user_repository import user_repository

logger = logging.getLogger(__name__)

_CODEMIE_SUFFIX_SEP = "_codemie_"
_SENTINEL_VALUES: frozenset[str] = frozenset({"unknown"})

ResolutionTarget = Literal["email", "name", "id"]


class UserIdentityResolver:
    """Single source of truth for resolving raw user identifiers.

    Resolution steps (applied to each candidate in priority order):
      1. Strip _codemie_* suffix.
      2. For target="email": if result contains @ it is already an email, return immediately.
      3. UUID  → bulk lookup by UserDB.id
      4. Name / email string → bulk lookup by UserDB.username, UserDB.name, or UserDB.email

    target controls which field of the resolved user record is returned:
      "email" (default) — UserDB.email
      "name"            — UserDB.name or UserDB.username (prefers display name)
      "id"              — UserDB.id (canonical UUID)
    """

    # ── Public API ────────────────────────────────────────────────────────────

    @staticmethod
    async def resolve(*values: str | None, target: ResolutionTarget = "email") -> str:
        """Resolve the first valid identity from the given candidates (tried in order).

        Each value is suffix-stripped; for target="email" if the result contains @
        it is returned immediately. Otherwise all stripped candidates are resolved
        via a single bulk PostgreSQL lookup and the highest-priority match is returned.

        Usage: await UserIdentityResolver.resolve(user_email, user_name, raw_id)
        """
        candidates = [UserIdentityResolver._strip_suffix(v) for v in values if v]

        # Fast path: plain email when target is email
        if target == "email":
            for c in candidates:
                if UserIdentityResolver._is_email(c):
                    logger.debug(f"resolve: email candidate returned as-is: {c}")
                    return c

        if not candidates:
            return ""

        async with get_async_session() as session:
            resolved = await user_repository.afind_users_by_identifiers(session, set(candidates))

        for c in candidates:
            if record := resolved.get(c):
                value = UserIdentityResolver._pick_field(record, target)
                logger.debug(f"resolve: {c!r} -> {value!r}")
                return value

        logger.debug(f"resolve: no record found for candidates={candidates!r}, returning {candidates[0]!r}")
        return candidates[0]

    @staticmethod
    async def resolve_rows(
        rows: list[dict],
        *column_keys: str,
        target: ResolutionTarget = "email",
        target_map: dict[str, ResolutionTarget] | None = None,
    ) -> None:
        """Normalise user identifier columns in rows **in-place**.

        Two calling styles:

        Single target (all columns resolved to the same field):
            await resolve_rows(rows, "user_email")               # default target="email"
            await resolve_rows(rows, "user_name", target="name")

        Per-column target map (one DB round-trip for multiple columns):
            await resolve_rows(rows, target_map={"user_name": "name", "user_email": "email"})

        Steps per column:
          1. Strip _codemie_* suffix.
          2. Skip values that are already in final form (email fast-path when target="email").
          3. Bulk-resolve remaining identifiers via a single PostgreSQL lookup.
          4. Replace each value with the field specified by that column's target.
        """
        effective: dict[str, ResolutionTarget] = (
            target_map if target_map is not None else dict.fromkeys(column_keys, target)
        )
        if not effective or not rows:
            return

        UserIdentityResolver._strip_row_identifiers(rows, effective)

        to_resolve = UserIdentityResolver._collect_unresolved(rows, effective)
        if not to_resolve:
            return

        logger.debug(f"resolve_rows: resolving {len(to_resolve)} identifier(s): {to_resolve!r}")

        async with get_async_session() as session:
            resolved = await user_repository.afind_users_by_identifiers(session, to_resolve)

        logger.debug(f"resolve_rows: DB resolved {len(resolved)}/{len(to_resolve)} identifier(s)")
        UserIdentityResolver._apply_resolved(rows, effective, resolved)

    @staticmethod
    async def resolve_and_merge(users_list: list[dict]) -> list[dict]:
        """Resolve raw ES identifiers and deduplicate rows by canonical user_id.

        Input rows have {id, name} from an ES composite aggregation on (user_id, user_name).
        The same physical user can appear multiple times with different raw identifiers stored
        in ES. After resolution, rows sharing the same canonical user_id are merged
        (first occurrence wins).

        Returns a new list with resolved {id, name, email} and no duplicates. email is None
        for rows that could not be resolved to a UserDB record.
        """
        if not users_list:
            return users_list

        stripped = UserIdentityResolver._collect_stripped(users_list)
        all_stripped = set(stripped.values()) - {""}
        async with get_async_session() as session:
            resolved = await user_repository.afind_users_by_identifiers(session, all_stripped)

        seen_keys: set[str] = set()
        merged: list[dict] = []
        for row in users_list:
            raw_id = row.get("id", "")
            raw_name = row.get("name", "")
            stripped_id = stripped.get(raw_id, raw_id)
            stripped_name = stripped.get(raw_name, raw_name)

            # Skip known placeholder/sentinel identifiers
            if stripped_id in _SENTINEL_VALUES or stripped_name in _SENTINEL_VALUES:
                continue

            record = resolved.get(stripped_id) or resolved.get(stripped_name)

            if record:
                # Dedup by canonical UUID from DB — reliable even when ES stores different forms
                dedup_key = record.id
                canonical_id = record.id
                canonical_name = record.name or record.username
                canonical_email = record.email
            else:
                # Unresolved: dedup by raw ES user_id (shared across rows for the same user).
                # Fall back to an email-shaped raw identifier when ES stored one.
                dedup_key = stripped_id or stripped_name
                canonical_id = stripped_id
                canonical_name = stripped_name or stripped_id
                canonical_email = next(
                    (v for v in (stripped_name, stripped_id) if v and UserIdentityResolver._is_email(v)), None
                )

            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            merged.append({"id": canonical_id, "name": canonical_name, "email": canonical_email})

        logger.debug(f"resolve_and_merge: {len(users_list)} raw rows → {len(merged)} after dedup")
        return merged

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _pick_field(record, target: ResolutionTarget) -> str:
        if target == "id":
            return record.id
        if target == "name":
            # For target="name", even email-shaped values go to the DB because
            # the caller wants the display name, not the email string back.
            return record.name or record.username
        return record.email

    @staticmethod
    def _collect_stripped(users_list: list[dict]) -> dict[str, str]:
        """Return a map of original identifier → suffix-stripped value for all id/name fields."""
        stripped: dict[str, str] = {}
        for row in users_list:
            for val in (row.get("id"), row.get("name")):
                if val:
                    stripped[val] = UserIdentityResolver._strip_suffix(val)
        return stripped

    @staticmethod
    def _strip_row_identifiers(rows: list[dict], target_map: dict[str, ResolutionTarget]) -> None:
        for row in rows:
            for key in target_map:
                if val := row.get(key):
                    row[key] = UserIdentityResolver._strip_suffix(val)

    @staticmethod
    def _collect_unresolved(rows: list[dict], target_map: dict[str, ResolutionTarget]) -> set[str]:
        result: set[str] = set()
        for row in rows:
            for key, tgt in target_map.items():
                val = row.get(key)
                if not val or val in _SENTINEL_VALUES:
                    continue
                # For target="email", skip values already in email form (fast-path)
                if tgt == "email" and UserIdentityResolver._is_email(val):
                    continue
                result.add(val)
        return result

    @staticmethod
    def _apply_resolved(
        rows: list[dict],
        target_map: dict[str, ResolutionTarget],
        resolved: dict,
    ) -> None:
        for row in rows:
            for key, tgt in target_map.items():
                UserIdentityResolver._apply_column(row, key, tgt, resolved)

    @staticmethod
    def _apply_column(row: dict, key: str, tgt: ResolutionTarget, resolved: dict) -> None:
        val = row.get(key)
        if not val or val in _SENTINEL_VALUES:
            return
        if tgt == "email" and UserIdentityResolver._is_email(val):
            return
        if record := resolved.get(val):
            new_val = UserIdentityResolver._pick_field(record, tgt)
            if new_val != val:
                logger.debug(f"resolve_rows[{key}]: {val!r} -> {new_val!r}")
            else:
                logger.debug(f"resolve_rows[{key}]: {val!r} unchanged")
            row[key] = new_val
        else:
            logger.debug(f"resolve_rows[{key}]: {val!r} unresolved, keeping as-is")

    @staticmethod
    def _strip_suffix(value: str) -> str:
        idx = value.find(_CODEMIE_SUFFIX_SEP)
        return value[:idx] if idx >= 0 else value

    @staticmethod
    def _is_email(value: str) -> bool:
        return "@" in value
