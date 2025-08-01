#!/bin/bash
set -e

echo "🍺 Installing deepctl via Homebrew..."

# Check if formula exists
if [ ! -f "homebrew/dist/deepctl.rb" ]; then
    echo "❌ Formula not found. Run 'make tap' first."
    exit 1
fi

# Check if deepctl is already installed
if brew list deepctl >/dev/null 2>&1; then
    echo "⚠️  deepctl is already installed via Homebrew"
    echo "   Run 'make tap-uninstall' first, or use 'brew reinstall' to update"
    exit 1
fi

# Check if deepctl binary exists in PATH (from other installation methods)
if command -v deepctl >/dev/null 2>&1; then
    echo "⚠️  deepctl is already available in PATH:"
    which deepctl
    echo "   This might be from pip, pipx, or uv installation"
    echo "   Consider uninstalling it first to avoid conflicts"
    read -p "   Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo "📦 Installing from local formula..."
cd homebrew/dist
brew install --formula ./deepctl.rb

echo "✅ Installation complete!"
echo "🧪 Testing installation..."
echo "   Version: $(deepctl --version)"
echo "   Location: $(which deepctl)"

echo ""
echo "🔍 Plugin system test:"
deepctl plugin list --verbose