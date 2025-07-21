# Audio Debug Command Architecture

## Overview

The audio debug command (`deepctl debug audio`) is a powerful diagnostic tool designed to help users analyze audio files before submitting them to Deepgram's transcription services. It leverages FFmpeg's ffprobe tool to extract detailed information about audio files and provides Deepgram-specific compatibility checks.

## Key Features

1. **FFmpeg Detection**: Automatically checks if FFmpeg is installed and provides platform-specific installation instructions
2. **Multi-Level Verbosity**: Three levels of output detail (basic, verbose, extra verbose)
3. **URL Support**: Can analyze both local files and remote URLs
4. **Custom FFprobe Arguments**: Allows advanced users to pass custom ffprobe arguments
5. **Deepgram Compatibility Checks**: Validates audio parameters for optimal Deepgram performance

## Architecture

### Command Structure

```python
class AudioCommand(BaseCommand):
    name = "audio"
    help = "Debug audio file issues for Deepgram transcription"
    requires_auth = False  # No authentication needed
    requires_project = False  # No project context needed
    ci_friendly = True  # Can run in CI/CD environments
```

### Dependencies

- **ffmpeg-python**: Python bindings for FFmpeg (used for standard probing)
- **subprocess**: Direct execution of ffprobe for custom arguments
- **rich**: Beautiful terminal output with tables and panels
- **pydantic**: Data validation and modeling

### Data Models

#### AudioInfo

The main container for all audio information:

- `format`: AudioFormat object with file-level metadata
- `streams`: List of AudioStream objects for each audio stream
- `raw_data`: Complete ffprobe JSON output (for debugging)

#### AudioFormat

File-level information:

- Filename, format name/type
- Duration, file size, bit rate
- Number of streams

#### AudioStream

Stream-level information:

- Codec information
- Sample rate, channels, channel layout
- Duration, bit rate
- Bits per sample

## Implementation Details

### FFmpeg Detection

The command uses `shutil.which()` to detect if ffprobe is installed:

```python
def check_ffmpeg_installed(self) -> bool:
    return shutil.which("ffprobe") is not None
```

If not found, it displays a helpful panel with installation instructions for each platform.

### Audio Analysis

1. **Standard Analysis**: Uses ffmpeg-python's `probe()` function
2. **Custom Analysis**: Allows users to pass raw ffprobe arguments via subprocess

```python
def run_ffprobe(self, file_path: str, custom_args: Optional[str] = None):
    if custom_args:
        # Direct subprocess execution
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json"]
        cmd.extend(custom_args.split())
        cmd.append(file_path)
    else:
        # Use ffmpeg-python
        probe = ffmpeg.probe(file_path)
```

### Output Levels

#### Basic Output (Default)

- Essential file information (format, duration, size, bit rate)
- Key audio stream properties (codec, sample rate, channels)
- Deepgram compatibility warnings

#### Verbose Output (-v)

- Detailed tables using Rich's Table component
- All available metadata in organized format
- Separate tables for format and each stream

#### Extra Verbose Output (-vv)

- Raw JSON output from ffprobe
- Syntax highlighted with line numbers
- Useful for debugging and custom processing

### Deepgram Compatibility Checks

The command performs specific checks for Deepgram compatibility:

1. **Sample Rate**: Warns if below 8kHz
2. **Channel Count**: Warns if more than 2 channels

```python
if stream.sample_rate and int(stream.sample_rate) < 8000:
    compatibility_issues.append(
        f"⚠️  Low sample rate ({stream.sample_rate} Hz) - Deepgram works best with 8kHz or higher"
    )
```

## Usage Examples

### Basic Usage

```bash
deepctl debug audio --file audio.mp3
```

### Verbose Analysis

```bash
deepctl debug audio --file https://example.com/audio.wav --verbose
```

### Custom FFprobe Arguments

```bash
deepctl debug audio --file audio.flac --ffprobe-args "-show_streams -select_streams a:0"
```

### Raw JSON Output

```bash
deepctl debug audio --file audio.m4a --extra-verbose
```

## Error Handling

The command gracefully handles various error scenarios:

1. **FFmpeg Not Installed**: Shows installation guide
2. **File Not Found**: Clear error message
3. **Invalid Audio File**: Displays ffprobe error details
4. **Network Issues**: For URL-based files

## Testing

The command includes comprehensive unit tests covering:

- FFmpeg detection
- Audio file parsing
- Output formatting
- Error handling
- Deepgram compatibility checks

Tests use mocking to avoid dependency on actual FFmpeg installation during CI/CD.

## Future Enhancements

Potential improvements for future versions:

1. **Audio Conversion Suggestions**: Recommend ffmpeg commands to fix compatibility issues
2. **Batch Processing**: Analyze multiple files at once
3. **Export Reports**: Save analysis results to JSON/CSV
4. **Audio Preview**: Play a short sample (if possible)
5. **Waveform Visualization**: ASCII or image-based waveform display
6. **Automatic Compatibility Fixes**: Option to convert files automatically
