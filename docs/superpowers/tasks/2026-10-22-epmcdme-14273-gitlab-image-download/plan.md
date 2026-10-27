# GitLab Tool Image Download Implementation Plan

**Goal:** `GitlabTool` returns GitLab image responses as base64 artifacts via `content_and_artifact`, with a bounded 5 MB read and same-origin absolute URL support.

**Architecture:** Follow the Jira multimodal pattern (`core/project_management/jira/tools.py:54-75, 503-530`): `execute()` returns the unchanged `HttpResult` string for non-images and a `GitlabMultimodalResponse` container for images; `_limit_output_content` / `_post_process_output_content` overrides always yield a 2-tuple. Shared image constants move to `codemie_tools/base`.

**Tech Stack:** Python, `requests`, LangChain `BaseTool`, pytest + `unittest.mock`.

**Spec:** `docs/superpowers/tasks/2026-10-22-epmcdme-14273-gitlab-image-download/spec.md`

**Commits:** Commit per task using the repository's existing convention, subject `EPMCDME-14273: <imperative>`. Stage task files by explicit path only; never `git add -A` / `git add .`. Never stage `.codemie/codemie-cli.config.json`, `config/llms/llm-azure-config.yaml`, or anything under `docs/superpowers/`.

## Global Constraints

- Image mime set: png, jpeg, jpg, gif, webp only. No svg/bmp/tiff.
- `MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024`.
- Artifact item shape: `{"data": <b64 str>, "mime_type": <mime>}`.
- No changes to `vcs/utils.py`, `_build_headers`, `PROTECTED_HEADERS`, default `Accept` header, redirects, non-GET handling, `image_artifact_hook`, workflow `tool_node`, script bridge.
- No `str` subclass for the response; no `unittest.mock` in production code.
- Existing gitlab and Jira tests pass unmodified. C901 below 15 per touched function: put image logic in helpers.
- Non-image `execute()` output byte-identical to today.

## Review Focus

- Existing `Mock` responses: `.headers` is a `Mock`, not a dict. A non-str Content-Type counts as missing (Task 4 test).
- `test_tools.py:172` asserts the exact GET `requests.request` kwargs of `_make_request`. Add `stream` as an opt-in keyword passed only when `True`, so that direct call stays identical (Task 4).
- `Content-Type: image/png; charset=binary` (params, upper case) must still detect via `normalise_mime` (Task 4 test).
- Content-Length absent and body > 5 MB: abort mid-stream, no artifact (Task 4 test).
- Absolute URL with same host but different scheme or port is foreign (Task 3 test).

---

### Task 1: Shared image constants

**Files:**
- Create: `src/codemie_tools/base/image_constants.py`
- Modify: `src/codemie_tools/core/project_management/jira/tools.py:48-49` (delete the two definitions; import both names from the new module so `jira.tools.MAX_IMAGE_SIZE_BYTES` still resolves for existing tests)
- Test: `tests/codemie_tools/base/test_image_constants.py`

**Interfaces:** Produces `IMAGE_MIME_TYPES: frozenset[str]`, `MAX_IMAGE_SIZE_BYTES: int`.

Test-first: yes — `test_jira_reuses_shared_constants` fails on import of `codemie_tools.base.image_constants`.

- [ ] Write tests: set equals `{"image/png","image/jpeg","image/jpg","image/gif","image/webp"}`; size `== 5 * 1024 * 1024`; `jira.tools.IMAGE_MIME_TYPES is image_constants.IMAGE_MIME_TYPES`. Run, expect ImportError.
- [ ] Create module, update Jira import. `MAX_IMAGES_PER_RESPONSE` stays in Jira.
- [ ] Run `poetry run pytest tests/codemie_tools/base/test_image_constants.py tests/codemie_tools/core/project_management/jira/ -q`; expect PASS.

### Task 2: Response container and tuple contract

**Files:**
- Modify: `src/codemie_tools/core/vcs/gitlab/tools.py` (new class above `GitlabTool` l.77; `response_format` and two overrides on `GitlabTool`)
- Test: `tests/codemie_tools/core/vcs/gitlab/test_image_download.py` (create)

**Interfaces:** Produces
- `class GitlabMultimodalResponse(BaseModel)`: `text: str`, `image_artifacts: list[dict[str, str]]`; `__str__ -> text`; `to_script_result(self, max_bytes: int | None = None) -> ScriptResult` returning text only (mirror Jira l.70).
- `GitlabTool.response_format = "content_and_artifact"`.
- `_limit_output_content(output)`: limits only `.text` for the container (rebuild container), else `super()`.
- `_post_process_output_content(output, *args, **kwargs) -> tuple[str, list | None]`: `(text, artifacts)` for container, `(str_result, None)` for every other path, including errors.

Test-first: yes — `test_invoke_non_image_returns_tuple_with_none` fails because `response_format` is `"content"`.

- [ ] Write tests: `GitlabTool.response_format == "content_and_artifact"`; `_post_process_output_content` on container returns `(text, [artifact])`, on plain string returns `(string, None)`; container `str()` and `to_script_result` hold no base64; `tool.run(...)`/`_run` on a 200 JSON mock response returns a 2-tuple whose text equals today's `execute()` string. Run, expect FAIL.
- [ ] Implement per Interfaces. Run `poetry run pytest tests/codemie_tools/core/vcs/gitlab/ -q`; expect PASS (including `test_models.py` error path through `_run`).

### Task 3: Same-origin absolute URLs

**Files:**
- Modify: `src/codemie_tools/core/vcs/gitlab/tools.py:185` (URL build in `execute`)
- Test: `tests/codemie_tools/core/vcs/gitlab/test_image_download.py`

**Interfaces:** Produces `GitlabTool._build_url(self, raw_url: str) -> str | None`. Relative: `f"{self.config.url}/{raw_url}"` as today. `http(s)://` input: `urlparse` scheme+netloc must equal those of `config.url`; return `config.url` + path (+ `?query`). Else `None`; `execute` then returns an error string naming the rejected origin, before any request.

Test-first: yes — `test_foreign_origin_absolute_url_not_requested` fails because today the URL is prefixed and `requests.request` is called.

- [ ] Write tests (`@patch("requests.request")`): same-origin `https://gitlab.example.com/api/v4/projects?x=1` requests `https://gitlab.example.com/api/v4/projects?x=1`; `https://evil.com/...`, `http://gitlab.example.com/...`, `https://gitlab.example.com:8443/...` return error text and `mock_request.assert_not_called()`; relative URL unchanged. Run, expect FAIL.
- [ ] Implement. Run gitlab tests; expect PASS.

### Task 4: Image detection, bounded read, artifacts

**Files:**
- Modify: `src/codemie_tools/core/vcs/gitlab/tools.py:96-115` (`_make_request`), `execute` response handling (l.~190-199), new helpers
- Test: `tests/codemie_tools/core/vcs/gitlab/test_image_download.py`

**Interfaces:**
- Consumes: Task 1 constants, Task 2 container, `normalise_mime` (`codemie_tools/base/file_object.py:26`).
- `_make_request(..., method_arguments: dict, stream: bool = False)`: add `stream=True` to the GET call only when `stream` is true. `execute` passes `stream=True` for GET.
- `_detect_image_mime(response, url: str) -> str | None`: Content-Type (only if `str`) normalised and in set; else fallback only when Content-Type is missing / non-str / `application/octet-stream` / `text/plain`, path contains `/uploads/` and `normalise_mime(mimetypes.guess_type(path)[0])` is in set.
- `_read_image_bounded(response) -> bytes | None`: `None` if `Content-Length` > limit; else accumulate `iter_content(chunk_size=65536)`, `None` once total > limit.
- Image path only on 2xx GET with detected mime: returns container with text `HTTP: GET <url> -> <status> <reason> Image downloaded (<mime>, <n> bytes)` and artifact `{"data": b64, "mime_type": mime}`. Oversized: status line plus `Image exceeds 5 MB limit; skipped.`, no artifact. Non-2xx or non-image: existing `HttpResult(... response.text ...)`.

Test-first: yes — `test_image_content_type_returns_artifact` fails because `execute` returns `response.text`.

- [ ] Write tests (mocks set `headers` dict, `iter_content`, `status_code`, `reason`, `text`): image/png Content-Type yields artifact whose `data` b64-decodes to the bytes, and `_run` yields `(text, [artifact])`; `image/PNG; charset=binary` detected; JSON body yields plain string; octet-stream on `/group/proj/uploads/abc/x.webp` detected as `image/webp`; `application/json` on `/uploads/abc/x.png` not an image; oversize via Content-Length (iter_content not called) and via stream without Content-Length both return warning, no artifact; 404 on an image URL returns existing error string and `(text, None)`; `.svg` upload not an image; POST call has no `stream` kwarg. Run, expect FAIL.
- [ ] Implement. Run `poetry run pytest tests/codemie_tools/core/vcs/gitlab/ -q`; expect PASS with existing files unmodified.

### Task 5: Tool description

**Files:**
- Modify: `src/codemie_tools/core/vcs/gitlab/tools_vars.py` (`GITLAB_TOOL.description`, `user_description`)
- Test: `tests/codemie_tools/core/vcs/gitlab/test_image_download.py`

Test-first: yes — `test_description_documents_image_responses` fails because `IMAGE RESPONSES` is absent.

- [ ] Write test: both texts contain `IMAGE RESPONSES`; description contains `/uploads/` example (`{"method": "GET", "url": "/<namespace>/<project>/uploads/<secret>/<file>.png"}` and `/api/v4/projects/:id/uploads/:secret/:filename`), the 5 MB limit, and a note that absolute URLs must share the configured GitLab origin. Run, expect FAIL.
- [ ] Add the section; keep every substring asserted in `test_tools_vars.py`. Run gitlab tests; expect PASS.
