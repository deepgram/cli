"""Root conftest.py for pytest configuration."""

import sys
import subprocess
from pathlib import Path


def pytest_addoption(parser):
    """Add custom options to pytest."""
    parser.addoption(
        "--all",
        action="store_true",
        help="Run all tests across the entire workspace"
    )
    parser.addoption(
        "--package",
        action="store",
        help="Run tests for specific package(s), comma-separated"
    )


def pytest_cmdline_main(config):
    """Intercept pytest execution to handle custom options."""
    # Check if custom options are used
    if config.getoption("--all") or config.getoption("--package"):
        # Forward to our test runner script
        cmd = [sys.executable, "scripts/test_runner.py"]

        # Add the custom options
        if config.getoption("--all"):
            cmd.append("--all")
        elif config.getoption("--package"):
            cmd.extend(["--package", config.getoption("--package")])

        # Forward other common options
        if config.getoption("--verbose") or config.getoption("-v"):
            cmd.append("--verbose")

        if config.getoption("--no-cov"):
            cmd.append("--no-cov")

        if config.getoption("-m"):
            cmd.extend(["-m", config.getoption("-m")])

        if config.getoption("-k"):
            cmd.extend(["-k", config.getoption("-k")])

        # Run the test runner
        result = subprocess.run(cmd)
        sys.exit(result.returncode)

    # Otherwise, continue with normal pytest execution
    return None
