---
schema: sdlc-factory/code-review@1
decision: request-changes
reviewed_head: e9df651cc42811abed9da7687091376efd135ce9
---

# Code review — 2026-10-30-epmcdme-16477

**Decision:** request-changes (final round, confidence low)  
**Reviewed head:** `e9df651` · **Rounds:** final

Balanced profile, no spec (acceptance n/a): only edge-case lens plus standards audit ran; 3 blocking findings incl. a standards security partial (internal renderer URL leaked in API error details). CR-001 reachability uncertain (standalone env may override the default URL). 0 deferred; 5 dismissed (whitespace URL, non-standalone flag is always-on by spec, background diagram save swallow, two exception-hierarchy deletion rows).

## Open — 3

### CR-001 · code · major — Default server URL defeats standalone availability check

`src/codemie/configs/config.py:83` · not re-checked

**Problem:** MERMAID_SERVER_URL defaults to http://localhost:8082, so MermaidService.is_configured() is always true unless the URL is explicitly blanked; with MERMAID_ENABLED defaulting True, standalone publishes features:mermaid enabled and the startup warning (main.py:524) never fires when no renderer exists.

**Impact:** Standalone deployments without a renderer show Mermaid as available and get no startup warning, defeating the ticket's core goal; renders then fail as 'unavailable'. Reachability unconfirmed: standalone env/compose may override the default.

**Recommendation:** In standalone, do not treat the built-in default http://localhost:8082 as a configured renderer (default MERMAID_SERVER_URL to empty, or distinguish explicitly-set from default) so is_configured() is false, the startup warning fires and features:mermaid is published disabled. Confirm intended behavior with the ticket owner if the default is meant to be the bundled renderer.

### CR-002 · code · major — Renderer error details leak internal URL

`src/codemie/service/file_service/mermaid_service.py:90` · not re-checked

**Problem:** MermaidServiceUnavailableError interpolates the raw requests exception (full internal MERMAID_SERVER_URL request: host, port, path) and the workflow and files routers pass str(e) as ExtendedHTTPException details to the caller.

**Impact:** Discloses internal infrastructure addresses and connection internals to any authenticated API user, violating security-patterns.md (expose only sanitized context).

**Recommendation:** Log the raw requests exception server-side only and raise MermaidServiceUnavailableError with a fixed message (no URL/exception text); routers in workflow.py (~814-820) and files.py (~434-448) should not forward str(e) for the unavailable case.

### CR-003 · code · major — Renderer faults misclassified as invalid diagram

`src/codemie/service/file_service/mermaid_service.py:96` · not re-checked

**Problem:** _process_response maps every non-200 status other than 5xx/429 (e.g. 401/403/404/408/413 from auth, misrouting, timeout or size limits) and any 200 with a non-image Content-Type (proxy HTML error page) to MermaidDiagramGenerationError, which routers return as 400 'invalid diagram, check syntax'.

**Impact:** Renderer-side faults tell users to fix their syntax, breaking the ticket's required distinction between 'renderer unavailable (no syntax hint)' and 'invalid diagram'.

**Recommendation:** Raise MermaidServiceUnavailableError for renderer-side 4xx (401, 403, 404, 408, 413) and for 200 responses with a non-image Content-Type; reserve MermaidDiagramGenerationError for genuine syntax rejections (400/422 from the renderer).

## Coverage

Standards: commit-format pass · code-quality pass · security partial
