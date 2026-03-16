# Contributing to CodeMie

Thank you for your interest in contributing to CodeMie! We welcome contributions from the community.

## How to Contribute

1. Fork the repository
2. Clone your fork locally
3. Create a feature branch from `main`: `git checkout -b <TICKET-ID>_short-description`
4. Make your changes following the guidelines below
5. Commit your changes using [Conventional Commits](#commit-message-format)
6. Push to your fork
7. Open a pull request against `main`

## Commit Message Format

This project uses [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `chore`, `ci`

**Scopes:** `agents`, `api`, `tools`, `auth`, `config`, `db`, `workflows`, `proxy`, `docs`

**Rules:**
- Use imperative mood (`add` not `added` or `adds`)
- Keep the first line under 72 characters
- Reference issues in the footer: `Closes #123`

**Examples:**
```
feat(agents): add support for custom LLM provider
fix(api): handle missing auth token gracefully
docs(readme): update quick start instructions
```

## Pull Request Requirements

- PR title must follow the Conventional Commits format
- At least 1 approval required
- CI pipeline must pass
- Describe what changed, why, and how it was tested
- Note any breaking changes clearly

## Development Setup

```bash
# Install dependencies
poetry install --sync

# Activate virtual environment
source .venv/bin/activate

# Run linting and formatting
make ruff

# Run tests
poetry run pytest tests/

# Run all checks (recommended before commit)
make verify
```

## Code Standards

- Python 3.12+ features and type hints required
- Async/await for all I/O operations
- Follow existing architecture patterns: API → Service → Repository
- Apache 2.0 license headers required on all source files — run `make license-fix` to add them

## Reporting Issues

Please use the [GitHub issue tracker](../../issues) to report bugs or request features. Include:
- A clear description of the issue or request
- Steps to reproduce (for bugs)
- Expected vs. actual behavior
- Environment details (OS, Python version, etc.)

## Code of Conduct

By participating in this project, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).
