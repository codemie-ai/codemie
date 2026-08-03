# Git Workflow

## Branch Naming Convention

Use ticket-first feature branches for project work.

| Avoid | Prefer |
|---|---|
| `feature/random-description` when a Jira task exists | `EPMCDME-12345_short-description` |
| Long or vague branch descriptions | Short kebab-case or snake-compatible summary after the ticket |

Evidence: README quick reference defines `<TICKET-ID>_short-description` at `README.md:162`.

## Commit Message Format

Use `EPMCDME-####: Description` for repository commits.

| Avoid | Prefer |
|---|---|
| `fix: update thing` | `EPMCDME-12345: Update thing` |
| `EPMCDME-12345 update thing` | `EPMCDME-12345: Update thing` |

Evidence: README defines `<TICKET-ID>: Short Description` at `README.md:163`; recent history uses `EPMCDME-11910: ...`, `EPMCDME-12359: ...`, and similar ticket-prefixed commits.

## Merge Strategy

Default to squash merge for review artifacts unless the reviewer or maintainer asks for a different strategy. Keep the final squash commit in the ticket-prefixed format.

| Avoid | Prefer |
|---|---|
| Merging a noisy WIP commit stack | One reviewed squash commit with the Jira ticket prefix |
| Changing merge strategy without review context | Follow the MR reviewer or maintainer instruction |

Evidence: README requires at least one approval and green pipeline before review completion at `README.md:165`.

## Git Side Effects

Only commit, push, branch, or create MRs when the user explicitly asks for git operations.

| Avoid | Prefer |
|---|---|
| Proactive commits after editing files | Report changed files and wait unless git was requested |
| Committing without a work item | Ask for the `EPMCDME-####` ticket first |

```bash
glab mr create \
  --title "EPMCDME-XXXX: Capital sentence" \
  --target-branch main \
  --remove-source-branch \
  --yes
```

Evidence: the current agent entrypoint requires explicit git operations at `AGENTS.md:82`.

## Inline MR Comments via glab

`glab mr note` = general comment only. For diff-anchored inline comments use the discussions API.

**Step 1 — get SHAs** (latest version = index 0):
```bash
glab api "projects/epm-cdme%2Fcodemie/merge_requests/<MR_ID>/versions" \
  | python3 -c "import sys,json; v=json.load(sys.stdin)[0]; print(v['base_commit_sha'], v['head_commit_sha'], v['start_commit_sha'])"
```

**Step 2 — find line number**: from diff hunk `@@ -OLD +NEW_START @@`, count non-`-` lines to target.

**Step 3 — post** (must use `--input` + `Content-Type`; `--field "position[...]"` silently drops nested objects):
```bash
cat > /tmp/comment.json << 'EOF'
{
  "body": "comment text",
  "position": {
    "base_sha": "<base>", "head_sha": "<head>", "start_sha": "<start>",
    "new_path": "src/path/to/file.py", "old_path": "src/path/to/file.py",
    "position_type": "text", "new_line": 123
  }
}
EOF
glab api "projects/epm-cdme%2Fcodemie/merge_requests/<MR_ID>/discussions" \
  --method POST -H "Content-Type: application/json" --input /tmp/comment.json
```

For added lines (`+`): set `new_line` only. Removed lines (`-`): set `old_line` only. Context: set both.

If response `notes[0].position` is `null` — position not resolved, fell back to general comment. Delete with:
```bash
glab api "projects/epm-cdme%2Fcodemie/merge_requests/<MR_ID>/notes/<NOTE_ID>" --method DELETE
```

## Troubleshooting

| Issue | Fix |
|---|---|
| Missing Jira ticket for commit | Ask for the `EPMCDME-####` work item before committing |
| Branch created from the wrong base | Stop and ask before rebasing or recreating branches |
| Existing unrelated changes | Preserve them; do not revert user work |
| Commit hook changes files | Review and stage only intended files before retrying |
| Inline comment posts as general note | Use `--input` + `-H "Content-Type: application/json"`; check `notes[0].position` in response |
