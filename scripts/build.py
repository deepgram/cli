#!/usr/bin/env python3
"""Build script for deepctl distribution packages."""

import os
import sys
import shutil
import subprocess
from pathlib import Path

def main():
    """Main build function."""
    project_root = Path(__file__).parent.parent
    os.chdir(project_root)
    
    print("Building deepctl distribution packages...")
    
    # Clean previous builds
    print("Cleaning previous builds...")
    for dir_name in ["build", "dist", "*.egg-info"]:
        if "*" in dir_name:
            import glob
            for path in glob.glob(dir_name):
                if os.path.exists(path):
                    shutil.rmtree(path)
        else:
            if os.path.exists(dir_name):
                shutil.rmtree(dir_name)
    
    # Check if uv is available
    try:
        subprocess.run(["uv", "--version"], check=True, capture_output=True)
        use_uv = True
        print("Using uv for build (faster!)...")
    except (subprocess.CalledProcessError, FileNotFoundError):
        use_uv = False
        print("Using traditional build tools...")
    
    # Build wheel and source distribution
    print("Building wheel and source distribution...")
    if use_uv:
        # Use uv build when available (much faster)
        subprocess.run(["uv", "build"], check=True)
    else:
        # Fallback to traditional build
        subprocess.run([sys.executable, "-m", "build"], check=True)
    
    print("Build completed successfully!")
    print("Distribution files:")
    
    dist_dir = Path("dist")
    if dist_dir.exists():
        for file in dist_dir.iterdir():
            print(f"  {file.name}")
    
    print("\nTo install locally:")
    if use_uv:
        print("  uv pip install dist/deepctl-*.whl")
    else:
        print("  pip install dist/deepctl-*.whl")
    
    print("\nTo upload to PyPI:")
    if use_uv:
        print("  uv publish dist/*")
    else:
        print("  twine upload dist/*")

if __name__ == "__main__":
    main() 