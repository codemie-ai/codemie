# Confluence Integration

## Datasource Processing

Keep Confluence ingestion in datasource processors and related services.

| Avoid | Prefer |
|---|---|
| Handling Confluence parsing in routers | Extend datasource processor behavior |
| Duplicating common datasource flow | Reuse base datasource processing patterns |

Evidence: Confluence datasource processor exists at `src/codemie/datasource/confluence_datasource_processor.py`; base datasource processor exists at `src/codemie/datasource/base_datasource_processor.py`.
