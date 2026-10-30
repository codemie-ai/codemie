# EPMCDME-13260 — MCP user_context propagation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-enable `user_context` on the MCP wire, exclude `extra_attributes` from that path, add PII-masked diagnostic logging on the MCP and DSP tool-invocation paths, and ship the tool-owner contract doc.

**Architecture:** One-line exclude-set edit in `MCPExecutionContext.to_request_fields()` plus two docstring rewrites is the entire production behaviour change for the wire. A new `UserContext.masked_for_logging()` (with module-level `_mask_value()` / `_mask_email()`) is consumed by one new `logger.debug` line at each of the two invocation sites. Tests that lock the old "never sent" behaviour are flipped or deleted; positive coverage is added.

**Tech Stack:** Python, Pydantic v2, pytest / pytest-asyncio, `unittest.mock`.

**Spec:** `docs/superpowers/tasks/2026-09-10-epmcdme-13260-mcp-user-context/spec.md`

## Global Constraints

- Commit per task using the repository's existing convention (see `.ai-run/guides/standards/git-workflow.md`); include the `EPMCDME-13260` ticket ref.
- Do NOT add `exclude_none` to `to_request_fields()`; `client.py:128` `exclude_none=True` stays as-is and is load-bearing.
- Masking is log output only — never mask, drop, or alter any wire payload.
- Write `masked_for_logging()` fresh on `main`; do NOT cherry-pick from `origin/EPMCDME-13260`.
- Do not touch: `MCPToolInvocationRequest` (models.py:504), `toolkit_service.py:243` and `:1696`, `UserContext.from_user()` (user.py:150-166), `epam_ai_run.spi.schema.json`, the `UserContext` model shape, the DSP wire serialization at `provider_tool_factory.py:175`.
- No new wire field / model / schema entry / feature flag; no `tools/list` vs `tools/call` conditional in the backend.

**negative-constraints:** enumerated in the spec's Non-goals section — honored as follows: no-new-field/flag → no task adds one; no `tools/list`/`tools/call` conditional → Task 1 only edits the exclude set; no `exclude_none` change → Task 1 forbids it explicitly; DSP forwarding unchanged → Task 4 adds only a log line, never edits `provider_tool_factory.py:175`; `from_user()` / SPI schema / `UserContext` shape / `toolkit_service.py` untouched → Task 2 adds only new symbols to `user.py`; no wire masking → Tasks 3-4 call `masked_for_logging()` solely inside `logger.debug`; no mcp-connect-repo / branch-deletion / cherry-pick → Task 2 writes fresh.

---

### Task 1: Re-enable `user_context`, exclude `extra_attributes` on the MCP wire

**Files:**
- Modify: `src/codemie/service/mcp/models.py` — exclude set in `to_request_fields()` (~:179-186); field description (~:156-163); method docstring (~:165-177).
- Test: `tests/codemie/service/mcp/test_models.py`, `tests/codemie/service/mcp/test_models_1_2_auth_config.py`

**Interfaces:**
- Produces: `MCPExecutionContext.to_request_fields()` now emits `"user_context"` (value or `None`) and never emits `"extra_attributes"`.

- [ ] **Step 1: Reconcile the locking tests (write them red first).**
  - Grep `tests/codemie/service/mcp/test_execution_context_integration.py` and `tests/codemie/service/mcp/test_client.py` for `user_context` and for exact-payload assertions (`assert_called_once_with(json=`, exact-dict `==`). `test_client.py` cases set no `user_context`, so `client.py:128` `exclude_none=True` drops the `None` key — verify each still passes; adjust only if a case actually sets `user_context`. Record findings in the commit message.
  - `test_models.py`: at the exclusion asserts (~:87, :107, :140) change `assert "user_context" not in fields` to `assert fields["user_context"] is None` and add `assert "extra_attributes" not in fields`. Delete `test_to_request_fields_excludes_user_context_regardless_of_value` (~:177) and `test_to_request_fields_never_includes_user_context` (~:197) — delete, do not invert.
  - `test_models_1_2_auth_config.py`: same treatment at the exclusion asserts (~:224, :245).
  - Every exact-dict-equality assertion on `to_request_fields()` output across both files: add `"user_context": None` to the expected dict (or switch that assertion to a subset check). `"extra_attributes"` must not appear in any expected dict.
- [ ] **Step 2: Add positive wire coverage** in `test_models.py`: (a) `user_context` present in `to_request_fields()` output when the context carries a `UserContext`; (b) with that `UserContext` also carrying `extra_attributes={...}`, `"extra_attributes"` is absent from the output while `user_context` is present; (c) `auth_token` is never a field on a `UserContext` instance (`"auth_token" not in UserContext(...).model_dump()`).
- [ ] **Step 3: Run the two model test files — expect failures** referencing the missing `user_context` key / still-excluded field.
  Run: `poetry run pytest tests/codemie/service/mcp/test_models.py tests/codemie/service/mcp/test_models_1_2_auth_config.py -q`
- [ ] **Step 4: Apply the production edit.** In `to_request_fields()` remove `"user_context",` from the exclude set and add `"extra_attributes",`. Rewrite the field description and the method docstring so they state: `user_context` IS serialised and forwarded to MCP-Connect (EPMCDME-13260), this requires an MCP-Connect release that accepts the field, and `extra_attributes` is deliberately excluded on the MCP path (no MCP consumer; deployment-variable raw IdP claims; in no published contract). Remove every EPMCDME-13546 "why it is excluded" rationale.
- [ ] **Step 5: Re-run the model test files and the two verified files — expect PASS.**
  Run: `poetry run pytest tests/codemie/service/mcp/test_models.py tests/codemie/service/mcp/test_models_1_2_auth_config.py tests/codemie/service/mcp/test_client.py tests/codemie/service/mcp/test_execution_context_integration.py -q`
- [ ] **Step 6: Commit.**

**Test-first: yes** — model tests assert `to_request_fields()` emits `user_context` (value when set, `None` otherwise) and omits `extra_attributes`; they fail before the exclude-set edit.

---

### Task 2: `UserContext.masked_for_logging()` + module-level mask helpers

**Files:**
- Modify: `src/codemie/rest_api/security/user.py` — add module-level `_mask_value()` / `_mask_email()`; add `masked_for_logging()` method on `UserContext`. Do not touch `from_user()` or the field list.
- Test: `tests/codemie/rest_api/security/test_user_context.py`

**Interfaces:**
- Produces: `UserContext.masked_for_logging() -> dict[str, Any]`; module-level `_mask_value(str | None) -> str | None`, `_mask_email(str | None) -> str | None` in `src/codemie/rest_api/security/user.py`.

- [ ] **Step 1: Write failing tests** in `test_user_context.py`: build a `UserContext` with `email="john.doe@epam.com"`, `name`, `picture`, `username`, `roles`, `is_admin=True`, `project_names`, `extra_attributes={"a":1,"b":2}`, `knowledge_bases=["kb1"]`. Assert the returned dict: `email == "j***@epam.com"`; `name` / `picture` / `username` are masked (not equal to input, not empty); `roles`, `is_admin`, `id`, `is_maintainer`, `is_auditor`, `user_type`, `project_names`, `admin_project_names` pass through unchanged; `extra_attributes == "<redacted, 2 keys>"`; `knowledge_bases == "<redacted, 1 items>"`; the raw email/name/attr values appear nowhere in `str(result)`. Add a `None`-fields case: a `UserContext` with only `id` set returns a dict with `id` and no key for any `None` field.
- [ ] **Step 2: Run — expect fail** (`AttributeError` / import error).
  Run: `poetry run pytest tests/codemie/rest_api/security/test_user_context.py -q`
- [ ] **Step 3: Implement.**

```python
# module level in src/codemie/rest_api/security/user.py

def _mask_value(value: str | None) -> str | None:
    if not value:
        return value
    return "***" if len(value) <= 2 else f"{value[:2]}***"


def _mask_email(value: str | None) -> str | None:
    if not value:
        return value
    local, sep, domain = value.partition("@")
    if not sep:
        return _mask_value(value)
    return f"{(local[:1] or '')}***@{domain}"
```

```python
# method on UserContext

def masked_for_logging(self) -> dict[str, Any]:
    """PII-safe view of this context for diagnostic logs (EPMCDME-13260).

    email/name/picture/username are masked; extra_attributes and
    knowledge_bases collapse to a count-only placeholder; every other
    field passes through. None fields are omitted.
    """
    out: dict[str, Any] = {}
    for key in ("id", "roles", "is_admin", "is_maintainer", "is_auditor",
                "user_type", "project_names", "admin_project_names"):
        val = getattr(self, key)
        if val is not None:
            out[key] = val
    if self.email is not None:
        out["email"] = _mask_email(self.email)
    if self.name is not None:
        out["name"] = _mask_value(self.name)
    if self.picture is not None:
        out["picture"] = _mask_value(self.picture)
    if self.username is not None:
        out["username"] = _mask_value(self.username)
    if self.extra_attributes is not None:
        out["extra_attributes"] = f"<redacted, {len(self.extra_attributes)} keys>"
    if self.knowledge_bases is not None:
        out["knowledge_bases"] = f"<redacted, {len(self.knowledge_bases)} items>"
    return out
```

- [ ] **Step 4: Run — expect PASS.**
  Run: `poetry run pytest tests/codemie/rest_api/security/test_user_context.py -q`
- [ ] **Step 5: Commit.**

**Test-first: yes** — `test_user_context.py` asserts the masked dict shape (masked email/name/picture/username, readable roles/flags/projects, count-only placeholders); fails before the helper exists.

---

### Task 3: Masked diagnostic log on the MCP invoke path

**Files:**
- Modify: `src/codemie/service/mcp/client.py` — one `logger.debug` line on the invoke path next to the existing tool-name log (~:237). Module uses `from codemie.configs.logger import logger`.
- Test: `tests/codemie/service/mcp/test_client.py`

**Interfaces:**
- Consumes: `UserContext.masked_for_logging()` from Task 2.

- [ ] **Step 1: Write a failing test** (`caplog` at `DEBUG`): invoke `invoke_tool` with an execution context whose `user_context` carries `email="john.doe@epam.com"` and `extra_attributes={"x":1}`. Assert a debug record is emitted that contains `j***@epam.com` and `<redacted, 1 keys>` and does NOT contain `john.doe@epam.com` or the raw attr value. When `user_context` is `None`, assert no such debug line is emitted (or it logs an explicit "no user_context" — pin whichever the implementation chooses).
- [ ] **Step 2: Run — expect fail.**
  Run: `poetry run pytest tests/codemie/service/mcp/test_client.py -q -k debug`
- [ ] **Step 3: Add the line** — after the existing invoke-path tool-name log, when `execution_context.user_context` is set, `logger.debug("MCP tool invocation user context: %s", execution_context.user_context.masked_for_logging())` (structured field; adapt to the module's logging style). Do not modify the existing `logger.info` messages or the request payload.
- [ ] **Step 4: Run — expect PASS**, and run the full `test_client.py` to confirm no payload assertion regressed.
  Run: `poetry run pytest tests/codemie/service/mcp/test_client.py -q`
- [ ] **Step 5: Commit.**

**Test-first: yes** — a `caplog` test asserts the MCP invoke path emits a DEBUG line with masked email and a count-only `extra_attributes` placeholder and no raw PII; fails before the line is added.

---

### Task 4: Masked diagnostic log in `provider_tool_factory.invoke()` (DSP)

**Files:**
- Modify: `src/codemie/service/provider/provider_tool_factory.py` — one net-new `logger.debug` line in `invoke()` just after the request is built (~:175-182). Module uses `from codemie.configs import logger`. Do not change line 175's serialization.
- Test: `tests/codemie/service/provider/test_provider_tool_factory.py`

**Interfaces:**
- Consumes: `UserContext.masked_for_logging()` from Task 2.

- [ ] **Step 1: Write a failing test** (`caplog` at `DEBUG`): drive `invoke()` with a user whose context has `email` and `extra_attributes` set (DSP still forwards `extra_attributes` on the wire — unchanged). Assert a debug record contains the masked email and the `<redacted, N keys>` placeholder and no raw email / raw attr value. Keep the existing `test_built_execute_method` assertion that the wire `user_context` still equals `UserContext.from_user(user).model_dump(exclude_none=True)` (i.e. still contains `extra_attributes`) — the log line must not perturb the wire.
- [ ] **Step 2: Run — expect fail.**
  Run: `poetry run pytest tests/codemie/service/provider/test_provider_tool_factory.py -q -k debug`
- [ ] **Step 3: Add the line** just after the `ToolInvocationRequest(...)` is constructed: `logger.debug("%s user context: %s", log_prefix, UserContext.from_user(user).masked_for_logging())` (match the function's existing `log_prefix` style). Leave the `logger.info` "Invoking tool" / success lines and line 175 untouched.
- [ ] **Step 4: Run — expect PASS**, then the full test file.
  Run: `poetry run pytest tests/codemie/service/provider/test_provider_tool_factory.py -q`
- [ ] **Step 5: Commit.**

**Test-first: yes** — a `caplog` test asserts `invoke()` emits a DEBUG line with masked email and redacted `extra_attributes` while the DSP wire payload is unchanged; fails before the line is added.

---

### Task 5: Tool-owner documentation

**Files:**
- Create: `docs/user-context-in-mcp-tool-invocations.md`

- [ ] **Step 1: Write the doc.** Cover every point: the `_meta` key `codemie.ai/user-context`; the full field list, all optional; values are backend-populated from the authenticated session and tools cannot inject or override them; trust model is transport-level (only the CodeMie backend can reach the tool) and NOT cryptographically signed; no credentials are included (`auth_token` is excluded — to act as the user against an external system use `mcp_headers` / `env`); PII obligations for tool owners handling `email` / `name` / `picture`; the payload is a per-call snapshot, not a session; `extra_attributes` is NOT forwarded on the MCP path. Match the flat `docs/*-reference.md` ad-hoc style.
- [ ] **Step 2: Commit.**

**Test-first: no** — documentation file; no executable behaviour to assert.

---

### Task 6: MR deploy-sequencing note

**Files:**
- Create: `docs/superpowers/tasks/2026-09-10-epmcdme-13260-mcp-user-context/mr-notes.md`

- [ ] **Step 1: Write the note** for `mr-creator` to fold into the MR description: MCP-Connect EPMCDME-14847 must be merged, deployed, and fully rolled out across all bridge buckets (StatefulSet; backend routes across buckets at `client.py:85-98`) BEFORE this MR merges; un-excluding the field early recreates the platform-wide EPMCDME-13546 regression that reopened this ticket; ship as two separate releases with a completed rollout in between; ask a reviewer to post `/sanity` (touches security-relevant code).
- [ ] **Step 2: Commit.**

**Test-first: no** — process artifact consumed by `mr-creator`; no code.
