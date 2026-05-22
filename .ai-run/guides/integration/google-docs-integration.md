# Google Docs Integration

## Document Datasources

Keep Google Docs ingestion in datasource/tool integration packages.

| Avoid | Prefer |
|---|---|
| Parsing Google Docs in generic API handlers | Use `google_doc` datasource or file-analysis utilities |
| Hardcoding Google API clients in feature code | Use existing Google integration boundaries |

Evidence: Google Docs datasource package exists under `src/codemie/datasource/google_doc/`; Google API dependencies are declared at `pyproject.toml:97`.
