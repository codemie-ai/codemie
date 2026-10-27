# Spec: EPMCDME-14273 — GitLab tool image download support

## Intent

`GitlabTool` (`src/codemie_tools/core/vcs/gitlab/tools.py`) reads only `response.text`, which corrupts binary images from GitLab upload URLs. Image responses must instead reach the LLM as base64 multimodal artifacts consumed by the existing `image_artifact_hook` (`src/codemie/agents/image_artifact_hook.py`). The design follows the Jira (`GenericJiraIssueTool`) and `SearchKBTool` pattern. This is a fresh implementation; nothing is taken from the abandoned MRs !4113 and !4222.

## Behaviour

**Output shape.**
- `GitlabTool.response_format = "content_and_artifact"`.
- For non-image responses, `execute()` returns the same `HttpResult` string as today, unchanged byte for byte.
- For image responses, `execute()` returns a response container. It holds the text and an artifact list `[{"data": <b64>, "mime_type": <mime>}]`. `__str__` returns the text, and `to_script_result` returns text only.
- Two overrides follow the Jira tool (`core/project_management/jira/tools.py`): `_limit_output_content` and `_post_process_output_content`. They limit only the text. They always return a 2-tuple: `(text, artifacts)` for images and `(text, None)` on every other path, including errors.
- The workflow `tool_node.py:306` and the script bridge call `execute()` directly. They never see base64.

**URL handling.**
- Relative URLs keep being prefixed with `config.url`, as today (tools.py:185).
- An absolute URL (`http(s)://...`) is accepted only when its scheme and netloc equal those of `config.url`. Its path and query are then used against `config.url`.
- Any other origin returns an error string, and **no HTTP request is sent**. This prevents the token from leaking to a foreign host.
- Redirects keep the default `requests` behaviour, which drops `Authorization` when the host changes. The size guard applies to the final response.

**Image detection** (GET requests only):
1. `Content-Type`, normalised via `normalise_mime` (`codemie_tools/base/file_object.py:26`), is in the shared image mime set. That set covers png/jpeg/jpg/gif/webp.
2. URL fallback is used only when `Content-Type` is missing, is not a `str`, or is `application/octet-stream` or `text/plain`. The path must contain `/uploads/` and end with an extension that maps to the shared set. The mapping uses `mimetypes.guess_type` plus `normalise_mime`.

An explicit JSON or other text Content-Type is never treated as an image.

**Bounded read.**
- GET requests are sent with `stream=True`. Non-image responses then read `.text` exactly as before, so non-image uploads have no size limit.
- Image responses are rejected early when `Content-Length` exceeds `MAX_IMAGE_SIZE_BYTES`. Otherwise they are read via `iter_content` and aborted once the bytes read exceed the limit.
- An oversized image returns the HTTP status line plus a size-limit warning, with no artifact.
- Non-GET requests are unchanged.

**Errors.** A non-2xx response, including one for an image URL, returns the existing error string with no artifact.

**Shared constants.**
- `IMAGE_MIME_TYPES` and `MAX_IMAGE_SIZE_BYTES` (5 MB) move from the Jira tools module (l.48–50) to a new module in `src/codemie_tools/base/`. Jira and GitLab both import them from there.
- Jira behaviour is unchanged. `MAX_IMAGES_PER_RESPONSE` stays in Jira.
- The module name is left to the plan, for example `image_constants.py`.

**Description.** `GITLAB_TOOL.description` and `user_description` in `tools_vars.py` gain:
- an `IMAGE RESPONSES` section;
- an example GET request for a project upload in the form `/<namespace>/<project>/uploads/<secret>/<file>.png` or `/api/v4/projects/:id/uploads/:secret/:filename`;
- a note that absolute URLs must share the configured GitLab origin.

Substrings already asserted in `test_tools_vars.py` stay.

## Acceptance criteria

1. An image is detected by Content-Type, or by the `/uploads/` plus extension URL fallback when Content-Type is missing or generic.
2. Image bytes are read with a bounded read and base64-encoded.
3. For an image, `_run` / `invoke` yields `(text, [{"data", "mime_type"}])`.
4. `GitlabTool.response_format == "content_and_artifact"`, and every path returns a 2-tuple.
5. An image over 5 MB is skipped with a text warning, whether Content-Length is present or absent.
6. The text and JSON output of `execute()` is unchanged, and the existing gitlab tests pass unmodified.
7. A same-origin absolute URL is requested against `config.url`. A foreign-origin absolute URL returns an error, and `requests.request` is not called.
8. `IMAGE_MIME_TYPES` and `MAX_IMAGE_SIZE_BYTES` live in `codemie_tools/base`, Jira imports them, and the Jira tests pass.
9. `tools_vars.py` has the `IMAGE RESPONSES` section and the example request.
10. `tests/codemie_tools/core/vcs/gitlab/test_image_download.py` covers these cases:
    - image yields an artifact;
    - non-image yields a string;
    - oversized image is skipped;
    - URL fallback;
    - same-origin and foreign-origin absolute URLs;
    - error response for an image URL;
    - JSON Content-Type on an `/uploads/*.png` URL is not treated as an image.
11. `make ruff` is clean, and each touched function has C901 complexity below 15.

## Non-goals

- No hand-written redirect loop and no redirect changes.
- No project id or path resolution. Bare `/uploads/` paths are not rewritten.
- No changes to `vcs/utils.py`, `_build_headers`, `PROTECTED_HEADERS`, or the default `Accept` header.
- No image types beyond png/jpeg/jpg/gif/webp. No svg/bmp/tiff.
- No change to non-GET request handling.
- No change to `image_artifact_hook`, workflow `tool_node`, or the script bridge.
- No `str` subclass for the response, and no `unittest.mock` in production code.
- No unrelated file changes, and no docs/superpowers files in the MR.

## Open risks

- It is unverified whether GitLab serves binary uploads when the request sends `Accept: application/json`.
- `stream=True` / `iter_content` is a new pattern in `src/`. Existing `Mock` responses have no `.headers` or `iter_content`, so a non-str Content-Type must be treated as missing.
- A bare `/uploads/...` path against the host root may return 404. Correct usage is left to the tool description.
