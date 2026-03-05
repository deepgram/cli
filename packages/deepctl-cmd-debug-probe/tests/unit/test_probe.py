"""Tests for debug probe command and proxy."""

import json
from unittest.mock import Mock, patch

import pytest
from deepctl_cmd_debug_probe.command import ProbeCommand, _find_available_port
from deepctl_cmd_debug_probe.models import (
    ProbeConnectionStats,
    ProbeDebugResult,
    ProbeSnapshot,
)
from deepctl_cmd_debug_probe.proxy import ProbeProxy


class TestModels:
    """Test probe data models."""

    def test_probe_snapshot_defaults(self):
        snap = ProbeSnapshot()
        assert snap.timestamp == 0.0
        assert snap.bytes_at_probe == 0
        assert snap.result is None

    def test_probe_connection_stats_defaults(self):
        stats = ProbeConnectionStats(connection_id="abc123")
        assert stats.connection_id == "abc123"
        assert stats.stream_type == "unknown"
        assert stats.bytes_sent == 0
        assert stats.snapshots == []
        assert stats.transcripts == []
        assert stats.audio_buffer == b""

    def test_probe_connection_stats_buffer_excluded(self):
        stats = ProbeConnectionStats(
            connection_id="test",
            audio_buffer=b"\x00" * 100,
        )
        data = stats.model_dump()
        assert "audio_buffer" not in data

    def test_probe_debug_result(self):
        result = ProbeDebugResult(
            status="success",
            message="done",
            port=3100,
            upstream_host="api.deepgram.com",
        )
        assert result.port == 3100
        assert result.connections == []


class TestProbeProxy:
    """Test ProbeProxy."""

    def test_detect_stream_type_stt(self):
        proxy = ProbeProxy(api_key="test-key")
        assert proxy._detect_stream_type("/v1/listen") == "stt"

    def test_detect_stream_type_tts(self):
        proxy = ProbeProxy(api_key="test-key")
        assert proxy._detect_stream_type("/v1/speak") == "tts"

    def test_detect_stream_type_agent(self):
        proxy = ProbeProxy(api_key="test-key")
        assert proxy._detect_stream_type("/agent") == "agent"

    def test_detect_stream_type_unknown(self):
        proxy = ProbeProxy(api_key="test-key")
        assert proxy._detect_stream_type("/other") == "unknown"

    def test_parse_transcript_valid(self):
        proxy = ProbeProxy(api_key="test-key")
        stats = ProbeConnectionStats(connection_id="test")

        data = {
            "channel": {
                "alternatives": [
                    {"transcript": "hello world", "confidence": 0.99}
                ]
            },
            "is_final": True,
        }

        proxy._parse_transcript(json.dumps(data), stats)
        assert len(stats.transcripts) == 1
        assert stats.transcripts[0] == "hello world"

    def test_parse_transcript_not_final(self):
        proxy = ProbeProxy(api_key="test-key")
        stats = ProbeConnectionStats(connection_id="test")

        data = {
            "channel": {
                "alternatives": [{"transcript": "hello"}]
            },
            "is_final": False,
        }

        proxy._parse_transcript(json.dumps(data), stats)
        assert len(stats.transcripts) == 0

    def test_parse_transcript_invalid_json(self):
        proxy = ProbeProxy(api_key="test-key")
        stats = ProbeConnectionStats(connection_id="test")

        proxy._parse_transcript("not json", stats)
        assert len(stats.transcripts) == 0

    def test_parse_transcript_empty(self):
        proxy = ProbeProxy(api_key="test-key")
        stats = ProbeConnectionStats(connection_id="test")

        data = {
            "channel": {"alternatives": [{"transcript": ""}]},
            "is_final": True,
        }

        proxy._parse_transcript(json.dumps(data), stats)
        assert len(stats.transcripts) == 0


class TestProbeCommand:
    """Test ProbeCommand."""

    def setup_method(self):
        self.cmd = ProbeCommand()
        self.config = Mock()
        self.auth = Mock()
        self.client = Mock()

    def test_command_metadata(self):
        assert self.cmd.name == "probe"
        assert self.cmd.requires_auth is True
        assert self.cmd.ci_friendly is False

    def test_get_arguments(self):
        args = self.cmd.get_arguments()
        names = []
        for a in args:
            n = a.get("names", [a.get("name")])
            names.append(n[0])
        assert "--port" in names
        assert "--probe-interval-bytes" in names
        assert "--probe-interval-seconds" in names
        assert "--verbose" in names

    @patch("deepctl_cmd_debug_probe.command.require_ffprobe")
    def test_handle_no_ffprobe(self, mock_require):
        mock_require.return_value = False

        result = self.cmd.handle(self.config, self.auth, self.client)

        assert result.status == "error"
        assert "ffprobe" in result.message

    @patch("deepctl_cmd_debug_probe.command.require_ffprobe")
    def test_handle_no_api_key(self, mock_require):
        mock_require.return_value = True
        self.auth.get_api_key.return_value = None

        result = self.cmd.handle(self.config, self.auth, self.client)

        assert result.status == "error"
        assert "API key" in result.message


class TestFindAvailablePort:
    """Test port finding utility."""

    @patch("socket.socket")
    def test_finds_first_available(self, mock_socket_cls):
        mock_socket = Mock()
        mock_socket.__enter__ = Mock(return_value=mock_socket)
        mock_socket.__exit__ = Mock(return_value=False)
        mock_socket_cls.return_value = mock_socket

        port = _find_available_port(3100, 3100)
        assert port == 3100

    @patch("socket.socket")
    def test_all_ports_taken(self, mock_socket_cls):
        mock_socket = Mock()
        mock_socket.__enter__ = Mock(return_value=mock_socket)
        mock_socket.__exit__ = Mock(return_value=False)
        mock_socket.bind.side_effect = OSError("Address already in use")
        mock_socket_cls.return_value = mock_socket

        port = _find_available_port(3100, 3100)
        assert port is None
