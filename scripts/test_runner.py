#!/usr/bin/env python3
"""Enhanced test runner script for deepctl monorepo.

This script provides flexible test running options:
- Default: Run only tests in ./tests/
- --all: Run all tests across the entire workspace
- --package=<name>: Run tests for a specific package
"""

import sys
import subprocess
from pathlib import Path
from typing import List, Optional, Set
import argparse


class TestRunner:
    """Test runner for deepctl monorepo."""

    def __init__(self):
        self.root_dir = Path(__file__).parent.parent
        self.packages_dir = self.root_dir / "packages"

    def get_package_names(self) -> Set[str]:
        """Get all available package names."""
        packages = set()
        if self.packages_dir.exists():
            for package_dir in self.packages_dir.iterdir():
                if package_dir.is_dir() and (package_dir / "pyproject.toml").exists():
                    # Exclude plugin example from normal test runs
                    if package_dir.name != "deepctl-plugin-example":
                        packages.add(package_dir.name)
        return packages

    def run_command(self, cmd: List[str], cwd: Optional[Path] = None) -> int:
        """Run a command and return exit code."""
        print(f"Running: {' '.join(cmd)}")
        print("-" * 80)
        result = subprocess.run(cmd, cwd=cwd or self.root_dir)
        return result.returncode

    def build_pytest_command(self, args: argparse.Namespace) -> List[str]:
        """Build the pytest command based on arguments."""
        cmd = ["uv", "run", "pytest"]

        # Determine test paths
        test_paths = []

        if args.all:
            # Run all tests - root + all packages
            test_paths.append("tests")
            for package_name in self.get_package_names():
                package_test_path = f"packages/{package_name}/tests"
                if (self.root_dir / package_test_path).exists():
                    test_paths.append(package_test_path)

        elif args.package:
            # Run tests for specific package(s)
            packages = args.package.split(",")
            for package in packages:
                package = package.strip()
                if package in self.get_package_names():
                    package_test_path = f"packages/{package}/tests"
                    if (self.root_dir / package_test_path).exists():
                        test_paths.append(package_test_path)
                    else:
                        print(
                            f"Warning: No tests directory found for package '{package}'")
                else:
                    print(f"Error: Package '{package}' not found")
                    available = ", ".join(sorted(self.get_package_names()))
                    print(f"Available packages: {available}")
                    return None

        else:
            # Default: only root tests
            test_paths.append("tests")

        # Add test paths to command
        cmd.extend(test_paths)

        # Add coverage options based on what we're testing
        if not args.no_cov:
            if args.all:
                # Coverage for all packages
                cov_sources = ["deepctl", "deepctl_core", "deepctl_cmd_login",
                               "deepctl_cmd_projects", "deepctl_cmd_transcribe",
                               "deepctl_cmd_usage", "deepctl_shared_utils"]
                for source in cov_sources:
                    cmd.extend([f"--cov={source}"])
            elif args.package:
                # Coverage for specific package(s)
                packages = args.package.split(",")
                for package in packages:
                    package = package.strip()
                    # Convert package name to module name (replace - with _)
                    module_name = package.replace("-", "_")
                    cmd.extend([f"--cov={module_name}"])
            else:
                # Default: coverage for main CLI only
                cmd.extend(["--cov=deepctl"])

            cmd.extend(["--cov-report=term-missing", "--cov-report=html"])

        # Add verbose flag
        if args.verbose:
            cmd.append("-vv")
        else:
            cmd.append("-v")

        # Add marker filter
        if args.marker:
            cmd.extend(["-m", args.marker])

        # Add pattern filter
        if args.pattern:
            cmd.extend(["-k", args.pattern])

        # Add any extra pytest arguments
        if args.pytest_args:
            cmd.extend(args.pytest_args)

        return cmd

    def run(self, args: argparse.Namespace) -> int:
        """Run tests with the given arguments."""
        cmd = self.build_pytest_command(args)
        if cmd is None:
            return 1

        exit_code = self.run_command(cmd)

        # Show coverage report location if generated
        if not args.no_cov and exit_code == 0:
            print("\n" + "=" * 80)
            print("Coverage HTML report generated at: htmlcov/index.html")
            print("Open with: open htmlcov/index.html")
            print("=" * 80)

        return exit_code


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Enhanced test runner for deepctl monorepo",
        epilog="""
Examples:
  %(prog)s                    # Run tests in ./tests/ only (default)
  %(prog)s --all              # Run all tests across the workspace
  %(prog)s --package=deepctl-core    # Run tests for a specific package
  %(prog)s --package=deepctl-core,deepctl-cmd-login  # Run tests for multiple packages
  %(prog)s -m unit            # Run only unit tests
  %(prog)s -k test_auth       # Run tests matching pattern
  %(prog)s --no-cov           # Run without coverage
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    # Test selection options
    selection_group = parser.add_mutually_exclusive_group()
    selection_group.add_argument(
        "--all",
        action="store_true",
        help="Run all tests across the entire workspace"
    )
    selection_group.add_argument(
        "--package",
        help="Run tests for specific package(s), comma-separated"
    )

    # Test filtering options
    parser.add_argument(
        "-m", "--marker",
        help="Run tests with specific marker (e.g., 'unit', 'integration')"
    )
    parser.add_argument(
        "-k", "--pattern",
        help="Run tests matching pattern"
    )

    # Output options
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output"
    )
    parser.add_argument(
        "--no-cov",
        action="store_true",
        help="Disable coverage reporting"
    )

    # Pass-through arguments to pytest
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="Additional arguments to pass to pytest"
    )

    args = parser.parse_args()

    # Create and run test runner
    runner = TestRunner()
    return runner.run(args)


if __name__ == "__main__":
    sys.exit(main())
