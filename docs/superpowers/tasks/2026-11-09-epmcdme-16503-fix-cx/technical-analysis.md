# EPMCDME-16503 correction analysis

## Codebase Findings

The existing research is in `../2026-11-02-epmcdme-16503/technical-analysis.md`. The accepted correction plan identifies the source boundaries: requirement derivation, internal models, outcome conversion, mode policy, and concrete comparison tests. The 16499 engine dependency is present at `fb45cbfa668646b10f299809c3e44a7b2b003ccf`; all 22 linker commits rebased without conflicts.

The current Jinja scanner lacks complete scope information. The correction uses Jinja's parser without rendering. Source locations must be assigned with the binding-owner decision, before text is sanitized. Real-engine tests are required because an abstract field model cannot prove all runtime values.

## Risk Indicators

- Jinja local names can share spelling with free names in other expressions.
- A numeric string key must not become a list index.
- A consumer state can read configuration owned by a tool or custom node.
- Unknown and optional effects must not become guaranteed fields.
- The full test environment requires the pinned enterprise package.

The current work reuses the accepted correction design. The implementation agents inspect the relevant source and tests through CodeGraph. No new architecture or runtime semantics are authorized.
