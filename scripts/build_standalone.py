#!/usr/bin/env python3
"""Build a standalone deepctl binary for testing system installations."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def build_standalone():
    """Build a standalone deepctl binary using PyInstaller."""
    # Check if we're in the right directory
    if not Path("pyproject.toml").exists():
        print("❌ Please run this script from the deepctl root directory")
        sys.exit(1)

    print("🔨 Building standalone deepctl binary...")

    # Install PyInstaller if needed
    try:
        import PyInstaller
    except ImportError:
        print("📦 Installing PyInstaller...")
        subprocess.run([sys.executable, "-m", "pip",
                       "install", "pyinstaller"], check=True)

    # First, build the package to ensure everything is up to date
    print("📦 Building deepctl package...")
    subprocess.run(["make", "build"], check=True)

    # Create a temporary directory for the build
    build_dir = Path("build_standalone")
    build_dir.mkdir(exist_ok=True)

    # Create a simple spec file for PyInstaller
    spec_content = """
# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['../src/deepctl/main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'deepctl',
        'deepctl_core',
        'deepctl_cmd_usage',
        'deepctl_cmd_transcribe',
        'deepctl_cmd_plugin',
        'deepctl_cmd_projects',
        'deepctl_cmd_mcp',
        'deepctl_cmd_login',
        'deepctl_cmd_debug',
        'deepctl_cmd_debug_audio',
        'deepctl_cmd_debug_browser',
        'deepctl_cmd_debug_network',
        'deepctl_cmd_update',
        'deepctl_shared_utils',
        'click',
        'rich',
        'httpx',
        'keyring',
        'pydantic',
        'deepgram',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='deepctl',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
"""

    spec_file = build_dir / "deepctl.spec"
    spec_file.write_text(spec_content)

    # Run PyInstaller
    print("🏗️  Running PyInstaller...")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--onefile",
            "--name", "deepctl",
            "--distpath", "dist_standalone",
            "--workpath", str(build_dir / "work"),
            "--specpath", str(build_dir),
            str(spec_file),
        ],
        check=True,
    )

    # Clean up build directory
    shutil.rmtree(build_dir)

    print("\n✅ Standalone binary built successfully!")
    print(f"📍 Binary location: {Path('dist_standalone/deepctl').absolute()}")
    print("\n🧪 To test the standalone binary:")
    print("   ./dist_standalone/deepctl --version")
    print("   ./dist_standalone/deepctl plugin list --verbose")
    print("   ./dist_standalone/deepctl plugin search")
    print("   ./dist_standalone/deepctl plugin install deepctl-plugin-example")
    print("\n💡 The standalone binary should detect as 'system' installation")
    print("   and create an isolated plugin environment at ~/.deepctl/plugins/")


if __name__ == "__main__":
    build_standalone()
