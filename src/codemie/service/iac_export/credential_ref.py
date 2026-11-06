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

"""Derivation of environment-variable references for exported credentials."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from codemie.service.iac_export.document import ExportError

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")
_ALNUM = re.compile(r"[A-Z0-9]")

# Active per-document registry: reference -> the raw (identifier, key) pair that produced it.
_ACTIVE: ContextVar[dict[str, tuple[str | None, str]] | None] = ContextVar("env_ref_registry", default=None)


def _normalise(text: str | None) -> str:
    return _NON_ALNUM.sub("_", text or "").upper()


@contextmanager
def env_ref_scope() -> Iterator[None]:
    """Track references built inside the block; distinct credentials mapping to one name raise ExportError."""
    token = _ACTIVE.set({})
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def _track(ref: str, raw: tuple[str | None, str]) -> None:
    registry = _ACTIVE.get()
    if registry is None:
        return
    if registry.setdefault(ref, raw) != raw:
        a, b = registry[ref], raw
        raise ExportError(f"Credential references collide: {a} and {b} both map to '{ref}'")


def env_ref(identifier: str | None, key: str) -> str:
    """Return ``env:{IDENTIFIER}_{KEY}`` for a credential reference.

    Same derivation as the envelope's ``CredentialRef.from_entity``: identifier and key are normalised separately
    (ASCII-upper-cased, every run of characters outside ASCII [A-Za-z0-9] becomes a single ``_``; leading and
    trailing ``_`` are kept). Either part without an ASCII alphanumeric raises ``ExportError``. Inside
    ``env_ref_scope`` two different (identifier, key) pairs yielding one reference also raise. Only names are
    accepted, never secret values.
    """
    norm_id, norm_key = _normalise(identifier), _normalise(key)
    if not (_ALNUM.search(norm_id) and _ALNUM.search(norm_key)):
        raise ExportError(
            "Credential references need an identifier and a key that each contain an ASCII alphanumeric "
            f"character, got identifier='{identifier}', key='{key}'"
        )
    ref = f"env:{norm_id}_{norm_key}"
    _track(ref, (identifier, key))
    return ref
