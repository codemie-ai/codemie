---
schema: sdlc-factory/code-review@1
decision: approve
reviewed_head: 57f710c94ff72bee4ce68712b0422b56c2278557
---

# Code review — 2026-10-23-epmcdme-16319-cimd-client-id-root-path

**Decision:** approve (check round, confidence final medium · check high)  
**Reviewed head:** `57f710c` · **Rounds:** final → check

**Closed:** 1 fixed · 0 moot · 0 decided

Targeted check of the one prior blocking finding: CR-001 is resolved. At HEAD, build_client_metadata_document_url rstrips the root path, so API_ROOT_PATH='/' gives no '//', and a (base, '/') test case was added. business_review and standards_review are carried forward, the prior defer was not re-checked, and the brief was written.

## Open — 0

## Pre-existing (not blocking) — 1

- **redirect_uri keeps '//' for slash-terminated base** — `src/codemie/enterprise/mcp_auth/_uri.py:58` — `_build_callback_uri` concatenates `CALLBACK_API_BASE_URL` without `rstrip('/')`, so a '/'-terminated base yields `https://host//code-assistant-api/v1/mcp-auth/oauth2/callback` while the new `client_id` is `https://host/code-assistant-api/oauth/client-metadata.json`; the two stop sharing a prefix in that case. Pre-existing: the missing strip predates this change, and the ticket itself excludes it (Implementation step 2: "Do not build the URL through _build_callback_uri()"; the Tests invariant holds only "for a base URL without a trailing slash").

## Coverage

Graded at the final round; the check round re-verified the findings, not these rows — Open and Closed above are current.

| Acceptance criterion | Status | Notes |
|---|---|---|
| Goal: advertised and served client_id both equal <base w/o trailing slash><root path>/oauth/client-metadata.json | pass | both callsites use build_client_metadata_document_url (_uri.py:121-123; _discovery.py:135) |
| Goal: one builder makes the URL and one constant defines its path | pass | only f-string at _uri.py:123; only assignment at _constants.py:40; router.py duplicate deleted |
| Non-goal: route path /oauth/client-metadata.json unchanged; OpenAPI baseline unchanged | pass | router imports same-valued constant from _constants |
| Non-goal: no change to _build_callback_uri / redirect_uri incl. missing trailing-slash strip | pass | _uri.py:58 untouched |
| Non-goal: no change to codemie-enterprise, deploy-templates or ingress | pass | all changes under src/codemie/enterprise/mcp_auth and tests |
| Non-goal: no setting to turn CIMD off or force DCR | pass | no config additions |
| Non-goal: no migration/invalidation of Redis discovered-flow snapshots | pass | no Redis/snapshot code touched |
| Non-goal: no guide or documentation updates | pass | no guide/docs files in diff |
| Approach: do not reuse _build_callback_uri; keep rstrip('/') in a new _uri.py helper | pass | new helper applies rstrip('/') |
| Approach: do not compute the URL at import time in _constants.py | pass | builder reads config at call time |
| Design: no-arg build_client_metadata_document_url() -> str in _uri.py reading config at call time | pass | _uri.py:121-123 |
| Design: build_client_metadata_document_response calls the builder directly | pass | inline f-string replaced |
| Design: re-export builder in alphabetised from ._uri import block of dependencies.py | pass | dependencies.py:132 |
| Design: discovery calls deps.build_client_metadata_document_url(); unused import removed; config stays | pass | _discovery.py:135; config still used at :83, :92 |
| Design: delete router.py:113; router imports _CLIENT_METADATA_DOCUMENT_PATH from _constants | pass | router.py:44 import |
| AC1: with API_ROOT_PATH=/code-assistant-api, discovery advertises https://<host>/code-assistant-api/oauth/client-metadata.json | pass | _discovery.py:135; parametrized seam test case 1 |
| AC2: served document client_id byte-equal to the AC1 URL | pass | same builder; served-document test asserts EXPECTED_ROOTED_CLIENT_ID |
| AC3: empty root path leaves URL unchanged, incl. '/'-terminated base | pass | rstrip + get_api_root_path() '' for empty; trailing-slash test and param case 4 |
| AC4: URL built in exactly one place; path constant defined in exactly one place | pass | f-string only _uri.py:123; assignment only _constants.py:40 |
| AC5: tests cover AC1-AC3, AC6, AC7; mcp_auth suites, resolver test and ruff pass | pending-stage-7 | coverage present; suite and ruff results are Stage 7 evidence |
| AC6: non-HTTPS, non-localhost base still returns 400 with config error | pass | router mapping and existing test unchanged |
| AC7: query string still returns 400 'Query strings are not allowed for this endpoint.' | pass | router check and existing test unchanged |
| Invariant: base without trailing slash -> client_id and redirect_uri share <base><root path> prefix | pass | served-document test asserts shared prefix |
| Tests: served-document cases with root path and with empty root path + trailing-slash base | pass | two new TestClient tests |
| Tests: discovery seam asserts client_metadata_document_url | pass | parametrized seam test |
| Tests: API_ROOT_PATH set explicitly via monkeypatch incl. ''; existing root-path-free expectations unchanged | pass | config default API_ROOT_PATH='' (configs/config.py:56) |

Standards: commit-format pass · code-quality pass · security pass
