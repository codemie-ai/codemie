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

"""Built-in tool that shows interactive UI to the user in chat via A2UI.

Executing the tool validates the requested components against the A2UI Basic
Catalog, emits the surface as A2UI wire envelopes (``createSurface`` →
``updateComponents`` → optional ``updateDataModel``) into the NDJSON stream,
and ends the agent turn (``return_direct``); the user's structured answer
arrives as an ``action`` envelope with the next chat message.
"""

import json
import logging
import uuid
from typing import Optional

from codemie_tools.base.codemie_tool import CodeMieTool
from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException
from pydantic import BaseModel, Field, field_validator

from codemie.chains.base import StreamedGenerationResult
from codemie.core.a2ui import adapter, catalog
from codemie.core.a2ui.config import (
    MAX_SURFACE_ATTEMPTS,
    MAX_SURFACE_COMPONENTS,
    MAX_SURFACE_PAYLOAD_BYTES,
)
from codemie.service.conversation.a2ui_intake import unanswerable_seed_paths

logger = logging.getLogger(__name__)

REQUEST_USER_INPUT_TOOL_NAME = "request_user_input"

SURFACE_REJECTED_NOTICE = "I could not build that interactive form. Please ask me again, or continue in plain text."


class RequestUserInputArgs(BaseModel):
    components: list[dict] = Field(
        description=(
            "A2UI Basic Catalog components as a flat adjacency list. EVERY element is a "
            "component OBJECT with an 'id' and a 'component' field — never a bare id "
            "string, and never a component nested inside another one. Containers list "
            "their children by id in their own 'children'/'child' field, and each of "
            "those children appears here as its own object. Must include a component "
            "with id 'root', and the submit Button must be reachable from it. Every "
            "component and every property it may carry is defined by the A2UI JSON Schema "
            f"between {catalog.SCHEMA_BLOCK_START} and {catalog.SCHEMA_BLOCK_END} in your "
            "instructions; objects there are closed, so a property the schema does not "
            "list makes the surface invalid."
        )
    )
    data_model: Optional[dict] = Field(
        default=None,
        description="Optional initial data model for two-way bound input components.",
    )

    @field_validator("components", mode="before")
    @classmethod
    def _drop_stray_id_references(cls, value):
        """Drop a bare id string that merely repeats a component declared in the same list.

        Models routinely emit a child's id immediately before the child object itself.
        A bare string means nothing here — children are named in their parent's own
        `children`/`child` field — so it is redundant, and rejecting it would cost the
        user a whole turn with no message (the failure happens during argument parsing,
        before the tool runs, so nothing can report it). A string naming no declared
        component is NOT dropped: that is a real authoring error and must fail loudly.
        The schema still advertises objects only, so this never invites the shape.
        """
        if not isinstance(value, list):
            return value
        declared = {item["id"] for item in value if isinstance(item, dict) and isinstance(item.get("id"), str)}
        return [item for item in value if not (isinstance(item, str) and item in declared)]


class RequestUserInputTool(CodeMieTool):
    # It ends the turn and waits for the user: a workspace script cannot call it.
    name: str = REQUEST_USER_INPUT_TOOL_NAME
    description: str = (
        "Show interactive UI (forms, buttons, choices) to the user and wait for "
        "their structured response. Call this when you need an explicit decision, "
        "option selection, or short-form input. This ends your current turn; the "
        "user's structured response arrives as the next message."
    )
    # False on the LangGraph path, where this tool ends the turn itself with
    # ``Command(goto=END)``. ``return_direct`` routes on the tool NAME and cannot tell a
    # rejected call from an accepted one, so leaving it on is what made a single malformed
    # surface fatal. The classic AgentExecutor understands no such Command, so there it
    # stays True and behaves exactly as before — no retry, but no regression either.
    return_direct: bool = True
    args_schema: type[BaseModel] = RequestUserInputArgs
    # Registration gating reads the assistant's plain ``interactive_enabled`` boolean;
    # the exposed catalog is the full static Basic Catalog (see codemie.core.a2ui.catalog).
    thread_generator: object = None
    #: Surfaces this instance has already refused. The instance is built per request, so it
    #: counts attempts within one turn and nothing else.
    attempts_used: int = 0

    def is_safe(self, args: dict) -> bool:
        return True

    def __init__(self, thread_generator=None, **kwargs):
        super().__init__(thread_generator=thread_generator, **kwargs)

    @property
    def _may_retry(self) -> bool:
        """Whether a refusal can go back to the model instead of ending the turn."""
        return not self.return_direct

    def _run(self, *args, **kwargs):
        """Own what a refusal means, which the framework cannot decide for us.

        The base class turns any failure into a ``ToolException``; what happens next is the
        question this method answers. When the turn is ours to end (LangGraph path), a
        refusal is handed back to the model with the validator's reason so it can correct
        its own surface — the normalizations upstream only cover mistakes we have seen
        before, and everything else used to be fatal on the first try. Attempts are capped:
        a model that cannot satisfy the catalog does not discover it by repeating.
        """
        try:
            result = super()._run(*args, **kwargs)
        except Exception:
            self.attempts_used += 1
            if self._may_retry and self.attempts_used < MAX_SURFACE_ATTEMPTS:
                # Straight back to the model: no notice, because the user is about to be
                # shown the corrected form instead of an apology for a form they never saw.
                logger.info(
                    f"A2UI surface refused, attempt {self.attempts_used} of {MAX_SURFACE_ATTEMPTS}; "
                    "handing the reason back to the agent"
                )
                raise
            self._notify_rejected()
            if not self._may_retry:
                raise
            logger.info(f"A2UI surface refused {self.attempts_used} times; ending the turn")
            return self._ended_turn(SURFACE_REJECTED_NOTICE)
        return self._ended_turn(result)

    def _ended_turn(self, content: str):
        """End the agent turn the way this runtime allows.

        With ``return_direct`` off, the graph would loop back to the model after a tool
        call, so the stop has to be explicit. `Command` is validated against the call it
        answers, which is why the id is injected rather than invented.
        """
        if self.return_direct or not self.answering_call_id:
            return content
        from langgraph.graph import END
        from langgraph.types import Command

        return Command(
            goto=END,
            update={"messages": [ToolMessage(content=content, tool_call_id=self.answering_call_id, name=self.name)]},
        )

    #: The call this invocation answers, captured where the framework already passes it.
    #: Declaring it as an injected ARGUMENT instead would force every caller to invoke with
    #: a full ToolCall — which the classic AgentExecutor does not do, and a valid form on
    #: that runtime started coming back as the rejection notice.
    answering_call_id: Optional[str] = None

    def _parse_input(self, tool_input, tool_call_id=None):
        """Cover the failure that happens BEFORE ``execute`` can report it.

        Arguments are validated by the framework first, so a shape the schema refuses —
        ``components`` as an object, ``data_model`` as a list — never reaches the rejection
        path inside ``execute``. The turn still ends (``return_direct`` routes on the tool
        NAME), and what the user was shown was the raw pydantic error, complete with a link
        to pydantic's docs. Stream the same notice as any other rejection, and replace the
        message with it so nothing internal is put in front of the user; the real error is
        logged by the base class.
        """
        self.answering_call_id = tool_call_id
        try:
            return super()._parse_input(tool_input, tool_call_id)
        except Exception as error:
            if self._may_retry:
                # Counted here too: an argument shape the schema refuses never reaches
                # `_run`, so without this a model repeating the same malformed call would
                # only be stopped by the graph's recursion limit.
                self.attempts_used += 1
                if self.attempts_used >= MAX_SURFACE_ATTEMPTS:
                    self._notify_rejected()
                    raise ToolException(SURFACE_REJECTED_NOTICE) from error
                # The model can fix this itself, and the reason has to survive to reach it.
                raise ToolException(f"Invalid arguments for {self.name}: {error}") from error
            self._notify_rejected()
            raise ToolException(SURFACE_REJECTED_NOTICE) from error

    def execute(self, components: list, data_model: Optional[dict] = None, **kwargs) -> str:
        # Every failure here simply propagates: whether the user is told or the model is
        # given another try is decided in `_run`, the only place that knows whether this
        # turn can continue.
        self._check_surface_bounds(components, data_model)
        data_model, repointed = catalog.rekey_pointer_data_model(data_model)
        if repointed:
            # Loud, because until now these answers were silently discarded and the user
            # got the empty form back after submitting a full one.
            logger.info(f"Rewrote data model keys written as binding paths: {repointed}")
        components, hoisted = catalog.hoist_misplaced_components(components)
        if hoisted:
            # First, because pruning would otherwise strip these down to empty husks and
            # the layout that references them would still be broken.
            logger.info(f"Lifted components the agent nested inside a property: {hoisted}")
        components, renamed_root = catalog.name_the_root(components)
        if renamed_root:
            logger.info(f"Renamed the surface's single top component '{renamed_root}' to 'root'")
        components, dropped = catalog.prune_to_catalog(components)
        if dropped:
            # Logged rather than silent: operators should see what agents keep asking
            # for that the catalog cannot express.
            logger.info(f"Dropped properties the A2UI catalog does not define: {dropped}")
        components, triggers = catalog.give_modal_triggers_an_action(components)
        if triggers:
            # Not silent: the surface only validates because we supplied a property the
            # agent left out, and an operator reading this should see how often that is.
            logger.info(f"Supplied the catalog-required action for Modal triggers: {triggers}")
        surface_id = str(uuid.uuid4())
        # Last normalization, and the only one that needs the validator: a malformed
        # check is client-side validation the server re-does anyway, so it is not worth
        # the whole form. Runs before the seed check, which reads the emitted surface.
        components, unchecked = catalog.drop_invalid_checks(
            components,
            lambda candidate: catalog.validate_envelopes(
                adapter.build_surface_envelopes(surface_id, candidate), root_id="root"
            ),
        )
        if unchecked:
            logger.info(f"Dropped checks that did not validate, keeping the surface: {unchecked}")
        envelopes = adapter.build_surface_envelopes(surface_id, components, data_model=data_model)
        # A seed the intake would refuse is not cosmetic: the client echoes the stored
        # data model back on every submit, so the form could never be sent and no retry
        # would clear it. Asked of the intake itself rather than restated here, so the
        # two directions cannot drift apart.
        refused = unanswerable_seed_paths(envelopes, data_model)
        if refused:
            logger.info(f"Dropped seeded data model keys the surface cannot accept back: {refused}")
            data_model = {k: v for k, v in data_model.items() if k not in set(refused)}
            envelopes = adapter.build_surface_envelopes(surface_id, components, data_model=data_model)
        errors = catalog.validate_envelopes(envelopes)
        if errors:
            raise ValueError(f"Invalid A2UI surface: {'; '.join(errors)}")
        self._check_surface_is_answerable(components)
        for envelope in envelopes:
            self.thread_generator.send(StreamedGenerationResult(a2ui=envelope).model_dump_json())
        logger.info(f"Emitted A2UI surface {surface_id} ({len(envelopes)} envelopes)")
        return ""  # return_direct=True ends the agent turn with no extra text

    def _notify_rejected(self) -> None:
        """Tell the user the form could not be built, since the turn is about to end."""
        self.thread_generator.send(StreamedGenerationResult(generated_chunk=SURFACE_REJECTED_NOTICE).model_dump_json())

    @staticmethod
    def _reachable_ids(by_id: dict[str, dict]) -> set[str]:
        """Ids reachable from 'root' — i.e. the components the user will actually see.

        A component the layout never references is legal A2UI and passes catalog
        validation (only the opposite, a reference to a missing component, is an error),
        but it renders nowhere.
        """
        reachable: set[str] = set()
        queue = ["root"]
        while queue:
            current = queue.pop()
            if current in reachable or current not in by_id:
                continue
            reachable.add(current)
            queue.extend(catalog.referenced_ids(by_id[current]))
        return reachable

    @classmethod
    def _check_surface_is_answerable(cls, components: list) -> None:
        """Refuse a surface the user has no way to answer.

        ``return_direct`` ends the agent turn here and the conversation waits for an
        action envelope, but ``Button`` is the only Basic Catalog component that declares
        one. The Button must also be REACHABLE from 'root' (an orphan renders nowhere) and
        must not be a Modal's trigger, whose click opens the dialog instead of submitting.
        Otherwise the user is stranded on a form with no send control, and the intake
        rejects any action claiming to come from it.
        """
        declared = [c for c in (components if isinstance(components, list) else []) if isinstance(c, dict)]
        by_id = {c["id"]: c for c in declared if isinstance(c.get("id"), str)}
        reachable = cls._reachable_ids(by_id)
        cls._check_every_picker_offers_something(declared, reachable)
        cls._check_a_submit_button_is_reachable(declared, reachable)

    @staticmethod
    def _check_every_picker_offers_something(declared: list[dict], reachable: set[str]) -> None:
        """A picker offering nothing cannot be answered, and now has a second way of arising:
        every option was marked unavailable, so pruning removed them all."""
        for component in declared:
            if component.get("component") != "ChoicePicker" or component.get("id") not in reachable:
                continue
            if not component.get("options"):
                raise ValueError(
                    f"Invalid A2UI surface: ChoicePicker '{component.get('id')}' offers no options the user "
                    "can pick (options marked unavailable are removed); offer at least one, or ask differently"
                )

    @staticmethod
    def _submits(component: dict) -> bool:
        """True when this Button carries an action the user can actually fire."""
        action = component.get("action")
        event = action.get("event") if isinstance(action, dict) else None
        return isinstance(event, dict) and bool(event.get("name"))

    @classmethod
    def _check_a_submit_button_is_reachable(cls, declared: list[dict], reachable: set[str]) -> None:
        modal_triggers = {
            c["trigger"] for c in declared if c.get("component") == "Modal" and isinstance(c.get("trigger"), str)
        }
        candidates = [
            c
            for c in declared
            if c.get("component") == "Button" and c.get("id") in reachable and c.get("id") not in modal_triggers
        ]
        if any(cls._submits(c) for c in candidates):
            return
        raise ValueError(
            "Invalid A2UI surface: no Button with an action event reachable from 'root'; add a Button the user can "
            "reach to submit their answer (a Modal trigger opens the dialog, it does not submit)"
        )

    @staticmethod
    def _check_surface_bounds(components: list, data_model: Optional[dict]) -> None:
        """Bound the surface BEFORE parsing/validation.

        The surface is authored by the agent, which is steerable via prompt injection,
        and it is streamed and then persisted into conversation history — where it is
        re-serialized into the prompt on every later turn. The ValueError reaches the model
        as a tool error, but only as context for its NEXT turn: this one ends regardless.
        """
        count = len(components) if isinstance(components, list) else 0
        if count > MAX_SURFACE_COMPONENTS:
            raise ValueError(
                f"Invalid A2UI surface: too many components ({count}, "
                f"max {MAX_SURFACE_COMPONENTS}); ask for less at a time"
            )
        size = len(json.dumps(components, default=str)) + len(json.dumps(data_model or {}, default=str))
        if size > MAX_SURFACE_PAYLOAD_BYTES:
            raise ValueError(
                f"Invalid A2UI surface: payload too large ({size} bytes, "
                f"max {MAX_SURFACE_PAYLOAD_BYTES}); shorten the surface"
            )
