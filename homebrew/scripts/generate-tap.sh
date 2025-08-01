#!/bin/bash
set -e

echo "🍺 Generating Homebrew formula..."

# Check if binary exists
if [ ! -f "homebrew/dist/deepctl" ]; then
    echo "❌ Binary not found. Run 'make build-binary' first."
    exit 1
fi

# Check if SHA256 exists
if [ ! -f "homebrew/dist/deepctl.sha256" ]; then
    echo "❌ SHA256 file not found. Run 'make build-binary' first."
    exit 1
fi

# Read SHA256
SHA256=$(cat homebrew/dist/deepctl.sha256)
TARBALL_PATH="$(pwd)/homebrew/dist/deepctl-0.1.8-macos-arm64.tar.gz"

echo "📝 Creating deepctl-standalone.rb formula..."

# Extract version from pyproject.toml
VERSION=$(python3 -c "import re; content=open('pyproject.toml').read(); print(re.search(r'version = \"(.+?)\"', content).group(1))")

cat > homebrew/dist/deepctl-standalone.rb << EOF
class DeepctlStandalone < Formula
  desc "Official CLI tool for Deepgram API (standalone binary)"
  homepage "https://github.com/deepgram/cli"
  url "file://$TARBALL_PATH"
  sha256 "$SHA256"
  version "$VERSION"

  def install
    bin.install "deepctl"
  end

  test do
    # Test basic functionality
    system "#{bin}/deepctl", "--version"
    
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

  def caveats
    <<~EOS
      This is the standalone binary version of deepctl.
      For the standard Python-based installation, use: brew install deepctl
      
      The standalone version includes all dependencies bundled in the binary,
      while the standard version uses Homebrew's Python and is more lightweight.
    EOS
  end
end
EOF

echo "✅ Standalone formula generated successfully!"
echo "📍 Formula: $(pwd)/homebrew/dist/deepctl-standalone.rb"
echo "🔗 Tarball: $TARBALL_PATH"
echo "🔐 SHA256: $SHA256"