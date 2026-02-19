"""Tests for WebSocket proxy."""

import pytest
from deepctl_cmd_debug_stream.models import ConnectionStats
from deepctl_cmd_debug_stream.proxy import WebSocketProxy


class TestWebSocketProxy:
    """Test cases for WebSocketProxy."""

    @pytest.fixture
    def proxy(self):
        """Create a WebSocketProxy instance."""
        return WebSocketProxy(
            api_key="test-key",
            upstream_host="api.deepgram.com",
            sample_size=1024,
        )

    def test_detect_stream_type_stt(self, proxy):
        """Test STT stream type detection."""
        assert proxy._detect_stream_type("/v1/listen") == "stt"
        assert proxy._detect_stream_type("/v1/listen?model=nova-3") == "stt"

    def test_detect_stream_type_tts(self, proxy):
        """Test TTS stream type detection."""
        assert proxy._detect_stream_type("/v1/speak") == "tts"
        assert proxy._detect_stream_type("/v1/speak?model=aura") == "tts"

    def test_detect_stream_type_agent(self, proxy):
        """Test Agent stream type detection."""
        assert proxy._detect_stream_type("/agent") == "agent"

    def test_detect_stream_type_unknown(self, proxy):
        """Test unknown stream type detection."""
        assert proxy._detect_stream_type("/v1/other") == "unknown"
        assert proxy._detect_stream_type("/") == "unknown"

    def test_proxy_initialization(self, proxy):
        """Test proxy is initialized correctly."""
        assert proxy.api_key == "test-key"
        assert proxy.upstream_host == "api.deepgram.com"
        assert proxy.sample_size == 1024
        assert proxy.no_analysis is False
        assert proxy.verbose is False
        assert proxy.connections == []

    def test_proxy_no_analysis(self):
        """Test proxy with analysis disabled."""
        proxy = WebSocketProxy(
            api_key="test-key",
            no_analysis=True,
        )
        assert proxy.no_analysis is True

    def test_proxy_verbose(self):
        """Test proxy with verbose mode."""
        proxy = WebSocketProxy(
            api_key="test-key",
            verbose=True,
        )
        assert proxy.verbose is True
