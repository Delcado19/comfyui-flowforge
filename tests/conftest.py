"""
Pytest configuration and shared fixtures.
"""

import pytest
import logging
from pathlib import Path
from flowforge.logger import setup_logger

# Global test logger
test_logger = setup_logger("pytest")

def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "slow: marks tests as slow")
    config.addinivalue_line("markers", "integration: marks tests as integration tests")

@pytest.fixture(scope="session", autouse=True)
def setup_test_logging():
    """Ensure logs directory exists for test session."""
    Path("logs").mkdir(exist_ok=True)
    yield
    # Session teardown if needed