# EPMCDME-16503 correction plan

Source: `/Users/Oleksandr_Cherevach/work/codemie-dev/docs/EPMCDME-16503/fix-plan-cx.md`.

Original linker head: `b13d5b2086bbdb3d8a13f9e7d8142208cce7a1ba`.
Engine base: `fb45cbfa668646b10f299809c3e44a7b2b003ccf`.
Rebased linker head before correction edits: `57f1d6348aeef27e41024470504d27d50c3c9505`.

## A. Integrate the engine dependency

Rebase onto the completed 16499 branch. Require the shared corpus harness. Remove the AC-40 exception tolerance. Check full reset with output writing on and off, output values after the reducer, and disabled-writing reset precedence.

Test-first: yes — the old tests tolerated a runtime crash and skipped a missing harness; the corrected assertions require the real result.

## B. Replace the Jinja scanner

Add a small parser module and retain the public extraction function. Track local bindings at each output occurrence. Test runtime output with a non-empty context and test both agent-task and tool-binding callers through warn and strict policy.

Test-first: yes — valid macro, with, tuple-loop, and global outputs must not create false requirements; a real missing free reference must still produce a finding.

## C. Produce source locations

Add `ContractLocation` and `LocationSection`. Assign locations when requirements and synthetic findings are created. Test actual derivation through report creation, exact tokens, owner selection, serialization, and copies.

Test-first: yes — report records must retain the selected configuration owner and unmodified string/index segments.

## D. Extend concrete engine comparisons

Use the real write/finalization/reducer paths, derive effects from node configuration, and compare values, keys, types, optionality, iteration scope, and overrides. Record new runtime defects separately.

Test-first: yes — a guaranteed abstract field must exist with a compatible concrete type; optional and unknown facts must retain their uncertainty.

## Verification and test map

The final map and exact command results are recorded in `verification.md`. Required checks are the focused derive/linker/effects/corpus tests, the workflows and workflow-model suite, `make test` with the pinned enterprise package, and the applicable SDLC Factory delivery checks. No dependency test may be removed or skipped to obtain a pass.
