# Local Verification

How to verify a change beyond unit tests before opening an MR: the harness sanity run and live API
calls. Gate commands and the MR compliance rules stay in `.ai-run/guides/quality-gates.md`; this guide is the procedure for choosing and running the extra checks.

## Order

Run the checks in this order and stop to investigate at the first unexplained failure.

1. The harness sanity run (`make test-harness`).
2. Live API calls against a local backend for what the change actually does.

| Avoid | Prefer |
|---|---|
| Running only the sanity run and calling the change verified | Add at least one live call that exercises the new behavior |
| Starting the harness before the backend runs the code under test | Start the backend from the branch checkout, then point the harness at it |

## Harness Sanity Run

`make test-harness` runs `uvx codemie-test-harness --sanity-api`, the suite `api and sanity`. Prerequisites are listed
in the Test Harness gate of `.ai-run/guides/quality-gates.md`.

| Avoid | Prefer |
|---|---|
| Using the shortcut as-is against a local backend | The shortcut starts many parallel workers (8 by default) and can overload a local backend; pass the suite to `run` with explicit limits, for example `uvx codemie-test-harness run sanity-api -n 2 --reruns 2` |
| Running `--collect-only` against the same database while a real run is active | Collect only when no run is active; collection cleans shared test data |
| Pasting a run that aborted or was overloaded | Paste the summary of a complete run, and say how it was invoked |

## Live API Calls

Unit tests and the sanity run prove what they assert. A live call proves that the running backend does what the
change claims.

1. Start the backend from the branch checkout with the configuration the feature needs, and confirm it serves the
   changed code.
2. Drive the feature through its public API, in the order a user would: create the resources, run the operation, read
   the result back.
3. Record the request, the response and the relevant backend log lines as evidence.
4. Repeat the failure paths the change handles (invalid input, timeout, the feature switched off), not only the
   happy path.

| Avoid | Prefer |
|---|---|
| Asserting that something is absent (a file, a record, a process) without proof the check can see it | Add a positive control in the same run: show the thing present at the moment it should exist, then absent afterwards |
| Testing only through a chat or assistant when the API offers a deterministic call | Use the REST call first; add the assistant scenario for the integration |
| Leaving backends, namespaces or containers running after the check | Stop everything that was started and confirm nothing is left |

## Triage Of Failures

A red result is not automatically caused by the change, and not automatically unrelated to it.

1. Read the first failing frame, not the last.
2. Reproduce the failure on `main` with the same complete configuration: environment variables, credentials, config
   overrides, image and architecture. A run on `main` with a different configuration proves nothing.
3. Classify the cause with evidence as environment, test or model variance, pre-existing, or caused by the change.
4. Tests that depend on an LLM judge can fail between runs without any code change. Rerun several times and compare
   the pass rate on `main` and on the branch before concluding.
5. A failure caused by the change is fixed test-first and re-verified; an environment failure is fixed in the
   environment and the run repeated.

| Avoid | Prefer |
|---|---|
| Declaring a failure flaky after one rerun | Compare pass rates over several runs on both refs |
| Attributing a failure to the environment without checking it | Name the variable, credential or image that differs |

## Reporting

Put the results into the MR description: the `## Test harness` section required by the compliance bot (see
`.ai-run/guides/quality-gates.md`), then each live scenario (what was done, what was expected, what happened).
