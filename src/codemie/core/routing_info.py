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

"""Canonical routing metadata value-object and header-codec mixin.

Pure DTOs, zero business logic: ``RoutingInfo`` (the router-agnostic value carried on domain
models), ``ClassifierUsage``, and ``RoutingHeaderCodec`` (shared by ``SwitchyardMeta`` and
``LiteLLMRouterMeta`` for HTTP-header (de)serialization). No import-time OR runtime
dependency on switchyard/litellm/service — genuinely a leaf module. This includes
``_ROUTING_INFO_KEY``/``stamp_routing_info``: they live here, next to ``RoutingInfo`` itself,
rather than in ``core/router_chat_model.py`` (which stamps them onto a response) — the
opposite placement used to force ``RoutingInfo.from_response`` to reach into
``router_chat_model``'s private module state to read its own serialization key back out,
which was backwards (a value type should own its own wire format) and created an import
cycle between the two modules, papered over only by a lazy import.

The ``Router`` interface, ``RoutingDecision``/``CallContext``, and the ``create_router()``
factory live elsewhere: ``core/router.py`` and
``codemie.service.llm_service.router_factory`` respectively.
"""

from __future__ import annotations

import contextlib
import dataclasses
from collections.abc import Mapping
from typing import TYPE_CHECKING, ClassVar, Final, Protocol, cast
from urllib.parse import quote

from langchain_core.messages import AIMessage
from pydantic import BaseModel, Field

# Key under which the canonical RoutingInfo (not a router-specific dataclass) is stamped onto
# an AIMessage's response_metadata after a routed call returns, by RouterChatModel._agenerate
# — read back self-describingly by RoutingInfo.from_response(), and by
# AgentInvokeCallback/AgentStreamingCallback, which never need a Router reference at all.
# Prefer stamp_routing_info()/RoutingInfo.from_response() over touching this key directly.
_ROUTING_INFO_KEY = "_routing_info"


def stamp_routing_info(message: AIMessage, info: "RoutingInfo") -> None:
    """Stamp the canonical RoutingInfo onto *message*.response_metadata, in place. Read back
    with RoutingInfo.from_response()."""
    message.response_metadata = {
        **(message.response_metadata or {}),
        _ROUTING_INFO_KEY: info.model_dump(),
    }


# Printable ASCII minus '%' — safe to pass through unencoded in HTTP/1.1 header values.
# Non-ASCII characters (e.g. Cyrillic from LLM output) are percent-encoded so the value
# stays valid latin-1 while remaining fully reversible via urllib.parse.unquote.
_HEADER_SAFE_CHARS: Final[str] = " " + "".join(chr(c) for c in range(0x21, 0x7F) if chr(c) != "%")


def encode_header_value(raw: str) -> str:
    """Percent-encode *raw* for safe use as a latin-1 HTTP header value (reversible)."""
    return quote(raw, safe=_HEADER_SAFE_CHARS)


class _RoutingCodecFields(Protocol):
    """The three ClassVars every RoutingHeaderCodec subclass declares."""

    FIELD_TO_HEADER: ClassVar[dict[str, str]]
    INT_FIELDS: ClassVar[frozenset[str]]
    FLOAT_FIELDS: ClassVar[frozenset[str]]


if TYPE_CHECKING:
    from _typeshed import DataclassInstance

    class _DataclassCodec(_RoutingCodecFields, DataclassInstance, Protocol):
        """RoutingHeaderCodec subclass that is also a dataclass — bound for from_headers."""
else:
    _DataclassCodec = object


class RoutingHeaderCodec:
    """Mixin giving a routing-metadata dataclass ``to_headers``/``from_headers``.

    Shared by ``SwitchyardMeta`` and ``LiteLLMRouterMeta`` so the two independent routing
    backends serialise metadata to HTTP headers identically, instead of each maintaining its
    own copy of the encoding/parsing logic. Subclasses declare:
      - ``FIELD_TO_HEADER``: dataclass field name -> header name (required)
      - ``INT_FIELDS`` / ``FLOAT_FIELDS``: field names that need numeric parsing in
        ``from_headers`` (header values otherwise arrive as strings)
    """

    FIELD_TO_HEADER: ClassVar[dict[str, str]] = {}
    INT_FIELDS: ClassVar[frozenset[str]] = frozenset()
    FLOAT_FIELDS: ClassVar[frozenset[str]] = frozenset()

    def to_headers(self) -> dict[str, str]:
        """Serialise non-None fields to {header_name: str_value}."""
        result: dict[str, str] = {}
        for field_name, header_name in self.FIELD_TO_HEADER.items():
            value = getattr(self, field_name)
            if value is None:
                continue
            raw = f"{value:.6g}" if isinstance(value, float) else str(value)
            result[header_name] = encode_header_value(raw)
        return result

    @classmethod
    def from_headers[T: _DataclassCodec](cls: type[T], headers: Mapping[str, object]) -> T:
        """Parse from HTTP response headers."""
        header_to_field = {v: k for k, v in cls.FIELD_TO_HEADER.items()}
        kwargs: dict[str, int | float | str] = {}
        for header_name, field_name in header_to_field.items():
            raw = headers.get(header_name)
            if raw is None:
                continue
            if field_name in cls.INT_FIELDS:
                with contextlib.suppress(ValueError, TypeError):
                    kwargs[field_name] = int(str(raw))
            elif field_name in cls.FLOAT_FIELDS:
                with contextlib.suppress(ValueError, TypeError):
                    kwargs[field_name] = float(str(raw))
            else:
                kwargs[field_name] = str(raw)
        return cast(T, dataclasses.replace(cls(), **kwargs))


class RoutingInfo(BaseModel):
    """Routing metadata carried on domain models and produced by extractors.

    Extractors populate only what they read from the response (``routed_model``,
    ``classifier_cost_usd``). ``routed_model_label`` is filled by the label layer
    after composition.

    ``meta`` is an opaque, mechanism-specific passthrough bag (header-name -> already
    -formatted string value). It exists purely for forwarding: the router that produces it
    is the only code allowed to populate or read specific keys out of it (e.g.
    SwitchyardRouter/apply_router_routing stash the full x-codemie-routing-* header set
    there so the proxy can re-emit it). Generic consumers (callbacks, RouterChatModel) must
    never branch on its contents — that would just move the RoutingDecision-leak problem
    from typed attributes into stringly-typed dict keys.
    """

    routed_model: str | None = None
    routed_model_label: str | None = None
    classifier_cost_usd: float | None = None
    meta: dict[str, str] = Field(default_factory=dict)

    def is_empty(self) -> bool:
        return self.routed_model is None and self.routed_model_label is None and self.classifier_cost_usd is None

    def merged_over(self, base: "RoutingInfo") -> "RoutingInfo":
        """Field-wise overlay: this instance's non-None fields win over ``base``.

        ``meta`` merges as a dict union with this instance's keys winning on collision,
        consistent with how every other field resolves.
        """
        return RoutingInfo(
            routed_model=self.routed_model if self.routed_model is not None else base.routed_model,
            routed_model_label=(
                self.routed_model_label if self.routed_model_label is not None else base.routed_model_label
            ),
            classifier_cost_usd=(
                self.classifier_cost_usd if self.classifier_cost_usd is not None else base.classifier_cost_usd
            ),
            meta={**base.meta, **self.meta},
        )

    @classmethod
    def from_response(cls, response: object) -> "RoutingInfo":
        """Read the canonical RoutingInfo that RouterChatModel._agenerate stamps onto a
        response's response_metadata under _ROUTING_INFO_KEY.

        Duck-typed, not isinstance-gated: accepts a bare AIMessage-like object (the shape the
        agent path passes) or an LLMResult-like wrapper (the shape LangChain's own callback
        hooks, e.g. on_llm_end, always pass instead) — including test doubles that only set
        the attributes they need. The single canonical implementation for what used to be
        three independent copies (SwitchyardRouter.extract, AgentInvokeCallback,
        AgentStreamingCallback).
        """

        def _from_metadata(metadata: object) -> "RoutingInfo":
            if isinstance(metadata, dict) and _ROUTING_INFO_KEY in metadata:
                return cls(**metadata[_ROUTING_INFO_KEY])
            return cls()

        info = _from_metadata(getattr(response, "response_metadata", None))
        if not info.is_empty():
            return info
        for gen_list in getattr(response, "generations", None) or []:
            for gen in gen_list:
                info = _from_metadata(getattr(getattr(gen, "message", None), "response_metadata", None))
                if not info.is_empty():
                    return info
        return cls()


class LastRoutingTracker:
    """Accumulates the most recently observed RoutingInfo across a sequence of LLM calls
    within one agent run, merging each new observation over what came before via
    RoutingInfo.merged_over. Extracted out of AgentInvokeCallback/AgentStreamingCallback,
    which each maintained their own near-identical ``_last_routing: RoutingInfo | None``
    field plus merge logic — this is the shared state and merge behavior, not the
    per-callback thought/metadata formatting, which still differs between the two and stays
    where it is.

    Used to know which routing tier/classifier cost to stamp onto tool thoughts that follow
    a routed LLM call, even though that information was only observed on an earlier call
    (e.g. the LLM call itself, before any tool call thoughts exist to stamp)."""

    def __init__(self) -> None:
        self._current: RoutingInfo | None = None

    @property
    def current(self) -> RoutingInfo | None:
        """The merged routing state observed so far, or None if nothing has ever been
        observed."""
        return self._current

    def observe(self, response: object) -> RoutingInfo:
        """Extract RoutingInfo.from_response(response), merge it into the tracked state (only
        if non-empty), and return the extracted, UNMERGED RoutingInfo for this call alone.

        Callers that need "what did this specific call report" (e.g. to avoid stamping a
        stale routed-model name before the real one is known) use the return value; callers
        that need "what's the running merged state" (e.g. to stamp a later, unrelated tool
        thought) use .current instead — these are deliberately different values.
        """
        info = RoutingInfo.from_response(response)
        if not info.is_empty():
            self._current = info.merged_over(self._current) if self._current is not None else info
        return info


@dataclasses.dataclass(frozen=True)
class ClassifierUsage:
    """One router's classifier sub-call usage, attributed to that router by construction —
    replaces the untagged tuple-based classifier usage plumbing tokens_callback.py used to
    build separately per mechanism (LiteLLM headers vs Switchyard RunnableConfig metadata)."""

    provider: str  # the Router.name that reported this usage
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = None
    model: str | None = None
