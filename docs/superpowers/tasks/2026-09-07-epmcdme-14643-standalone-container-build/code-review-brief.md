# Code review — 2026-09-07-epmcdme-14643-standalone-container-build (2026-09-07)

**request-changes** · confidence: low · 6 blocking · 0 deferred · 0 filtered as noise
Coverage: blind — n/a (compact profile) · edge-case ✓ · acceptance — n/a (no spec) · verification-gap — n/a (compact profile)  (1/4 lenses ran)

No spec artifact present; acceptance lens not applicable. Confidence is low.

## Look here first

- `standalone-build/Build-image.ps1:104` — [infra] nginx.conf copied without CRLF stripping; CRLF endings from Windows checkout may cause nginx parse failure in Linux container — CR-005
- `standalone-build/Build-image.ps1:20` — [infra] engine binary check only; daemon not tested for liveness; full context assembled before opaque connection failure — CR-002
- `standalone-build/Build-image.ps1:165` — [infra] ctx/ not removed after failed build; disk accumulates on retries — CR-006
- `standalone-build/Build-image.ps1:88` — [infra] fixed ctx/ path races on concurrent invocations from the same script root — CR-004
- `standalone-build/Build-image.ps1:15` — [infra] Resolve-Path called before Test-Path guard; user receives cryptic exception instead of 'Required directory not found' — CR-001

## Also flagged

- `standalone-build/Build-image.ps1:78` — [infra] git stderr captured verbatim as branch/SHA value when rev-parse fails; misleading source-state log — CR-003

## Checked and clean

commit-format — n/a · code-quality — n/a · security — n/a (standards review not applicable; standards_expected: false)
