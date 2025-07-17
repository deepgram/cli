"""Tests for deepctl."""

# Test configuration
import os
import sys
from pathlib import Path

# Add src to path for testing
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

# Test environment variables
os.environ["DEEPGRAM_API_KEY"] = "test-api-key"
os.environ["DEEPGRAM_PROJECT_ID"] = "test-project-id" 