# Pipeline evidence for CR-012

Source: GitLab API, project epm-cdme/codemie, merge request !3990,
note id 2365428 posted by `auto_epmd-edp_vcs` at 2026-09-01T09:31:32.908Z.

Retrieved with:

```
GITLAB_HOST=gitbud.epam.com glab api "projects/epm-cdme%2Fcodemie/merge_requests/3990/notes?per_page=100&sort=asc"
```

Ordering that matters: commit `7d6b8f4d4` with the subject `Sonar fix` was pushed at
2026-09-01T09:07:36Z (system note 2365341, "added 1 commit"). The pipeline below was
reported at 2026-09-01T09:31:32Z, i.e. after that commit was already on the branch, and it
passed in full — including `mr-title-validate`.

The 12-task list contains no commit-message or commit-subject validation task at all.

Verbatim task table from that note:

<!-- krci-pipeline-report codebase=codemie -->
## Pipeline [`review-codemie-main-fc48p`](https://portal.core.kuberocketci.io/c/core/cicd/pipelineruns/edp-delivery/review-codemie-main-fc48p) — ✓ Passed

| Status | Task | Duration |
|---|---|---|
| ✓ Passed | report-pipeline-start-to-gitlab | 49s |
| ✓ Passed | get-gitlab-project-id | 16s |
| ✓ Passed | mr-title-validate | 28s |
| ✓ Passed | fetch-repository | 43s |
| ✓ Passed | gitleaks-scan | 20s |
| ✓ Passed | helm-docs | 2m15s |
| ✓ Passed | dockerfile-lint | 2m14s |
| ✓ Passed | helm-lint | 2m9s |
| ✓ Passed | build | 9m13s |
| ✓ Passed | sonar | 3m4s |
| ✓ Passed | dockerbuild-verify | 8m47s |
| ✓ Passed | gitlab-report-pipeline-status | 6s |

> Pushing new commits re-runs this pipeline automatically.
> To re-run it without new commits, comment `/recheck`.
