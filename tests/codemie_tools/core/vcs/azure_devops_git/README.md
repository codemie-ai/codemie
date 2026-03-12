# Azure DevOps Git Tool Tests

This directory contains both unit tests and integration tests for the Azure DevOps Git tool.

## Unit Tests

The unit tests (`test_tools.py`, `test_models.py`) validate the basic functionality of the tool and its configuration without requiring actual Azure DevOps credentials.

To run just the unit tests:

```bash
python -m pytest tests/codemie_tools/core/vcs/azure_devops_git/test_*.py -k "not integration"
```

## Integration Tests

The integration tests (`test_integration.py`) test the tool against a real Azure DevOps organization and repository. These tests:

1. List repositories
2. Get README.md content from a repository
3. Create a new branch from 'develop'
4. Modify README.md in the new branch
5. Create a commit with the changes
6. Create a pull request to 'develop'
7. Add comments to the pull request
8. Reply to the comment

### Setup for Integration Tests

To run the integration tests, you need to:

1. Set the following environment variables:

   ```bash
   # Windows PowerShell
   $env:GIT_FOR_CODEMIE_3_NEW_ADG_TOOL_TEST_TOKEN="your-personal-access-token"
   $env:GIT_FOR_CODEMIE_3_NEW_ADG_TOOL_TEST_REPO_URL="https://dev.azure.com/your-org/your-project/_git/your-repo"
   
   # Windows CMD
   set GIT_FOR_CODEMIE_3_NEW_ADG_TOOL_TEST_TOKEN=your-personal-access-token
   set GIT_FOR_CODEMIE_3_NEW_ADG_TOOL_TEST_REPO_URL=https://dev.azure.com/your-org/your-project/_git/your-repo
   
   # Bash/Linux/macOS
   export GIT_FOR_CODEMIE_3_NEW_ADG_TOOL_TEST_TOKEN="your-personal-access-token"
   export GIT_FOR_CODEMIE_3_NEW_ADG_TOOL_TEST_REPO_URL="https://dev.azure.com/your-org/your-project/_git/your-repo"
   ```

2. Run the integration tests:

   ```bash
   python -m pytest tests/codemie_tools/core/vcs/azure_devops_git/test_integration.py -v
   ```

### Test Repository Requirements

The target repository must:

1. Have a 'develop' branch that can be used as a source for creating new branches
2. Have a README.md file at the root of the repository
3. Allow your personal access token to create branches, commits, and pull requests

### Personal Access Token Requirements

The personal access token must have the following permissions:
- Code (Read, Write)
- Pull Request Threads (Read, Write)

### Notes

- The integration tests use a unique branch name to avoid conflicts
- Pull requests are created as drafts to prevent accidental merges
- All operations are properly logged for troubleshooting