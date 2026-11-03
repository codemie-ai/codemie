# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""What the LLM call in flight is *for*, so the outbound spend-logs header can say so.

**Why a context variable and not an argument.** The value is decided where a kind of work begins -
a conversation turn, a workflow run, an indexing job - and consumed several layers down, where the
model's headers are built. Threading it through every constructor between the two would touch the
same call chains `workflow_context` and `run_context` already refused to touch, for the same reason.

**Why it restores rather than overwrites.** `workflow_context.set_current_workflow_id` overwrites
and never restores, and the workflow executor clears it by hand afterwards. That is safe for one
value bound once per pooled thread; it is not safe for a value that nests. A tool-owned model built
inside a conversation, or a sub-workflow inside a workflow, publishes on the same thread as its
parent, and an overwrite there would stamp the rest of the parent's calls with the child's origin
once the child returned. `publish_origin` therefore returns a token and `spend_origin` restores it,
so the innermost value wins exactly while the innermost work runs.

**Why the reader tolerates an unset value.** Thread pools that do not copy the context - the
assistant executor, the workflow producer - construct models on threads where nothing was
published. The header builder then reads `None`, sends no origin key, and the row lands with a
NULL origin that the ledger classifies by its native columns instead. A missing origin is a
*known* hole, never a wrong value.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

from codemie.service.spend.traffic_class import TrafficOrigin

__all__ = ["current_origin", "publish_origin", "reset_origin", "spend_origin"]

_current_origin: ContextVar[TrafficOrigin | None] = ContextVar("codemie_spend_origin", default=None)


def current_origin() -> TrafficOrigin | None:
    """The origin published for the work in flight, or None where nothing was published."""
    return _current_origin.get()


def publish_origin(origin: TrafficOrigin) -> Token[TrafficOrigin | None]:
    """Bind an origin to the current context; the token restores what it replaced."""
    return _current_origin.set(origin)


def reset_origin(token: Token[TrafficOrigin | None]) -> None:
    """Restore the origin a `publish_origin` replaced."""
    _current_origin.reset(token)


@contextmanager
def spend_origin(origin: TrafficOrigin) -> Iterator[None]:
    """Publish an origin for the duration of a block, restoring the enclosing one on the way out.

    The form every entry point uses: a nested block - a sub-workflow, a tool that runs its own
    model - sees its own origin while it runs and hands the parent's back when it returns, and an
    exception on the way out restores it too, so a failed child never leaves its origin behind on a
    pooled thread.
    """
    token = publish_origin(origin)
    try:
        yield
    finally:
        reset_origin(token)
