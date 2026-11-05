# CLI Analytics Event Contract

The six events CodeMie CLI (formerly the `sdlc-analytics` plugin) sends on top of OTel telemetry and the 13 activity hook events (of these, the `lines_added` / `lines_removed` of `agent.tool.end`): their JSON, where each field is stored, and the rules a sender must keep. The storage, the recalculation and the operations are in [`cli-analytics-storage.md`](cli-analytics-storage.md).

The server accepts these events. A change to a field name, a type or a rule on this page is a change to the contract: agree it before a sender or the server implements it.

## Routes

| Route | Form |
|---|---|
| `POST /v1/analytics/cli-analytics/event-hooks` | NDJSON: one event per line. The sender's email comes from the token |
| `POST /v1/analytics/cli-analytics/logs` | An OTLP log record with an `event_type` attribute is a hook event: `event_type` holds the event's `type`, every other field is an attribute of the same name (`session.id` and `prompt.id` are read as well as `session_id` and `prompt_id`). The sender's email is the `user.email` attribute |
| `POST /v1/analytics/cli-analytics/metrics`, `…/traces` | OTLP metrics and spans, protobuf or JSON |

All four routes require CLI Analytics enablement for shared destinations. Evidence requests can use the owner's personal project without separate enablement (see Classification and routing).

- `event-hooks` and `logs` store an event the same way; an event sent through both is stored once when it has an `event_id` (see Identity and re-sends).
- A request body above 5,242,880 bytes (`ANALYTICS_INGEST_MAX_BODY_BYTES`) is refused with 413. A sender that posts a whole spool in one request must split it: about 7,000 unsent `agent.usage.request` events reach the limit, and the same body is refused on every retry.
- A request that carries more than 100 distinct sessions (`MAX_SESSIONS_PER_REQUEST`) is refused with 413 before the gate, any connection, advisory lock, ledger entry or write; duplicates of a session count once. Every distinct session takes one advisory lock inside the request's single transaction, and the whole-request validation, owner and mixed-session checks stay atomic, so the request is bounded, not split. The evidence CLI sends one session per request. The bound limits locks per request; it is not a database capacity claim (lock table size, concurrent requests and the connection pool are separate).
- **Trust boundary of the account evidence.** The organization, subscription-type, routing and repository headers are client reports: the backend authenticates the owner and enforces destination, access, enablement and privacy, but cannot observe the local payment credentials, and a further self-reported auth-type header would not be independent proof. The class is derived from CodeMie routing plus the organization (the client's `X-CodeMie-Subscription-Type` only detects a lost organization or a stale policy). The paired CLI withholds a stale saved OAuth organization for API keys, tokens, cloud flags, an `apiKeyHelper` and non-CodeMie gateways, so such a session arrives as Unknown. A modified client can misreport its payer; billing classification is not cryptographically verified. This trust limitation requires explicit human security review before merge.

## Project gate

Every route checks the project before it stores anything. The project is the one named in the `X-CodeMie-Project` header (case-insensitive); the sender must have access to it and the project must have `cli_analytics_enabled` (a global administrator or maintainer sets it; it is `false` for every project until then). A request accepted for a project is stored under that project's stored name, whatever case the header or the events used: the `codemie_project_name` column of the stored hook events (which feeds a session's project, the project filter and the project admin's access) is set to the stored name of the project the request was accepted for, also on hook events that name no project, while the rest of the payload and the identity of each event are stored as sent. Data stored before this behaviour is not rewritten. The instance flag `cliAnalytics`, authentication, the size limit and format validation are unchanged.

Order on every POST: instance flag (404), size (413), content type (415), decode (a malformed body is 400, never a discard), then the gate. A body that decodes is either stored or **discarded as a whole**: nothing is written to the raw tables, resources, the dedup ledger or the rollup queue. A mixed batch is never split.

A discard is HTTP 200, because the CLI treats any 2xx as done and deletes its spool (so a discard is final and is not replayed after the project is enabled), while 403 suspends its forwarding and other errors are retried for ever.

| Reason | When |
|---|---|
| `cli_analytics_disabled` | the project exists, the sender has access, and the setting is off |
| `project_context_required` | the header is missing or blank, or names an unknown or deleted project, or one the sender cannot access. For a **legacy request** (no evidence header, see Classification and routing) the user's default project is never inferred; an evidence request falls back to the default project by the routing order below |
| `project_context_conflict` | the batch carries an explicit project value that differs (case-insensitive, otherwise exact: no trimming, same rule as the project lookup) from the header, or two different values |

An explicit project value is `codemie_project_name` in: an NDJSON event (a hook or `agent.usage.request`), an OTLP log record, span or metric data-point attribute, and a resource attribute. Data points the server never stores (histogram, exponential histogram, summary, non-finite values) and hook events skipped as too deeply nested are checked as well. A project carried under another key is not detected.

Discard answers:

| Route | Body |
|---|---|
| `logs`, `metrics`, `traces` | the OTLP `Export*ServiceResponse` in the request's format (JSON or protobuf) with `partial_success`: `rejected_log_records`, `rejected_data_points` or `rejected_spans` = the records the client sent, and the reason in `error_message`. A request that carried no records gets the plain empty success response |
| `event-hooks` | `{"accepted": 0, "dropped": N, "reason": "<reason>"}` |

The count is what the client sent, not what would have been stored: every log record, span and metric data point of every kind (a metric without data points adds none), and every NDJSON line that parses as an object. Lines that do not parse are not counted and never were; a body with none that parse is still 400.

If the project policy cannot be read the answer is 503 and nothing is written; the CLI retries the whole spool. Disabling a project takes effect on the next request; stored history is untouched.

## Classification and routing

Optional request headers on all four routes: `X-CodeMie-Session-Id`, `X-CodeMie-Routed-Via-CodeMie`, `X-CodeMie-Organization` or `X-CodeMie-Organization-Encoded` (a name used to classify and never stored; see the organization envelope below), `X-CodeMie-Project-Source` (`linked_folder` or `profile`), `X-CodeMie-Subscription-Type`, `X-CodeMie-Policy-Version` (the client's advisory copy), `X-CodeMie-Repo-Remote` (when absent, the `repo_remote` of the earliest hook row that carries one is used for registration routing). Any of them present makes an **evidence request**; none makes a **legacy request**, whose gate, conflict check and dedup are unchanged.

**Classification** (server-derived, in this order): routed via CodeMie gives `codemie`; organization missing or blank, or an empty corporate list, gives `unknown`; organization in the list (trimmed, case-insensitive) gives `corporate`; anything else is `personal`.

**Policy.** `GET /v1/analytics/cli-analytics/classification-policy` (authenticated, behind the instance flag) returns `{"organizations": [...], "version": "<short SHA-256 of the sorted normalized list>"}`; an empty list is explicit. The list is the admin setting `cliAnalytics.corporateOrganizations` (one per line, no defaults).

**Order for an evidence request.** One session boundary, classification, stale check, session binding, destination, one storage transaction. The session boundary and the stale answer are decided before anything is looked up; the binding, the destination and the checks of the destination all run in the storage transaction, under the session lock, so a bound session is validated against its bound destination only.

- One request carries one session. Every decoded session id must equal `X-CodeMie-Session-Id`, else the whole request is discarded with `evidence_session_conflict`. An evidence request whose `X-CodeMie-Session-Id` is missing or blank (for example stripped by a proxy, or only `X-CodeMie-Repo-Remote` was sent) is malformed, not a mismatch: HTTP 400 `{"detail": "session_id_required"}` before anything is decoded, read or written. The CLI does not acknowledge a 400, so the range stays in its spool.
- **Organization envelope** (validated before classification, binding, provisioning or any write; all four routes; HTTP 400, which the CLI does not acknowledge, so the range stays in its spool). `X-CodeMie-Organization` is unchanged for values the header can carry (Latin-1; never percent-decoded, a literal `%` stays literal). A name it cannot carry travels in `X-CodeMie-Organization-Encoded: utf8-b64u.v1:<payload>`, the canonical unpadded base64url of the original UTF-8 text (no trim, casefold or NFC before encoding; the existing strip/casefold classifier runs after decoding). Limits: payload at most 1366 characters, decoded at most 1024 bytes and 256 code points, strict UTF-8, no C0/C1 control characters, not blank. Both headers present (even equal): `organization_conflict`. Malformed, blank, over-limit or unsupported-version encoded value: `organization_invalid`. A non-routed request whose client class (`X-CodeMie-Subscription-Type`) is `personal` or `corporate` but which carries no usable organization (stripped by a proxy, or sent by a client that could not represent the name): `organization_required`, never classified Unknown (the client class is used only to detect the incomplete envelope; the classification stays derived). CodeMie routing needs no organization. Support is advertised as `"organization_encodings": ["utf8-b64u.v1"]` on `GET classification-policy` (not part of `version`). **Deployment gate (open, not guaranteed):** one instance advertising support does not prove every serving instance is upgraded, and an older backend ignores the encoded header and stores `unknown`. Release this backend on every serving instance before an encoded-sending CLI is released or activated; rolling back the backend after a CLI cached the capability is unsafe until encoded sends stop or the cache is cleared. This is a deployment/security sign-off for the release owner, not something the protocol enforces.
- Stale policy: the client's policy version differs from the server's and its classification differs from the derived one gives HTTP 409 `{"reason": "stale_policy", "policy_version": "<current>"}`. Nothing is stored and no binding is created or changed.
- Personal destinations require ownership but no separate activation setting. First-upgrade corner case: while old and new backends serve the same installation during an update, an administrator using an old backend may read Personal sessions already saved by the new backend. This window ends when old backends stop serving requests; it does not recur on later upgrades between versions with owner-only reads, but returns on rollback to an unprotected version. This limitation requires explicit security sign-off.

**Destination order** (first hit wins): a personal-derived session goes only to the owner's personal project (origin `personal`; a missing project is provisioned; a failure is 503; never a shared project). A user type that cannot have a personal project (`external`, service accounts; EPMCDME-14413) is the exception: its Personal-paid session keeps `subscription_type=personal` and is routed as below to an authorized work project, and no personal project is created. Otherwise (a) the `X-CodeMie-Project` header, **only** with `X-CodeMie-Project-Source`, when the project exists, is not deleted and passes access and membership (origin `linked_folder` or `profile`); (b) the project registered for the normalized `repo_remote` (hook rows or `X-CodeMie-Repo-Remote`); (c) the effective default project (origin `default_project`; it may be the user's personal project, except for a user type without one). For that user type (`external`, service accounts) the old personal project is never a destination of a NEW session of any classification (Personal, Corporate, CodeMie, Unknown): a header, registration or default naming it is treated as unusable (the header and registration fall through with their route note; a default naming it is the retryable `default_project_unavailable`, no other project is substituted and none is created). A header without a source is ignored for routing. Shared destinations still need `cli_analytics_enabled`; an owner's personal destination does not.

**`route_note`** (empty for linked, registered and personal): `folder_not_linked`, `linked_project_unavailable`, `registered_project_inaccessible`, `registration_ambiguous`. Every stored route note, and `default_project_unavailable` (retry), is logged and counted by the server.

**Default failures.** The backend never discards a request for lacking a destination (approved AC9): it answers a retryable 503. No projects at all (any user type), or a default that is missing, deleted or not accessible: 503 `default_project_unavailable`, which the CLI does not acknowledge, so the data stays in its spool and is retried. Client retention (paired-client contract of the approved AC9; implemented and reviewed in the paired CLI change, not live end-to-end verified): the CLI retains unacknowledged data and retries with bounded backoff for a finite window, 24 hours by default (the existing held-data retention setting). The window is measured from the first failure, survives restarts and a change of error reason, and is not reset by another attempt. At expiry only the affected unacknowledged range is discarded, with a recorded reason, byte count and a warning; acknowledged, unrelated and newer data are untouched. Delivery after the window is not guaranteed. This is the client contract; the backend response is unchanged and does not depend on it. A failed default never reroutes elsewhere, and a project is never invented for the user. Final discards for a new session remain only for an explicit choice: a shared destination with `cli_analytics_enabled` off (`cli_analytics_disabled`).

**Personal-paid work-project sessions (AC3 with AC9).** A Personal-derived session of a user type without a personal project (`external`, service accounts) is stored under a work project with `subscription_type=personal` and a shared `project_origin`, never a personal project (EPMCDME-14413 forbids them: they bypassed project budgets and hid service-account spend). It follows the work project's read permissions and shows as project activity, but its cost is left out of every aggregate spend value (see the read rules in the analytics-read spec); an ordinary user's Personal session (origin `personal`) stays owner-only. Read side: a row is private only when `project_origin=personal`, or `subscription_type=personal` with a missing or unknown origin (fail closed). No usable work project is the retryable `default_project_unavailable`.

**A bound session whose project changes.** The binding stores the project **name**. Rename is refused by `ProjectService` while any user is assigned to the project, so it can only affect sessions of admins, maintainers and project admins (or a deployment without user management). When an ALREADY BOUND session's project is confirmed renamed, hard or soft deleted, or the sender lost access or membership, the request is discarded `{"reason": "session_project_unavailable"}` with HTTP 200 on all four routes: final, so the CLI acknowledges and drops that range (current CLI: any 2xx with a non-empty `reason` / OTLP `error_message` is acknowledged and the reason recorded). History stays, nothing is rerouted to the default or a renamed project, migrated or provisioned. Retryable (503) stay: a failed project lookup (database or network error), an unbound session whose legacy project is unavailable, and a new session with no destination. Another user's session is still `session_owner_conflict` first. Rename continuity and history migration are outside this story; nothing rewrites `session_dims` or hook rows across the analytics database. A legacy request (no evidence headers) is accepted for its header project only: a session already bound to another project is `session_project_conflict` on every signal (OTLP logs, metrics, traces and hooks, not only hook rows), and the bound project it does write is validated with the same checks (existence, access, live membership, enablement, personal ownership; excluded user types never write a personal project), so a revoked or disabled bound project is the final `session_project_unavailable` / `cli_analytics_disabled` discard and a failed lookup is retried. A legacy request never binds, rebinds or reroutes a session. For a legacy-only session (no binding) there is no additional bound-destination validation and no new binding; the request-project gate check of the header project still runs for every legacy request.

**Policy changes on an anchored session.** A session keeps its anchor (bound destination, or the project of its legacy rows); a later classification never reroutes or migrates it. Exactly one change ends collection: a request newly classified Personal for an ordinary user (one who has a personal project) on a session anchored anywhere but that user's own personal project is discarded `session_project_conflict` (final; history and binding kept; nothing provisioned). Operational effect: when an administrator first fills the corporate-organization list, or removes an organization, live or resumed sessions of ordinary users that now derive Personal while anchored in a shared project stop collecting; a new CLI session applies the current rules. Every other change (Personal to Corporate/CodeMie/Unknown on a personal anchor, any change for external or service-account users, any non-Personal request on a shared anchor) neither conflicts nor discards by itself; only the destination checks do (deleted/inaccessible project, `cli_analytics_disabled`).

**Unbound legacy session.** Any evidence request except an ordinary user's private Personal one (which keeps the conflict rules above) on a session that only has legacy rows joins the project of those rows, and only that project is validated (existence, access, live membership, enablement; a user type without a personal project never lands in a personal project, for any classification: `personal_destination_unavailable`); the fresh candidate (default, registration, header) is not resolved, so its failure never discards or delays the request, and its origin is never stamped on the legacy project. Its origin is `legacy_project` for every eligible (non-private) adoption, whatever the classification (Personal-paid of a user type without a personal project, Corporate, CodeMie, Unknown; approved AC8), whether or not the fresh candidate names that project; an ordinary user's private Personal request on their own personal legacy anchor keeps origin `personal`, and for Personal-paid it also keeps a missing origin from reading as private. An already bound session keeps its stored origin; existing rows are not backfilled.

**Stored columns.** `hook_events` gains the nullable text columns `subscription_type`, `project_origin`, `route_note`, `client_subscription_type`, `client_policy_version`; `codemie_project_name` of every row of an evidence request is replaced with the resolved destination. They are server-stamped, never read from the payload, and rows written before this change read as `unknown` / empty origin. The **session binding** lives in `session_dims`: new nullable `owner_email`, `subscription_type`, `project_origin`, `route_note`, plus `project_name` and `jwt_email` set to destination and owner. The first accepted evidence request of a session (any of the four routes, hook rows or not) creates it in the same transaction and under the same per-session advisory lock as the rows; a rejected request creates nothing; the recompute never sets or overrides it. `owner_email` set means bound.

**Guard on every write.** Every ingest, legacy or evidence, takes the per-session lock and checks the binding (cost: one sorted `pg_advisory_xact_lock` statement over the batch's distinct sessions, held to commit, plus one `session_dims` primary-key lookup per request; an evidence request adds the binding upsert). A different user, or an unknown sender (no email) on a bound session or an evidence request, is discarded `session_owner_conflict` (emails compare case-insensitively, as the user lookups do); an evidence request also cannot adopt an unbound legacy session whose retained `session_dims.jwt_email` or hook rows' `user_email` name another user (same reason, nothing written), while a legacy session with no known email is still adopted; a Personal-derived request on a session bound to a shared project, or a legacy hook of another project on a bound session, is discarded `session_project_conflict`; a bound session otherwise stores under the bound project and type. Discards are HTTP 200 with the reason (as above), final for the request, and a mixed request loses its other sessions: the server logs and counts `other_sessions_dropped`.

**Repository registration** (no UI): `GET`, `PUT` and `DELETE /v1/projects/{project_name}/repositories`, for global admins, maintainers and project admins; personal projects are refused (400), a repository registered to another project is 409, an unparseable or ambiguous body 400, a project that was deleted or renamed after the request was authorized 404 (nothing written). One normalized remote maps to one project. A body names the repository by exactly one of two fields: `{"remote": "<raw URL or scp spelling>"}`, which is normalized, or `{"key": "<identity>"}`, which is taken exactly as returned (never normalized again, never both, never guessed from which rows exist). `GET` and `PUT` return `{"key", "project_name"}`, and a returned item is a valid `PUT`/`DELETE` body. Normalization: lowercase the host only, keep the path case, strip credentials, drop scheme, trailing `/` and `.git`, and a default port (ssh 22, https 443, http 80); `git@host:org/repo`, `ssh://git@host/org/repo` and `https://host/org/repo` all become `host/org/repo`. The key is `host[:port]/path` (IPv6 as `[addr]`); `https://host:8443/a/b` is key `host:8443/a/b` and `git@host:8443/a/b` (path `8443/a/b`) is key `host/8443/a/b`, two repositories that can coexist in one project. Registration, project rename and project delete all lock the project row (`SELECT ... FOR UPDATE`) for their transaction, so a registration authorized before a rename or delete waits for it and then fails 404; a rename moves the registrations and a delete removes them in the same transaction. Table `repository_project_registrations(normalized_remote UNIQUE, project_name, created_by, created_at)`.

**CLI requirements (cross-child).** Send one request per session with `X-CodeMie-Session-Id`; handle a discard response per session and never clear the spool entries of the request's other sessions blindly (retry them grouped per session, clear only the offending session as dropped); keep the spool on 409 (refresh the policy and reclassify) and on 503, and retry within the finite retention window described under Default failures (bounded backoff, no tight loop, expiry discards only the affected range with a recorded reason and byte count); send `X-CodeMie-Repo-Remote` when hook rows carry no `repo_remote`.

Additional discard reasons: `evidence_session_conflict`, `session_owner_conflict`, `session_project_conflict`, `session_project_unavailable`, `personal_destination_unavailable` (only a destination that is a personal project of a user type without one, whatever the classification).

**Destination origins** (`project_origin`): `personal`, `linked_folder`, `profile`, `registered_repository`, `default_project`, and `legacy_project` (any classification adopting an existing legacy project, see above). **Stale check** applies only when the client sends both `X-CodeMie-Subscription-Type` and `X-CodeMie-Policy-Version`. **Sender**: the authenticated user is the session owner on all four routes, legacy requests included; Claude account data is never read. **Destination re-check**: the ingestor can replace the resolved destination under the session lock (an existing binding or a legacy project), so the service validates the destination actually written there: for a bound session a deleted, inaccessible or unowned project is the final discard `session_project_unavailable` (for an unbound legacy project, 503 with the same reason), a shared project without `cli_analytics_enabled` is the discard `cli_analytics_disabled`. Also discarded: `project_context_conflict` (a body project differs from the header).

## Conventions

| # | Rule |
|---|---|
| 1 | One flat JSON object per line, keys in `snake_case`. The envelope is `type`, `timestamp`, `session_id`, `prompt_id` |
| 2 | A field that fills a column carries the column's name |
| 3 | Numbers, booleans, arrays and objects are JSON values of that type, not strings |
| 4 | A missing string is `""`; a missing number, boolean, array or object is `null` or is left out. Never `0`, never a default. Any subset of the fields is accepted: an event holding only `type`, `timestamp` and `session_id` is stored |
| 5 | `timestamp` is ISO-8601 in UTC with `Z`; milliseconds are allowed. Its source is fixed per event (see each event) and never the time of sending. On the OTLP route the event carries the same value in a `timestamp` attribute; without it the record time is used and the event can be stored a second time. Timestamps inside the payload use the same format |
| 6 | `event_id` is an opaque string: the server hashes it and never parses it. The examples use `<kind>:<session_id>:<discriminator>` |
| 7 | Text taken from a prompt is cut by the sender to 200 characters, text taken from a tool or a description to 300 |
| 8 | No field reuses a typed hook key with another meaning. The typed keys are `developer_name`, `codemie_project_name`, `cwd`, `git_branch`, `repo_remote`, `permission_mode`, `source`, `effort`, `tool_name`, `tool_use_id`, `tool_input`, `tool_output`, `error_message`, `error_type`, `reason`, `agent_id`, `agent_type`, `trigger`, `denial_reason`, `notification_type`, `prompt_body`, `skill_name`. A value under one of them is stored as text, so a nested value is never sent there |
| 9 | Command names (`commands`, `primary_command`, `command_name`) carry no leading slash |
| 10 | A prompt (`prompt_body` of `agent.prompt.submit`) may carry `<pasted_content ...>` tags that the client wraps around pasted text. The session's first prompt is the earliest prompt that is not empty and not a sentinel (`/clear`, `/resume`, `/compact`, `/exit`, `/quit`, a `<command-name>/clear` or `<command-name>/resume` start, or a continuation or local-command caveat message), taken after cleaning: the open, close and truncated pasted-content tags are removed, the pasted text between them is kept, and spaces, tabs, carriage returns, line feeds and no-break spaces (U+00A0) are trimmed at both ends. The sentinel tests read the cleaned text (the two continuation and caveat prefixes also read the prompt as sent), so a sentinel inside a paste block is skipped, and a prompt that is empty once cleaned is skipped |
| 11 | `prompt_original` is not read: it is not stored as a column, not served and not searched, and the first prompt never comes from it |

## Where an event is stored

| Event | Stored in | Read by the recalculation into |
|---|---|---|
| `agent.usage.request` | `usage_requests` only | `session_usage`, `session_usage_hourly`, `cost_daily`, `subagent_invocations` |
| `agent.subagent.usage` | `hook_events` | `subagent_invocations`; `session_usage` when it is the only token source |
| `agent.session.summary` | `hook_events` | `session_dims` |
| `agent.session.env` | `hook_events` | `session_dims` |
| `agent.skill.dispatch` | `hook_events` | the skill counters and `session_dims.skills` |
| `agent.git.snapshot` | `hook_events` | `session_dims` |

- In `hook_events` a field named like a typed key (rule 8) goes to that column and is absent from `attrs`; every other field goes to `attrs` with its JSON type.
- None of the six moves a session's start or end: `session_dims.started_at` and `last_event_at` come from the 13 activity hook events only. A session that sent none of those is in no dashboard window.

## Common fields

Any event may carry them; all are optional.

| Field | Type | Goes to |
|---|---|---|
| `schema_version` | int, `2` | `session_dims.schema_version` |
| `event_id` | string | the identity of the event |
| `platform`, `entrypoint`, `client_version`, `codemie_cli_version` | string | `session_dims` columns of the same names |
| `developer_name`, `codemie_project_name`, `cwd` | string | typed columns of `hook_events` |
| `identity_source` | string | the rank of the sender's identity in `session_dims` |
| `story_id`, `story_source` | string | `session_dims.story_id`, `story_source` |
| `session_title` | string | `session_dims.session_title`: the latest non-empty value, trimmed of spaces, tabs, carriage returns, line feeds and no-break spaces (U+00A0) and cut to 200 characters. It is a column of `session_dims` and never goes to `session_dims.attrs`. A value of only blanks is no value and does not replace an earlier name |
| `agent_id`, `agent_type` | string, `""` outside a subagent | typed columns |

The examples show `schema_version` and `event_id` and leave the other common fields out.

## `agent.usage.request`

One event per request to the model: one group of transcript lines with the same request id (else message id) and model. Every number is the maximum over the lines of the group, never their sum. It is sent once the group is closed: the first version stored is the final one.

```json
{
  "type": "agent.usage.request",
  "timestamp": "2026-09-29T10:59:41.512Z",
  "session_id": "3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11",
  "schema_version": 2,
  "event_id": "usage:3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11:req_011CTx9aB:claude-opus-5-5-20260101",
  "request_id": "req_011CTx9aB",
  "message_id": "msg_01H7Yq",
  "agent_id": "",
  "agent_type": "",
  "scope_kind": "main",
  "scope_name": "",
  "model_raw": "claude-opus-5-5-20260101",
  "model": "claude-opus-5-5",
  "speed": "standard",
  "inference_geo": "not_available",
  "service_tier": "standard",
  "input_tokens": 12,
  "cache_creation_5m_tokens": 0,
  "cache_creation_1h_tokens": 2500,
  "cache_read_tokens": 48000,
  "output_tokens": 295,
  "thinking_tokens": 289,
  "web_search_requests": 0,
  "web_fetch_requests": 0,
  "stop_reason": "tool_use",
  "is_api_error": false,
  "git_branch": "feature/ABC-123"
}
```

| Field | Type | Meaning | Column of `usage_requests` |
|---|---|---|---|
| `timestamp` | string | the last transcript line of the group: the one closest to the OTel record of the request | `ts` |
| `request_id` | string | the request id of the API; `""` when the transcript has none (requests through a proxy) | `request_id` |
| `message_id` | string | the message id; the group key when there is no request id | `message_id` |
| `agent_id`, `agent_type` | string | the subagent that made the request; `""` in the main thread | same names |
| `scope_kind` | string: `main`, `skill`, `agent` | where the request was made | `scope_kind` |
| `scope_name` | string | the skill name or the agent type; `""` for `main`, never `main` | `scope_name` |
| `model_raw` | string | the model as the API names it | `model_raw` |
| `model` | string | normalised, without speed or region suffixes | `model` |
| `speed` | string: `standard`, `fast` | sent explicitly; OTel says `normal` for `standard` | `speed` |
| `inference_geo`, `service_tier` | string | as the API reports them | same names |
| `input_tokens`, `output_tokens`, `cache_read_tokens` | int | token counts of the request | same names |
| `cache_creation_5m_tokens`, `cache_creation_1h_tokens` | int | cache writes by lifetime | same names |
| `thinking_tokens` | int | a part of `output_tokens`, never added to it | same name |
| `web_search_requests`, `web_fetch_requests` | int | server tool use | same names |
| `stop_reason` | string | the stop reason of the message; empty or absent on a non-error request means a partial streaming snapshot; an `is_api_error` request is never partial | same name |
| `output_tokens_partial` | bool | sent by the CLI when `output_tokens` is a partial count; the backend does not store it, partial is derived from an empty `stop_reason` on a request that is not an `is_api_error` one; the backend ignores `output_tokens_partial`, `stop_reason` (on a non-`is_api_error` request) decides | not stored |
| `is_api_error` | bool | the line is an API error | same name |
| `git_branch` | string | the branch of the line | same name |
| any other key | | | `attrs` |

The discriminator of `event_id` is the request id, else the message id, with the raw model.

## `agent.subagent.usage`

One event at the end of a subagent.

```json
{
  "type": "agent.subagent.usage",
  "timestamp": "2026-09-29T11:04:12.000Z",
  "session_id": "3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11",
  "prompt_id": "p-7",
  "schema_version": 2,
  "event_id": "subagent:3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11:a41f09c2",
  "agent_id": "a41f09c2",
  "agent_type": "Explore",
  "description": "Trace the failing path",
  "tool_use_id": "toolu_abc123",
  "workflow_run": "",
  "spawn_depth": 1,
  "worktree": "",
  "started_at": "2026-09-29T11:01:02.250Z",
  "ended_at": "2026-09-29T11:04:12.000Z",
  "duration_ms": 189750,
  "model": "claude-haiku-4-5",
  "api_calls": 9,
  "tool_calls": 14,
  "tool_results": 14,
  "tool_errors": 1,
  "lines_added": 0,
  "lines_removed": 0,
  "files_changed": 0,
  "tools": {"Read": {"calls": 9, "errors": 0}, "Grep": {"calls": 5, "errors": 1}},
  "skills": {"sdlc-factory:tech-analysis-orchestrator": 1},
  "commands": [],
  "compactions": [],
  "usage": [
    {
      "model": "claude-haiku-4-5",
      "model_raw": "claude-haiku-4-5-20251001",
      "speed": "standard",
      "inference_geo": "not_available",
      "service_tier": "standard",
      "scope_kind": "agent",
      "scope_name": "Explore",
      "input_tokens": 900,
      "cache_creation_5m_tokens": 1200,
      "cache_creation_1h_tokens": 0,
      "cache_read_tokens": 31000,
      "output_tokens": 2100,
      "thinking_tokens": 0,
      "web_search_requests": 0,
      "web_fetch_requests": 0,
      "api_calls": 9
    }
  ]
}
```

| Field | Type | Meaning | Column of `subagent_invocations` |
|---|---|---|---|
| `timestamp` | string | the last line of the subagent transcript; equal to `ended_at` | — (`hook_events.ts`) |
| `agent_id` | string | the id of the subagent | `agent_id` |
| `agent_type` | string | its type | `agent_type` |
| `description` | string, at most 300 characters | the task it was given | `description` |
| `tool_use_id` | string | the tool call that started it | `tool_use_id` |
| `workflow_run` | string | the workflow run it belongs to | `workflow_run` |
| `spawn_depth` | int | how deep it was spawned | `spawn_depth` |
| `worktree` | string | its worktree path | `worktree` |
| `started_at`, `ended_at` | string | the first and the last line of its transcript | same names |
| `duration_ms` | int | `ended_at − started_at` | `duration_ms` |
| `model` | string | the model with the most requests | `model` |
| `api_calls`, `tool_calls`, `tool_results`, `tool_errors` | int | requests; tool calls; tool results; results that are errors | same names |
| `lines_added`, `lines_removed`, `files_changed` | int | edit diffs; distinct file paths | same names |
| `tools` | object `{tool: {"calls": int, "errors": int}}` | calls and errors per tool | `tools` |
| `skills` | object `{name: int}` | skill calls | `skills` |
| `commands` | array of strings | slash commands in order | `commands` |
| `compactions` | array, elements as in `agent.session.summary` | compactions | `compactions` |
| `usage` | array of objects | the requests summed by `(model, speed, inference_geo, service_tier, scope_kind, scope_name)` | `attrs` |

- A row of `usage` carries the field names of `agent.usage.request` plus `api_calls`, so one row maps onto one `session_usage` row.
- The totals in `usage` are never added to per-request rows. They become `session_usage` rows only when the session has no `agent.usage.request` of that agent and no OTel `api_request` at all; otherwise they stay in `subagent_invocations.attrs`.
- The totals are read from the agent's latest event whose `usage` is an array with at least one object. A later event without it replaces nothing.
- `attribution` is not sent; the recalculation sets it.

## `agent.session.summary`

Sent on every stop of the agent and at the end of the session. Each one is a separate event with its own `event_id`; the recalculation takes the latest by `timestamp`.

```json
{
  "type": "agent.session.summary",
  "timestamp": "2026-09-29T11:20:05.000Z",
  "session_id": "3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11",
  "schema_version": 2,
  "event_id": "summary:3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11:412",
  "is_final": false,
  "title": "Extend the analytics schema",
  "started_at": "2026-09-29T10:15:30.000Z",
  "ended_at": "2026-09-29T11:20:05.000Z",
  "duration_ms": 3875000,
  "turns": 7,
  "api_calls": 86,
  "tool_calls": 41,
  "tool_results": 41,
  "tool_errors": 2,
  "lines_added": 120,
  "lines_removed": 34,
  "files_changed": 5,
  "files_written": 1,
  "files_edited": 4,
  "primary_model": "claude-opus-5-5",
  "models": ["claude-opus-5-5", "claude-haiku-4-5"],
  "primary_command": "plan",
  "commands": ["plan", "commit"],
  "skills": {"sdlc-factory:sdlc-standard": 3},
  "agents": {"Explore": 2},
  "tools": {"Edit": {"calls": 12, "errors": 1}, "Bash": {"calls": 29, "errors": 1}},
  "compaction_count": 1,
  "compaction_pre_tokens": 182000,
  "compactions": [
    {"start": "2026-09-29T10:50:00.000Z", "end": "2026-09-29T10:50:04.200Z",
     "duration_ms": 4200, "trigger": "auto",
     "pre_tokens": 182000, "post_tokens": 41000, "dropped_tokens": 141000}
  ],
  "branch_dominant": "feature/ABC-123",
  "branch_counts": {"main": 1, "feature/ABC-123": 6},
  "git_branch": "feature/ABC-123",
  "client_version": "2.1.284",
  "client_versions": ["2.1.283", "2.1.284"]
}
```

| Field | Type | Meaning | Column of `session_dims` |
|---|---|---|---|
| `timestamp` | string | the last transcript line the summary covers | `summary_ts`; decides which summary is the latest |
| `is_final` | bool | `true` at the end of the session, `false` on a stop | `attrs` |
| `title` | string, at most 200 characters | the session's AI-generated title | `title`; stored, not served: the sessions list and the session detail never read it |
| `started_at` | string | the first transcript line | `activity_started_at`, not `started_at` |
| `ended_at` | string | the last transcript line | `ended_at` |
| `duration_ms` | int | `ended_at − started_at` | `duration_ms` |
| `turns` | int | user prompts | `turns` |
| `api_calls` | int | requests to the model | `api_calls` |
| `tool_calls`, `tool_results`, `tool_errors` | int | tool calls; tool results; results that are errors | same names |
| `lines_added`, `lines_removed` | int | edit diffs and the content lines of a new file | same names |
| `files_changed`, `files_written`, `files_edited` | int | distinct files touched; written; edited | same names |
| `primary_model` | string | the model with the most requests | `primary_model` |
| `models` | array of strings, primary first | distinct normalised models | `models` |
| `primary_command` | string, no slash | the first command | `primary_command` |
| `commands` | array of strings, call order, duplicates kept, no slash | slash commands | `commands` |
| `skills` | object `{name: int}` | skill calls | `skills` |
| `agents` | object `{agent_type: int}` | subagent calls | `agents` |
| `tools` | object `{tool: {"calls": int, "errors": int}}` | calls and errors per tool | `tools` |
| `compaction_count` | int | compactions | `compaction_count` |
| `compaction_pre_tokens` | int | the sum of the tokens before each compaction | `compaction_pre_tokens` |
| `compactions` | array of `{start, end, duration_ms, trigger, pre_tokens, post_tokens, dropped_tokens}`; `duration_ms` in milliseconds | one element per compaction | `compactions` |
| `branch_dominant` | string | the branch with the most user lines; a tie goes to the later one | `branch_dominant`; the source of `feature_id` |
| `branch_counts` | object `{branch: int}` | user lines per branch | `attrs` |
| `git_branch` | string | the branch of the last line | — (`hook_events.git_branch`) |
| `client_version` | string | the latest client version | `client_version` |
| `client_versions` | array of strings | the versions seen, in order | `client_versions` |

- The summary covers the main thread only. Nothing of a subagent is added to a session counter.
- It carries no tokens, no active time and no cost: tokens travel in `agent.usage.request`, the other two exist in OTel only.
- The summary columns are written as a set, and only from a summary that is not older than the stored one. A session with a summary counts its slash commands from `commands` alone.

## `agent.session.env`

The environment of the session. The discriminator of `event_id` is a hash of the payload, so an unchanged environment sent again on the same day is one record.

```json
{
  "type": "agent.session.env",
  "timestamp": "2026-09-29T10:15:30.000Z",
  "session_id": "3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11",
  "schema_version": 2,
  "event_id": "env:3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11:9c1d2e3f4a5b6c7d",
  "platform": "claude-code",
  "entrypoint": "cli",
  "client_version": "2.1.284",
  "codemie_cli_version": "0.14.1",
  "provider": "codemie-proxy",
  "api_host": "codemie.example.com",
  "configured_model": "claude-opus-5-5",
  "effort": "high",
  "output_style": "default",
  "permission_mode": "default",
  "plugins": {"sdlc-factory": "1.4.2"},
  "mcp_servers": ["codegraph", "jira"],
  "os": "darwin",
  "arch": "arm64",
  "node_version": "22.11.0",
  "timezone": "Europe/Warsaw",
  "hostname_hash": "5f2b0c9d7a1e4b38"
}
```

| Field | Type | Column of `session_dims` |
|---|---|---|
| `timestamp` | string, the time of the hook | — |
| `platform`, `entrypoint`, `client_version`, `codemie_cli_version` | string | same names |
| `provider` | string: `anthropic`, `bedrock`, `vertex`, `litellm-proxy`, `codemie-proxy` | `provider` |
| `api_host` | string, host only | `api_host` |
| `configured_model`, `output_style` | string | same names |
| `effort`, `permission_mode` | string | typed columns of `hook_events`, then the same names |
| `plugins` | object `{name: version}` | `plugins` |
| `mcp_servers` | array of strings, names only | `mcp_servers` |
| `os`, `arch`, `node_version`, `timezone` | string | same names |
| `hostname_hash` | string; the server stores what it receives and does not define the algorithm | `hostname_hash` |

## `agent.skill.dispatch`

A skill started by a slash command of the user. For a forked skill this event is the initiator of the fork: the fork's `agent.subagent.start` with the same `prompt_id` is joined to it, by its skill name, or without one when it ran in the foreground, when this is the only Claude Code `agent.skill.dispatch` of its `prompt_id`.

```json
{
  "type": "agent.skill.dispatch",
  "timestamp": "2026-09-29T10:16:02.000Z",
  "session_id": "3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11",
  "prompt_id": "p-1",
  "schema_version": 2,
  "event_id": "skill:3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11:p-1:sdlc-factory:sdlc-standard",
  "skill_name": "sdlc-factory:sdlc-standard",
  "command_name": "sdlc-factory:sdlc-standard",
  "command_source": "plugin"
}
```

| Field | Type | Goes to |
|---|---|---|
| `timestamp` | string, the time of the hook | `hook_events.ts` |
| `skill_name` | string; the event is counted only when it is non-empty | `hook_events.skill_name`, a typed column |
| `command_name` | string, no slash | `attrs` |
| `command_source` | string | `attrs` |

- One invocation of a skill counts once, by its initiator: this event when a slash command starts the skill, the Skill tool call (its `TOOL` span, or `agent.tool.start` with `skill_name`) when the model starts it. A Claude Code Skill or `Agent` call counts only with evidence it ran, since a denied call gets only `agent.tool.denied`: its `TOOL` span (`span_name` starting with `claude_code.`) needs a `TOOL_EXECUTION` span or an `agent.tool.end` / `agent.tool.error` of that tool with its `tool_use_id`, from 1 hour before the call to 1 day after it, and a Skill call's `agent.tool.start` needs that end or error; a denied call is neither a skill nor an agent and has no timeline row. A call without a `tool_use_id`, and another client's span or `agent.tool.start`, count as they are; this event and the skill-tagged `agent.subagent.start` are not gated. A forked skill also sends the `agent.subagent.start` of its fork, joined to the initiator by an exact key: the fork's id the Skill call's `agent.tool.end` names, or the `prompt_id` of this event. A fork a Skill call names is never this event's fork; this event has a fork only when it is the only Claude Code `agent.skill.dispatch` of its session and `prompt_id` (the CLI sends one per prompt; with two or more, none of them has a fork and their forks count on their own); its fork is the earliest first start of an `agent_id` at or after it with its `prompt_id` and skill name, else, when none has the name, the earliest with its `prompt_id` and no skill name (a foreground fork) that no `Agent` call's `agentId` names (an ordinary subagent of the same turn), a start with an empty or absent `agent_id` never; one event, one fork; an event whose `skill_name` is over 256 bytes is not counted and has no fork, so its fork counts on its own. A joined fork is not counted again and is never an agent, with or without `skill_name`; a fork with no counted initiator counts on its own: with `skill_name` as a skill, without it as an agent on a fallback pair only; on a day with spans such a fork without `skill_name` is not counted, since agents there come from `Agent` `TOOL` spans. A fork continued by SendMessage sends another `agent.subagent.start` with the same `agent_id` (under the new turn's `prompt_id`): only the first start of an `agent_id` is counted, and only a first start can be this event's fork. On the session timeline the invocation is one row, the initiator's (the Skill call's `TOOL` span or this event), with the fork's run and cost; the cost is that of the API requests under the span its LLM requests name by `agent_id`, or, when no LLM request names the fork (Claude Code stamps `agent_id` only on a subagent a tool call started, so a fork this event runs in the foreground has none), of the API requests the rollups' pairing matched with its own `agent.usage.request` rows (same `agent_id`), never both; a Skill call without a `TOOL` span has no row, so its fork is its own `command` row when it carries `skill_name` and has no row when it does not. The joins by `agentId` and `prompt_id` read Claude Code events only; an event without `platform` counts as Claude Code, the hook rows the server stores from OTel logs included. A `skill_name` on a Claude Code `agent.subagent.start` marks a skill fork: such a start is a skill, never an agent; another client's tagged start counts as an agent, as its initiator is never paired. A fork named by a Skill call that is not itself counted (no `TOOL` span, no `skill_name` on its `agent.tool.start`) and without its own `skill_name` counts as an agent. The Skill call's `agent.tool.end` names the fork by its `fork_agent_id`, which the CLI reads out of the result before it cuts `tool_response` to 300 characters, else by the complete `agentId` in `tool_response` (it survives for skill names up to about 203 characters for a background fork, 221 for a foreground one); its typed `agent_id` is never the key, since on a call made inside a subagent it is the caller's id.
- The arguments of the command are prompt text and are not sent.

## `agent.tool.end`

An activity hook event: a tool call that completed. Its line counts are the source of `lines_daily` for the session.

| Field | Type | Goes to |
|---|---|---|
| `lines_added`, `lines_removed` | int; present on Edit, MultiEdit and Write; absent for NotebookEdit, Bash and every other tool (rule 4: absent, never 0) | `attrs` |

- A new file counts its content lines (a trailing newline is not a line); an overwrite and an edit count the patch.
- The server builds `lines_daily` from them for every session where any `agent.tool.end` carries a numeric `lines_added`. Only the `agent.tool.end` events that carry a numeric `lines_added` count, one per non-empty `tool_use_id` (the earliest of them); a line count that is not a non-negative number counts 0. A session without them, a `null` or string `lines_added` included, takes its lines from the OTel lines metric.
- A `lines_daily` row means the session's lines are known; the API returns null when there is no row and 0 only for a real zero. Zero-sum sessions are stored as a `lines_daily` row with 0 (`active_time_daily` still omits zero rows).

## `agent.git.snapshot`

The state of the repository, sent at the end of the session.

```json
{
  "type": "agent.git.snapshot",
  "timestamp": "2026-09-29T11:25:00.000Z",
  "session_id": "3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11",
  "schema_version": 2,
  "event_id": "git:3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11:b7e1c02",
  "git_branch": "feature/ABC-123",
  "repo_remote": "org/repo",
  "git_email": "dev@example.com",
  "git_head_start": "9a4f3d1",
  "git_head_end": "b7e1c02",
  "git_commits": [
    {"sha": "b7e1c02", "time": "2026-09-29T11:10:44Z", "files": 3, "added": 120, "removed": 34}
  ]
}
```

| Field | Type | Goes to |
|---|---|---|
| `timestamp` | string, the time of the hook | `hook_events.ts` |
| `git_branch`, `repo_remote` | string | typed columns of `hook_events` |
| `git_email` | string | `session_dims.git_email` |
| `git_head_start`, `git_head_end` | string, abbreviated or full SHA | `session_dims` columns of the same names |
| `git_commits` | array of `{sha: string, time: string, files: int, added: int, removed: int}`; `files` is a count, not a list of paths | `session_dims.git_commits` |

## Identity and re-sends

| Event | Identity |
|---|---|
| with a non-empty `event_id`, either route | `(type, session_id, event_id)` |
| without `event_id` | the whole content, as for the 13 activity hook events |

- The first stored version of an identity stays. A re-send is dropped whatever its other fields, its route or the login it arrives under. A changed event must therefore carry a new `event_id`; this is why every summary has its own.
- Two sessions that share an `event_id` are both stored, as are two types.
- A repeat is recognised when it carries the same UTC day in `timestamp` and arrives within `CLI_ANALYTICS_DEDUP_RETENTION_DAYS` (14) days. This is why `timestamp` must be deterministic (rule 5): an event re-sent with a `timestamp` on the other side of midnight is stored twice.
- The identity is taken from the values as they are stored: cleaned, and the session id cut to 256 bytes.

## Values the server changes or refuses

| Input | Stored |
|---|---|
| A count of `agent.usage.request`: an integer or a digit string up to 10^9 | the integer |
| … above 10^9 | `0`, the original in `attrs` |
| … absent, `null` or `""` | NULL |
| … anything else (`"abc"`, `-1`, `12.5`, `true`, an array, an object) | NULL, the original in `attrs` |
| `is_api_error`: `true` / `false`, or the strings `"true"` / `"false"` in any case | the boolean |
| … anything else | NULL, the original in `attrs` |
| Text of a usage row, the sender's email included | NUL characters removed, broken Unicode repaired, cut to 256 bytes; `""` is stored as NULL |
| A count or a duration read from `attrs` by the recalculation (summary counters, `usage` rows, OTel `duration_ms`) | the integer part when it is a non-negative number within the column's range, with at most 30 digits after the point; otherwise NULL |
| A `timestamp` that cannot be parsed | the time the event was received |
| `started_at`, `ended_at` and the other times inside a payload that are not valid ISO-8601 UTC | NULL |
| A client time after today | ignored when idle sessions are purged, so it cannot keep a session past its retention |
| A bound session (EPMCDME-16440) | idle only when its server-side `updated_at` (refreshed by every accepted write) is also before the cutoff; an unbound legacy row keeps the hook-time rule |

A missing or refused value never fails the request and never drops the event.

## Read side: classification and Personal visibility

- **Params.** `subscription_type` and `project_origin` (multi-value) filter every analytics route; `unknown` / `none` match legacy NULL or empty rows. `personal_only` (default false) exists only on `/sessions`.
- **Fields.** Session rows and detail carry scalar `subscription_type` and `project_origin`. Aggregates (repository, user, model, day, tool/activity, overview) carry sorted distinct lists `subscription_types` and `project_origins`. Vocabulary: stored values lowercased; missing gives `unknown` (subscription) or `none` (origin); an unrecognised non-empty value gives `other`.
- **Visibility.** A private Personal session (`project_origin=personal`, or Personal-paid with a missing or unknown origin) is visible only to its owner (email match is case- and whitespace-insensitive; a missing owner never matches), strictly, even for a super-admin or auditor, and is excluded from every shared aggregate, including the owner's. A Personal-paid session on a work project (shared origin) follows that project's read permissions as activity; its cost is excluded from aggregate spend. Detail of another user's Personal session returns 404. Non-admins get only their own Personal sessions on `/sessions` and `/sessions/{trace_id}`; other routes stay 403. `/sessions?personal_only=true` returns an admin's own Personal sessions only. Privilege is the CLI policy, not the shared `AccessFilter`: only a super admin is unrestricted, project admins keep exactly their own projects, and an auditor has no extra right (own private Personal only, unless they are also a project admin). Session list and detail also return the stored diagnostic `route_note` (null when missing) and `routing_label`, exactly `folder not linked to a project` for a default-project session routed by one of the four fallback notes, null otherwise.
