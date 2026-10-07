# EPMCDME-15109: Fixes for the three retained issues

Date: 2026-09-26

Scope: R01, R03 and R05 from [findings.md](findings.md), following the acceptance
criteria supplied by the user. Changes are local to the backend and frontend
working trees. No commits, pushes or merge-request changes were made.

## R01: Attribute fallback for the selected budget category

The proxy now marks personal-budget fallback only after the actual selected
category fails to produce a project runtime. An allocation in another category
no longer hides this fallback from monitoring.

Direct model creation records attribution from the runtime outcome. The context
retains the personal project identity separately from the provider customer
identity, and preserves the original routing project in `budget_fallback_from`
when personal billing is selected. Subsequent model resolution can still use
that original project's funded category. Agent context reconstruction passes
the actual agent model; final chat-history attribution uses the routed/requested
model to detect an unavailable category in the fresh persistence context.

Existing budget-category precedence, personal-key bypass and asset ownership
rules are preserved. Other memberships are never searched for funding.

Changed backend files:

- `src/codemie/enterprise/litellm/proxy_router.py`
- `src/codemie/enterprise/litellm/llm_factory.py`
- `src/codemie/service/llm_service/utils.py`
- `src/codemie/rest_api/models/settings.py`
- `src/codemie/agents/langgraph_agent.py`
- `src/codemie/agents/tool_confirmation/tool_call_confirmation_mixin.py`
- `src/codemie/rest_api/handlers/assistant_handlers.py`

## R03: Serialize default mutations for each user

Both set and clear acquire a PostgreSQL row lock on the user before reading any
membership flags. The stable user row provides a common lock even when no
default exists. Concurrent mutations therefore read state after the preceding
mutation commits, avoiding the stale target flag that could clear the newly
selected default. Existing old-row-first flushing and the unique partial index
remain in place.

Changed file: `src/codemie/repository/user_project_repository.py`.

## R05: Preserve confirmed UI changes across refresh failures

The projects table passes a confirmed membership-state update to its parent
after a successful default change or add operation. The parent commits this
update before refreshing. If adding succeeds but setting the default fails,
the confirmed ordinary membership is retained.

User details and budget requests settle independently: a failed budget request
does not discard successfully fetched user details. Request IDs prevent older
refreshes from overwriting the newest state. The redundant default-change
success notification in the component was also removed.

Changed files in `D:/Projects/codemie-ui`:

- `src/pages/settings/administration/usersManagement/components/UserProjectsTable.tsx`
- `src/pages/settings/administration/usersManagement/components/popups/UserDetailsPopup.tsx`

## Validation

Backend formatting and Ruff lint passed for all eight changed Python files.
Frontend dependency installation, full lint and TypeScript checking passed.
Whitespace checks passed in both repositories. No tests were written or run:
the repository instructions require an explicit request for tests.
Runtime budget/provider verification, controlled PostgreSQL concurrency and
browser failure injection remain unverified by this change session.

Backend commands (run from `D:/Projects/codemie`):

```bash
poetry env info --path
poetry run ruff format src/codemie/agents/langgraph_agent.py src/codemie/agents/tool_confirmation/tool_call_confirmation_mixin.py src/codemie/enterprise/litellm/llm_factory.py src/codemie/enterprise/litellm/proxy_router.py src/codemie/repository/user_project_repository.py src/codemie/rest_api/handlers/assistant_handlers.py src/codemie/rest_api/models/settings.py src/codemie/service/llm_service/utils.py
poetry run ruff check src/codemie/agents/langgraph_agent.py src/codemie/agents/tool_confirmation/tool_call_confirmation_mixin.py src/codemie/enterprise/litellm/llm_factory.py src/codemie/enterprise/litellm/proxy_router.py src/codemie/repository/user_project_repository.py src/codemie/rest_api/handlers/assistant_handlers.py src/codemie/rest_api/models/settings.py src/codemie/service/llm_service/utils.py
git diff --check
```

Frontend commands (run from `D:/Projects/codemie-ui`):

```bash
npm ci
npx prettier --write src/pages/settings/administration/usersManagement/components/UserProjectsTable.tsx src/pages/settings/administration/usersManagement/components/popups/UserDetailsPopup.tsx
npm run lint
npm run typecheck
npm run secrets:check
git diff --check
npx prettier --check src/pages/settings/administration/usersManagement/components/UserProjectsTable.tsx src/pages/settings/administration/usersManagement/components/popups/UserDetailsPopup.tsx
npx prettier --write src/pages/settings/administration/usersManagement/components/popups/UserDetailsPopup.tsx
npx eslint src/pages/settings/administration/usersManagement/components/UserProjectsTable.tsx src/pages/settings/administration/usersManagement/components/popups/UserDetailsPopup.tsx
```

The initial typecheck found a nullable-state narrowing error; the initial lint
found the forbidden `++` operator. Both were corrected and the full commands
passed on rerun. Formatting was reapplied after the increment change, followed
by targeted lint. `npm run secrets:check` could not perform its scan because
the Docker daemon was not running (exit 1); this gate is not claimed as passed.
No dependency manifests changed, so dependency-license validation was skipped.

## Exclusions

D02 (inactive-budget probe inconsistency) remains a separate pre-existing issue.
Q04 is a UX suggestion, and Q06 was withdrawn as an acceptance-criteria defect.
The unrelated `docker-compose.yml` change and prior review documents were
preserved.
