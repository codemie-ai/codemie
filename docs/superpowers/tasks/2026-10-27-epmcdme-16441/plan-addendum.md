# Plan addendum (owner review of plan.md, applied after the first implementer pass)

Applies on top of plan.md and spec.md. Same branch, same commit conventions (follow
.ai-run/guides/standards/git-workflow.md; ticket key EPMCDME-16441 in the subject).

1. Task 3, zero-event discards: add a test where the OTLP discard has wire_count == 0. The discard must
   still be logged and counted explicitly (it is otherwise indistinguishable from success).
2. Task 1, filters on the totals branch: verify the session_usage branch of the UNION in
   get_session_model_costs and get_session_detail_model_costs carries the same date, project,
   repository and developer filters as the cost_daily branch (not only session_id scoping). If
   filtering is only via the `sel` CTE and that CTE already applies those filters, say so in a test or
   comment; otherwise add the filters. Add a test where a filter excludes the session and the
   subagent tokens do not leak into the result.
3. Task 2, detail view: check what the session detail endpoint does for a session with no cost rows
   (get_session_detail and its reader calls). If it needs cost_daily rows and returns 404 or empty,
   the list would link to a dead page: fix it or record it as a residual in the storage guide, and
   add a test for the chosen behaviour.
4. Task 4, counter semantics: state in the storage guide that the discard counter is in-process, one
   per worker, reset on restart, so it is not a global number.
5. Task 4, ops step for the hash: document that the new HMAC key defaults to empty, that no
   user-hash field appears in the discard log until the key is configured, and the rollout step
   (set the key per environment). Name the config key in the guide.
