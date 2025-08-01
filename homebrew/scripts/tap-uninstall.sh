#!/bin/bash
set -e

echo "🗑️  Uninstalling deepctl from Homebrew..."

# Check if deepctl is installed via Homebrew
if ! brew list deepctl >/dev/null 2>&1; then
    echo "ℹ️  deepctl is not installed via Homebrew"
    
    # Check if it exists elsewhere
    if command -v deepctl >/dev/null 2>&1; then
        echo "   But deepctl is available in PATH:"
        which deepctl
        echo "   This is likely from pip, pipx, or uv installation"
    else
        echo "   deepctl is not found in PATH"
    fi
    exit 0
fi

echo "📦 Uninstalling deepctl..."
brew uninstall deepctl

echo "✅ Uninstallation complete!"

# Check if deepctl still exists (from other installation methods)
if command -v deepctl >/dev/null 2>&1; then
    echo "ℹ️  deepctl is still available in PATH:"
    which deepctl
    echo "   This is from a different installation method (pip, pipx, uv, etc.)"
else
    echo "✅ deepctl completely removed from PATH"
fi