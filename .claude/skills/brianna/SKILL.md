---
name: brianna
description: Business Analyst Assistant - expert to work with Jira. Used for creating/getting/managing Jira tickets in EPM-CDME project (Epics, Stories, Tasks, and Bugs). Main role is to analyze requirements from the request, clarify additional questions if necessary, generate requirements with the description structure defined in the prompt and additional details from the request, and create tickets in EPM-CDME project Jira. The Assistant uses Generic Jira tool for Jira tickets creation.
---

# BriAnnA

Business Analyst Assistant - expert to work with Jira. Used for creating/getting/managing Jira tickets in EPM-CDME project (Epics, Stories, Tasks, and Bugs). Main role is to analyze requirements from the request, clarify additional questions if necessary, generate requirements with the description structure defined in the prompt and additional details from the request, and create tickets in EPM-CDME project Jira. The Assistant uses Generic Jira tool for Jira tickets creation.

## Instructions

1. Extract the user's message from the conversation context
2. Execute the command with the message
3. Return the response

**File attachments are automatically detected** - any images or documents uploaded in recent messages are automatically included with the request.

**ARGUMENTS**: "message"

**Command format:**
```bash
codemie assistants chat "f14e801a-1e6c-4d2a-ab70-f59795c11a1b" "message"
```

## Examples

**Simple message:**
```bash
codemie assistants chat "f14e801a-1e6c-4d2a-ab70-f59795c11a1b" "help me with this"
```

**ARGUMENTS**: "check this code" --file /path/to/your/script.py

**With file attachment:**
```bash
codemie assistants chat "f14e801a-1e6c-4d2a-ab70-f59795c11a1b" "analyze this code" --file "script.py"
```

**With multiple files:**
```bash
codemie assistants chat "f14e801a-1e6c-4d2a-ab70-f59795c11a1b" "review these files" --file "file1.png" --file "file2.py"
```