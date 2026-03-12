# Git Workflow Standards

## Quick Summary

Git workflow standards for CodeMie using trunk-based development with feature branches, standardized branch naming (`EPMCDME-XXXX_description`), commit message patterns (`EPMCDME-XXXX: Description`), 9-step PR process, and code review guidelines.

**Category**: Development/Standards
**Complexity**: Medium
**Prerequisites**: Git, GitHub, PR review process

## Prerequisites

- **Git**: Version control system
- **GitHub Access**: Repository permissions
- **Branching Model**: Trunk-based development (main branch)
- **Jira Integration**: Issue tracking with EPMCDME prefix

---

## 🚨 GIT OPERATIONS POLICY 🚨

**Git operations must be performed ONLY when EXPLICITLY requested by the user.**

### What This Means

- ❌ **NEVER** proactively commit, push, branch, or perform any git operations
- ❌ **NEVER** suggest git operations unless asked
- ✅ **ONLY** perform git operations when user explicitly requests:
  - "commit these changes"
  - "create a commit"
  - "push to remote"
  - "create a branch"
  - "create a pull request"
- ❓ If unsure whether the user wants git operations, **ASK FOR CLARIFICATION**

### Why This Policy Exists

- Prevents unintended commits or pushes
- Gives developers full control over version control
- Avoids disrupting work-in-progress
- Ensures commits are intentional and properly scoped

---

## Branching Model

### Trunk-Based Development

**Main Branch**: `main`

All feature branches merge to `main` via Pull Requests. No long-lived feature branches.

**Benefits**:
- Continuous integration
- Reduced merge conflicts
- Faster feedback cycles
- Simplified workflow

### Branch Naming Convention

**Pattern**: `EPMCDME-XXXX_short-description`

| Component | Description | Example |
|-----------|-------------|---------|
| **Prefix** | Jira ticket ID | `EPMCDME-8643` |
| **Separator** | Underscore | `_` |
| **Description** | Short kebab-case description | `add-custom-feature` |

**Examples**:

```bash
# Feature branches
EPMCDME-8643_add-custom-feature
EPMCDME-8644_fix-authentication-bug
EPMCDME-8645_update-documentation

# Bug fix
EPMCDME-9001_fix-null-pointer-error

# Refactoring
EPMCDME-9002_refactor-service-layer
```

**Creating Branches**:

```bash
# From main
git checkout main
git pull origin main
git checkout -b EPMCDME-8643_add-custom-feature

# Verify branch name
git branch --show-current
```

---

## Commit Message Standards

### Format

**Pattern**: `EPMCDME-XXXX: Description of changes`

| Component | Description | Example |
|-----------|-------------|---------|
| **Ticket ID** | Jira ticket with hyphen | `EPMCDME-8643` |
| **Separator** | Colon + space | `: ` |
| **Description** | Capitalize first word, imperative mood | `Add custom feature` |

**Rules**:
1. Start with Jira ticket ID
2. Colon + space separator
3. Capitalize first word after colon
4. Use imperative mood ("Add" not "Added")
5. Be concise but descriptive
6. No period at end

### Examples

**Good Commits**:

```bash
# Feature addition
EPMCDME-8643: Add custom feature for resource management

# Bug fix
EPMCDME-8644: Fix authentication bug in JWT verification

# Documentation update
EPMCDME-8645: Update documentation for new API endpoints

# Refactoring
EPMCDME-8646: Refactor service layer to use dependency injection

# Configuration change
EPMCDME-8647: Configure Elasticsearch connection pooling
```

**Bad Commits** (avoid):

```bash
# Missing ticket ID
fix bug

# Lowercase after colon
EPMCDME-8643: added feature

# Past tense
EPMCDME-8644: Fixed authentication

# Too vague
EPMCDME-8645: Update stuff

# Period at end
EPMCDME-8646: Add feature.
```

### Making Commits

```bash
# Stage changes
git add src/codemie/service/custom_service.py
git add tests/codemie/service/test_custom_service.py

# Commit with proper message
git commit -m "EPMCDME-8643: Add custom feature for resource management"

# Verify commit
git log -1 --oneline
```

---

## Pull Request Process

### 9-Step PR Workflow

#### 1. Create Feature Branch

```bash
git checkout main
git pull origin main
git checkout -b EPMCDME-8643_add-custom-feature
```

#### 2. Implement Changes

Follow coding standards:
- **Code Quality**: Ruff linting/formatting
- **Type Hints**: Comprehensive type annotations
- **Docstrings**: For complex functions
- **Architecture**: API → Service → Repository pattern

#### 3. Write Tests

**ONLY if explicitly requested by user**:
- Unit tests for new functionality
- Integration tests for API endpoints
- Test coverage >80% for new code

#### 4. Run Verification

```bash
# Format and lint
ruff format .
ruff check --fix .

# Run tests (ONLY if user requested)
pytest tests/

# Or use Makefile
make verify
```

#### 5. Ensure All Tests Pass

- All tests passing
- Linting clean (no Ruff errors)
- Complexity ≤16 for all functions

#### 6. Create Pull Request

```bash
# Push branch
git push origin EPMCDME-8643_add-custom-feature

# Create PR on GitHub
# Title: EPMCDME-8643: Add custom feature for resource management
# Description: Clear explanation of changes and why they're needed
```

**PR Description Template**:

```markdown
## Summary
Brief description of what this PR does

## Changes
- List of key changes
- New features added
- Bug fixes

## Testing
- Unit tests added for X
- Integration tests cover Y
- Manual testing performed

## Checklist
- [ ] Code follows style guidelines
- [ ] Tests pass
- [ ] Documentation updated
- [ ] No merge conflicts
```

#### 7. Ensure CI Pipeline Passes

- Automated tests pass
- Linting checks pass
- Build succeeds
- No security vulnerabilities

#### 8. Request Code Review

- At least 1 approval required
- Address reviewer feedback
- Update code as needed
- Re-request review if changes made

#### 9. Merge PR

**After approval**:
- Ensure no merge conflicts with main
- Verify CI pipeline green
- **Do not merge during code freeze**
- Use "Squash and merge" or "Rebase and merge" (team preference)

```bash
# After merge, clean up
git checkout main
git pull origin main
git branch -d EPMCDME-8643_add-custom-feature
```

---

## Code Review Guidelines

### For PR Authors

**Before Requesting Review**:
- [ ] Self-review code for obvious issues
- [ ] Verify all tests pass
- [ ] Check for hardcoded credentials
- [ ] Remove debug statements
- [ ] Update documentation
- [ ] Add clear PR description

### For Reviewers

**Review Checklist**:

1. **Code Quality**
   - [ ] Follows naming conventions
   - [ ] Complexity ≤16
   - [ ] Proper type hints
   - [ ] Clear docstrings

2. **Architecture**
   - [ ] Follows API → Service → Repository pattern
   - [ ] No layer bypass
   - [ ] Proper dependency injection

3. **Security**
   - [ ] No hardcoded credentials
   - [ ] Parameterized SQL queries
   - [ ] Input validation with Pydantic
   - [ ] No sensitive data in logs

4. **Error Handling**
   - [ ] Proper exception usage
   - [ ] Helpful error messages
   - [ ] Errors logged with context

5. **Testing** (if tests requested)
   - [ ] Tests cover new functionality
   - [ ] Edge cases tested
   - [ ] Mocks used correctly

6. **Performance**
   - [ ] No N+1 queries
   - [ ] Async/await used appropriately
   - [ ] Caching where beneficial

**Providing Feedback**:
- Be constructive and specific
- Suggest improvements, not just point out problems
- Distinguish between blocking issues and suggestions
- Use GitHub review comments effectively

---

## Branch Management

### Working with Branches

**Update from main**:

```bash
# Rebase on main
git checkout EPMCDME-8643_add-custom-feature
git fetch origin
git rebase origin/main

# Resolve conflicts if any
git add .
git rebase --continue

# Force push (after rebase)
git push --force-with-lease origin EPMCDME-8643_add-custom-feature
```

**Delete merged branches**:

```bash
# Local cleanup
git checkout main
git pull origin main
git branch -d EPMCDME-8643_add-custom-feature

# Remote cleanup (if not auto-deleted)
git push origin --delete EPMCDME-8643_add-custom-feature
```

### Branch Protection Rules

**Main Branch** (configured on GitHub):
- Require PR reviews before merge
- Require status checks to pass
- Require branches to be up to date
- Restrict force push
- Restrict deletion

---

## Merge Strategies

### Squash and Merge (Recommended)

**Benefits**:
- Clean linear history
- One commit per feature
- Easy to revert

**When to Use**:
- Feature branches with multiple commits
- Want clean main branch history

```bash
# GitHub: Use "Squash and merge" button
# Results in single commit: EPMCDME-8643: Add custom feature
```

### Rebase and Merge

**Benefits**:
- Preserves individual commits
- Linear history without merge commits

**When to Use**:
- Well-structured commit history
- Each commit is meaningful

```bash
# GitHub: Use "Rebase and merge" button
```

### Merge Commit

**Benefits**:
- Preserves full branch history
- Shows when feature was merged

**When to Use**:
- Long-lived feature branches
- Want to preserve branch context

---

## Common Git Operations

### Update Local Repository

```bash
# Update main
git checkout main
git pull origin main

# Update feature branch
git checkout EPMCDME-8643_add-custom-feature
git pull origin EPMCDME-8643_add-custom-feature
```

### Stash Changes

```bash
# Stash uncommitted changes
git stash save "WIP: implementing feature X"

# List stashes
git stash list

# Apply stash
git stash apply stash@{0}

# Drop stash
git stash drop stash@{0}
```

### View History

```bash
# Recent commits
git log --oneline -10

# Branch history
git log --oneline --graph --decorate

# Changes in commit
git show <commit-hash>
```

### Undo Changes

```bash
# Unstage file
git reset HEAD file.py

# Discard local changes
git checkout -- file.py

# Undo last commit (keep changes)
git reset --soft HEAD~1

# Undo last commit (discard changes)
git reset --hard HEAD~1
```

---

## Anti-Patterns

### ❌ Vague Commit Messages

```bash
# BAD
git commit -m "fix"
git commit -m "update"
git commit -m "changes"

# GOOD
git commit -m "EPMCDME-8643: Fix authentication bug in JWT verification"
```

### ❌ Direct Commits to Main

```bash
# BAD
git checkout main
git commit -m "EPMCDME-8643: Add feature"
git push origin main

# GOOD
git checkout -b EPMCDME-8643_add-feature
git commit -m "EPMCDME-8643: Add feature"
git push origin EPMCDME-8643_add-feature
# Create PR
```

### ❌ Long-Lived Feature Branches

```bash
# BAD: Branch exists for weeks/months
EPMCDME-8643_big-refactor  (30 days old, 100+ commits behind main)

# GOOD: Small, focused branches merged quickly
EPMCDME-8643_refactor-auth  (2 days old, merged)
EPMCDME-8644_refactor-db    (1 day old, in review)
```

### ❌ Force Push to Shared Branches

```bash
# BAD: Force push to branch others are using
git push --force origin feature-branch

# GOOD: Use force-with-lease for safety
git push --force-with-lease origin feature-branch
# Or coordinate with team before force pushing
```

---

## Code Freeze

### During Code Freeze

**Restrictions**:
- ❌ No merges to main
- ❌ No deployments
- ✅ Bug fixes only (with approval)
- ✅ Continue development on feature branches

**Process**:
1. Code freeze announced by team lead
2. All PRs must be approved by release manager
3. Only critical bug fixes merged
4. Feature development continues on branches
5. Code freeze lifted after successful release

---

## Troubleshooting

### Common Issues

**Issue**: Merge conflicts when rebasing

```bash
# Solution: Resolve conflicts manually
git rebase origin/main
# Fix conflicts in files
git add .
git rebase --continue
```

**Issue**: Accidentally committed to main

```bash
# Solution: Create feature branch from main
git branch EPMCDME-8643_fix-commit
git reset --hard origin/main
git checkout EPMCDME-8643_fix-commit
```

**Issue**: Need to update PR after review

```bash
# Solution: Add more commits to same branch
git checkout EPMCDME-8643_add-feature
# Make changes
git add .
git commit -m "EPMCDME-8643: Address review comments"
git push origin EPMCDME-8643_add-feature
# PR automatically updates
```

**Issue**: Forgot to create feature branch

```bash
# Solution: Create branch from uncommitted changes
git stash
git checkout -b EPMCDME-8643_add-feature
git stash pop
git add .
git commit -m "EPMCDME-8643: Add feature"
```

---

## Next Steps

- **Code Quality**: Apply standards to code → `.codemie/guides/standards/code-quality.md`
- **Testing**: Write tests for PRs → `.codemie/guides/testing/testing-patterns.md`
- **CI/CD**: Understand pipeline → (CI/CD guide TBD)
- **Release Process**: Deployment workflow → (Release guide TBD)

---

## References

- **Branching Model**: Trunk-based development pattern
- **Commit Format**: EPMCDME-XXXX: Description
- **PR Process**: 9-step workflow with code review
- **Related Standards**: Code Quality (Story 5.1), Testing (Story 5.2)
- **Git Documentation**: https://git-scm.com/doc
