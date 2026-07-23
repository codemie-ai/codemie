# Webhook Configuration Guide

## Supported Providers

- GitHub
- GitLab

## GitHub Webhooks

1. GitHub repository → Settings → Webhooks.
2. Payload URL: `https://<your-codemie-host>/v1/webhooks/{webhook_id}`.
3. Secret: generate a secret in CodeMie settings and paste it into GitHub.
4. Optional **GitHub Event Filter**: comma-separated event types, e.g. `pull_request,push`.

Security: GitHub signs each delivery with HMAC-SHA256 (SHA-1 fallback for legacy),
sent in `X-Hub-Signature-256`. CodeMie verifies the signature against the stored secret.

## GitLab Webhooks

1. GitLab project → Settings → Webhooks.
2. URL: `https://<your-codemie-host>/v1/webhooks/{webhook_id}`.
3. **Secret token**: generate a token in CodeMie (`GitLab Webhook Secret Token`) and paste
   the same value into GitLab's "Secret token" field.
4. Trigger: enable **Merge request events**.

### Security model (important)

GitLab does **not** sign the payload. It sends the configured secret token *verbatim*
in the `X-Gitlab-Token` header. CodeMie verifies it with a constant-time comparison
against the stored `GitLab Webhook Secret Token`. (This differs from GitHub, which sends
an HMAC signature.) Always use HTTPS so the plaintext token is not exposed in transit.

### MR event filtering

Set **GitLab MR Event Filter** to a comma-separated list of merge-request actions. Only
those actions trigger the workflow; every other MR action returns `400` and does not run.
Leaving the filter empty triggers on **all** MR actions (default behavior).

Supported actions:

- `open` — merge request created
- `close` — merge request closed
- `merge` — merge request merged
- `update` — merge request updated (commits, description, etc.)
- `reopen` — merge request reopened
- `approved` — approval added
- `unapproved` — approval removed

Examples:

- `open` — only on creation.
- `merge` — only when merged.
- `open,merge,reopen` — on creation, merge, or reopen.

## Troubleshooting

- **401 Invalid GitLab token** — the `X-Gitlab-Token` sent by GitLab does not match the
  stored `GitLab Webhook Secret Token`. Re-copy the token into both places (no extra spaces).
- **400 GitLab MR event action '…' is not allowed** — the MR action is not in your filter.
  Add it to **GitLab MR Event Filter**, or clear the filter to allow all actions.
- **Workflow not triggering** — confirm the webhook is enabled, the token matches, and the
  MR action is within the filter. Check CodeMie logs for the delivery and verification result.
