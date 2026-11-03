# R5 verification: A5 aggregate identifiers

No ES metrics data exists locally (see r2-verification.md), so data overlap could not be measured; code evidence only.

## (1) users.id -> lower(email) == session_dims.user_email
- `app_db.load_people` (app_db.py:185) selects `u.id, ..., coalesce(u.email,'')` from `users` keyed by ES `attributes.user_id` (F_USER_ID). So uid -> users.email exists.
- Postgres `user_email` in `_sessions_cte` is `coalesce(nullif(coalesce(nullif(jwt_email,''), nullif(dev_name_max,'')),''), nullif(developer_name,''), '')` (reader.py:~357); the reader filters it as `lower(user_email)` (reader.py:335), i.e. it is not stored lower-cased and may be a `developer_name` (not an email) when no JWT email. Email comes from the CLI-reported `user.email`/git/os identity, not from the Codemie `users` row.
- Result: NOT guaranteed. Equality holds only when the CLI-reported email equals the platform user's email (case-insensitively); a fallback `developer_name` or a different git email breaks it. Locally unverifiable (users has autotest rows, session_dims has `admin@codemie.ai` demo rows, no ES uid).

## (2) ES attributes.repository (lower-cased) == coalesce(nullif(repo_remote,''), nullif(repository,''), '')
- ES side: `owner/repo` parsed by the CLI from the git remote without host (identity.py docstring), `_git/<repo>` for Azure, or last two folder names with no remote; pipeline lower-cases values (pipeline.py:343, identity.py:17).
- Postgres side: `repo_remote` is the raw hook field, stored verbatim as first non-empty value (rollups.py `_earliest("repo_remote")`, no normalisation anywhere in src); the documented example is a full URL `https://gitlab.example.com/org/repo.git` (cli-analytics-events.md:410). `repository` is the raw `cwd`. Neither is lower-cased nor stripped of host/`.git`.
- Result: FAILS. Strings differ in form (URL with host and `.git` vs `owner/repo`) and case; they would only match after a new normaliser, which does not exist.

## (3) session ids
Same conclusion as r2-verification.md: same raw CLI session id (clean + 256-byte cut only).

Verdict: R5 deferred (item 2 fails: session_dims repo key is a raw URL/cwd, not the lower-cased owner/repo ES uses; item 1 also not guaranteed; A5 stays ES-only)

## Owner override (R5 implemented despite the deferral)
The owner decided to implement R5 now, with normalisation instead of raw-key matching: the repository key is derived in Python from `session_dims.repo_remote` / `repository` by mirroring the CLI rule (codemie-code `src/utils/processes.ts` `parseGitRemoteUrl` / `resolveRepositoryName` and `src/utils/paths.ts` `extractRepository`), and the user key is `lower(users.email)` vs `lower(session_dims.user_email)`. Pairs that do not map are dropped (undercount only; the `max` with ES keeps old data).
