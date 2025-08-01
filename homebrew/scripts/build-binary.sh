#!/bin/bash
set -e

echo "🔨 Building standalone deepctl binary for Homebrew..."

# Check if we're in the right directory
if [ ! -f "pyproject.toml" ]; then
    echo "❌ Please run this script from the deepctl root directory"
    exit 1
fi

# Create homebrew dist directory
mkdir -p homebrew/dist

# Install PyInstaller if needed
if ! uv pip show pyinstaller >/dev/null 2>&1; then
    echo "📦 Installing PyInstaller..."
    uv pip install pyinstaller
fi

# Build the standalone binary
echo "🏗️  Building binary with PyInstaller..."
uv run pyinstaller \
    --onefile \
    --name deepctl \
    --distpath homebrew/dist \
    --workpath homebrew/build \
    --specpath homebrew \
    --clean \
    --additional-hooks-dir homebrew/hooks \
    --hidden-import deepctl_core \
    --hidden-import deepctl_cmd_usage \
    --hidden-import deepctl_cmd_transcribe \
    --hidden-import deepctl_cmd_plugin \
    --hidden-import deepctl_cmd_projects \
    --hidden-import deepctl_cmd_mcp \
    --hidden-import deepctl_cmd_login \
    --hidden-import deepctl_cmd_debug \
    --hidden-import deepctl_cmd_debug_audio \
    --hidden-import deepctl_cmd_debug_browser \
    --hidden-import deepctl_cmd_debug_network \
    --hidden-import deepctl_cmd_update \
    --hidden-import deepctl_shared_utils \
    --collect-data deepctl_cmd_debug_browser \
    --copy-metadata deepctl \
    --copy-metadata deepctl-core \
    --copy-metadata deepctl-cmd-usage \
    --copy-metadata deepctl-cmd-transcribe \
    --copy-metadata deepctl-cmd-plugin \
    --copy-metadata deepctl-cmd-projects \
    --copy-metadata deepctl-cmd-mcp \
    --copy-metadata deepctl-cmd-login \
    --copy-metadata deepctl-cmd-debug \
    --copy-metadata deepctl-cmd-debug-audio \
    --copy-metadata deepctl-cmd-debug-browser \
    --copy-metadata deepctl-cmd-debug-network \
    --copy-metadata deepctl-cmd-update \
    --copy-metadata deepctl-shared-utils \
    src/deepctl/main.py

# Create tarball
echo "📦 Creating tarball..."
cd homebrew/dist
tar -czf deepctl-0.1.8-macos-arm64.tar.gz deepctl

# Calculate SHA256
echo "🔐 Calculating SHA256..."
SHA256=$(shasum -a 256 deepctl-0.1.8-macos-arm64.tar.gz | cut -d' ' -f1)
echo "SHA256: $SHA256"

# Save SHA256 to file for the tap script
echo "$SHA256" > deepctl.sha256

echo "✅ Binary built successfully!"
echo "📍 Binary: $(pwd)/deepctl"
echo "📍 Tarball: $(pwd)/deepctl-0.1.8-macos-arm64.tar.gz"
echo "📍 SHA256: $SHA256"