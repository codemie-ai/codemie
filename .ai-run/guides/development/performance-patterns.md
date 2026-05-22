# Performance Patterns

## Async I/O

Keep I/O paths async where the surrounding layer is async.

| Avoid | Prefer |
|---|---|
| Blocking calls in request or workflow execution paths | Await async clients or isolate blocking work |
| Per-item remote calls in list operations | Batch or cache when the API contract allows |

Evidence: FastAPI handlers and workflow executor creation are async-aware at `src/codemie/workflows/workflow.py:112`.

## Background And Streaming Work

Use existing queues, background task services, and workflow execution services for long-running work.

| Avoid | Prefer |
|---|---|
| Holding request threads for long-running agent work | Use background task or workflow execution services |
| Creating ad hoc streaming channels | Reuse `ThoughtQueue` and existing thread/message abstractions |

Evidence: workflow execution creates a `ThoughtQueue` for background mode at `src/codemie/workflows/workflow.py:148`.
