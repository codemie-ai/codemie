# CLI Analytics: remove heavy application-side processing from the top dashboard requests — Story

**Date**: 2026-11-09
**Status**: Approved
**Architecture review**: intent, 2026-11-09, d4923f9810e6
**Ticket**: EPMCDME-17050, EPMCDME-17051, EPMCDME-17052 (sub-tasks of EPMCDME-16427)

---

## Context

- The Sessions list was made fast under EPMCDME-16427. The other CLI analytics dashboard requests were not part of that work.
- Five requests load every session in the selected period into the application, calculate there, and only then return a result: Overview, paged Repositories, Users, User Charts and Cost.
- The Users, User Charts and Cost requests repeat a scan of all priced rows per user. At about 8,000 users this is billions of row checks in one request (estimated from traffic figures, not measured), and it blocks every other request on that worker, including usage ingestion.
- The Overview and Users tabs each ask for 18 parallel database statements against 8 connections per pod. Usage ingestion gives up after waiting 1 second for a connection and returns 503.
- Cost pricing per session already exists in the database for the Sessions sort, and the protection that limits a dashboard request's connections exists for Sessions only. Grouped pricing by user, repository, model and funding route does not exist yet.
- A statement timeout on these requests is currently returned as 503, not 504. Only the Cost request has a stored baseline of expected results, and no timings exist at production volume.

---

## Complexity

**Size**: L (23/36) · **Recommended flow**: sdlc-standard

Wide change across the handler, readers and pricing with strict result parity and no precedent for grouped SQL pricing; as one story it is 4-5 days, so the three bugs in D6 carry the work in smaller pieces.

---

## Story

**As a** platform administrator viewing CLI analytics, **I want** the heaviest dashboard requests to do their aggregation in the database **so that** they complete without 503 or 504 errors and do not interrupt usage ingestion or other users.

---

## Background

CLI analytics now handles about 8,000 users and 200,000 sessions a month, or about 600,000 sessions in a 90-day window. The dashboard was designed for far smaller volumes. One dashboard request can occupy every database connection on a pod or stall its worker, so usage data can be rejected while someone is looking at a chart. The Repositories tab (every repository with branches) and Efficiency share the same handlers and pricing as these requests, so they are included. The Tools and Activity requests are lower priority and are handled separately.

---

## Acceptance Criteria

- [ ] Given a dataset of about 8,000 users and 600,000 sessions, when Overview, Repositories (paged or with branches), Users, User Charts, Cost or Efficiency is requested for the widest supported period, then it returns successfully with no 503 or 504.
- [ ] Given that dataset, when one of these requests runs while usage events are being ingested, then ingestion does not return 503 because of that request.
- [ ] Given a seeded dataset of the stated size (with unpriced models, personally paid sessions, unknown durations and near-equal costs), when the outputs of the five requests are recorded from the current code before any change, then those recordings are the baseline for every comparison below.
- [ ] Given that dataset, when the Overview and Users tabs are opened k times at once while ingestion runs (k to be set in the plan), then every request returns successfully, and none is slower than the current version on the same data.
- [ ] Given that dataset, when several of these requests run at once, as when the Overview or Users tab opens, then they do not take all database connections on the pod and the tab loads without 503 or 504.
- [ ] Given any of the five requests is made, when the response is built, then the application receives rows already aggregated to what the response returns, and does not load every session in the period, and other requests on the pod are not held up by its processing.
- [ ] Given the same data and filters, when a changed request is compared with the current version, then each displayed dollar figure matches within $0.01, all counts and row membership match exactly, every other field matches exactly (except fields derived from a dollar figure, which are computed from that figure), and row order matches exactly, except that rows whose costs differ by less than $0.000001 may change order.
- [ ] Given a session uses a model with no known price, when a changed request is returned, then the unpriced model list, each bucket's unpriced models and its incomplete flag are exactly as today.
- [ ] Given a filter that matches no sessions, when a changed request is made, then it returns the same empty result as today.
- [ ] Given a statement exceeds the database time limit, when the request fails, then it returns 504 with a message naming the time limit, as the Sessions list does, and no connection stays held after the response.
- [ ] Given a changed request, when its response is compared with the current API contract, then the fields and their meaning are unchanged and no dashboard change is needed; the timeout status above is the one allowed contract change.

---

## Out of Scope

- Tools and Activity requests.
- Changing the stored usage data itself, or pricing rules. A derived per-session value rebuilt from that data is allowed as a fallback (D12).
- Limiting facts to the selected days, which would change reported values.
- Dashboard changes.
- A fixed millisecond target.

---

## Decisions

- D1: May dollar totals differ slightly when calculated in the database? → Yes, within $0.01 per displayed figure; all counts must match exactly. — user
- D2: What counts as success? → No 503/504 and optimized requests, with no millisecond target. — user
- D3: How are the requests grouped into bugs? → Combine requests that share a cause and fix; split only where they cannot be combined. — user
- D4: Which requests are in the top group? → Overview, paged Repositories, Users, User Charts and Cost. — user
- D5: At what level is pricing calculated before totals are added up? → Per session and model, as the Sessions sort does, because coarser grouping could change totals by more than the tolerance. — proposed by PO: technical-analysis.md, Risk Indicators

- D6: How are the bugs split? → Three bugs, by cause: (1) per-user and per-model scans and per-session sums in the application, covering Users, User Charts and Cost; (2) per-session facts summed in the application, covering Overview and paged Repositories; (3) too many parallel statements for the connection pool, covering Overview and Users, with the fetch Users never uses. Each owns the criteria on its requests. — proposed by PO: architecture review
- D7: Which status does a database timeout return? → 504 with a message naming the time limit, as Sessions does. — proposed by PO: architecture review
- D8: How is "faster" checked, given no millisecond target? → Relative to the current version on the same seeded data, with outputs recorded before any change. — proposed by PO: architecture review
- D9: Must all five requests get already-aggregated rows from the database, with other requests on the pod not held up? → Yes. — user
- D11: May rows whose costs differ by less than $0.000001 change order, including across a page boundary? → Yes; counts that depend on cost being above zero must use the same rounded cost. — user
- D12: Does "stored data does not change" forbid a derived per-session value rebuilt from usage data? → No; it is allowed as a fallback if request-time pricing proves too slow. The usage data itself stays unchanged. — user
- D10: Are the Repositories tab (the `include_branches=true` request) and Efficiency in scope? → Yes, both, with the API contract unchanged. — user

---

## Open Questions

- None
