# User Context in MCP Tool Invocations — Tool-Owner Reference

This document is for owners of MCP servers/tools invoked through CodeMie. It describes the
`user-context` payload CodeMie attaches to tool calls, what it is for, and the obligations that come
with receiving it.

Introduced by EPMCDME-13260. Requires an MCP-Connect release that accepts and forwards the field
(EPMCDME-14847).

---

## Overview

When CodeMie invokes one of your tools, it can attach a small, non-sensitive profile of the
**authenticated end user** who initiated the request. Your tool can use it for audit logging,
per-user scoping of results, or personalisation.

- **On by default, with an opt-out** (EPMCDME-14984). It is sent unless `allow_user_context` is
  switched off: on the MCP server configuration (the server-level setting), or as a per-assistant
  override. For a server taken from the MCP catalog, the catalog value is a ceiling: an assistant can
  switch it off but cannot switch it on if the administrator turned it off. For a server with its own
  custom configuration, the per-assistant override takes precedence when set. Data source providers
  have the same `allow_user_context` setting, also on by default. Ask the CodeMie administrator
  (catalog servers and providers) or the assistant owner (custom servers) to turn it off if you do not
  want your tool to receive it.
- Delivered in the request `_meta` object under the key **`codemie.ai/user-context`**.
- Forwarded by MCP-Connect only on `tools/call` (not on `tools/list`); MCP-Connect is what relays it
  to the tool on invocation.
- Every field is **optional**. Treat a missing field, a `null`, and an empty list as "not provided".
  Legacy or unauthenticated calls may omit the object entirely.

---

## Fields

| Field | Type | Notes |
|---|---|---|
| `id` | string | Stable CodeMie user identifier. |
| `username` | string | Login / handle. |
| `name` | string | Display name. **PII.** |
| `email` | string | **PII.** |
| `picture` | string (URL) | Avatar URL. **PII.** |
| `roles` | string[] | CodeMie roles. |
| `is_admin` | bool | |
| `is_maintainer` | bool | |
| `is_auditor` | bool | |
| `user_type` | string | e.g. `regular`, `external`. |
| `project_names` | string[] | Projects the user is a member of. |
| `admin_project_names` | string[] | Projects the user administers. |
| `knowledge_bases` | string[] | Knowledge bases the user can access. |

`extra_attributes` (raw IdP claims) is **not forwarded on the MCP path**. It has no MCP consumer,
varies by deployment, and is in no published contract. If you need a claim that is not in the list
above, raise it with the CodeMie team rather than depending on `extra_attributes` — it will not
arrive here.

---

## What it is not

- **Not credentials.** No token, password, or secret is included. `auth_token` is deliberately
  excluded from the payload. If your tool must act *as the user* against an external system, use the
  configured `mcp_headers` / `env` of the MCP server (e.g. an OAuth2 token injected via a header
  placeholder) — never anything from `user-context`.
- **Not a session.** The payload is a **per-call snapshot** taken at invocation time. Do not cache it
  as a session, and do not assume two calls with the same `id` belong to the same conversation.
- **Not tool-controllable.** Values are populated by the CodeMie backend from the authenticated
  session. A tool cannot inject, override, or spoof them through its arguments or response.

---

## Trust model

Trust in `user-context` is **transport-level, not cryptographic**:

- The payload is only as trustworthy as the channel. It is asserted by the CodeMie backend and
  relayed by MCP-Connect; it is **not signed** and carries no verifiable proof of origin.
- Rely on it only when the deployment guarantees that **only the CodeMie backend can reach your
  tool** (network isolation, mTLS, an authenticating gateway, etc.). If your MCP server is reachable
  by other clients, they can send an arbitrary `user-context` and you must not trust it.
- Do not use `user-context` as the sole basis for a security decision that a hostile caller could
  exploit by forging the field. Combine it with transport authentication.

---

## PII obligations for tool owners

`name`, `email`, and `picture` are personal data. If your tool receives and handles them:

- Log them only when necessary, and mask them when you do (CodeMie itself logs this context in a
  masked form — e.g. `j***@epam.com`).
- Do not forward them to third-party services beyond what the user's request requires.
- Apply your organisation's retention and deletion policy; a per-call snapshot should not become a
  long-lived user record without a lawful basis.
- Prefer `id` (opaque) over `email`/`name` wherever an identifier is all you need.
