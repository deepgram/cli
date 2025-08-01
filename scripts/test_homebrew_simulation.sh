#!/bin/bash
# Script to simulate a Homebrew-like installation for testing

set -e

echo "🍺 Simulating Homebrew installation test..."

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Create a test directory
TEST_DIR="/tmp/deepctl-homebrew-test"
rm -rf "$TEST_DIR"
mkdir -p "$TEST_DIR"

echo -e "${BLUE}📁 Test directory: $TEST_DIR${NC}"

# Build the package
echo -e "\n${YELLOW}📦 Building deepctl package...${NC}"
make build

# Create a virtual environment (simulating Homebrew's Python)
echo -e "\n${YELLOW}🐍 Creating test virtual environment...${NC}"
python3 -m venv "$TEST_DIR/homebrew-python"
source "$TEST_DIR/homebrew-python/bin/activate"

# Install deepctl into the "homebrew" environment
echo -e "\n${YELLOW}📥 Installing deepctl into test environment...${NC}"
pip install --upgrade pip
pip install dist/deepctl-*.whl

# Deactivate to simulate system Python
deactivate

# Make the virtual environment read-only to simulate system installation
echo -e "\n${YELLOW}🔒 Making installation read-only (simulating system install)...${NC}"
chmod -R a-w "$TEST_DIR/homebrew-python"

# Create an alias for the "homebrew" deepctl
DEEPCTL_BIN="$TEST_DIR/homebrew-python/bin/deepctl"

# Test 1: Verify deepctl works
echo -e "\n${GREEN}✅ Test 1: Basic functionality${NC}"
"$DEEPCTL_BIN" --version

# Test 2: Check installation detection
echo -e "\n${GREEN}✅ Test 2: Installation detection${NC}"
"$DEEPCTL_BIN" plugin list --verbose | grep -E "(Installation method|isolated plugin environment)" || true

# Test 3: Search for plugins
echo -e "\n${GREEN}✅ Test 3: Plugin search${NC}"
"$DEEPCTL_BIN" plugin search

# Test 4: Install a plugin (should create isolated environment)
echo -e "\n${GREEN}✅ Test 4: Plugin installation${NC}"
"$DEEPCTL_BIN" plugin install deepctl-plugin-example

# Test 5: Verify plugin environment was created
echo -e "\n${GREEN}✅ Test 5: Check isolated plugin environment${NC}"
if [ -d "$HOME/.deepctl/plugins/venv" ]; then
    echo -e "${GREEN}✓ Isolated plugin environment created at ~/.deepctl/plugins/venv${NC}"
    ls -la "$HOME/.deepctl/plugins/"
else
    echo -e "${RED}✗ Isolated plugin environment NOT created${NC}"
    exit 1
fi

# Test 6: List plugins (should show the installed plugin)
echo -e "\n${GREEN}✅ Test 6: List installed plugins${NC}"
"$DEEPCTL_BIN" plugin list

# Test 7: Run the plugin command
echo -e "\n${GREEN}✅ Test 7: Run plugin command${NC}"
"$DEEPCTL_BIN" example

# Test 8: Remove the plugin
echo -e "\n${GREEN}✅ Test 8: Remove plugin${NC}"
"$DEEPCTL_BIN" plugin remove deepctl-plugin-example -y

# Cleanup (restore write permissions before removing)
echo -e "\n${YELLOW}🧹 Cleaning up...${NC}"
chmod -R u+w "$TEST_DIR/homebrew-python"
rm -rf "$TEST_DIR"

echo -e "\n${GREEN}🎉 All tests passed! The plugin system works correctly with system installations.${NC}"
echo -e "${BLUE}Note: The isolated plugin environment remains at ~/.deepctl/plugins/ for inspection.${NC}" 