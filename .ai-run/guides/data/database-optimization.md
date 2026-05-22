# Database Optimization

## Pagination And Batching

Avoid loading unbounded data unless the existing API contract explicitly requires it.

| Avoid | Prefer |
|---|---|
| Fetching all rows for list endpoints | Pagination, limit parameters, or batch queries |
| Repeated per-item repository calls | Bulk queries when the repository supports them |

Evidence: recent history includes batching work for users list retrieval; repository tests cover bulk filtering under `tests/codemie/repository/test_user_project_repository_bulk_filter.py`.

## Search Limits

Elasticsearch queries should use explicit sizes and narrowly scoped query bodies.

| Avoid | Prefer |
|---|---|
| Unbounded search defaults | Explicit `size` or limit handling |
| Post-filtering large search results in Python | Push filters into the query where possible |

Evidence: `BaseElasticRepository.get_all` applies a limit-derived size at `src/codemie/repository/base_elastic_repository.py:34`.
