# Technical Research

**Task**: gitlab vcs-tool multimodal-artifacts
**Generated**: 2026-10-22
**Research path**: filesystem

---

## 1. Original Context

# Handoff: EPMCDME-14273 – GitLab tool image download support

## Goal
Implement https://jiraeu.epam.com/browse/EPMCDME-14273 from scratch on a fresh branch off current `main`, and open a new MR under my name. Two earlier MRs by another author were closed and abandoned: !4113 and !4222 (gitbud.epam.com/epm-cdme/codemie). Do not cherry-pick their commits.

## Ticket (size S, In Progress, assigned to me, due 2026-10-31)
`GitlabTool` in `src/codemie_tools/core/vcs/gitlab/tools.py` reads only `response.text`, which corrupts binary images from GitLab upload URLs (`/uploads/<secret>/screenshot.png`). It should return base64 multimodal artifacts so the agent graph's `image_artifact_hook` can inject them into the LLM context. Follow the existing Jira tool pattern.

Acceptance criteria:
1. Detect images by `Content-Type` (`image/png|jpeg|gif|webp`) OR a URL fallback (path contains `/uploads/` and ends with a supported image extension).
2. Read `response.content` as bytes and base64-encode it.
3. Return `(text, [{"data": "<b64>", "mime_type": "image/png"}])`.
4. Set `response_format = "content_and_artifact"` on `GitlabTool`, matching `GenericJiraIssueTool` and `SearchKBTool`.
5. Images over 5 MB are skipped, with a textual warning returned instead.
6. Ordinary text and JSON behavior is unchanged.
7. Unit tests in `tests/codemie_tools/core/vcs/gitlab/test_image_download.py` cover:
   - an image response returns an artifact
   - a non-image response returns a string
   - an oversized image is skipped
   - URL-path fallback detection
   - absolute URL handling
   - an error response for an image URL
8. Update `GITLAB_TOOL.description` and `user_description` in `tools_vars.py` with an `IMAGE RESPONSES` section and an example request.

## Analysis of the old MRs (judged from MR diffs only)
**!4113** (closed 2026-09-11):
- 496 changed files (carries ~20 unrelated main commits), merge conflicts, failed build.
- Reviewer Critical: a PAT could be sent to an attacker-controlled absolute URL. Major: 5 MB limit checked only after whole body in memory.

**!4222** (closed 2026-10-08, stale):
- ~270 added lines in tools.py, 19 commits, scope creep: `_resolve_project_id_or_path` running `git config --get remote.origin.url` via subprocess; hand-written redirect loop (replayed POST bodies on redirect); `utils.py` changes (`PROTECTED_HEADERS`, `auth_type` in `_build_headers`) and PAT header rewrite; unrelated `google_doc_datasource_processor.py` change; `GitlabResponse(str)` subclass with `.artifacts` plus overrides of `_limit_output_content` / `_post_process_output_content` (criterion 4 wants a plain tuple); `except (TypeError, AttributeError): pass` for mocked tests; ~18 committed docs/superpowers files.
- Open review problems: 5 MB guard applied to every /uploads/ or image-extension URL (non-image upload >5MB blocked); non-GET responses buffered before size check; unresolved: remove magic numbers, reuse supported extensions, reuse existing mime maps, no unittest.mock in production code; stream-by-Content-Type thread marked resolved but diff only streams by URL.

## What to reuse from !4222 (ideas and tests, not commits)
- Test scenarios, rewritten against a clean implementation.
- Strict origin check (scheme and netloc) for absolute URLs.
- Two-way detection; URL fallback only when Content-Type missing or generic (application/octet-stream, text/plain), never explicit JSON/text.
- Bounded 5 MB read via iter_content and Content-Length, limited to responses that can be images.
- `normalise_mime` from `codemie_tools/base/file_object.py`. Check for existing mime and extension maps before defining new ones.

## Plan
1. Branch from main (done: EPMCDME-14273_gitlab-image-download).
2. Read Jira tool pattern (GenericJiraIssueTool, SearchKBTool) for content_and_artifact tuple and image_artifact_hook.
3. Minimal change in gitlab/tools.py and tools_vars.py. No redirect loop, project resolution or header changes. Harden absolute-URL origin check only if needed, as a separate decision.
4. Tests last.
5. Run `poetry run pytest tests/codemie_tools/core/vcs/gitlab/` and ruff. McCabe complexity gate < 15.
6. MR titled `EPMCDME-14273: Add gitlab tool image download support`.

Research focus: current GitlabTool request flow (how absolute URLs, PAT headers, redirects are handled today in tools.py/utils.py); how BaseTool output post-processing (_limit_output_content etc.) treats tuple outputs with response_format content_and_artifact; Jira tool image path and image_artifact_hook contract (artifact dict shape); existing mime/extension maps and normalise_mime; existing gitlab tests and fixtures/mocking style; requests streaming usage elsewhere.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie_tools/core/vcs/gitlab/tools.py` (199 lines) — `GitlabInput`, `GitlabTool(CodeMieTool)`. `script_callable = True`, `tokens_size_limit = 70_000`, no `response_format` set (default `"content"`).
  - `execute()` (l.148–199): parses JSON query, unwraps double-wrapped `{"query": {...}}`, requires `method`/`url`, builds `url = f"{self.config.url}/{query.get('url')}"` (l.185) — **always prefixes config URL; no absolute-URL branch exists today**. An absolute URL becomes `https://host/https://...`. Leading `/` in `/api/v4/...` yields `host//api/v4/...` (works today).
  - Headers: `_build_headers(GITLAB_DEFAULT_HEADERS, access_token, custom_headers)`; `GITLAB_DEFAULT_HEADERS = {"Accept": "application/json"}`.
  - `_make_request()` (l.96–115): `requests.request(...)` — GET → `params=`, POST/PUT/PATCH → `json=`, else `data=`. No `stream`, no `timeout`, no explicit `allow_redirects` (requests default follows redirects; requests strips `Authorization` on cross-host redirect).
  - Returns `HttpResult(method, url, status, reason, response.text, "spaced")` — the `HTTP: {method} {url} -> {status} {reason} {body}` string. Non-2xx is returned as data, not raised.
  - `_resolve_access_token()`: OAuth via `GitLabOAuthTokenManager` when `config.auth_type == "oauth"`, else `config.token`.
- `src/codemie_tools/core/vcs/utils.py` — `PROTECTED_HEADERS = frozenset({'authorization'})`, `_merge_custom_headers` (drops `Authorization` overrides with warning), `_build_headers` (sets `Authorization: Bearer <token>`), `file_response_handler` (unused by GitLab).
- `src/codemie_tools/core/vcs/gitlab/tools_vars.py` — `GITLAB_TOOL = ToolMetadata(name="gitlab", description=..., user_description=..., label="Gitlab", settings_config=True, config_class=GitlabConfig)`. Description sections: INPUT FORMAT, REQUIREMENTS, FEATURES, RESPONSE FORMAT, SECURITY, EXAMPLES.
- `src/codemie_tools/core/vcs/toolkit.py:32` — `Tool.from_metadata(GITLAB_TOOL, tool_class=GitlabTool)`.

### Architecture and Layers Affected
- Tool layer (`codemie_tools.core.vcs.gitlab`): `GitlabTool`, `tools_vars.py`.
- Tool base (`codemie_tools.base.codemie_tool.CodeMieTool`): `_run` → `execute` → `_limit_output_content` → `_post_process_output_content` (l.142–159, 276–330).
- Agent runtime (read-only consumer): `codemie/agents/supervisor/pre_model_hooks.py::_image_artifact_pre_model_hook`, wired for all agents in `codemie/agents/langgraph_agent.py:360`.
- Workflow runtime (consumer): `codemie/workflows/nodes/tool_node.py:306` calls `tool.execute(**tool_input)` directly — bypasses `_run` and post-processing.
- Script bridge: `CodeMieTool.run_for_script` / `to_script_result` (l.64–96) — calls `_execute_for_script` → `execute`; output converted via `ScriptResultSource.to_script_result` if implemented, else `_to_script_value` (tuple → `str(output)`).

### Integration Points
- GitLab REST API via `requests` (sync). Upload URLs: relative paths like `/uploads/<secret>/x.png` are project-relative in GitLab markdown.
- `image_artifact_hook` contract (`src/codemie/agents/image_artifact_hook.py`): `ToolMessage.artifact` must be a `list`; each item a dict with `"data"` (base64 str) and `"mime_type"`; optional `"text"` used as caption; other keys ignored. Non-list artifact (incl. `None`) is ignored.
- Artifacts persisted for replay: `agents/callbacks/agent_invoke_callback.py:321–329` stores list artifacts into `replay_metadata["image_artifacts"]`; `service/conversation/history_projection_service.py:437` restores `tool_msg.artifact`.
- `normalise_mime(mime)` in `src/codemie_tools/base/file_object.py:26` — `mime.split(";")[0].strip().lower()`.

### Patterns and Conventions
- **Base output pipeline does not support a raw tuple from `execute`.** `CodeMieTool._limit_output_content` calls `encoding.encode(str(output))` and, over the limit, returns a truncated *string*; `_post_process_output_content` returns str or `json.dumps(output)`. A tuple from `execute` would be token-counted including the base64 blob and serialized to a JSON list string. LangChain `content_and_artifact` requires `_run` to return a 2-tuple, else it raises.
- Both existing `content_and_artifact` tools use a **container + two overrides**:
  - Jira (`core/project_management/jira/tools.py`): `JiraMultimodalResponse(BaseModel)` with `text`, `image_attachments`, private `_http_result`, `__str__` → text, `to_script_result` (images never reach scripts). `_limit_output_content` limits only `.text`; `_post_process_output_content` returns `(text, artifacts)` or `(text, None)` for every path, including plain strings. Constants l.48–50: `IMAGE_MIME_TYPES = {"image/png","image/jpeg","image/jpg","image/gif","image/webp"}`, `MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024`, `MAX_IMAGES_PER_RESPONSE = 5`. Download uses `response.content` (non-streamed), size filtered from attachment metadata beforehand.
  - SearchKB (`agents/tools/kb/search_kb.py:98–240`): `SearchKBResponse(text, image_artifacts)`, same override pair; artifact dict `{"data": b64, "mime_type": ...}`.
- Existing mime/extension maps: Jira `IMAGE_MIME_TYPES` (module constant, importable); `file_analysis/email/tools.py:70–85` ext→mime map and `_IMAGE_EXTENSIONS` (private, includes bmp/tiff); azure_devops loaders have their own `IMAGE_MIME_TYPES` frozensets. stdlib `mimetypes.guess_type` used widely. No shared image-extension constant in `codemie_tools/base`.
- Streaming (`iter_content`/`stream=True` with `requests`): **no usage in `src/`** — new pattern for this repo.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/agents/custom-tool-creation.md`, `tool-overview.md`, `integration/external-services.md`, `development/security-patterns.md` mention GitLab generally; none documents `content_and_artifact` or image artifacts.
- `.ai-run/guides/quality-gates.md`: `make ruff` (format + `ruff check --fix` + `ruff check`).

### Architectural Decisions
- Inline docstrings are the only record: Jira `_post_process_output_content` and SearchKB state that artifacts are consumed by the pre-model hook; `langgraph_agent.py:358` comment "Runs for all agents, but does nothing unless a tool returns image artifacts".
- `HttpResult` docstring: `str(result)` must stay byte-identical to the legacy f-string (LLM-facing output unchanged).

### Derived Conventions
- Multimodal tool output = response container object + `_limit_output_content`/`_post_process_output_content` overrides; container exposes `to_script_result` so scripts get text only.
- Module-level constants for limits/mime sets (addresses "magic numbers" review point).

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie_tools/core/vcs/gitlab/`: `conftest.py` (fixtures `gitlab_config` url `https://gitlab.example.com` token `test_token`, `gitlab_tool`, `mock_response` (200, `.text` JSON), `mock_error_response` (404)); `test_tools.py` (252 l., `@patch("requests.request")`, asserts on `execute()` string), `test_tools_advanced.py`, `test_tools_vars.py` (asserts description sections and phrases), `test_models.py` (`tool.invoke({"query": ...})` asserts error string — goes through `_run`), `test_tools_resolve_per_user.py`, integration tests.
- Jira reference tests: `tests/codemie_tools/core/project_management/jira/test_image_attachments.py` (container `__str__`, oversized skip, tuple-return of `_post_process_output_content` for images / no images / plain string).
- Hook: `tests/codemie/agents/test_image_artifact_hook.py`; KB: `tests/codemie/agents/tools/kb/test_search_kb.py`.

### Testing Framework and Patterns
- pytest + `unittest.mock` (`Mock`, `patch("requests.request")`), class-based test groups, fixtures in conftest. Mock responses set `.status_code`, `.reason`, `.text`; no `.content`, `.headers`, or `iter_content` set today (a bare `Mock` returns `Mock` for these).

### Coverage Gaps
- No test of `GitlabTool` via `_run`/LangChain `ToolMessage` path (tuple contract).
- No test for absolute URL input, `Content-Type` handling, binary bodies, or redirects.
- `test_image_download.py` does not exist.

---

## 5. Configuration and Environment

### Environment Variables
- `config.TOOL_TOKENS_SIZE_LIMIT` (base default; GitLab overrides to 70 000). No GitLab-image-specific env vars.

### Configuration Files
- `src/codemie_tools/core/vcs/gitlab/models.py` — `GitlabConfig` (`url`, `token`, `auth_type`, `integration_id`, `acting_user_id`).
- `pyproject.toml:250` — ruff `max-complexity = 16` (C901). Handoff's gate "< 15" is stricter than the configured value.
- `pyproject.toml` — `langchain-core = "1.3.3"`, `langchain = "1.2.15"`.

### Feature Flags and Deployment Concerns
- None. Token comes from stored PAT or OAuth token manager; no secrets changes.

---

## 6. Risk Indicators

- Criterion 3/4 vs base pipeline: returning a plain tuple from `execute()` breaks `CodeMieTool._limit_output_content` (stringifies tuple, counts base64 tokens, may truncate to a string) and `_post_process_output_content` (json.dumps). Speculative: overrides of both methods (as Jira/SearchKB do) or a container are needed regardless of the handoff's "plain tuple" wording; the plain tuple can be the final `_run` return.
- Speculative: once `response_format = "content_and_artifact"`, every path (text, JSON, errors) must return a 2-tuple from `_post_process_output_content`, else LangChain raises.
- Workflow `tool_node.py:306` calls `execute()` directly; a tuple there leaks base64 into workflow state/output. Script bridge (`script_callable = True`) converts a tuple with `str()`. Speculative: container with `__str__`/`to_script_result` keeps both paths text-only.
- Absolute URLs not supported today (l.185 always prefixes). Adding them introduces the PAT-to-foreign-host risk flagged Critical on !4113; origin check (scheme+netloc vs `config.url`) is the safety boundary.
- Redirects: requests follows by default and strips `Authorization` on host change; GitLab uploads may redirect to object storage (presigned URL) — streamed size guard must apply to the final response.
- Streaming (`stream=True`, `iter_content`) is new in this repo; existing tests' `Mock` responses lack `.headers`/`.content`/`iter_content`, so detection code must not break them (e.g. `Mock().headers.get(...)` returns a `Mock`, not a str).
- Upload URL shape: `/uploads/<secret>/file` in GitLab markdown is project-relative (`/<group>/<project>/uploads/...` or `/api/v4/projects/:id/uploads/:secret/:filename`); bare `/uploads/...` against host root may 404. Not resolved by code today.
- Mime/extension reuse: Jira `IMAGE_MIME_TYPES` includes `image/jpg`; email ext map is private and includes bmp/tiff (outside criterion 1). No shared constant — reuse vs new constant is a design choice.
- `Accept: application/json` default header sent to upload URLs; GitLab may still serve binary, but behavior is unverified.
- `test_tools_vars.py` asserts description substrings; editing description must keep them.
- C901 gate: `execute()` already has several branches; image path should live in helpers.

---

## 7. Summary for Complexity Assessment

The change sits in one tool package: `src/codemie_tools/core/vcs/gitlab/tools.py` (request flow, response handling, output overrides) and `tools_vars.py` (description text), plus a new test file. Consumers (`image_artifact_hook`, pre-model hook wiring, artifact replay persistence) already exist and accept the `[{"data", "mime_type"}]` list shape, so no agent-runtime change is indicated. Two reference implementations (Jira, SearchKB) show the exact pattern: response container with `__str__` and `to_script_result`, plus `_limit_output_content` and `_post_process_output_content` overrides that always return a 2-tuple.

Novelty is moderate: bounded streaming reads (`stream=True`/`iter_content`) have no precedent in `src/`, and absolute-URL support does not exist today, so origin validation is new security-sensitive logic. The "plain tuple" wording in criterion 3/4 conflicts with how `CodeMieTool._run` processes `execute()` output; the base pipeline needs overrides either way. Workflow `tool_node` and the script bridge call `execute()` directly, which constrains what `execute()` may return.

Tests: solid existing GitLab unit suite using `@patch("requests.request")` and `Mock` responses, but nothing covers the `_run`/artifact path, absolute URLs, Content-Type, or binary bodies. Key risks: PAT leakage on absolute URLs, LangChain tuple contract on all paths, memory bounding of large bodies, Mock-based tests interacting with header/stream checks, and project-relative upload URL semantics. Size S-to-M is consistent with the surface.

---

## 8. External References

- None named by the task as a readable source. Jira EPMCDME-14273 and MRs !4113 / !4222 (gitbud.epam.com) are URLs; they were not fetched — the handoff text in Section 1 already summarizes their content.
