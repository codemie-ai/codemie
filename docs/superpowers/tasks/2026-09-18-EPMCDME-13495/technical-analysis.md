# Technical Research

**Task**: gitlab API inline-discussions JSON request bodies
**Generated**: 2026-09-18T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

# EPMCDME-13495
# GitLabTool sends POST/PUT/PATCH bodies as form data instead of JSON, breaking nested-object GitLab API requests

## Summary
GitLabTool sends POST/PUT/PATCH bodies as form data instead of JSON, breaking nested-object GitLab API requests.

## Description
CodeMie Git tool fails to create GitLab inline merge request discussions because GitLabTool sends request payloads using {data=method_arguments} instead of {json=method_arguments}. The issue was observed in a webhook-driven GitLab merge request review bot. Fetching merge request diffs and posting merge-request-level notes work, but creating inline diff discussions anchored to a file/line fails. GitLab API returns 400 errors saying position fields are missing/invalid even though logs show them present. The same JSON payload succeeds manually via curl and creates a DiffNote. This likely affects all GitLabTool POST, PUT, and PATCH requests containing nested request bodies.

ServiceNow reference: Catalog Task SCTASK002403913; Request Item RITM0002251649; reporter yahor_azarau@epam.com.

Potential fix: change requests.request(method=method, url=url, headers=headers, data=method_arguments) to requests.request(method=method, url=url, headers=headers, json=method_arguments).

## Preconditions
GitLabTool is available and configured; it can access a target project/MR, fetch diffs, post MR-level notes, and has a valid inline discussion payload with nested position fields base_sha, start_sha, head_sha, position_type, and file/line metadata.

## Reproduction
Fetch MR diffs; prepare a valid inline discussion payload with nested position; call POST /api/v4/projects/:project/merge_requests/:iid/discussions; observe 400. Send same JSON via curl; observe successful DiffNote.

## Expected
GitLabTool sends JSON bodies for GitLab API calls requiring JSON. A valid nested position payload creates an inline diff discussion, equivalent to curl, without fallback to MR-level notes.

## Actual
GitLabTool sends form data and GitLab rejects nested position fields as missing/invalid; MR-level note fallback works.

## Affected areas
CodeMie Git tool, GitLabTool request execution, GitLab API integration, MR review automation, inline diff-anchored comments, and POST/PUT/PATCH endpoints with nested bodies.

## Acceptance criteria
- Use json=method_arguments for JSON GitLab API request bodies.
- Inline discussions can be created with nested position.
- CodeMie and curl behavior equivalent.
- No fallback needed for valid inline payload.
- Existing requests without nested payloads continue working.
- Regression coverage for POST, PUT, PATCH with nested JSON objects.
- Validate at least one GitLab inline discussion scenario that creates a DiffNote.

Feature area keywords: gitlab API inline-discussions JSON request bodies. Include Codebase Findings and Risk Indicators sections, plus the required sections 6 and 7 for the caller's digest.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie_tools/core/vcs/gitlab/tools.py:33-72` defines `GitlabInput`, the generic request schema. It accepts a method, `/api/v4/` URL, `method_arguments`, and optional custom headers; its current documentation describes non-GET arguments as request body data.
- `src/codemie_tools/core/vcs/gitlab/tools.py:75-111` defines `GitlabTool._make_request`. GET uses `params=method_arguments`; every other method, including POST, PUT, PATCH, and DELETE, calls `requests.request(..., data=method_arguments)`. This is the direct serialization seam for the reported defect.
- `src/codemie_tools/core/vcs/gitlab/tools.py:144-191` parses string/dict queries, supports one level of `{"query": {...}}` unwrapping, resolves the access token, builds headers, invokes `_make_request`, and formats the textual HTTP response. It does not transform or validate nested body schemas.
- `src/codemie_tools/core/vcs/gitlab/models.py:19-52` defines `GitlabConfig` with URL, PAT/OAuth mode, token, integration ID, and acting-user ID.
- `src/codemie_tools/core/vcs/gitlab/tools_vars.py:16-99` provides tool metadata, input examples, and the generic GitLab REST capability description. Its “automatic request parameter handling” currently describes non-GET requests as using a request body but does not specify JSON encoding.
- `src/codemie_tools/core/vcs/utils.py:14-85` builds default/custom headers, protects authorization, and sets `Accept: application/json` plus `Authorization: Bearer ...` through the GitLab tool.

### Architecture and Layers Affected

- Reusable external-tool adapter: `codemie_tools` owns the generic GitLab HTTP boundary and inherits lifecycle/configuration behavior from `CodeMieTool` (`src/codemie_tools/base/codemie_tool.py`).
- Configuration/service layer: `src/codemie/service/settings/settings.py:228-250,903-919` maps `GitlabConfig` to the shared Git credential type and GitLab OAuth provider.
- Agent-facing metadata/schema: `GitlabInput` and `GITLAB_TOOL` expose the request contract to agents. The separate specialized toolkit under `src/codemie_tools/git/gitlab/` is not the generic `GitlabTool._make_request` path.
- No router, repository, database model, migration, or workflow implementation is involved in this request serialization seam.

### Integration Points

- Outbound `requests` call to a configured GitLab server, using a relative API path joined to `GitlabConfig.url`.
- Authentication is resolved either from the configured PAT or `GitLabOAuthTokenManager` for OAuth, then passed through `_build_headers`.
- The affected GitLab endpoint is represented generically by the tool; no endpoint-specific inline-discussion code or fallback implementation was found in this repository.

### Patterns and Conventions

- External API behavior stays behind a `codemie_tools` adapter; output is normalized to a formatted string containing method, URL, status, reason, and response text.
- Unit tests patch `requests.request` and assert keyword arguments rather than making live SaaS calls.
- Mutation safety is determined from the HTTP method: `CodeMieTool._SAFE_HTTP_METHODS` is read-only only for GET/HEAD/OPTIONS, while `GitlabTool.is_safe` delegates to that policy.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/agents/tool-overview.md` and `custom-tool-creation.md` require reusable provider integrations to remain under `src/codemie_tools/` and recommend narrow boundary tests.
- `.ai-run/guides/integration/external-services.md` documents the adapter boundary and config-backed credentials.
- `.ai-run/guides/testing/testing-patterns.md` and `testing-service-patterns.md` prescribe package-local tests, mocked external boundaries, and seam assertions.
- `.ai-run/guides/quality-gates.md` and `.ai-run/guides/development/local-testing.md` define Makefile gates and the explicit-only test policy.
- `.ai-run/guides/project.md` identifies GitLab as source control and GitLab CLI as the MR adapter. `docs/stories/2026-09-07-gitlab-image-download/technical-analysis.md` documents the same generic GitLab tool but covers binary responses, not request-body serialization.

### Architectural Decisions

No GitLab request-serialization ADR or inline decision marker was found. Existing documentation establishes the toolkit boundary but does not define whether mutation bodies must be form-encoded or JSON.

### Derived Conventions

The nearest established convention is to test the adapter at its outbound `requests.request` seam with `unittest.mock.patch`, preserve authentication/header behavior, and avoid live GitLab calls in unit tests.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie_tools/core/vcs/gitlab/test_tools.py:155-187` covers `_make_request` GET and POST dispatch; the POST assertion currently explicitly expects `data=method_arguments`.
- `tests/codemie_tools/core/vcs/gitlab/test_tools_advanced.py:130-145` loops over GET, POST, PUT, DELETE, and PATCH for response formatting, but does not inspect request keyword arguments.
- `tests/codemie_tools/core/vcs/gitlab/test_tools_advanced.py:148-177` covers a complex POST payload and custom headers, and currently reads the payload from the mock’s `data` keyword.
- `tests/codemie_tools/core/vcs/gitlab/test_integration.py` exercises mocked user, projects, merge-request, and error flows; it does not contact GitLab or assert JSON request bytes.
- `tests/codemie_tools/core/vcs/gitlab/conftest.py` supplies reusable mocked `GitlabConfig`, tool, and response fixtures. `test_models.py`, `test_tools_vars.py`, and `test_tools_resolve_per_user.py` cover configuration, metadata, and OAuth token resolution.

### Testing Framework and Patterns

Pytest is configured in `pytest.ini` with `src` on `PYTHONPATH`, importlib mode, and xdist; tests use pytest fixtures, `unittest.mock.patch`, and `Mock` responses. The Makefile’s relevant commands are `poetry run pytest tests/` (`make test`) and `make ruff`.

### Coverage Gaps

No current test verifies nested dictionary preservation through the outbound JSON keyword. No focused assertions cover POST, PUT, and PATCH separately for `json=method_arguments`, and no repository test validates a real GitLab inline discussion or resulting `DiffNote`; such validation requires configured GitLab credentials/project/MR and is not part of the mocked suite.

---

## 5. Configuration and Environment

### Environment Variables

No GitLab-specific environment variable is read directly by `GitlabTool`. `GitlabConfig` receives URL/token/auth fields through the application’s settings/credential mechanism; OAuth access tokens are obtained at request time from the token manager.

### Configuration Files

- `src/codemie_tools/core/vcs/gitlab/models.py` defines the runtime configuration schema.
- `src/codemie_tools/core/vcs/gitlab/tools_vars.py` registers `GITLAB_TOOL` metadata and its `GitlabConfig` class.
- `src/codemie/service/settings/settings.py` maps the config into credential and OAuth-provider lookup.
- `pyproject.toml` declares Python `>=3.12,<3.14` and the project dependencies; `requests` is imported by the tool (its transitive/direct lockfile resolution is not changed by this behavior).
- `pytest.ini` and `Makefile` define the local test harness and gates.

### Feature Flags and Deployment Concerns

No feature flag or deployment toggle controls request serialization. The repository has a minimal `.gitlab-ci.yml` guard and no endpoint-specific integration-test job found for this behavior. Authentication and custom-header handling must remain unchanged, and tokens must not appear in added diagnostics.

---

## 6. Risk Indicators

- The current unit-test contract asserts `data=method_arguments` in `test_tools.py:172-187` and `test_tools_advanced.py:165-177`; changing the boundary requires updating those assertions rather than treating failures as unrelated.
- `GitlabTool` applies the non-GET branch to DELETE as well as POST/PUT/PATCH (`tools.py:108-111`). A broad serialization change affects all mutating methods, including callers that currently rely on scalar/form-compatible fields.
- `GITLAB_DEFAULT_HEADERS` contains `Accept` but no default JSON `Content-Type`; `requests` handles headers differently for `data=` and `json=`. Speculative: the implementation should verify that custom `Content-Type` behavior does not create a contradictory or incompatible request.
- Existing tests are entirely mocked. Speculative: validating a DiffNote needs a separately configured GitLab integration scenario, while unit coverage can only prove the exact outbound keyword and nested-object preservation.
- The generic tool accepts arbitrary GitLab endpoints and arguments. Speculative: changing the shared seam may expose endpoint-specific APIs that intentionally expect form encoding, so compatibility should be checked against existing documented callers and the stated POST/PUT/PATCH JSON contract.

---

## 7. Summary for Complexity Assessment

The likely change is narrowly located at the outbound request boundary in `src/codemie_tools/core/vcs/gitlab/tools.py`, with associated contract assertions in the GitLab tool tests and possibly the input/metadata wording in `tools.py` and `tools_vars.py`. Configuration, OAuth token resolution, header construction, response formatting, routers, persistence, and workflows are existing adjacent surfaces rather than implementation targets. The primary integration is Python `requests` to arbitrary GitLab API endpoints.

Technical novelty is low because the request abstraction already separates GET query parameters from non-GET bodies; the defect is the choice of `data` versus JSON serialization. Test posture is good for mocked request dispatch and response formatting, but there is no nested-body regression test, no separate POST/PUT/PATCH keyword coverage, and no live DiffNote validation. Main risks are the broad generic-method impact, stale tests/documentation that describe form data, and the lack of a configured external GitLab scenario to prove the API-level result.

---

## 8. External References

None named by the task.
