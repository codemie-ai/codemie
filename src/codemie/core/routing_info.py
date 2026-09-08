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

"""Canonical routing metadata value-object and provider-neutral composer.

This module depends on neither the switchyard nor the litellm package, so both can
import RoutingInfo / RoutingMetadataExtractor from here without an import cycle.
Concrete extractors live in their owning packages; the composer is assembled by the
caller (callbacks) from an explicit provider list.
"""

from __future__ import annotations

import contextlib
import dataclasses
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, ClassVar, Final, Protocol, cast, runtime_checkable
from urllib.parse import quote

from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult
from pydantic import BaseModel

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
    """

    routed_model: str | None = None
    routed_model_label: str | None = None
    classifier_cost_usd: float | None = None

    def is_empty(self) -> bool:
        return self.routed_model is None and self.routed_model_label is None and self.classifier_cost_usd is None

    def merged_over(self, base: "RoutingInfo") -> "RoutingInfo":
        """Field-wise overlay: this instance's non-None fields win over ``base``."""
        return RoutingInfo(
            routed_model=self.routed_model if self.routed_model is not None else base.routed_model,
            routed_model_label=(
                self.routed_model_label if self.routed_model_label is not None else base.routed_model_label
            ),
            classifier_cost_usd=(
                self.classifier_cost_usd if self.classifier_cost_usd is not None else base.classifier_cost_usd
            ),
        )


@runtime_checkable
class RoutingMetadataExtractor(Protocol):
    """Reads routing metadata from an LLM response into a (partial) RoutingInfo."""

    def extract(self, response: LLMResult | AIMessage) -> RoutingInfo: ...


def compose_routing_info(
    response: LLMResult | AIMessage,
    extractors: Sequence[RoutingMetadataExtractor],
) -> RoutingInfo:
    """Fold extractors in list order; the earlier extractor wins per field.

    Precedence == list order at the call site. To change precedence, reorder the list.
    Called with a real LLM response; extractors return an empty RoutingInfo when they
    find no routing metadata.
    """
    result = RoutingInfo()
    for extractor in extractors:
        # result accumulated so far wins over later extractors:
        result = result.merged_over(extractor.extract(response))
    return result


def default_routing_extractors() -> list[RoutingMetadataExtractor]:
    """Canonical extractor list: Switchyard (x-codemie-*) wins over LiteLLM-router (x-litellm-*).

    Single source for every call site that needs to compose RoutingInfo from an LLM
    response, so precedence can never drift between callers. Imported lazily so this
    module keeps its documented zero import-time dependency on switchyard/litellm (see
    module docstring) — both packages are fully initialized by the time any caller
    actually invokes this function.
    """
    from codemie.enterprise.litellm.routing_headers import LiteLLMRouterExtractor
    from codemie.enterprise.switchyard.extractor import SwitchyardRoutingExtractor

    return [SwitchyardRoutingExtractor(), LiteLLMRouterExtractor()]
