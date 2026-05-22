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
| Adapter instructions | Use the Brianna/Jira workflow when a task requires ticket lookup; commit and MR skills require an `EPMCDME-####` ticket. |

## Ticket Adapter

**Status**: configured
**Adapter**: BriAnnA CodeMie assistant via `codemie assistants chat`
**Lookup**: Invoke BriAnnA with the ticket key or URL and request summary, description, acceptance criteria, status, assignee, issue type, and relevant links. Use `--conversation-id` for multi-step flows.
**Create**: Invoke BriAnnA with a complete ticket payload or attach the approved story/local work-item file. Do not use conversational references such as "as drafted" unless the same `--conversation-id` is used and the create call still includes the full final payload.
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
