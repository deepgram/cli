"""Input validation utilities for deepctl."""

import os
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from rich.console import Console

from .models import FileInfo

console = Console()

# Supported audio file extensions
SUPPORTED_AUDIO_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".flac",
    ".m4a",
    ".aac",
    ".ogg",
    ".wma",
    ".opus",
    ".amr",
    ".3gp",
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
}

# Common audio MIME types
AUDIO_MIME_TYPES = {
    "audio/mpeg",
    "audio/wav",
    "audio/flac",
    "audio/m4a",
    "audio/aac",
    "audio/ogg",
    "audio/opus",
    "audio/amr",
    "audio/3gp",
    "audio/mp4",
    "video/mp4",
    "video/quicktime",
    "video/x-msvideo",
    "video/x-matroska",
    "video/webm",
}


def validate_audio_file(file_path: str | Path) -> bool:
    """Validate that a file exists and appears to be an audio file.

    Args:
        file_path: Path to the file

    Returns:
        True if file is valid audio file, False otherwise
    """
    try:
        path = Path(file_path)

        # Check if file exists
        if not path.exists():
            console.print(f"[red]Error:[/red] File not found: {file_path}")
            return False

        # Check if it's a file (not directory)
        if not path.is_file():
            console.print(f"[red]Error:[/red] Path is not a file: {file_path}")
            return False

        # Check file extension
        extension = path.suffix.lower()
        if extension not in SUPPORTED_AUDIO_EXTENSIONS:
            console.print(
                f"[yellow]Warning:[/yellow] File extension '{extension}' "
                f"is not in supported list"
            )
            console.print(
                f"[dim]Supported extensions: "
                f"{', '.join(sorted(SUPPORTED_AUDIO_EXTENSIONS))}[/dim]"
            )
            return False

        # Check if file is readable
        if not os.access(path, os.R_OK):
            console.print(
                f"[red]Error:[/red] File is not readable: {file_path}"
            )
            return False

        # Check file size (warn if very large)
        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > 500:  # 500 MB
            console.print(
                f"[yellow]Warning:[/yellow] Large file detected "
                f"({size_mb:.1f} MB)"
            )
            console.print("[dim]Large files may take longer to process[/dim]")

        return True

    except Exception as e:
        console.print(f"[red]Error validating file:[/red] {e}")
        return False


def validate_url(url: str, check_accessibility: bool = True) -> bool:
    """Validate URL format and optionally check accessibility.

    Args:
        url: URL to validate
        check_accessibility: Whether to check if URL is accessible

    Returns:
        True if URL is valid, False otherwise
    """
    try:
        # Parse URL
        parsed = urlparse(url)

        # Check basic URL structure
        if not parsed.scheme or not parsed.netloc:
            console.print(f"[red]Error:[/red] Invalid URL format: {url}")
            return False

        # Check scheme
        if parsed.scheme not in ("http", "https"):
            console.print(
                f"[red]Error:[/red] Only HTTP/HTTPS URLs are supported: {url}"
            )
            return False

        # Check if URL looks like an audio file
        path = parsed.path.lower()
        if path:
            extension = Path(path).suffix
            if extension and extension not in SUPPORTED_AUDIO_EXTENSIONS:
                console.print(
                    f"[yellow]Warning:[/yellow] URL doesn't appear to be an "
                    f"audio file: {url}"
                )
                console.print(
                    f"[dim]Expected extensions: "
                    f"{', '.join(sorted(SUPPORTED_AUDIO_EXTENSIONS))}[/dim]"
                )

        # Check accessibility if requested
        if check_accessibility:
            return _check_url_accessibility(url)

        return True

    except Exception as e:
        console.print(f"[red]Error validating URL:[/red] {e}")
        return False


def _check_url_accessibility(url: str) -> bool:
    """Check if URL is accessible.

    Args:
        url: URL to check

    Returns:
        True if accessible, False otherwise
    """
    try:
        console.print("[dim]Checking URL accessibility...[/dim]")

        with httpx.Client(timeout=10.0) as client:
            # Use HEAD request to check without downloading
            response = client.head(url, follow_redirects=True)

            if response.status_code == 200:
                # Check content type if available
                content_type = response.headers.get("content-type", "").lower()
                if content_type and not any(
                    mime in content_type for mime in AUDIO_MIME_TYPES
                ):
                    console.print(
                        f"[yellow]Warning:[/yellow] Content type may not be "
                        f"audio: {content_type}"
                    )

                # Check content length if available
                content_length = response.headers.get("content-length")
                if content_length:
                    size_mb = int(content_length) / (1024 * 1024)
                    if size_mb > 500:  # 500 MB
                        console.print(
                            f"[yellow]Warning:[/yellow] Large file detected "
                            f"({size_mb:.1f} MB)"
                        )

                return True
            else:
                console.print(
                    f"[red]Error:[/red] URL not accessible "
                    f"(HTTP {response.status_code}): {url}"
                )
                return False

    except httpx.TimeoutException:
        console.print(f"[red]Error:[/red] URL request timed out: {url}")
        return False
    except httpx.RequestError as e:
        console.print(f"[red]Error:[/red] Failed to access URL: {e}")
        return False
    except Exception as e:
        console.print(f"[red]Error checking URL accessibility:[/red] {e}")
        return False


def validate_api_key(api_key: str) -> bool:
    """Validate API key format.

    Args:
        api_key: API key to validate

    Returns:
        True if format is valid, False otherwise
    """
    if not api_key:
        console.print("[red]Error:[/red] API key cannot be empty")
        return False

    # Check basic format
    if not api_key.startswith(("sk-", "pk-")):
        console.print(
            "[yellow]Warning:[/yellow] API key doesn't match expected "
            "format (should start with 'sk-' or 'pk-')"
        )
        return False

    # Check length (Deepgram API keys are typically longer)
    if len(api_key) < 20:
        console.print("[yellow]Warning:[/yellow] API key seems too short")
        return False

    # Check for invalid characters
    if not re.match(r"^[a-zA-Z0-9_-]+$", api_key):
        console.print("[red]Error:[/red] API key contains invalid characters")
        return False

    return True


def validate_project_id(project_id: str) -> bool:
    """Validate project ID format.

    Args:
        project_id: Project ID to validate

    Returns:
        True if format is valid, False otherwise
    """
    if not project_id:
        console.print("[red]Error:[/red] Project ID cannot be empty")
        return False

    # Check if it's a valid UUID format
    uuid_pattern = (
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    if not re.match(uuid_pattern, project_id, re.IGNORECASE):
        console.print(
            "[yellow]Warning:[/yellow] Project ID doesn't match expected "
            "UUID format"
        )
        return False

    return True


def validate_language_code(language: str) -> bool:
    """Validate language code format.

    Args:
        language: Language code to validate

    Returns:
        True if format is valid, False otherwise
    """
    if not language:
        return False

    # Common language codes supported by Deepgram
    supported_languages = {
        "en-US",
        "en-GB",
        "en-AU",
        "en-NZ",
        "en-IN",
        "es-ES",
        "es-US",
        "es-419",
        "fr-FR",
        "fr-CA",
        "de-DE",
        "de-CH",
        "it-IT",
        "pt-BR",
        "pt-PT",
        "ru-RU",
        "tr-TR",
        "hi-IN",
        "ja-JP",
        "ko-KR",
        "zh-CN",
        "zh-TW",
        "nl-NL",
        "pl-PL",
        "sv-SE",
        "no-NO",
        "da-DK",
        "fi-FI",
        "uk-UA",
        "el-GR",
        "cs-CZ",
        "hu-HU",
        "ro-RO",
        "sk-SK",
        "sl-SI",
        "bg-BG",
        "hr-HR",
        "et-EE",
        "lv-LV",
        "lt-LT",
        "mt-MT",
    }

    if language not in supported_languages:
        console.print(
            f"[yellow]Warning:[/yellow] Language '{language}' may not be "
            f"supported"
        )
        console.print(
            "[dim]Common supported languages: en-US, es-ES, fr-FR, de-DE, "
            "it-IT, pt-BR, ja-JP, ko-KR, zh-CN[/dim]"
        )
        return False

    return True


def validate_model_name(model: str) -> bool:
    """Validate model name.

    Args:
        model: Model name to validate

    Returns:
        True if format is valid, False otherwise
    """
    if not model:
        return False

    # Common Deepgram models
    supported_models = {
        "nova-2",
        "nova",
        "enhanced",
        "base",
        "meeting",
        "phonecall",
        "voicemail",
        "finance",
        "conversationalai",
        "video",
        "nova-2-general",
        "nova-2-meeting",
        "nova-2-phonecall",
        "nova-2-voicemail",
        "nova-2-finance",
        "nova-2-conversationalai",
        "nova-2-video",
        "whisper-tiny",
        "whisper-base",
        "whisper-small",
        "whisper-medium",
        "whisper-large",
    }

    if model not in supported_models:
        console.print(
            f"[yellow]Warning:[/yellow] Model '{model}' may not be supported"
        )
        console.print(
            "[dim]Common supported models: nova-2, nova, enhanced, base, "
            "meeting, phonecall[/dim]"
        )
        return False

    return True


def validate_date_format(date_str: str) -> bool:
    """Validate date format (ISO 8601).

    Args:
        date_str: Date string to validate

    Returns:
        True if format is valid, False otherwise
    """
    if not date_str:
        return False

    # ISO 8601 date format patterns
    patterns = [
        r"^\d{4}-\d{2}-\d{2}$",  # YYYY-MM-DD
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$",  # YYYY-MM-DDTHH:MM:SS
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",  # YYYY-MM-DDTHH:MM:SSZ
        # YYYY-MM-DDTHH:MM:SS+TZ
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$",
    ]

    for pattern in patterns:
        if re.match(pattern, date_str):
            return True

    console.print(f"[red]Error:[/red] Invalid date format: {date_str}")
    console.print(
        "[dim]Expected formats: YYYY-MM-DD, YYYY-MM-DDTHH:MM:SS, or "
        "YYYY-MM-DDTHH:MM:SSZ[/dim]"
    )
    return False


def validate_file_permissions(file_path: str | Path) -> bool:
    """Validate file permissions.

    Args:
        file_path: Path to check

    Returns:
        True if permissions are valid, False otherwise
    """
    try:
        path = Path(file_path)

        # Check read permission
        if not os.access(path, os.R_OK):
            console.print(
                f"[red]Error:[/red] No read permission for file: {file_path}"
            )
            return False

        # Check if file is not empty
        if path.stat().st_size == 0:
            console.print(f"[red]Error:[/red] File is empty: {file_path}")
            return False

        return True

    except Exception as e:
        console.print(f"[red]Error checking file permissions:[/red] {e}")
        return False


def sanitize_filename(filename: str) -> str:
    """Sanitize filename for cross-platform compatibility.

    Args:
        filename: Original filename

    Returns:
        Sanitized filename
    """
    # Remove invalid characters
    sanitized = re.sub(r'[<>:"/\\|?*]', "_", filename)

    # Remove leading/trailing whitespace and dots
    sanitized = sanitized.strip(" .")

    # Ensure filename is not empty
    if not sanitized:
        sanitized = "unnamed_file"

    # Truncate if too long
    if len(sanitized) > 255:
        sanitized = sanitized[:255]

    return sanitized


def validate_output_format(format_type: str) -> bool:
    """Validate output format.

    Args:
        format_type: Format to validate

    Returns:
        True if format is valid, False otherwise
    """
    supported_formats = {"json", "yaml", "table", "csv"}

    if format_type not in supported_formats:
        console.print(
            f"[red]Error:[/red] Unsupported output format: {format_type}"
        )
        console.print(
            f"[dim]Supported formats: "
            f"{', '.join(sorted(supported_formats))}[/dim]"
        )
        return False

    return True


def get_file_info(file_path: str | Path) -> FileInfo:
    """Get detailed information about a file.

    Args:
        file_path: Path to the file

    Returns:
        FileInfo model with file information
    """
    try:
        path = Path(file_path)
        stat = path.stat()

        return FileInfo(
            path=str(path.absolute()),
            name=path.name,
            extension=path.suffix.lower(),
            size_bytes=stat.st_size,
            size_mb=stat.st_size / (1024 * 1024),
            modified=stat.st_mtime,
            readable=os.access(path, os.R_OK),
            exists=path.exists(),
            is_file=path.is_file(),
            is_audio=path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS,
        )

    except Exception as e:
        return FileInfo(path=str(file_path), error=str(e), exists=False)
