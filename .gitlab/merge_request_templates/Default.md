## Overview

<!-- 1–3 sentences: what problem this solves and for whom. No implementation details. -->

## How it is solved

<!-- 3–6 bullets: the shape of the solution, where the logic lives, the main design choice.
     A reviewer should get a rough picture of the implementation before opening the diff. -->

-

## Reviewer notes

<!-- Only what a reviewer needs to review and merge: risky or non-obvious places, deviations from the ticket or spec,
     rollout steps. Not a list of everything that changed, not lint or cleanup fixes, not the history of this MR.
     Delete the lines that do not apply. -->

- **Not directly related to the task:** <!-- tooling, config, refactors bundled in, and why -->
- **Risky or non-obvious places:** <!-- concurrency, security, migrations, compatibility -->
- **Deviations from the ticket/spec:** <!-- and why -->
- **Rollout / config:** <!-- new env vars, customer-config, feature flags, required env changes -->
- **Follow-ups / known limitations:** <!-- what is deliberately not done -->

## Test harness

<!-- Keep this heading and the code block below: the MR compliance bot checks for them (screenshots are not accepted).
     Paste only results that were actually produced. -->

Command: `make test-harness`

```
<paste the terminal summary. All green: a single line, e.g. `197 passed, 4 skipped, 5 rerun`>
```

### Failed tests

<!-- Delete this subsection if nothing failed. Group tests by cause: one `####` block per cause.
     Several failed tests usually share one cause and one fix MR, so do not repeat the reasoning per test. -->

#### <cause in a few words>

- **Tests:** `path::test_a`, `path::test_b`
- **Why this is not caused by this MR:** <!-- evidence, e.g. the same tests fail on main; the cause is in the test or the data -->
- **Fix:** <!-- link to the MR in codemie-sdk, or N/A -->

- [ ] I checked that these failures are not caused by my local setup: all required environment variables, credentials and config overrides are set correctly, and the result on `main` was taken with that same complete config (a run on `main` with missing variables proves nothing).

### Additional tests (optional)

<!-- Tests run beyond the sanity set: what was run (path, marker, command) and the result. Details are optional. -->

## Verified locally

<!-- Checks done by hand or against a real environment, not unit tests. One `###` subsection per scenario:
     what was done, what was expected, what happened. Any length.
     Describe only the verification itself. Do not list bugs found or environment problems fixed while preparing
     the setup; those are not verification results and belong in the commits or the ticket if they matter. -->

### <scenario name>

## Documentation

<!-- Link to the PR in the documentation repo, or `N/A — no user-facing change`. -->

## Checklist

- [ ] Self-reviewed
- [ ] Tests written/updated
- [ ] No breaking changes (or clearly documented above)
- [ ] `/sanity` requested — required if the MR touches dependencies, the Dockerfile or security-relevant code
