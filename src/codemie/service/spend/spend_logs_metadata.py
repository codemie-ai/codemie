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

"""The one builder for the `x-litellm-spend-logs-metadata` header.

Seven keys in **four groups**, kept apart because their failure semantics differ:

| Group | Keys | Absence means |
| --- | --- | --- |
| correlation | `codemie_request_id`, `codemie_run_id` | the row cannot be tied to the work that caused it |
| identity | `codemie_user_id`, `codemie_category`, `codemie_project` | a **defect** - the fallback parser takes over |
| dimension | `codemie_workflow_id` | **normal** - most calls are not inside a workflow |
| origin | `codemie_origin` | **normal** - what the request was for; absent before the sending release |

**The header value is built with `json.dumps` and never by concatenation, and that is not style.**
LiteLLM parses this header with a swallow-everything `safe_json_loads` and then *skips the
assignment* when it returns None. One malformed character therefore discards **all seven keys at
once** - the identity triple included - with no error on either side, and the row lands with no
project. `ensure_ascii=True` (the default, stated here so it is not "simplified" away) matters
because `codemie_project` is free text while httpx and the OpenAI SDK require latin-1/ASCII header
values. `sort_keys=True` only makes the value deterministic to assert on.

**Every value is a flat string**, because every target column is `text`. LiteLLM applies no schema,
whitelist, key limit or size limit - the parsed dict is stored wholesale - so nothing downstream
will catch a nested value.

**The identity triple is all-or-nothing** (`_identity_or_nothing`). A partial tuple is never sent in
part: a row carrying a user id and no category would satisfy every provenance check and then vanish
from every category budget, which is the unattributed-row failure wearing the provenance column as a
disguise. Better to send nothing and be parsed by the fallback, which is a *counted* state.

**The two non-identity keys are outside that rule, and the asymmetry is written here because this is
where it is enforced.** A chat call legitimately has no workflow id, and a call made on a pooled
thread nothing published on legitimately has no origin. If completeness were ever tested over "every
key present in the object", every ordinary chat call would fail it, fall to the fallback parser, and
collapse project attribution platform-wide. So each of the two is validated on its own terms - the
workflow id for emptiness, the origin against its closed vocabulary - and a missing, empty or
unrecognised value affects only its own column. Never the identity triple, never the correlation ids.
"""

from __future__ import annotations

import json
from uuid import UUID

from codemie.configs import logger
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend.traffic_class import TrafficOrigin

#: The header LiteLLM reads and promotes into columns. On the proxy path it is also **stripped**
#: from the inbound request before this one is set - see the proxy router. That strip is an
#: attribution-integrity control, not hygiene: this header carries identity, so a client able to
#: set it could book its spend against another project's budget.
SPEND_LOGS_METADATA_HEADER = "x-litellm-spend-logs-metadata"

KEY_REQUEST_ID = "codemie_request_id"
KEY_RUN_ID = "codemie_run_id"
KEY_USER_ID = "codemie_user_id"
KEY_CATEGORY = "codemie_category"
KEY_PROJECT = "codemie_project"
KEY_WORKFLOW_ID = "codemie_workflow_id"
KEY_ORIGIN = "codemie_origin"

__all__ = [
    "KEY_CATEGORY",
    "KEY_ORIGIN",
    "KEY_PROJECT",
    "KEY_REQUEST_ID",
    "KEY_RUN_ID",
    "KEY_USER_ID",
    "KEY_WORKFLOW_ID",
    "SPEND_LOGS_METADATA_HEADER",
    "build_spend_logs_metadata",
    "spend_logs_metadata_header",
]


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _identity_or_nothing(
    user_id: str | None,
    category: str | None,
    project: str | None,
) -> dict[str, str]:
    """The identity triple, or an empty dict - never a subset of it.

    `user_id` must parse as a UUID and `category` must be one of the closed `BudgetCategory`
    values. `project` is **optional**: its absence means principal form, not a missing field, so it
    never fails the triple.
    """
    if not user_id or not category or not _is_uuid(user_id):
        return {}
    try:
        BudgetCategory(category)
    except ValueError:
        return {}

    identity = {KEY_USER_ID: user_id, KEY_CATEGORY: category}
    if project:
        identity[KEY_PROJECT] = project
    return identity


def _origin_or_nothing(origin: str | None) -> dict[str, str]:
    """The origin key, or an empty dict - never a word outside the closed vocabulary.

    An unrecognised origin is worse than none: it belongs to no traffic class, so the row is claimed
    by neither the origin branch of a class selector nor the native predicate the NULL branch falls
    back to, and it disappears from the split while looking well-formed. Dropping it restores the
    *known* hole instead.
    """
    if not origin:
        return {}
    try:
        TrafficOrigin(origin)
    except ValueError:
        logger.debug(
            f"spend_logs_metadata: {origin!r} is not a known traffic origin; the row is sent with no origin key"
        )
        return {}
    return {KEY_ORIGIN: origin}


def build_spend_logs_metadata(
    *,
    request_id: str | None = None,
    run_id: str | None = None,
    user_id: str | None = None,
    category: str | None = None,
    project: str | None = None,
    workflow_id: str | None = None,
    origin: str | None = None,
) -> dict[str, str]:
    """The metadata object, with each group admitted on its own terms."""
    payload: dict[str, str] = {}

    if request_id:
        payload[KEY_REQUEST_ID] = request_id
    if run_id:
        payload[KEY_RUN_ID] = run_id

    payload.update(_identity_or_nothing(user_id, category, project))

    # Validated separately from the identity triple, and separately from each other - see this
    # module's docstring.
    if workflow_id:
        payload[KEY_WORKFLOW_ID] = workflow_id
    payload.update(_origin_or_nothing(origin))

    return payload


def spend_logs_metadata_header(**values: str | None) -> dict[str, str]:
    """The ready-to-send header, or `{}` when there is nothing worth sending.

    An empty dict rather than a header with an empty object: an empty object is a well-formed
    payload carrying no facts, which is indistinguishable downstream from a call that sent
    something and had it dropped.
    """
    payload = build_spend_logs_metadata(**values)
    if not payload:
        return {}
    return {SPEND_LOGS_METADATA_HEADER: json.dumps(payload, ensure_ascii=True, sort_keys=True)}
