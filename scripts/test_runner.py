#!/usr/bin/env python3
"""Test runner script for deepctl test suite.

This script runs all tests across the workspace packages.
"""

import sys
import subprocess
from pathlib import Path
from typing import List, Optional


def run_command(cmd: List[str], cwd: Optional[Path] = None) -> int:
    """Run a command and return exit code."""
    print(f"Running: {' '.join(cmd)}")
    print("-" * 80)
    result = subprocess.run(cmd, cwd=cwd)
    return result.returncode


def main():
    """Run tests with selected options."""
    import argparse

    parser = argparse.ArgumentParser(description="deepctl Test Runner")
    parser.add_argument(
        "command",
        choices=["test", "coverage", "watch",
                 "unit", "integration", "specific"],
        help="Test command to run"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output"
    )
    parser.add_argument(
        "--no-cov",
        action="store_true",
        help="Disable coverage reporting"
    )
    parser.add_argument(
        "--marker", "-m",
        help="Run tests with specific marker (e.g., 'unit', 'slow')"
    )
    parser.add_argument(
        "--file", "-f",
        help="Run specific test file"
    )
    parser.add_argument(
        "--pattern", "-k",
        help="Run tests matching pattern"
    )

    args = parser.parse_args()

    # Base pytest command
    cmd = ["uv", "run", "pytest"]

    # Handle different commands
    if args.command == "test":
        # Run all tests with standard options
        if not args.no_cov:
            cmd.extend(["--cov=src/deepctl", "--cov-report=term-missing"])
        if args.verbose:
            cmd.append("-vv")

    elif args.command == "coverage":
        # Run with full coverage report
        cmd.extend([
            "--cov=src/deepctl",
            "--cov-report=term-missing:skip-covered",
            "--cov-report=html:htmlcov",
            "--cov-report=xml",
            "--cov-branch",
        ])

    elif args.command == "watch":
        # Run in watch mode (requires pytest-watch)
        cmd = ["uv", "run", "ptw", "--", "tests/"]
        if not args.no_cov:
            cmd.extend(["--cov=src/deepctl"])

    elif args.command == "unit":
        # Run only unit tests
        cmd.extend(["-m", "unit"])
        if not args.no_cov:
            cmd.extend(["--cov=src/deepctl"])

    elif args.command == "integration":
        # Run only integration tests
        cmd.extend(["-m", "integration"])

    elif args.command == "specific":
        # Run specific test file
        if args.file:
            cmd.append(args.file)
        else:
            print("Error: --file required for 'specific' command")
            return 1

    # Add optional arguments
    if args.marker:
        cmd.extend(["-m", args.marker])

    if args.pattern:
        cmd.extend(["-k", args.pattern])

    # Run the command
    exit_code = run_command(cmd)

    # Show coverage report location if generated
    if args.command == "coverage" and exit_code == 0:
        print("\n" + "=" * 80)
        print("Coverage HTML report generated at: htmlcov/index.html")
        print("Open with: open htmlcov/index.html")
        print("=" * 80)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
