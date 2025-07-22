#!/usr/bin/env python3
"""Test deepctl with multiple Python versions."""

import subprocess
import sys
from pathlib import Path

# Python versions to test
PYTHON_VERSIONS = ["3.10", "3.11", "3.12"]

# Commands to test
TEST_COMMANDS = [
    "python -m deepctl --version",
    "python -c 'from deepctl.main import main; print(\"Import OK\")'",
    "python -m pytest tests/unit/test_main.py -v",
]


def check_python_version(version):
    """Check if a Python version is available."""
    try:
        result = subprocess.run(
            [f"python{version}", "--version"],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def test_with_python(version):
    """Test with a specific Python version."""
    print(f"\n{'='*60}")
    print(f"Testing with Python {version}")
    print('='*60)

    python_cmd = f"python{version}"

    # Check if version is available
    if not check_python_version(version):
        print(
            f"❌ Python {version} not found. Install with: pyenv install {version}")
        return False

    # Create virtual environment
    venv_path = Path(f".venv-test-{version}")
    print(f"📦 Creating virtual environment: {venv_path}")

    subprocess.run([python_cmd, "-m", "venv", str(venv_path)], check=True)

    # Activate and install
    if sys.platform == "win32":
        pip_cmd = str(venv_path / "Scripts" / "pip")
        python_in_venv = str(venv_path / "Scripts" / "python")
    else:
        pip_cmd = str(venv_path / "bin" / "pip")
        python_in_venv = str(venv_path / "bin" / "python")

    print("📥 Installing deepctl...")
    subprocess.run([pip_cmd, "install", "-e", "."], check=True)

    # Run tests
    success = True
    for cmd in TEST_COMMANDS:
        print(f"\n🧪 Running: {cmd}")
        test_cmd = cmd.replace("python", python_in_venv)
        result = subprocess.run(test_cmd, shell=True,
                                capture_output=True, text=True)

        if result.returncode == 0:
            print(f"✅ Success")
            if result.stdout:
                print(f"   Output: {result.stdout.strip()}")
        else:
            print(f"❌ Failed")
            if result.stderr:
                print(f"   Error: {result.stderr.strip()}")
            success = False

    # Cleanup
    print(f"\n🧹 Cleaning up {venv_path}")
    import shutil
    shutil.rmtree(venv_path)

    return success


def main():
    """Run tests for all Python versions."""
    print("🐍 Testing deepctl with multiple Python versions")
    print("=" * 60)

    results = {}
    for version in PYTHON_VERSIONS:
        try:
            results[version] = test_with_python(version)
        except Exception as e:
            print(f"❌ Error testing Python {version}: {e}")
            results[version] = False

    # Summary
    print("\n" + "="*60)
    print("📊 SUMMARY")
    print("="*60)

    for version, success in results.items():
        status = "✅ PASSED" if success else "❌ FAILED"
        print(f"Python {version}: {status}")

    # Check for version conflicts
    print("\n⚠️  IMPORTANT NOTES:")
    print("1. All packages require Python >=3.10")
    print("2. Ensure all dependencies are compatible with Python 3.10+")


if __name__ == "__main__":
    main()
