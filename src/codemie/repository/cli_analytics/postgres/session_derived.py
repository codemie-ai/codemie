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

"""The session_dims columns derived in Python: `feature_id` and `delivery_framework`.

They are recomputed last in the refresher's transaction, from what the SESSION statements have just
written. The delivery-framework rules are customer configuration read by the service layer: the
classifier is handed in from outside, and without one the column is not written.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

import asyncpg

logger = logging.getLogger(__name__)

# The skill names the classifier reads: the keys of `skills` once a summary filled the row, else the
# session's session_skills (OTel) together with the keys of the `skills` fallback, which also counts
# the skills known from hooks. The keys are read only from an object, so no row can raise.
_SKILLS_OBJECT = "CASE WHEN jsonb_typeof(sd.skills) = 'object' THEN sd.skills END"
_READ = f"""
SELECT sd.session_id, sd.branch_dominant,
       CASE WHEN sd.summary_ts IS NOT NULL THEN
                (SELECT array_agg(s.name ORDER BY s.name COLLATE "C") FROM jsonb_object_keys({_SKILLS_OBJECT}) s (name)
                 WHERE s.name <> '')
            ELSE (SELECT array_agg(x.name ORDER BY x.name COLLATE "C")
                  FROM (SELECT ss.skill_name AS name FROM session_skills ss WHERE ss.session_id = sd.session_id
                        UNION
                        SELECT s.name FROM jsonb_object_keys({_SKILLS_OBJECT}) s (name)) x
                  WHERE x.name <> '')
       END AS skill_names
FROM session_dims sd
WHERE sd.session_id = ANY($1::text[])
ORDER BY sd.session_id"""
# A NULL never overwrites a stored value; a row is rewritten only when a value changes.
_WRITE_FEATURE_ID = """
UPDATE session_dims sd SET feature_id = u.feature_id
FROM unnest($1::text[], $2::text[]) AS u (session_id, feature_id)
WHERE sd.session_id = u.session_id AND u.feature_id IS NOT NULL AND sd.feature_id IS DISTINCT FROM u.feature_id"""
_WRITE_DERIVED = """
UPDATE session_dims sd
SET feature_id = coalesce(u.feature_id, sd.feature_id),
    delivery_framework = coalesce(u.delivery_framework, sd.delivery_framework)
FROM unnest($1::text[], $2::text[], $3::text[]) AS u (session_id, feature_id, delivery_framework)
WHERE sd.session_id = u.session_id
  AND (sd.feature_id IS DISTINCT FROM coalesce(u.feature_id, sd.feature_id)
       OR sd.delivery_framework IS DISTINCT FROM coalesce(u.delivery_framework, sd.delivery_framework))"""


def feature_id(branch: str | None) -> str | None:
    """`branch_dominant` without its first path segment: `feature/ABC-1-x` -> `ABC-1-x`, `main` -> `main`.

    NULL stays NULL, never a default such as `unknown`.
    """
    if branch is None:
        return None
    _, slash, rest = branch.partition("/")
    return (rest if slash else branch) or None


def _framework(classify: Callable[[list[str]], str], session_id: str, skill_names: Sequence[str] | None) -> str | None:
    """The classifier's label; None (column not written) without a skill name, as it would answer a
    default label for an empty list. A failing classifier must not hold the rollup queue back."""
    if not skill_names:
        return None
    try:
        return classify(list(skill_names)) or None
    except Exception as exc:
        logger.exception(
            f"cli_analytics: classifying the delivery framework of session_id={session_id!r} failed; "
            f"its stored value is kept: {exc}"
        )
        return None


async def update_session_derived(
    conn: asyncpg.Connection, session_ids: list[str], classify: Callable[[list[str]], str] | None, timeout: float
) -> None:
    """Recompute feature_id (always) and delivery_framework (with a classifier) of the sessions' rows.

    `timeout` is the client-side limit of each statement: the caller's, so that a slow one ends as
    every other statement of its transaction does.
    """
    if not session_ids:
        return
    ids: list[str] = []
    features: list[str | None] = []
    frameworks: list[str | None] = []
    for row in await conn.fetch(_READ, session_ids, timeout=timeout):
        feature = feature_id(row["branch_dominant"])
        framework = None if classify is None else _framework(classify, row["session_id"], row["skill_names"])
        if feature is None and framework is None:
            continue
        ids.append(row["session_id"])
        features.append(feature)
        frameworks.append(framework)
    if not ids:
        return
    if classify is None:
        await conn.execute(_WRITE_FEATURE_ID, ids, features, timeout=timeout)
    else:
        await conn.execute(_WRITE_DERIVED, ids, features, frameworks, timeout=timeout)
