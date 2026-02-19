"""Audio analyzer using ffprobe for stream debug."""

import json
import shutil
import subprocess
import tempfile

from .models import AudioFormatReport


class AudioAnalyzer:
    """Analyze audio buffers using ffprobe."""

    @staticmethod
    def is_available() -> bool:
        """Check if ffprobe is available on the system."""
        return shutil.which("ffprobe") is not None

    @staticmethod
    def analyze_buffer(
        audio_bytes: bytes, label: str = "audio"
    ) -> AudioFormatReport | None:
        """Analyze an audio buffer using ffprobe.

        Writes bytes to a temp file, runs ffprobe, parses output.
        Returns None if analysis fails or ffprobe is not available.
        """
        if not audio_bytes:
            return None

        if not AudioAnalyzer.is_available():
            return None

        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".raw", delete=False, prefix=f"deepctl_{label}_"
            ) as tmp_file:
                tmp_name = tmp_file.name
                tmp_file.write(audio_bytes)

            cmd = [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                tmp_name,
            ]

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10
            )

            if result.returncode != 0:
                return None

            data = json.loads(result.stdout)
            report = AudioFormatReport()

            # Parse format info
            if "format" in data:
                fmt = data["format"]
                report.format_name = fmt.get("format_name")
                report.bit_rate = fmt.get("bit_rate")
                duration = fmt.get("duration")
                if duration:
                    report.duration = float(duration)

            # Parse first audio stream
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "audio":
                    report.codec = stream.get("codec_name")
                    report.sample_rate = stream.get("sample_rate")
                    report.channels = stream.get("channels")
                    if not report.bit_rate:
                        report.bit_rate = stream.get("bit_rate")
                    break

            return report

        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            return None
        finally:
            if tmp_name:
                import os
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
