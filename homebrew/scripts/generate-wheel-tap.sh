#!/bin/bash
set -e

echo "🍺 Generating wheel-based Homebrew formula..."

# Check if we have built packages
if [ ! -d "dist" ] || [ -z "$(ls -A dist/*.whl 2>/dev/null)" ]; then
    echo "❌ No wheel files found in dist/. Run 'make build' first."
    exit 1
fi

# Extract version from pyproject.toml
VERSION=$(python3 -c "import re; content=open('pyproject.toml').read(); print(re.search(r'version = \"(.+?)\"', content).group(1))")

if [ -z "$VERSION" ]; then
    echo "❌ Could not extract version from pyproject.toml"
    exit 1
fi

# Create homebrew dist directory
mkdir -p homebrew/dist

echo "📝 Creating deepctl.rb formula (wheel-based)..."

cat > homebrew/dist/deepctl.rb << EOF
class Deepctl < Formula
  desc "Official CLI tool for Deepgram API"
  homepage "https://github.com/deepgram/cli"
  url "https://files.pythonhosted.org/packages/source/d/deepctl/deepctl-${VERSION}.tar.gz"
  sha256 "PLACEHOLDER_SHA256"
  license "MIT"
  version "${VERSION}"

  depends_on "python@3.12"

  resource "deepctl-core" do
    url "https://files.pythonhosted.org/packages/source/d/deepctl-core/deepctl-core-${VERSION}.tar.gz"
    sha256 "PLACEHOLDER_SHA256"
  end

  resource "deepctl-shared-utils" do
    url "https://files.pythonhosted.org/packages/source/d/deepctl-shared-utils/deepctl-shared-utils-${VERSION}.tar.gz"
    sha256 "PLACEHOLDER_SHA256"
  end

  resource "deepctl-cmd-login" do
    url "https://files.pythonhosted.org/packages/source/d/deepctl-cmd-login/deepctl-cmd-login-${VERSION}.tar.gz"
    sha256 "PLACEHOLDER_SHA256"
  end

  resource "deepctl-cmd-projects" do
    url "https://files.pythonhosted.org/packages/source/d/deepctl-cmd-projects/deepctl-cmd-projects-${VERSION}.tar.gz"
    sha256 "PLACEHOLDER_SHA256"
  end

  resource "deepctl-cmd-transcribe" do
    url "https://files.pythonhosted.org/packages/source/d/deepctl-cmd-transcribe/deepctl-cmd-transcribe-${VERSION}.tar.gz"
    sha256 "PLACEHOLDER_SHA256"
  end

  resource "deepctl-cmd-usage" do
    url "https://files.pythonhosted.org/packages/source/d/deepctl-cmd-usage/deepctl-cmd-usage-${VERSION}.tar.gz"
    sha256 "PLACEHOLDER_SHA256"
  end

  resource "deepctl-cmd-debug" do
    url "https://files.pythonhosted.org/packages/source/d/deepctl-cmd-debug/deepctl-cmd-debug-${VERSION}.tar.gz"
    sha256 "PLACEHOLDER_SHA256"
  end

  resource "deepctl-cmd-debug-audio" do
    url "https://files.pythonhosted.org/packages/source/d/deepctl-cmd-debug-audio/deepctl-cmd-debug-audio-${VERSION}.tar.gz"
    sha256 "PLACEHOLDER_SHA256"
  end

  resource "deepctl-cmd-debug-browser" do
    url "https://files.pythonhosted.org/packages/source/d/deepctl-cmd-debug-browser/deepctl-cmd-debug-browser-${VERSION}.tar.gz"
    sha256 "PLACEHOLDER_SHA256"
  end

  resource "deepctl-cmd-debug-network" do
    url "https://files.pythonhosted.org/packages/source/d/deepctl-cmd-debug-network/deepctl-cmd-debug-network-${VERSION}.tar.gz"
    sha256 "PLACEHOLDER_SHA256"
  end

  resource "deepctl-cmd-mcp" do
    url "https://files.pythonhosted.org/packages/source/d/deepctl-cmd-mcp/deepctl-cmd-mcp-${VERSION}.tar.gz"
    sha256 "PLACEHOLDER_SHA256"
  end

  resource "deepctl-cmd-update" do
    url "https://files.pythonhosted.org/packages/source/d/deepctl-cmd-update/deepctl-cmd-update-${VERSION}.tar.gz"
    sha256 "PLACEHOLDER_SHA256"
  end

  resource "deepctl-cmd-plugin" do
    url "https://files.pythonhosted.org/packages/source/d/deepctl-cmd-plugin/deepctl-cmd-plugin-${VERSION}.tar.gz"
    sha256 "PLACEHOLDER_SHA256"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    # Test basic functionality
    assert_match "deepctl", shell_output("#{bin}/deepctl --version")
    
    # Test plugin detection (should show as system installation)
    output = shell_output("#{bin}/deepctl plugin list --verbose 2>&1")
    assert_match "Installation method: system", output
    
    # Test that all command groups are available
    help_output = shell_output("#{bin}/deepctl --help 2>&1")
    assert_match "login", help_output
    assert_match "projects", help_output
    assert_match "transcribe", help_output
    assert_match "usage", help_output
    assert_match "debug", help_output
    assert_match "plugin", help_output
    assert_match "mcp", help_output
  end
end
EOF

echo "✅ Wheel-based formula generated successfully!"
echo "📍 Formula: $(pwd)/homebrew/dist/deepctl.rb"
echo "📋 Version: $VERSION"
echo ""
echo "ℹ️  Note: SHA256 placeholders need to be replaced with actual PyPI SHA256s after publishing"
echo "ℹ️  This formula will install deepctl using Python wheels and Homebrew's Python"