def pytest_configure(config):
    config.addinivalue_line("markers", "integration: mark test as requiring real Azure DevOps credentials")
