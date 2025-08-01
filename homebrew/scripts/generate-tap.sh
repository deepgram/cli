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

echo "📝 Creating deepctl.rb formula..."

cat > homebrew/dist/deepctl.rb << EOF
class Deepctl < Formula
  desc "Official CLI tool for Deepgram API"
  homepage "https://github.com/deepgram/cli"
  url "file://$TARBALL_PATH"
  sha256 "$SHA256"
  version "0.1.8"

  def install
    bin.install "deepctl"
  end

  test do
    # Test basic functionality
    system "#{bin}/deepctl", "--version"
    
    # Test plugin detection (should show as system installation)
    output = shell_output("#{bin}/deepctl plugin list --verbose 2>&1")
    assert_match "Installation method: system", output
  end
end
EOF

echo "✅ Formula generated successfully!"
echo "📍 Formula: $(pwd)/homebrew/dist/deepctl.rb"
echo "🔗 Tarball: $TARBALL_PATH"
echo "🔐 SHA256: $SHA256"