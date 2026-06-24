# Work Item — AgentCore Invocation Fix & Config Expansion

**Status**: In Progress
**External Ticket**: EPMCDME-12240
**Branch**: EPMCDME-12240_agentcore
**Slug**: agentcore-invocation-fix-and-config-expansion

## Summary

Fix the broken AWS AgentCore endpoint invocation and expand the `configuration_json`
schema to give users control over streaming vs JSON responses and response field extraction.

## Acceptance Criteria

- [ ] AWS `invoke_agent_runtime` is called with the correct ARN (guard and call use the same ARN field)
- [ ] Users can specify `is_stream` in configuration to switch between SSE and JSON `accept` headers
- [ ] Users can specify `response_path` to extract the response text from any JSON key or nested path
- [ ] Users can specify `thought_path` to extract reasoning/thinking content
- [ ] Users can specify `chunk_path` to extract text from each SSE chunk
- [ ] Import API accepts the new response config fields; existing imports without them continue to work
- [ ] DB model stores the new fields; existing records remain valid (all new fields Optional)

## Linked Artifacts

- docs/superpowers/runs/20260528-1438-EPMCDME-12240_agentcore/requirements.md

## History

| Timestamp | Event | Notes |
|---|---|---|
| 2026-05-28T14:38:00Z | work_item.created | Created from free-form input tied to branch EPMCDME-12240_agentcore |
