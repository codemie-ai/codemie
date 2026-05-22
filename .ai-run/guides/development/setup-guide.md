# Setup Guide

## Local Dependencies

Install with Poetry and use Docker Compose for dependent services.

| Avoid | Prefer |
|---|---|
| Running Python commands before dependencies are installed | `poetry install` or `poetry install --sync` |
| Starting the API without PostgreSQL/Elasticsearch when needed | Start required services with Docker Compose |

Evidence: README setup steps are documented at `README.md:83`; Makefile install targets are at `Makefile:15`.

## Running The API

Use the Makefile or README command depending on context.

| Avoid | Prefer |
|---|---|
| Inventing a new app entrypoint | Use `codemie.rest_api.main:app` |
| Changing ports without noting it | Default to port 8080 unless the user asks otherwise |

Evidence: Makefile run target starts Uvicorn at `Makefile:66`; README startup uses the same app at `README.md:96`.
