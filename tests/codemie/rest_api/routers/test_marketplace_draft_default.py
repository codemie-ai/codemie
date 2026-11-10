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

"""A newly created assistant is a DRAFT, not an APPROVED one (EXECUTION-PLAN 12.3).

`5df1d2e53fbf:29` gave `assistants.marketplace_status` a server default of
`'APPROVED'` and the SQLModel class repeated it (`models/assistant.py:657`), so
every assistant the platform has ever created was stamped with the terminal
state of the review state machine before anybody looked at it. It was latent
only because `is_global` gates visibility; the record itself claimed a
moderation decision that no human made, and EXECUTION-PLAN 7.2 already had to
run a backfill to undo exactly that for 63 rows.

DRAFT rather than NULL, for three reasons that all come from the state machine
rather than from taste:

1. `assistant._SUBMITTABLE_STATUSES` is `("DRAFT", "REJECTED", "WITHDRAWN")`,
   and `queue_service`'s compare-and-set guards the transition with
   `AND marketplace_status = ANY(:allowed)`. NULL is equal to nothing in SQL,
   so a NULL row could never be submitted: its first publish would 409 against
   its own transition table.
2. `ck_assistants_marketplace_status` (`o1r2e3v4i5e6:106`) admits NULL only as
   legacy tolerance -- `MarketplaceStatus` itself has no NULL member. DRAFT is
   the enum's own initial state.
3. `AssistantAdapter.list_records` / `_CATALOGUE_SCOPE` already exclude DRAFT,
   so a draft stays off every moderation surface -- which is the property the
   7.2 backfill was reaching for.
"""

from __future__ import annotations


from codemie.rest_api.models.assistant import Assistant


def _payload(name: str) -> dict:
    return {
        "name": name,
        "description": "An assistant nobody has submitted to the marketplace.",
        "system_prompt": "You answer questions about the codebase.",
        "project": "demo",
        "llm_model_type": "gpt-4o",
        "toolkits": [],
        "context": [],
    }


def test_the_orm_default_is_draft() -> None:
    """The in-memory object the create path builds is a draft.

    `create_assistant` constructs `Assistant(**request.model_dump(...))` and
    never touches `marketplace_status`, so whatever the field defaults to is
    what gets persisted.
    """
    fresh = Assistant(
        name="Fresh",
        description="d",
        system_prompt="p",
    )

    assert fresh.marketplace_status != "APPROVED", (
        "a never-submitted assistant is stamped with the review state machine's " "TERMINAL state"
    )
    assert fresh.marketplace_status == "DRAFT"
