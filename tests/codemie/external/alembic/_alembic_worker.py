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

"""A long-lived alembic subprocess, driven one command at a time.

Why this exists (EXECUTION-PLAN 13.2 item 3)
--------------------------------------------
`test_marketplace_migrations.py` asserts real DDL, so every test has to move a
real database between real revisions -- 46 alembic invocations across the module.
Each one used to be its own `python -m alembic ...` process, and each of those
paid the same fixed cost before doing any migration work at all: `env.py` imports
`codemie.configs`, the whole SQLModel model layer and `alembic_postgresql_enum`,
measured at **8.5 s** on this machine (`python -m alembic heads`, which touches no
database, takes 8.52 s wall). 46 x 8.5 s is roughly six of the module's seven
minutes spent importing the same application forty-six times.

So the import is paid once. This module is that one process: it reads a JSON
request per line on stdin, runs it through `alembic.command`, and answers with a
JSON line. The migrations, the DDL, the revision graph and the assertions are
all completely unchanged -- only the number of interpreter start-ups is.

Why not simply run alembic in the pytest process
------------------------------------------------
The reason the original code gave for a subprocess is still true and still
binding: `env.py` starts `unittest.mock` patches against
`alembic_postgresql_enum` at import time and never stops them, reloads two of
that package's submodules, and builds its engine from the process-wide
`codemie.configs.config`. Importing it into the pytest process would leak all of
that into every other test in the session. A separate process is the isolation;
whether that process serves one command or forty-six is not what the isolation
depends on.

The protocol, and why fd 1 is not the protocol channel
------------------------------------------------------
`env.py` configures logging, and alembic itself prints to stdout. If the
responses shared a stream with that output, one stray log line would desynchronise
the protocol -- and the failure would look like a migration bug rather than a
plumbing bug. So the first thing this module does is take a private duplicate of
the real stdout for responses and point fd 1 at stderr, which the parent captures
to a file and quotes back when a command fails. Anything that writes to fd 1
after that -- Python, a C extension, a subprocess -- lands in the log, never in
the protocol.

Commands that *report* rather than migrate (`heads`, `history`) are read back by
the tests, so their output is captured through `Config.stdout` -- alembic's own
reporting channel -- and returned in the response rather than being left in the
log with everything else.

Request:  {"url": "<sqlalchemy url>", "args": ["upgrade", "head"]}
Response: {"ok": true,  "output": "<what alembic reported>"}
          {"ok": false, "error": "<traceback>", "output": "<partial report>"}
"""

from __future__ import annotations

import io
import json
import os
import sys
import traceback
from pathlib import Path

_ALEMBIC_DIR = Path(__file__).resolve().parents[4] / "src" / "external" / "alembic"


def _take_protocol_channel():
    """Private stdout for responses; everything else goes to the log."""
    protocol = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return protocol


def _point_at(url: str) -> None:
    """Aim the application's engine at `url`, exactly as `PG_URL=... alembic` did.

    `env.py` builds its connection from `PostgresClient.get_engine()`, which
    reads `config.PG_URL` and then **caches the engine per process id**. A
    process that served one command never had to care; this one does, so the
    cached engine is dropped whenever the target changes. Without this the
    second database of a session would silently be migrated into the first.
    """
    os.environ["PG_URL"] = url

    from codemie.clients.postgres import PostgresClient
    from codemie.configs import config

    config.PG_URL = url
    engine = PostgresClient._engines.pop(os.getpid(), None)
    if engine is not None:
        engine.dispose()


def _dispatch(url: str, args: list[str], report: io.StringIO) -> None:
    from alembic import command
    from alembic.config import Config

    _point_at(url)
    cfg = Config(str(_ALEMBIC_DIR / "alembic.ini"), stdout=report)
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))

    name, rest = args[0], list(args[1:])
    if name == "upgrade":
        command.upgrade(cfg, rest[0])
    elif name == "downgrade":
        command.downgrade(cfg, rest[0])
    elif name == "heads":
        command.heads(cfg)
    elif name == "history":
        # `history -r base:<rev>`, the one shape the module uses.
        rev_range = rest[rest.index("-r") + 1] if "-r" in rest else None
        command.history(cfg, rev_range=rev_range, verbose=False)
    else:  # pragma: no cover - a typo in a test, surfaced rather than guessed
        raise ValueError(f"unsupported alembic command for this worker: {args!r}")


def main() -> int:
    protocol = _take_protocol_channel()
    os.chdir(_ALEMBIC_DIR)
    sys.path.insert(0, str(_ALEMBIC_DIR))

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:  # pragma: no cover - parent always sends JSON
            protocol.write(json.dumps({"ok": False, "error": f"bad request: {line!r}"}) + "\n")
            continue

        report = io.StringIO()
        try:
            _dispatch(request["url"], request["args"], report)
            response = {"ok": True, "output": report.getvalue()}
        except BaseException:  # noqa: BLE001 - reported verbatim to the parent
            response = {
                "ok": False,
                "error": traceback.format_exc(),
                "output": report.getvalue(),
            }

        protocol.write(json.dumps(response) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
