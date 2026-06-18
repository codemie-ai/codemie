# Project Context

## Project Identity

| Field | Value | Source |
|---|---|---|
| Project name | CodeMie backend | README.md:19 |
| Repository/package | codemie | pyproject.toml:1 |
| Project code/key | EPMCDME | README.md:162 |

## Work Item Tracker

| Field | Value |
|---|---|
| Provider | Jira |
| Key/prefix | EPMCDME |
| Adapter status | configured |
| Adapter instructions | Use the codemie-jira-assistant/Jira workflow when a task requires ticket lookup; commit and MR skills require an `EPMCDME-####` ticket. |

## Ticket Adapter

**Status**: configured
**Adapter**: `codemie-jira-assistant` skill — invoke via the `Skill` tool with the approved story content or file path as the argument. Do not hardcode the underlying command or assistant ID.
**Lookup**: Invoke the `codemie-jira-assistant` skill with the ticket key or URL and request summary, description, acceptance criteria, status, assignee, issue type, and relevant links.
**Create**: Invoke the `codemie-jira-assistant` skill with the complete ticket payload or the approved story file attached. Do not use conversational references such as "as drafted" unless the full final payload is included.
**Output**: Jira ticket key and URL.

## Source Control And Review

| Field | Value |
|---|---|
| Provider | GitLab |
| Repository remote | git@gitbud.epam.com:epm-cdme/codemie.git |
| Default target branch | main |
| Review artifact type | MR |

## MR Adapter

**Status**: configured
**Adapter**: GitLab CLI (`glab`) via project MR skills
**Instructions**: Use `.ai-run/guides/standards/git-workflow.md` for branch, commit, push, and MR rules before creating review artifacts.
