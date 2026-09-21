# Opt-in GitLab DiffNote validation

The mocked GitLabTool tests verify that a nested `position` object is passed
through `requests` using `json=method_arguments`. The following opt-in test
verifies the GitLab API contract by creating an inline discussion and checking
that the response contains a `DiffNote` with the requested position:

```bash
poetry run pytest \
  tests/codemie_tools/core/vcs/gitlab/test_diffnote_integration.py -q
```

The test is skipped unless all of these environment variables are set:

| Variable | Value |
|---|---|
| `GITLAB_DIFFNOTE_BASE_URL` | Reachable GitLab instance URL |
| `GITLAB_DIFFNOTE_TOKEN` | PAT permitted to create MR discussions |
| `GITLAB_DIFFNOTE_PROJECT_ID` | Project ID or URL-encoded project path |
| `GITLAB_DIFFNOTE_MR_IID` | Approved disposable/test merge-request IID |
| `GITLAB_DIFFNOTE_BASE_SHA` | Target MR diff version `base_sha` |
| `GITLAB_DIFFNOTE_START_SHA` | Target MR diff version `start_sha` |
| `GITLAB_DIFFNOTE_HEAD_SHA` | Target MR diff version `head_sha` |
| `GITLAB_DIFFNOTE_NEW_PATH` | Changed file path from the target MR |
| `GITLAB_DIFFNOTE_NEW_LINE` | Valid changed line in that file |

`GITLAB_DIFFNOTE_BODY` is optional and defaults to an identifiable validation
comment. The target MR must contain the selected line, and the caller should
remove the created discussion after recording the `201 Created`/`DiffNote`
evidence when project policy permits. Never commit or print the token.
