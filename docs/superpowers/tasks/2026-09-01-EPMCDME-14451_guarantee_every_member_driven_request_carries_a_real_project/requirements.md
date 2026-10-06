# Requirements — EPMCDME-14451

**Work Item**: EPMCDME-14451  
**Flow**: sdlc-light  
**Date**: 2026-09-01

## Goal

Make a real, caller-owned project a hard precondition on every member-driven request path — sign-in, assistant create/update, the LLM proxy path, and the CLI — so no governed request can arrive with an empty, username-derived, or silently substituted project.

## Acceptance Criteria

1. ✓ Given the platform cannot create a personal project for a user, when that user signs in, then sign-in is refused with a message that no project could be created, rather than proceeding.
2. ✓ Given an assistant is created or updated without a project named by the caller, when it is saved, then it is attached to a project the caller actually belongs to, not a shared/empty fallback.
3. ✓ Given assistants that already exist with a fallback/empty project, when this ships, then every one is reported to administrators as needing a real project.
4. ✓ Negative: given a request arrives on the LLM proxy path with no project identified, when processed, then it is refused for missing project context rather than served under a username or empty project.
5. ✓ Given a member works through the CLI with no project configured, when they make a request, then they're told a project is required and how to set one, before model selection.
6. ✓ Given KB/datasource indexing, skill/workflow generation, or a toolkit resolves a model with no member/project context, when it runs, then it continues using the platform default, unaffected by this ticket.

## Context

**Repository scope**: `codemie` backend only. No `codemie-ui` change expected except surfacing the new sign-in failure state, if the UI does not already render generic auth errors.

**Why this exists**: an availability rule keyed on project is bypassable today by arriving without one. This ticket is the precondition for tickets 2–5 (allow-list storage and enforcement) and must land first, because those paths assume a project is always present.

**Four gaps identified:**

1. `src/codemie/service/project/personal_project_service.py:72-104` — `ensure_personal_project_async` catches bare `Exception`, logs, returns `False` without raising. Callers (`authentication_service.py:729-732` and `:390-392`; `registration_service.py:208-211,244-247`) let sign-in proceed either way.

2. `src/codemie/agents/tools/platform/platform_tool.py:206` — `project=assistant.project or ""`; `workflows/assistant_generator/nodes/validation/utils.py:113` — falls back to `user.current_project`. The empty-string/fallback path itself is what the story calls the "hardcoded demo project" gap.

3. `src/codemie/enterprise/litellm/proxy_router.py:291-292` — `_extract_request_info` computes `project = headers.get(HEADER_CODEMIE_CLI_PROJECT) or (user.username if user else "")`, falling back to username and never refusing.

4. CLI: no project-required guard ahead of model selection.

## Scope

**Sign-in**: if `ensure_personal_project_async` fails, refuse sign-in with a clear message instead of logging and proceeding.

**Assistant create/update**: require a project resolvable to one the caller actually belongs to; remove the `or ""` / `or user.current_project` silent fallback pattern — reject or resolve explicitly, never silently substitute.

**Migration**: one-off report (log/admin-facing list) of all assistants currently carrying an empty/fallback project, for a human to reattach. Do not auto-move them.

**Proxy path**: remove the username/empty fallback; refuse the request (clear error) when `HEADER_CODEMIE_CLI_PROJECT` is absent and no project can otherwise be resolved.

**CLI**: when a member has no project configured, tell them a project is required and how to set one, before model selection runs.

**Explicit exemptions**: background/no-user consumers — KB/datasource indexing, skill/workflow generators, and toolkits resolving the platform-default model — keep using the platform default and are untouched by this ticket. Identified by `HEADER_CODEMIE_INTEGRATION`.

## Out of Scope

- The allow-list itself, its storage, or its enforcement (tickets 2–5)
- Auto-reattaching demo/fallback-project assistants to a real project — excluded platform-wide per story assumptions; report only

## Accepted Breaking Change

Any proxy caller omitting project today will start failing. Accepted per story assumptions — "an availability rule bypassable by omission is not a rule."

## Implementation Approach

This task addresses **10 identified risk indicators** through:

1. **Shared validation utilities** (Task 1) — `ProjectRequiredException`, `require_valid_project()`, `is_background_consumer()`
2. **Explicit exception raising** (Tasks 2, 3, 5) — Replace silent failures with clear errors
3. **Integration marker** (Task 5) — Explicit background consumer identification via `HEADER_CODEMIE_INTEGRATION`
4. **User type exclusions** (Task 2) — Honor existing `is_personal_project_excluded()` logic
5. **Test-first implementation** (Tasks 1, 2, 3, 5) — Close test coverage gaps proactively

See `plan.md` for detailed implementation tasks.
