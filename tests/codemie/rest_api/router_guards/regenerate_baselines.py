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

"""Manual regeneration of the router guard baselines (EPMCDME-15651 step 0).

Run from the repository root, only for an intended and reviewed contract change (for example the
``PaginationInfo`` renames of delivery step 1), never to make a failing guard pass::

    poetry run python -m tests.codemie.rest_api.router_guards.regenerate_baselines

Writes ``baselines/openapi.json`` and ``baselines/route_precedence.json`` (the overlapping route pairs, in
registration order, earlier route first) and prints the pair total. The baselines are written from the
guarded app of the current tree, so run it on the tree whose contract you mean to record and review the
resulting diff. Pytest never runs this module: it has no ``test_`` name and nothing imports it.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tests.codemie.rest_api.router_guards.baseline import OPENAPI_BASELINE, ROUTE_PRECEDENCE_BASELINE, canonical_json
from tests.codemie.rest_api.router_guards.guarded_app import load_guarded_app_dump
from tests.codemie.rest_api.router_guards.route_overlap import overlapping_pairs


def _write_baseline(path: Path, content: object) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(canonical_json(content).encode("utf-8"))


def main() -> None:
    dump = load_guarded_app_dump()
    pairs = [pair._asdict() for pair in overlapping_pairs(dump["routes"])]
    _write_baseline(OPENAPI_BASELINE, dump["openapi"])
    _write_baseline(ROUTE_PRECEDENCE_BASELINE, pairs)
    sys.stdout.write(f"wrote {OPENAPI_BASELINE} ({len(dump['openapi']['paths'])} paths)\n")
    sys.stdout.write(f"wrote {ROUTE_PRECEDENCE_BASELINE} ({len(pairs)} overlapping route pairs)\n")


if __name__ == "__main__":
    main()
