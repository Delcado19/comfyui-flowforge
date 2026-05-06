"""
Test runner that configures logging and discovers all tests.
Run with: python -m pytest tests/ -v
"""

import sys
import os
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

if __name__ == "__main__":
    import pytest
    # Ensure logs directory exists
    os.makedirs("logs", exist_ok=True)
    # Create a root test logger
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(f"logs/{Path(__file__).stem}_test_run.log", encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    # Run pytest
    exit_code = pytest.main(["-v", "tests/", "--tb=short"])
    sys.exit(exit_code)