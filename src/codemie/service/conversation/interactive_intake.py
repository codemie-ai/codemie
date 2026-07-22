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

"""Validation and history materialization for structured interactive responses."""

import json
from typing import Iterable

from pydantic import ValidationError

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.interactive import InteractiveRequest, InteractiveResponse, validate_response_values


def validate_interactive_intake(
    history: Iterable,
    response: InteractiveResponse,
    replacing_history_index: int | None = None,
) -> InteractiveRequest:
    """Validate an incoming structured response against the conversation history.

    Ensures the referenced request exists, has not been answered yet, and the
    submitted values pass server-side re-validation (client validation is not
    trusted). Raises ExtendedHTTPException(422) on any violation.

    Re-answering a form mirrors editing the previous user request: when this submit
    replaces a turn (``replacing_history_index`` set), the stored answer AT THAT EXACT
    turn is being overwritten, so it does not count as "already answered".

    ``replacing_history_index`` comes from the client, so the match is strict equality
    on the stored answer's OWN ``history_index`` — never ``>=``. Otherwise a client
    could send a low index (e.g. 0) to mark every prior answer as "being replaced" and
    slip a duplicate past the once-only guard (and steer the replaced turn).
    """
    request = None
    for message in history:
        stored_request = getattr(message, "interactive_request", None)
        if stored_request is not None and stored_request.request_id == response.request_id:
            request = stored_request
        stored_response = getattr(message, "interactive_response", None)
        if stored_response is not None and stored_response.request_id == response.request_id:
            message_index = getattr(message, "history_index", None)
            being_replaced = (
                replacing_history_index is not None
                and message_index is not None
                and message_index == replacing_history_index
            )
            if not being_replaced:
                raise ExtendedHTTPException(code=422, message="Interactive request already answered")
    if request is None:
        raise ExtendedHTTPException(code=422, message="Unknown interactive request_id")
    try:
        validate_response_values(response, request)
    except (ValidationError, RecursionError):
        # A malformed/pathological stored surface must surface as a clean 4xx,
        # never an unhandled 500. ValidationError subclasses ValueError, so this
        # specific clause must come first or it would be shadowed below.
        raise ExtendedHTTPException(code=422, message="Invalid interactive request or response")
    except ValueError as error:
        raise ExtendedHTTPException(code=422, message=str(error))
    return request


def materialize_interactive_message_text(display_text: str, response: InteractiveResponse) -> str:
    """Deterministic structured text replayed to the LLM for an interactive response."""
    return (
        f"{display_text}\n\n"
        f"[Structured response to interactive request {response.request_id}]\n"
        f"{json.dumps(response.payload, ensure_ascii=False)}"
    )


def materialize_interactive_request_text(display_text: str, request: InteractiveRequest) -> str:
    """Deterministic text describing the interactive surface the assistant showed the user.

    The ``request_user_input`` tool returns "" (return_direct), so the assistant message is
    empty; without this the resumed turn would see the user's structured answer with no
    record of what was asked. Replay the surface so the model keeps the context of its own
    question, symmetric to ``materialize_interactive_message_text`` on the response side.
    """
    surface = request.model_dump(mode="json").get("surface", [])
    materialized = (
        f"[Interactive request {request.request_id} shown to the user]\n" f"{json.dumps(surface, ensure_ascii=False)}"
    )
    return f"{display_text}\n\n{materialized}" if display_text else materialized
