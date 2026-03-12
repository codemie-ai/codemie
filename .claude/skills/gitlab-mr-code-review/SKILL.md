---
name: gitlab-mr-code-review
description: >-
   Reviews GitLab merge requests by fetching changes, analyzing code against
   project guidelines, and posting inline comments. Use when user asks to
   "review MR", "check merge request", "code review", "review !123",
   or provides a GitLab MR URL.
---

# GitLab MR Code Review

Fetch MR changes → analyze using code review guidelines → get user approval → post inline comments to GitLab.

## Prerequisites

- `glab` CLI authenticated (`glab auth status`)
- Code review guidelines at `.claude/agents/code-reviewer.md`

## Configuration

**Default Project:** `epm-cdme/codemie` (ID: 15667)

For other projects, specify `--repo <PROJECT>` in glab commands.

## Workflow

### Step 1: Extract MR ID

From user input:
- `!123` → `123`
- `https://gitbud.epam.com/epm-cdme/codemie/-/merge_requests/123` → `123`

### Step 2: Fetch MR Data and Checkout Branch

```bash
# Get MR metadata
glab mr view <MR_ID> --repo epm-cdme/codemie

# Get diff refs (required for inline comments)
glab api "projects/:id/merge_requests/<MR_ID>" --repo epm-cdme/codemie | \
  jq '{base_sha: .diff_refs.base_sha, start_sha: .diff_refs.start_sha, head_sha: .diff_refs.head_sha}'

# Store SHAs for Step 5
```

**For large PRs:** Pull and checkout the branch locally:
```bash
git fetch origin
git checkout <source_branch>
```

**Validation:**
- MR `merged`/`closed` → ask user if should continue
- Diff >5000 lines → warn user, focus on critical files

### Step 3: Review Code

1. **Read guidelines:** `.claude/agents/code-reviewer.md`
2. **For large diffs:** Read key changed files directly from checkout (API, Service, Repository layers)
3. **Analyze against Codemie patterns:**
   - Architecture: API→Service→Repository (never skip layers)
   - Security: SQL injection, secrets, input validation
   - Performance: N+1 queries, async patterns (`asyncio.sleep()` not `time.sleep()`)
   - Error handling: `codemie.core.exceptions`
   - Type hints: Python 3.12+ syntax (`str | int | None`)

4. **Classify findings:**
   - **CRITICAL:** Security vulnerabilities, data corruption, crashes
   - **MAJOR:** Performance bottlenecks, missing error handling, type violations

### Step 4: Format Comments for Approval

Present findings with position details:

```markdown
## Proposed Comments for MR !<ID>

### Critical Issues (<count>)

**[1]** 🚨 CRITICAL: <Title>
- **File:** `path/to/file.py`
- **Line:** 45 (new_line)

**Issue:** <description>
**Impact:** <what could go wrong>
**Fix:**
```python
# corrected code
```

---

### Major Issues (<count>)

**[2]** ⚠️ MAJOR: <Title>
- **File:** `path/to/file.py`
- **Line:** 23 (new_line)

**Issue:** <description>
**Fix:** <solution>

---

**SHA References:**
- base_sha: `<sha>`
- start_sha: `<sha>`
- head_sha: `<sha>`
```

### Step 5: User Approval Gate ⚠️

**REQUIRED: Get explicit approval before posting.**

```
Found X critical and Y major issues. Choose action:
1. Approve all - Post all comments
2. Select specific - e.g., "1,3,5"
3. Edit - Modify before posting
4. Cancel - Don't post
```

**Default to Cancel if unclear.**

### Step 6: Post Inline Comments to GitLab

**IMPORTANT:** Inline comments can ONLY be placed on lines that appear in the diff (added/modified/deleted lines).

#### 6.1: Verify Line is in Diff

Before posting, confirm the target line is actually changed:
```bash
# Check if line appears in diff (look for the line content)
glab mr diff <MR_ID> --repo epm-cdme/codemie | grep -A2 -B2 "the_code_on_that_line"
```

If the line isn't in the diff, you cannot post an inline comment there. Use a general MR comment instead.

#### 6.2: Post Inline Comment

For **added/modified lines** (lines with `+` in diff):
```bash
printf '%s' '{
  "body": "**🚨 CRITICAL: Issue Title**\n\nDescription of the issue.\n\n**Impact:** What could go wrong.\n\n**Fix:**\n```python\n# Fixed code\n```",
  "position": {
    "position_type": "text",
    "base_sha": "<BASE_SHA>",
    "start_sha": "<START_SHA>",
    "head_sha": "<HEAD_SHA>",
    "old_path": "<FILE_PATH>",
    "new_path": "<FILE_PATH>",
    "new_line": <LINE_NUMBER>
  }
}' | glab api --method POST "projects/15667/merge_requests/<MR_ID>/discussions" --input - -H "Content-Type: application/json"
```

For **deleted lines** (lines with `-` in diff):
```bash
printf '%s' '{
  "body": "Comment on deleted code",
  "position": {
    "position_type": "text",
    "base_sha": "<BASE_SHA>",
    "start_sha": "<START_SHA>", 
    "head_sha": "<HEAD_SHA>",
    "old_path": "<FILE_PATH>",
    "new_path": "<FILE_PATH>",
    "old_line": <LINE_NUMBER>
  }
}' | glab api --method POST "projects/15667/merge_requests/<MR_ID>/discussions" --input - -H "Content-Type: application/json"
```

#### 6.3: Fallback - General MR Note

If inline comment fails (line not in diff), post as general discussion:
```bash
printf '%s' '{
  "body": "**🚨 CRITICAL: Issue Title**\n\n**File:** `path/to/file.py` **Line:** 411\n\nDescription..."
}' | glab api --method POST "projects/15667/merge_requests/<MR_ID>/discussions" --input - -H "Content-Type: application/json"
```

#### Common Position Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `line_code can't be blank` | Line not in diff | Verify line is actually changed; use general comment if not |
| `position is invalid` | Wrong SHA or missing field | Re-fetch diff_refs; ensure old_path + new_path both present |
| `new_line is invalid` | Line number doesn't exist | Check actual line numbers in the new file version |

### Step 7: Summary

```
✓ Posted X/Y comments to MR !<ID>
  - Critical: <count>
  - Major: <count>
  - Failed: <count> (if any)

View: https://gitbud.epam.com/epm-cdme/codemie/-/merge_requests/<ID>
```

## Error Handling

| Error | Solution |
|-------|----------|
| `glab: command not found` | Install: `brew install glab` |
| `not authenticated` | Run `glab auth login` |
| `MR not found` | Verify ID: `glab mr list --repo epm-cdme/codemie` |
| `HTTP 415` | Add `-H "Content-Type: application/json"` |
| `HTTP 400 position is invalid` | Verify SHAs and position fields match (new_line vs old_line) |
| Comment not inline | Ensure JSON has complete `position` object |

## Examples

**Standard review:**
```
User: "Review MR !2653"
→ Fetch metadata + SHAs
→ glab mr diff 2653 --repo epm-cdme/codemie  
→ Read .claude/agents/code-reviewer.md
→ Analyze: 1 critical, 2 major
→ Show proposals with file/line/position
→ User: "Approve all"
→ Post inline comments via glab api
→ Summary with MR URL
```

**Large MR:**
```
User: "Review !2648" (27 files)
→ Warn: Large diff
→ git checkout source_branch
→ Read key files directly
→ Focus on API, Service, Repository layers
→ Present findings → User approval → Post
```

**Selective posting:**
```
→ Find 5 issues [1]-[5]
→ User: "Post 1,3,5"
→ Post only selected comments
```