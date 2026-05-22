# X-ray Integration

## QA Adapter Boundary

Keep X-ray behavior in QA integration packages.

| Avoid | Prefer |
|---|---|
| Embedding X-ray GraphQL/API details in app routers | Use the X-ray toolkit or datasource package |
| Reusing unrelated Jira code for X-ray-specific behavior | Keep test-management behavior in QA packages |

Evidence: X-ray datasource code exists under `src/codemie/datasource/xray/`; QA X-ray tools exist under `src/codemie_tools/qa/xray/`.
