# Orchestrator MCP Failure Propagation Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make orchestrator surface MCP configuration errors to users instead of silently failing and hallucinating responses.

**Architecture:** Modify exception handling in `create_assistant_executors()` to re-raise `MCPToolLoadException` instead of catching and logging it. This allows MCP errors to propagate through the existing API error handler that already properly formats them for users.

**Tech Stack:** Python, LangChain, FastAPI, pytest

---

## File Structure

**Modified files:**
- `src/codemie/service/tools/assistant_factory.py` - Add MCPToolLoadException re-raise logic
- `src/codemie/service/mcp/toolkit_service.py` - Fix debug logging (already has getattr fix)
- Backend logs - Clean up temporary test logs

**No new files needed** - This is a targeted bug fix to existing error handling.

---

### Task 1: Add MCPToolLoadException Import

**Test-first:** no
**Files:**
- Modify: `src/codemie/service/tools/assistant_factory.py:1-30`

- [ ] **Step 1: Add import statement**

Add to the imports section (after line 18, near other service imports):

```python
from codemie.service.mcp.models import MCPToolLoadException
```

- [ ] **Step 2: Verify import resolves**

Run Python import check:
```bash
python -c "from codemie.service.mcp.models import MCPToolLoadException; print('Import OK')"
```

Expected: `Import OK`

- [ ] **Step 3: Commit**

```bash
git add src/codemie/service/tools/assistant_factory.py
git commit -m "fix(orchestrator): add MCPToolLoadException import for error propagation"
```

---

### Task 2: Modify Exception Handler to Re-raise MCP Errors

**Test-first:** no (fixing existing behavior, manual test in Task 3)
**Files:**
- Modify: `src/codemie/service/tools/assistant_factory.py:155-156`

- [ ] **Step 1: Update exception handler**

Replace the current exception handler at line 155-156:

```python
        except Exception as e:
            logger.error(f"[DEBUG-EPMCDME-13499] ERROR Failed to create executor for assistant {assistant.id} ({assistant.name}): {type(e).__name__}: {e}", exc_info=True)
            # Re-raise MCP configuration errors so they surface to users
            if isinstance(e, MCPToolLoadException):
                raise
            # Continue catching other exceptions for backwards compatibility
```

- [ ] **Step 2: Verify syntax**

Run syntax check:
```bash
python -m py_compile src/codemie/service/tools/assistant_factory.py
```

Expected: No output (success)

- [ ] **Step 3: Run existing tests**

```bash
poetry run pytest tests/codemie/service/tools/test_assistant_factory.py -v
```

Expected: All tests pass (no new test failures)

- [ ] **Step 4: Commit**

```bash
git add src/codemie/service/tools/assistant_factory.py
git commit -m "fix(orchestrator): re-raise MCPToolLoadException to surface MCP config errors

When a subagent fails to initialize due to MCP configuration errors
(invalid URL, connection failures), the exception is now re-raised
instead of being caught and logged silently.

This ensures orchestrators properly surface MCP errors to users
instead of building with 0 subagents and generating hallucinated
responses.

Fixes EPMCDME-13499"
```

---

### Task 3: Manual Verification Test

**Test-first:** n/a (manual integration test)
**Files:**
- Read: backend logs
- Execute: curl command to orchestrator API

- [ ] **Step 1: Restart backend server**

Stop current backend process:
```powershell
Get-NetTCPConnection -LocalPort 8080 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

Start backend with clean logs:
```bash
poetry run uvicorn codemie.rest_api.main:app --host=0.0.0.0 --port=8080 --reload > backend.log 2>&1 &
```

Wait 10 seconds for startup.

Expected: Backend listening on port 8080

- [ ] **Step 2: Execute test request via orchestrator**

Login and get token:
```bash
TOKEN=$(curl -s "http://localhost:5173/api/v1/local-auth/login" \
  -H "Content-Type: application/json" \
  --data-raw '{"email":"john_doe@example.com","password":"<test-password>"}' | \
  grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
```

Send orchestrator request:
```bash
curl "http://localhost:5173/api/v1/assistants/cd77131e-ecd2-4725-ab17-f0bfa2cfb7bd/model" \
  -H "Content-Type: application/json" \
  -H "Cookie: codemie_access_token=$TOKEN" \
  --data-raw '{"conversationId":"3334787d-4ee5-4612-af46-b8e9a56fa220","text":"ask subagent to invoke get usage tool","file_names":[],"llmModel":null,"history":[],"mcpServerSingleUsage":false,"stream":false}' \
  -v 2>&1 | grep -A 20 "< HTTP"
```

Expected: HTTP 422 or 500 with error message about MCP connection failure (NOT a 200 with hallucinated success message)

- [ ] **Step 3: Verify error logs**

```bash
grep -a "MCPToolLoadException" backend_error.log | tail -5
```

Expected: Error logs showing MCPToolLoadException was raised and caught at API layer

- [ ] **Step 4: Document test results**

Create verification note:
```bash
cat > docs/superpowers/tasks/2026-07-17-fix-epmcdme-13499-orchestrator-mcp-failure-propagation/verification.txt <<EOF
Manual Test Results ($(date -u +"%Y-%m-%dT%H:%M:%SZ")):

BEFORE FIX:
- Orchestrator returned 200 OK with hallucinated response
- Logs showed "Created 0 total subagent executors"
- No error surfaced to user

AFTER FIX:
- Orchestrator returns 422/500 with proper error message
- MCPToolLoadException propagates to API layer
- User sees clear error about MCP configuration issue

Test passed: ✓
EOF
```

Expected: File created with results

- [ ] **Step 5: No commit** (manual test, no code changes)

---

### Task 4: Clean Up Debug Artifacts

**Test-first:** no
**Files:**
- Delete: `backend.log`, `backend_error.log`, `test_request.log`
- Keep: Debug logging in source code (valuable for future diagnostics)

- [ ] **Step 1: Remove temporary log files**

```bash
rm -f backend.log backend_error.log test_request.log /tmp/orchestrator_response.txt /tmp/login_response.json /tmp/token.txt
```

Expected: Files deleted

- [ ] **Step 2: Remove untracked skill file**

```bash
rm -f .claude/skills/.codemie-sync.json
```

Expected: File deleted

- [ ] **Step 3: Verify clean working tree (except our changes)**

```bash
git status --short
```

Expected output:
```
M  src/codemie/service/tools/assistant_factory.py
M  src/codemie/service/mcp/toolkit_service.py (debug logging fix)
M  src/codemie/agents/langgraph_agent.py (debug logging)
M  src/codemie/service/assistant/assistant_engine_builder.py (debug logging)
?? docs/superpowers/tasks/2026-07-17-fix-epmcdme-13499-orchestrator-mcp-failure-propagation/
```

- [ ] **Step 4: Commit cleanup**

```bash
git status --porcelain | grep "^??" | grep -v "docs/superpowers" | awk '{print $2}' | xargs rm -f
git add -A
git commit -m "chore: clean up temporary test artifacts"
```

---

### Task 5: Review Debug Logging Changes

**Test-first:** no
**Files:**
- Review: Debug logging added during investigation

- [ ] **Step 1: Review debug logging in toolkit_service.py**

```bash
git diff main src/codemie/service/mcp/toolkit_service.py | grep -A 2 -B 2 "DEBUG-EPMCDME-13499"
```

Expected: See debug logging for MCP server processing and error states

Decision: **KEEP** - Provides valuable diagnostics for future MCP issues

- [ ] **Step 2: Review debug logging in langgraph_agent.py**

```bash
git diff main src/codemie/agents/langgraph_agent.py | grep -A 2 -B 2 "DEBUG-EPMCDME-13499"
```

Expected: See agent initialization and handoff logging

Decision: **KEEP** - Helps diagnose orchestrator/subagent issues

- [ ] **Step 3: Review debug logging in assistant_engine_builder.py**

```bash
git diff main src/codemie/service/assistant/assistant_engine_builder.py | grep -A 2 -B 2 "DEBUG-EPMCDME-13499"
```

Expected: See subagent executor creation logging

Decision: **KEEP** - Critical for debugging subagent initialization failures

- [ ] **Step 4: Document decision**

All debug logging added during root cause analysis is being **retained** as it provides valuable diagnostics that helped identify this issue and will help with future debugging.

- [ ] **Step 5: No commit** (decision only, no code changes)

---

## Self-Review Checklist

**Spec coverage:**
- ✓ Re-raise MCPToolLoadException in assistant_factory.py
- ✓ Import MCPToolLoadException
- ✓ Backwards compatibility (other exceptions still caught)
- ✓ Manual verification test
- ✓ Debug logging reviewed

**Placeholder scan:**
- ✓ No TBD, TODO, or "implement later"
- ✓ All code blocks complete
- ✓ All commands include expected output
- ✓ No "add appropriate" without showing what

**Type consistency:**
- ✓ MCPToolLoadException used consistently
- ✓ Exception types match imports

**Gaps:** None identified

---

## Implementation Notes

**Backwards Compatibility:** The fix only re-raises `MCPToolLoadException`. Other exception types continue to be caught and logged, maintaining existing error tolerance for non-MCP failures.

**Debug Logging:** All `[DEBUG-EPMCDME-13499]` logging added during investigation is intentionally retained as it provides valuable diagnostics for production issues.

**Testing Strategy:** Manual integration test is sufficient because:
1. This fixes existing error handling, not new behavior
2. The API layer already has tests for MCPToolLoadException handling
3. The fix is a one-line exception re-raise
4. Automated tests would require complex orchestrator + broken MCP setup
