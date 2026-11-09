# ruff: noqa: T201
"""EPMCDME-16522 live repro: ingest two Claude-Code-shaped OTLP sessions into the local backend and read them back.

  A  <run>-unpriced : model mai-code-1.1-flash only (not in the catalog)
  B  <run>-mixed    : claude-sonnet-4-6 (priced) + mai-code-1.1-flash (unpriced)

Needs the backend on BASE with FEATURE_CLI_ANALYTICS=true and a project with cli_analytics_enabled (PROJECT).
Run from the repo root: poetry run python <this file> <project-name>
Writes api_session_<id>.json and api_sessions_list.json next to this file.
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from tests.codemie.repository.cli_analytics.support import otlp_builders as b
from tests.codemie.repository.cli_analytics.support.hook_event_builders import as_otlp_log, minimal

BASE = "http://127.0.0.1:8080"
OUT = Path(__file__).parent
PROJECT = sys.argv[1]
RUN = f"epmcdme-16522-{datetime.now(timezone.utc):%H%M%S}"
EMAIL = "admin@codemie.ai"

login = httpx.post(f"{BASE}/v1/local-auth/login", json={"email": EMAIL, "password": "password"}, timeout=30)
login.raise_for_status()
auth = {"Authorization": f"Bearer {login.json()['access_token']}"}


def api_request(session: str, model: str, seq: int, cache_read: int) -> dict:
    return {
        "event.name": "api_request",
        "session.id": session,
        "prompt.id": f"{session}-p1",
        "user.email": EMAIL,
        "model": model,
        "query_source": "repl_main_thread",
        "input_tokens": 1200,
        "output_tokens": 340,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": 0,
        "event.sequence": seq,
    }


sessions = {
    f"{RUN}-unpriced": [("mai-code-1.1-flash", 40110)],
    f"{RUN}-mixed": [("claude-sonnet-4-6", 20000), ("mai-code-1.1-flash", 40110)],
}
resource = {"service.name": "claude-code", "service.version": "2.0.1", "user.email": EMAIL}
now = datetime.now(timezone.utc)
for sid, models in sessions.items():
    ts = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    # A dimension hook event gives the session its started_at, without which the list leaves it out
    start = {**minimal("agent.session.start", sid, ts), "cwd": "/work/epmcdme-16522", "user_email": EMAIL}
    records = [as_otlp_log(start, now)]
    records += [b.log_record(now, api_request(sid, m, i + 1, cr)) for i, (m, cr) in enumerate(models)]
    body = b.logs_request(resource, records).SerializeToString()
    r = httpx.post(
        f"{BASE}/v1/analytics/cli-analytics/logs",
        content=body,
        headers={**auth, "Content-Type": "application/x-protobuf", "X-CodeMie-Project": PROJECT},
        timeout=30,
    )
    print(f"ingest {sid}: {r.status_code} {r.text[:300]}")

# The rollup refresher builds cost_daily in the background: poll the detail until it has a cost answer.
for sid in sessions:
    detail = None
    for _ in range(24):
        d = httpx.get(f"{BASE}/v1/analytics/cli-analytics/sessions/{sid}", headers=auth, timeout=30)
        if d.status_code == 200 and (d.json().get("metadata") or {}).get("unpriced_models") is not None:
            detail = d.json()
            if (detail.get("data") or {}).get("cost_available") is not None:
                break
        time.sleep(5)
    if detail is None:
        print(f"detail {sid}: {d.status_code} {d.text[:300]}")
        continue
    (OUT / f"api_session_{sid}.json").write_text(json.dumps(detail, indent=1))
    data, meta = detail.get("data") or {}, detail.get("metadata") or {}
    keys = ("cost_usd", "cache_read_cost_usd", "cache_read_tokens", "cost_available")
    print(f"detail {sid}: " + ", ".join(f"{k}={data.get(k)!r}" for k in keys))
    print(f"   metadata.unpriced_models={meta.get('unpriced_models')} catalog_source={meta.get('catalog_source')}")

lst = httpx.get(
    f"{BASE}/v1/analytics/cli-analytics/sessions",
    params={"per_page": 50, "time_period": "last_7_days"},
    headers=auth,
    timeout=30,
)
(OUT / "api_sessions_list.json").write_text(lst.text)
print(f"list: {lst.status_code}; metadata={json.dumps((lst.json() or {}).get('metadata'))[:400]}")
