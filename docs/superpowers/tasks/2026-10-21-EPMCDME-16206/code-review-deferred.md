# Deferred from code review — 2026-10-21-EPMCDME-16206 (2026-10-21)

- **Duplicate Pillow declaration across groups** — `pyproject.toml:130` — Pillow is declared in the main group and again as lowercase `pillow` in a dev group (line 172), so the constraints can drift apart on a later bump. Pre-existing: both declarations existed before this bump; the change only raised both to ^13.0.0.
