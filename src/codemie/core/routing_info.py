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
models), ``ClassifierUsage``, and ``RoutingHeaderCodec`` (the generic to_headers/from_headers
mixin used by ``LiteLLMRouterHeaders`` to parse LiteLLM's own wire vocabulary — see
``enterprise/litellm/litellm_router_headers.py``). No import-time OR runtime
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
from pydantic import BaseModel

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

    Generic (de)serialization logic for a wire-format dataclass, kept independent of any one
    routing backend's field set. ``LiteLLMRouterHeaders`` (enterprise/litellm) is the sole
    production subclass today, using ``from_headers`` to parse LiteLLM's own external
    x-litellm-router-*/x-litellm-classifier-* response headers. Subclasses declare:
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


def _sum_or_none(a: float | int | None, b: float | int | None) -> float | int | None:
    """Return a + b, treating None as 0.  Returns None only when BOTH are None."""
    if a is None and b is None:
        return None
    return (a if a is not None else 0) + (b if b is not None else 0)


def normalize_decision_source(raw: str | None) -> str | None:
    """Normalize routing decision sources to lower-case kebab-case."""
    if raw is None:
        return None
    return str(raw).strip().lower().replace("_", "-")


class RoutingInfo(BaseModel):
    """Routing metadata carried on domain models and produced by extractors.

    Extractors populate only what they read from the response (``routed_model``,
    ``classifier_cost_usd``). ``routed_model_label`` is filled by the label layer
    after composition.
    """

    routed_model: str | None = None
    routed_model_label: str | None = None
    classifier_cost_usd: float | None = None
    requested_model: str | None = None
    tier: str | None = None
    routing_tier_raw: str | None = None
    decision_source: str | None = None
    routing_source: str | None = None
    confidence: float | None = None
    # Plain str, not a closed Literal: inversion of control — each concrete Router
    # self-declares its own identity (see Router.routing_family in core/router.py), so this
    # leaf module never needs to know the full set of mechanisms that will ever exist.
    routing_family: str | None = None
    routing_cost_known: bool | None = None
    classifier_model: str | None = None
    router_type: str | None = None
    router_score: float | None = None
    classifier_input_tokens: int | None = None
    classifier_output_tokens: int | None = None
    classifier_cached_tokens: int | None = None
    classifier_cache_creation_tokens: int | None = None
    classifier_total_tokens: int | None = None
    routed_input_tokens: int | None = None
    routed_output_tokens: int | None = None
    routed_cached_tokens: int | None = None
    routed_cache_creation_tokens: int | None = None
    routed_total_tokens: int | None = None
    routed_cache_hit: bool | None = None
    counterfactual_model: str | None = None
    original_cost_usd: float | None = None
    estimated_max_cost_usd: float | None = None
    potential_savings_usd: float | None = None

    # Fields that accumulate across multiple LLM runs in one request (None treated as 0;
    # result is None only when both sides are None) instead of using last-non-None semantics.
    # Everything else declared above is a last-non-None scalar (a name/id/label/flag, not a
    # quantity to sum) and is handled generically in merged_over() without needing to be listed
    # here. Kept as the single explicit list so adding a field to the model above never
    # silently changes is_empty()/merged_over() behaviour for it — is_empty() derives from
    # model_fields directly, and merged_over() only needs this one set to know which of those
    # fields to sum rather than overlay.
    _ADDITIVE_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "classifier_cost_usd",
            "classifier_input_tokens",
            "classifier_output_tokens",
            "classifier_cached_tokens",
            "classifier_cache_creation_tokens",
            "classifier_total_tokens",
            "routed_input_tokens",
            "routed_output_tokens",
            "routed_cached_tokens",
            "routed_cache_creation_tokens",
            "routed_total_tokens",
            "original_cost_usd",
            "estimated_max_cost_usd",
            "potential_savings_usd",
        }
    )

    def is_empty(self) -> bool:
        return all(value is None for value in self.__dict__.values())

    def merged_over(self, base: "RoutingInfo") -> "RoutingInfo":
        """Field-wise overlay: non-None scalar fields from ``self`` win over ``base``.

        Additive fields (see ``_ADDITIVE_FIELDS`` — classifier_cost_usd, classifier_*_tokens,
        routed_*_tokens, and the three USD savings fields) are summed: None is treated as 0;
        result is None only when both sides are None. This ensures that multi-LLM-run requests
        correctly accumulate total costs and savings across all calls. Every other field
        (including ``counterfactual_model``, a name rather than a quantity) uses last-non-None
        semantics.
        """
        merged: dict[str, object] = {}
        for field_name in type(self).model_fields:
            self_value = getattr(self, field_name)
            base_value = getattr(base, field_name)
            if field_name in self._ADDITIVE_FIELDS:
                merged[field_name] = _sum_or_none(self_value, base_value)
            else:
                merged[field_name] = self_value if self_value is not None else base_value
        return RoutingInfo(**merged)

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
