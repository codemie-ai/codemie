## Acceptance criteria

1. **POST/PUT/PATCH wire format:** `GitlabTool._make_request` calls
   `requests.request(..., json=method_arguments)` for each of POST, PUT, and
   PATCH. The request call must not also contain a `data` or `params` keyword.
2. **Nested payload preservation:** For each JSON-body method, a nested
   `method_arguments["position"]` object containing GitLab inline-discussion
   fields (`base_sha`, `start_sha`, `head_sha`, `position_type`, file path, and
   line) is passed unchanged as part of the `json` value. The test must assert
   the complete nested object, not only a top-level field.
3. **GET compatibility:** GET continues to call
   `requests.request(..., params=method_arguments)` and does not send the
   arguments as `json` or `data`.
4. **DELETE compatibility:** DELETE remains on its current
   `requests.request(..., data=method_arguments)` path. DELETE is explicitly
   outside this ticket because the ticket names only POST, PUT, and PATCH.
5. **Flat-body compatibility:** Existing scalar, string, boolean, list, and
   flat-dictionary POST/PUT/PATCH payloads continue to be represented by the
   same Python object in the outbound request, now serialized as JSON by
   `requests`.
6. **Header and auth compatibility:** The change does not alter
   `_build_headers`, authorization resolution, custom-header precedence,
   URL construction, response formatting, or OAuth/PAT behavior. Existing
   custom `Content-Type` and authorization assertions remain covered.
7. **Regression matrix:** Mocked seam tests explicitly cover GET, POST, PUT,
   PATCH, and DELETE, with separate POST, PUT, and PATCH cases proving nested
   JSON preservation. Existing stale expectations that read or assert
   `data=method_arguments` for POST are updated to `json=method_arguments`.
8. **Agent-facing contract:** `GitlabInput` and `GITLAB_TOOL` descriptions
   clearly distinguish GET query parameters, POST/PUT/PATCH JSON bodies, and
   DELETE's retained body behavior. No unrelated tool metadata is changed.
9. **Inline discussion evidence:** With configured GitLab credentials and a
   safe test merge request, at least one valid nested-position request returns
   `201 Created` and creates a discussion note whose type is `DiffNote`.
   The result is compared with the equivalent curl request, and the evidence
   shows that no MR-level-note fallback was needed.
10. **Validation honesty:** The repository's mocked unit coverage is
    executable without credentials. Live DiffNote validation is reported as
    not run when the required GitLab target, merge request, changed-line
    metadata, and credentials are unavailable; it is never represented as a
    passing automated test in that case.

# GitLabTool JSON Request Bodies Implementation Plan

> **For agentic workers:** Use `superpowers:subagent-driven-development` or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox syntax for tracking.

**Goal:** Correct the generic GitLab adapter's serialization of JSON-bearing
POST, PUT, and PATCH requests so nested GitLab API objects survive the
transport boundary, while preserving existing GET and DELETE behavior.

**Architecture:** The fix stays at the reusable external-tool boundary in
`GitlabTool._make_request`; no router, service, repository, workflow,
configuration, or specialized GitLab toolkit changes are needed. The method
will have three explicit policies: GET uses `params`, POST/PUT/PATCH use
`json`, and the remaining current path—including DELETE—uses `data`. The
agent-facing schema and metadata will describe that policy so callers do not
infer that every non-GET request is form-encoded.

**Tech Stack:** Python 3.12+, `requests`, Pydantic, pytest,
`unittest.mock`, Poetry, Makefile quality gates, and Ruff.

**Requirements source:** `docs/superpowers/tasks/2026-09-18-EPMCDME-13495/technical-analysis.md`.
This plan is the implementation document; no separate specification is being
created.

## Scope, compatibility, and behavior matrix

The current implementation at
`src/codemie_tools/core/vcs/gitlab/tools.py:75-111` treats GET specially and
sends every other method through `data=method_arguments`. The required
dispatch is:

| Method | Current call shape | Required call shape | Compatibility rationale | Regression evidence |
|---|---|---|---|---|
| GET | `params=method_arguments` | **Unchanged:** `params=method_arguments` | GitLab filters, pagination, and query options must remain URL query parameters. | Direct mock assertion; verify no `json`/`data` keyword. |
| POST | `data=method_arguments` | `json=method_arguments` | Fixes inline discussion bodies whose `position` is a nested object; flat bodies remain the same Python mapping before `requests` serialization. | Parametrized nested-position test plus existing POST test updated from `data` to `json`. |
| PUT | `data=method_arguments` | `json=method_arguments` | Applies the same JSON contract to update endpoints and protects nested update payloads. | Dedicated parametrized PUT case with nested object and exact keyword assertion. |
| PATCH | `data=method_arguments` | `json=method_arguments` | Applies the same JSON contract to partial-update endpoints and protects nested update payloads. | Dedicated parametrized PATCH case with nested object and exact keyword assertion. |
| DELETE | `data=method_arguments` | **Unchanged:** `data=method_arguments` | The ticket explicitly scopes the fix to POST/PUT/PATCH. A broad `else`-branch conversion could break callers that depend on DELETE's current form/body behavior. | Dedicated DELETE assertion proves `data` remains and `json` is absent. |

`requests` owns JSON encoding and will preserve nested dictionaries and lists
when `json=method_arguments` is supplied. Do not manually call
`json.dumps`, add a new default `Content-Type`, normalize method casing, or
change custom-header precedence. The current default headers contain
`Accept: application/json`; `requests` handles the JSON body encoding, while
an explicitly supplied custom `Content-Type` must continue to pass through
unchanged.

## Files and symbol-level responsibilities

- `src/codemie_tools/core/vcs/gitlab/tools.py`
  - `GitlabInput.query` field description around lines 33-72: describe the
    method-specific query/body encoding.
  - `GitlabTool._make_request` around lines 75-111: implement the explicit
    dispatch matrix above.
  - Do not change `GitlabTool.execute`, `_resolve_access_token`,
    `_validate_config`, `_build_headers` usage, or response formatting.
- `src/codemie_tools/core/vcs/gitlab/tools_vars.py`
  - `GITLAB_TOOL.description` feature text around lines 40-48: replace the
    generic “GET uses query params, others use request body” wording with the
    exact POST/PUT/PATCH JSON and DELETE-retained behavior.
- `tests/codemie_tools/core/vcs/gitlab/test_tools.py`
  - `TestGitlabTool.test_make_request_get`: retain or strengthen the GET
    `params` assertion.
  - `TestGitlabTool.test_make_request_post`: update the stale expectation to
    `json=method_arguments`.
  - Add the parametrized nested JSON matrix and a DELETE preservation test.
  - If schema wording is asserted, use `GitlabInput.model_fields["query"].description`.
- `tests/codemie_tools/core/vcs/gitlab/test_tools_advanced.py`
  - `TestGitlabToolAdvanced.test_execute_with_complex_query`: read the
    submitted payload from the `json` keyword while preserving custom-header
    assertions and complex list/boolean/string values.
  - Keep `test_execute_with_all_http_methods` as response-format coverage; the
    direct `_make_request` tests are the precise wire-contract coverage.
- `tests/codemie_tools/core/vcs/gitlab/test_tools_vars.py`
  - Extend `test_gitlab_tool_description_content` with assertions for the
    method-specific encoding wording if the metadata text is changed.
- No new dependency, fixture, migration, endpoint-specific fallback, or live
  integration-test file is planned.

## Implementation tasks

### Task 1: Establish the RED request-dispatch regression matrix

**Test-first: yes — the new POST/PUT/PATCH seam assertions must fail because the
current `_make_request` sends `data=method_arguments`, while the updated POST
and complex-body assertions also still observe `data` before the fix.**

**Files:**

- Modify: `tests/codemie_tools/core/vcs/gitlab/test_tools.py`
- Modify: `tests/codemie_tools/core/vcs/gitlab/test_tools_advanced.py`

**Interfaces:**

- Consumes: `GitlabTool._make_request(method: str, url: str,
  headers: dict[str, str], method_arguments: dict) -> requests.Response`.
- Produces: A mock-boundary contract that Task 2 can satisfy without changing
  the tool's public `execute` signature.

- [ ] **Step 1: Add one parametrized test with explicit POST, PUT, and PATCH
  cases.**

Place the test beside `test_make_request_post` in
`tests/codemie_tools/core/vcs/gitlab/test_tools.py`. Keep the nested payload
representative of the reported inline discussion defect and vary one ordinary
field per method so the matrix proves both nested and non-nested values are
preserved:

```python
@pytest.mark.parametrize(
    ("method", "extra_fields"),
    [
        ("POST", {"body": "Inline review comment"}),
        ("PUT", {"description": "Updated description"}),
        ("PATCH", {"labels": ["reviewed", "security"]}),
    ],
)
@patch("requests.request")
def test_make_request_json_body_preserves_nested_payload(
    self, mock_request, gitlab_tool, mock_response, method, extra_fields
):
    mock_request.return_value = mock_response
    headers = {"Authorization": "Bearer test_token"}
    position = {
        "base_sha": "base-sha",
        "start_sha": "start-sha",
        "head_sha": "head-sha",
        "position_type": "text",
        "new_path": "src/example.py",
        "new_line": 42,
    }
    method_arguments = {**extra_fields, "position": position}
    url = "https://gitlab.example.com/api/v4/projects/123/merge_requests/7/discussions"

    response = gitlab_tool._make_request(method, url, headers, method_arguments)

    mock_request.assert_called_once_with(
        method=method,
        url=url,
        headers=headers,
        json=method_arguments,
    )
    assert mock_request.call_args.kwargs["json"] == method_arguments
    assert mock_request.call_args.kwargs["json"]["position"] == position
    assert "data" not in mock_request.call_args.kwargs
    assert "params" not in mock_request.call_args.kwargs
    assert response == mock_response
```

The endpoint is intentionally used only at the transport seam; the test does
not claim that PUT or PATCH is a valid operation on the discussions endpoint.
Its purpose is to verify the generic method dispatcher for each HTTP method
named by the ticket.

- [ ] **Step 2: Preserve and make the GET contract explicit.**

Keep `test_make_request_get` and its direct assertion, or rewrite it to include
negative assertions for the other body keywords:

```python
mock_request.assert_called_once_with(
    method="GET",
    url="https://gitlab.example.com/api/v4/projects",
    headers=headers,
    params={"visibility": "public"},
)
assert "json" not in mock_request.call_args.kwargs
assert "data" not in mock_request.call_args.kwargs
```

This protects query behavior independently of the mutation-body change.

- [ ] **Step 3: Update the stale direct POST and complex-body expectations.**

In `test_make_request_post`, change the expected call from:

```python
data=method_arguments
```

to:

```python
json=method_arguments
```

In `TestGitlabToolAdvanced.test_execute_with_complex_query`, change:

```python
data = mock_request.call_args[1]["data"]
```

to:

```python
json_body = mock_request.call_args.kwargs["json"]
```

Continue asserting `name`, `description`, `visibility`, and
`initialize_with_readme` from `json_body`. Retain the existing
`X-Custom-Header` and `Content-Type: application/json` assertions; they prove
the body change does not discard custom headers.

- [ ] **Step 4: Add an explicit DELETE compatibility test.**

Add this test to `TestGitlabTool`:

```python
@patch("requests.request")
def test_make_request_delete_keeps_existing_data_body(
    self, mock_request, gitlab_tool, mock_response
):
    mock_request.return_value = mock_response
    headers = {"Authorization": "Bearer test_token"}
    method_arguments = {"discussion_id": 9}
    url = "https://gitlab.example.com/api/v4/projects/123/discussions/9"

    response = gitlab_tool._make_request("DELETE", url, headers, method_arguments)

    mock_request.assert_called_once_with(
        method="DELETE",
        url=url,
        headers=headers,
        data=method_arguments,
    )
    assert "json" not in mock_request.call_args.kwargs
    assert "params" not in mock_request.call_args.kwargs
    assert response == mock_response
```

- [ ] **Step 5: Run the focused matrix and record the expected RED result.**

Run the narrowest affected tests:

```bash
poetry run pytest \
  tests/codemie_tools/core/vcs/gitlab/test_tools.py \
  tests/codemie_tools/core/vcs/gitlab/test_tools_advanced.py -q
```

Expected before the implementation: the parametrized POST, PUT, and PATCH
cases fail with an assertion showing `data` instead of `json`; the updated
direct POST and complex-body assertions fail for the same reason. GET and
DELETE assertions pass, demonstrating that the RED result is limited to the
intended serialization contract.

### Task 2: Implement the method-scoped request serialization

**Test-first: yes — Task 1's focused matrix is RED until
`GitlabTool._make_request` routes POST, PUT, and PATCH through the `json`
keyword while retaining the GET and DELETE branches.**

**Files:**

- Modify: `src/codemie_tools/core/vcs/gitlab/tools.py`

**Interfaces:**

- Consumes: The method/keyword contract from Task 1.
- Produces: `GitlabTool._make_request` with the unchanged signature and
  `requests.Response` return behavior; all callers through `execute` inherit
  the corrected serialization.

- [ ] **Step 1: Replace only the broad non-GET branch in `_make_request`.**

At `GitlabTool._make_request`, retain the existing GET call and replace the
single `else` branch with explicit POST/PUT/PATCH handling followed by the
unchanged fallback. The resulting control flow should be equivalent to:

```python
if method == "GET":
    return requests.request(
        method=method,
        url=url,
        headers=headers,
        params=method_arguments,
    )
if method in {"POST", "PUT", "PATCH"}:
    return requests.request(
        method=method,
        url=url,
        headers=headers,
        json=method_arguments,
    )
return requests.request(
    method=method,
    url=url,
    headers=headers,
    data=method_arguments,
)
```

Use the existing uppercase method value as-is. Do not add method
normalization, endpoint-specific payload conversion, `json.dumps`, a new
header, retry logic, fallback note creation, or changes to `execute`.
`requests` receives the original mapping and performs the nested JSON
serialization.

- [ ] **Step 2: Run the focused matrix for GREEN.**

Run the same command:

```bash
poetry run pytest \
  tests/codemie_tools/core/vcs/gitlab/test_tools.py \
  tests/codemie_tools/core/vcs/gitlab/test_tools_advanced.py -q
```

Expected: PASS. Specifically verify that:

- POST, PUT, and PATCH each have exactly one `json` keyword containing the
  complete nested `position` mapping.
- None of those calls has `data` or `params`.
- GET still has only `params` for its method arguments.
- DELETE still has only `data` for its method arguments.
- The advanced complex POST still retains custom headers and all body values.
- Existing response formatting and all-method execution tests remain green.

- [ ] **Step 3: Review the diff for accidental surface expansion.**

Confirm the diff changes only the request branch and tests at the listed
symbols. In particular, verify that `_resolve_access_token`, `_build_headers`,
`GITLAB_DEFAULT_HEADERS`, `GitlabTool.execute`, and the specialized
`src/codemie_tools/git/gitlab/` toolkit are untouched.

### Task 3: Align the agent-facing request contract and metadata

**Test-first: yes — metadata assertions for method-specific JSON wording fail
until the stale generic “others use request body” descriptions are updated.**

**Files:**

- Modify: `src/codemie_tools/core/vcs/gitlab/tools.py`
- Modify: `src/codemie_tools/core/vcs/gitlab/tools_vars.py`
- Modify: `tests/codemie_tools/core/vcs/gitlab/test_tools.py`
- Modify: `tests/codemie_tools/core/vcs/gitlab/test_tools_vars.py`

**Interfaces:**

- Consumes: The stable request behavior from Task 2.
- Produces: Consistent schema/tool guidance for callers and regression
  assertions that prevent the descriptions from drifting back to a generic
  non-GET body statement.

- [ ] **Step 1: Add metadata assertions before changing the descriptions.**

Extend `TestGitlabToolVars.test_gitlab_tool_description_content` with exact
contract assertions:

```python
assert "GET uses query params" in description
assert "POST/PUT/PATCH use JSON request bodies" in description
assert "DELETE retains its existing request-body behavior" in description
```

Add a focused `TestGitlabTool.test_gitlab_input_request_encoding_description`
test using the Pydantic v2 field metadata:

```python
def test_gitlab_input_request_encoding_description(self):
    description = GitlabInput.model_fields["query"].description
    assert "POST/PUT/PATCH requests: method_arguments sent as JSON request body" in description
    assert "GET requests: method_arguments sent as query parameters" in description
```

Import `GitlabInput` alongside `GitlabTool` in `test_tools.py` so the test
uses the actual schema field rather than duplicating its description.

- [ ] **Step 2: Run the metadata tests for RED.**

Run:

```bash
poetry run pytest \
  tests/codemie_tools/core/vcs/gitlab/test_tools.py::TestGitlabTool::test_gitlab_input_request_encoding_description \
  tests/codemie_tools/core/vcs/gitlab/test_tools_vars.py -q
```

Expected before the documentation change: the new method-specific wording
assertions fail because the current metadata says only that “others” use a
request body.

- [ ] **Step 3: Update `GitlabInput.query` documentation.**

In the `Field(description=...)` text in
`src/codemie_tools/core/vcs/gitlab/tools.py`, replace the generic line:

```text
POST/PUT/DELETE/PATCH requests: method_arguments sent as request body data
```

with these three explicit lines:

```text
GET requests: method_arguments sent as query parameters
POST/PUT/PATCH requests: method_arguments sent as JSON request body
DELETE requests: method_arguments sent using the existing request-body behavior
```

Also change the `method_arguments` description from “request
parameters_or_body_data” to wording that indicates its interpretation depends
on the method. Keep the accepted method list, endpoint constraint, auth
protection, examples, and response format unchanged.

- [ ] **Step 4: Update `GITLAB_TOOL.description` without changing metadata
  scope.**

In `src/codemie_tools/core/vcs/gitlab/tools_vars.py`, replace:

```text
Automatic request parameter handling (GET uses query params, others use request body)
```

with:

```text
Automatic request parameter handling (GET uses query params; POST/PUT/PATCH use JSON request bodies; DELETE retains its existing request-body behavior)
```

Do not alter the tool name, label, configuration class, authentication
wording, response format, or unrelated examples.

- [ ] **Step 5: Run the metadata tests for GREEN.**

Run the focused metadata tests again:

```bash
poetry run pytest tests/codemie_tools/core/vcs/gitlab/test_tools_vars.py -q
```

Also run the request tests from Task 2 to verify documentation edits did not
change behavior:

```bash
poetry run pytest \
  tests/codemie_tools/core/vcs/gitlab/test_tools.py \
  tests/codemie_tools/core/vcs/gitlab/test_tools_advanced.py -q
```

Expected: PASS for both commands.

### Task 4: Execute repository gates and perform live inline DiffNote validation

**Test-first: no — this is a validation and evidence task, not an additional
implementation task; the failing tests for the code change are defined in
Task 1.**

**Files:**

- No repository file is required for live validation.
- Record commands, outcomes, and any unavailable prerequisites in the
  implementation or MR validation notes; do not add credentials to the
  repository.

**Interfaces:**

- Consumes: The passing mocked request matrix and the configured
  `GitlabConfig`/GitLab integration, when available.
- Produces: A clear separation between executable unit evidence and external
  GitLab API evidence.

- [ ] **Step 1: Run the affected tests and lint gate in the documented order.**

Run:

```bash
poetry run pytest \
  tests/codemie_tools/core/vcs/gitlab/test_tools.py \
  tests/codemie_tools/core/vcs/gitlab/test_tools_advanced.py \
  tests/codemie_tools/core/vcs/gitlab/test_tools_vars.py -q
make ruff
```

The focused pytest command proves the mock-boundary matrix. `make ruff` is the
repository lint/format gate. If the delivery workflow requires broader
validation, run `make test` (all tests) and `make build`, reporting each
command and result exactly. Do not call a skipped gate passed.

- [ ] **Step 2: Verify live-validation prerequisites before making a request.**

The external scenario requires all of the following:

1. A reachable GitLab base URL and a configured PAT with permission to read
   the merge request and create discussions, or a configured OAuth integration
   that can resolve an equivalent access token.
2. A project identifier and merge-request IID for a disposable or explicitly
   approved test MR.
3. A changed file and valid target line in that MR.
4. The MR diff version metadata: `base_sha`, `start_sha`, and `head_sha`,
   plus the correct `new_path`/`old_path` and `new_line`/`old_line`.
5. Permission to remove or otherwise clean up the validation comment after
   evidence is captured, if the target project requires cleanup.

No GitLab-specific environment variable is read directly by `GitlabTool`;
credentials arrive through `GitlabConfig` and its PAT/OAuth configuration.
Do not invent a local environment-variable contract for this test.

- [ ] **Step 3: Build and submit one valid nested inline-discussion payload.**

Use GitLab's merge-request diff/version response to select an actual changed
line. Construct the exact tool query:

```python
# These values come from the configured target MR's diff/version responses.
project_id = configured_project_id
merge_request_iid = configured_merge_request_iid
base_sha = diff_version["base_sha"]
start_sha = diff_version["start_sha"]
head_sha = diff_version["head_sha"]
changed_path = selected_change["new_path"]
changed_line = selected_change["new_line"]

query = {
    "method": "POST",
    "url": f"/api/v4/projects/{project_id}/merge_requests/{merge_request_iid}/discussions",
    "method_arguments": {
        "body": "EPMCDME-13495 JSON body validation",
        "position": {
            "base_sha": base_sha,
            "start_sha": start_sha,
            "head_sha": head_sha,
            "position_type": "text",
            "new_path": changed_path,
            "new_line": changed_line,
        },
    },
}
```

Use `old_path` and `old_line` instead when `selected_change` is a deleted or
old-side line. The variables must be populated from the real target MR; do
not substitute invented SHA or line values. Invoke `GitlabTool.execute(query)`
with the configured tool rather than bypassing the adapter.

- [ ] **Step 4: Capture and verify concrete GitLab evidence.**

Record the tool's formatted response and inspect the returned discussion JSON.
The evidence must show:

- HTTP status `201 Created` from the merge-request discussions endpoint.
- A created note with `type == "DiffNote"` (not a generic MR note).
- A returned `position` matching the target file/line and the submitted SHA
  values.
- No 400 response reporting missing/invalid `position`.
- No fallback request to the merge-request notes endpoint was required. The
  repository currently contains no fallback implementation, so if request
  tracing is available, capture that only the discussion POST occurred; do not
  add fallback logic as part of this ticket.

Clean up the test discussion if project policy permits, preserving the
response/status evidence without exposing the token.

- [ ] **Step 5: Compare the adapter behavior with equivalent curl behavior.**

On the same disposable/test MR, send the same endpoint and JSON body through
curl using the same authorization. Store the body in `payload.json` from the
same values used for the tool query and keep the token in the configured
`GITLAB_TOKEN` secret variable:

```bash
curl --request POST \
  --header "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
  --header "Content-Type: application/json" \
  --data @payload.json \
  "${GITLAB_BASE_URL}/api/v4/projects/${PROJECT_ID}/merge_requests/${MERGE_REQUEST_IID}/discussions"
```

Compare HTTP status, created note type, and returned position. If running both
requests would create duplicate comments, use a disposable MR and distinct
validation markers while keeping the nested body structure and values
equivalent. Never paste a real token into logs or this repository.

- [ ] **Step 6: Report missing external validation as an explicit prerequisite.**

If the repository has no configured GitLab target, credentials, safe merge
request, or valid changed-line metadata, stop after the mocked unit/gate
validation. Report live DiffNote validation as “not run — external GitLab
credentials/scenario unavailable,” while reporting the exact executable pytest
and lint results separately. This is an environment limitation, not evidence
that the live acceptance criterion passed.

## Risks, rollback, and review checkpoints

- **Generic-adapter compatibility risk:** `GitlabTool` accepts arbitrary GitLab
  endpoints, so some undocumented POST/PUT/PATCH caller may have depended on
  form encoding. Mitigation is the explicit method scope, preservation of
  GET/DELETE, review of existing callers/documentation, and the direct
  outbound-call assertions. If a known endpoint requires form data, it must be
  raised as a separate compatibility decision rather than silently expanding
  this ticket.
- **Header interaction risk:** `json=` causes `requests` to encode JSON and
  normally supplies the appropriate content type, but custom headers may
  provide their own value. Do not add or overwrite headers; retain the
  advanced test's custom `Content-Type` assertion.
- **Nested-object regression risk:** Checking only `json` keyword presence
  could miss dropped nested fields. The parameterized test compares the full
  `method_arguments` mapping and the complete `position` object.
- **Live-test side effects:** Creating a discussion mutates a real MR. Use an
  approved disposable/test MR, a uniquely identifiable body, and cleanup
  permissions. Never put credentials in fixtures, test output, or source.
- **Rollback:** If validation exposes an incompatible POST/PUT/PATCH caller,
  revert the dispatcher change in
  `src/codemie_tools/core/vcs/gitlab/tools.py` together with the matching
  `json` expectations and documentation edits, restoring the original
  non-GET `data` path. Do not “roll back” by changing only tests or by
  broadening DELETE to JSON.
- **Review checkpoint:** Before delivery, inspect the diff for only the
  symbols and test files named above, run the focused matrix plus `make ruff`,
  and attach live DiffNote evidence or the explicit missing-prerequisite
  statement.

## Self-review checklist

- Every acceptance criterion maps to a task and a concrete assertion or
  external evidence item.
- The behavior matrix explicitly covers POST, PUT, PATCH, GET, and DELETE.
- RED and GREEN commands, expected failures, mock keywords, and nested
  assertions are spelled out.
- The plan distinguishes the generic request seam from the unrelated
  specialized GitLab toolkit and does not introduce fallback behavior.
- Documentation changes are limited to `GitlabInput` and `GITLAB_TOOL`
  request-contract wording.
- Risks, rollback boundaries, custom-header behavior, and live-test safety are
  explicit.
