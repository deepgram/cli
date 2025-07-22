#!/bin/bash
# Test deepctl with multiple Python versions locally

echo "🧪 Testing deepctl with multiple Python versions..."
echo "================================================"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Python versions to test
PYTHON_VERSIONS=("3.10" "3.11" "3.12")

# Track results (using simple arrays for compatibility)
RESULTS_VERSIONS=()
RESULTS_STATUSES=()

for version in "${PYTHON_VERSIONS[@]}"; do
    echo -e "\n📌 Testing with Python $version..."
    
    # Check if Python version exists
    if ! command -v python$version &> /dev/null; then
        echo -e "${YELLOW}⚠️  Python $version not found, skipping...${NC}"
        RESULTS_VERSIONS+=("$version")
        RESULTS_STATUSES+=("SKIPPED")
        continue
    fi
    
    # Create a temporary virtual environment
    echo "Creating virtual environment..."
    python$version -m venv .tox/py${version//./}
    
    # Activate and test
    source .tox/py${version//./}/bin/activate
    
    echo "Installing uv in virtual environment..."
    pip install -q uv
    
    echo "Installing local packages..."
    # Install all packages
    uv pip install -q -e . \
        -e packages/deepctl-core \
        -e packages/deepctl-shared-utils \
        -e packages/deepctl-cmd-login \
        -e packages/deepctl-cmd-projects \
        -e packages/deepctl-cmd-transcribe \
        -e packages/deepctl-cmd-usage \
        -e packages/deepctl-cmd-debug \
        -e packages/deepctl-cmd-debug-audio \
        -e packages/deepctl-cmd-debug-browser \
        -e packages/deepctl-cmd-debug-network \
        -e packages/deepctl-cmd-mcp
    
    # Install test dependencies
    uv pip install -q pytest pytest-asyncio pytest-cov pytest-mock
    
    # Run basic import test
    echo "Testing imports..."
    if python -c "from deepctl.main import main; print('✓ Import successful')"; then
        # Run actual tests
        echo "Running tests..."
        if pytest tests/unit/test_main.py -q; then
            echo -e "${GREEN}✅ Python $version: PASSED${NC}"
            RESULTS_VERSIONS+=("$version")
            RESULTS_STATUSES+=("PASSED")
        else
            echo -e "${RED}❌ Python $version: FAILED (tests)${NC}"
            RESULTS_VERSIONS+=("$version")
            RESULTS_STATUSES+=("FAILED")
        fi
    else
        echo -e "${RED}❌ Python $version: FAILED (import)${NC}"
        RESULTS_VERSIONS+=("$version")
        RESULTS_STATUSES+=("FAILED")
    fi
    
    deactivate
done

# Summary
echo -e "\n📊 Test Summary:"
echo "================"
for i in "${!RESULTS_VERSIONS[@]}"; do
    version="${RESULTS_VERSIONS[$i]}"
    status="${RESULTS_STATUSES[$i]}"
    case $status in
        "PASSED")
            echo -e "${GREEN}✅ Python $version: $status${NC}"
            ;;
        "FAILED")
            echo -e "${RED}❌ Python $version: $status${NC}"
            ;;
        "SKIPPED")
            echo -e "${YELLOW}⚠️  Python $version: $status${NC}"
            ;;
        *)
            echo "❓ Python $version: $status"
            ;;
    esac
done 