"""Input validation utilities for deepctl."""

import os
import re
from pathlib import Path
from typing import TypeAlias
from urllib.parse import ParseResult, urlparse

from .diagnostics import create_diagnostic_console
from .models import FileInfo

# Diagnostics only: every console.print below is an error, a warning or a
# progress line -- never a payload -- so it goes to stderr and can never
# corrupt the machine-readable result a command writes to stdout (#104).
# deepctl-shared-utils does not depend on deepctl-core, so this local factory
# mirrors core's agentic/no-color policy instead of importing `stderr_console`.
console = create_diagnostic_console()

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


# A problem (the error to show, or None) and the warnings to show with it.
Problems: TypeAlias = "tuple[str | None, list[str]]"


def _supported() -> str:
    return ", ".join(sorted(SUPPORTED_AUDIO_EXTENSIONS))


def audio_file_problem(file_path: str | Path) -> Problems:
    """Why ``file_path`` can't be sent as audio (None when it can), and any
    warnings. Prints nothing: the caller reports both, once."""
    # A URL passed as a path may carry a token: show it without one.
    shown = display_url(str(file_path)) if "://" in str(file_path) else file_path
    warnings: list[str] = []
    try:
        path = Path(file_path)
        if not path.exists():
            return f"File not found: {shown}", warnings
        if not path.is_file():
            return f"Not a file: {shown}", warnings
        extension = path.suffix.lower()
        if extension not in SUPPORTED_AUDIO_EXTENSIONS:
            return (
                f"{shown} doesn't look like an audio file "
                f"({extension or 'no extension'}). Supported: {_supported()}. "
                "Pass --no-validate to send it anyway.",
                warnings,
            )
        if not os.access(path, os.R_OK):
            return f"File is not readable: {shown}", warnings
        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > 500:  # 500 MB
            warnings.append(
                f"Large file ({size_mb:.1f} MB): it may take longer to process."
            )
        return None, warnings
    except Exception as e:
        return (
            f"Couldn't check {shown}: {scrub_url_text(e, str(file_path))}",
            warnings,
        )


def _print(problems: Problems) -> bool:
    problem, warnings = problems
    for warning in warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")
    if problem is not None:
        console.print(f"[red]Error:[/red] {problem}")
    return problem is None


def validate_audio_file(file_path: str | Path) -> bool:
    """Validate that a file exists and appears to be an audio file.

    Prints the problem and warnings (see :func:`audio_file_problem`).

    Args:
        file_path: Path to the file

    Returns:
        True if file is valid audio file, False otherwise
    """
    return _print(audio_file_problem(file_path))


_SCHEME_RE = re.compile(r"\s*([A-Za-z][A-Za-z0-9+.\-]*)://(.*)", re.S)


def _parsed(url: str) -> ParseResult | None:
    """``urlparse(url)`` when it has a scheme, a host and a valid port.

    ``None`` otherwise: a password holding an unescaped ``/``, ``?`` or
    ``#`` ends the authority early and shows up as a bad port or host.
    """
    if not _SCHEME_RE.match(url):
        return None
    try:
        parts = urlparse(url)
        parts.port  # noqa: B018 (raises ValueError on a bad port)
    except ValueError:
        return None
    if not parts.scheme or not parts.hostname:
        return None
    return parts


def _url_parts(url: str) -> tuple[str, str, str] | None:
    """``(scheme, host[:port], path)`` when no user info can show.

    ``None`` when the URL doesn't parse, or when an ``@`` appears outside the
    parsed authority: that may be user info whose password holds ``/``, ``?``
    or ``#``, so no part of it is shown.
    """
    parts = _parsed(url)
    if parts is None:
        return None
    match = _SCHEME_RE.match(url)
    rest = match.group(2) if match else ""
    if rest.count("@") != parts.netloc.count("@"):
        return None
    return parts.scheme, parts.netloc.rpartition("@")[2], parts.path


def _redacted(url: str) -> str:
    """The shown form of a URL that can't be parsed safely."""
    match = _SCHEME_RE.match(url)
    if match:
        return f"{match.group(1).lower()}://<redacted>/…"
    if "@" in url:
        return "<redacted>"
    return re.split(r"[?#]", url, maxsplit=1)[0]


def output_url(url: str) -> str:
    """``url`` for machine output: no user info, query string or fragment.

    Presigned URLs carry their token in the query string, so a URL is never
    shown, logged or echoed whole. The path is kept: automation needs it to
    identify the file. A URL that can't be parsed safely is shown as
    ``scheme://<redacted>/…``.
    """
    parts = _url_parts(url)
    if parts is None:
        return _redacted(url)
    scheme, host, path = parts
    return f"{scheme}://{host}{path}"


def display_url(url: str) -> str:
    """``url`` for status lines and errors: ``scheme://host/…/<last segment>``.

    As :func:`output_url`, and the path is cut to its last segment, since some
    hosts put a token in the path. A path of one segment is shown whole.
    """
    parts = _url_parts(url)
    if parts is None:
        return _redacted(url)
    scheme, host, path = parts
    segments = [s for s in path.split("/") if s]
    if len(segments) <= 1:
        return f"{scheme}://{host}{path}"
    tail = "/" if path.endswith("/") else ""
    return f"{scheme}://{host}/…/{segments[-1]}{tail}"


def scrub_url_text(text: object, url: str) -> str:
    """``text`` (an error message) with ``url``'s query and user info removed.

    Every piece of the raw text before the URL's last ``@`` is replaced, so a
    message that quotes part of a password shows ``<redacted>`` instead.
    """
    out = str(text)
    match = _SCHEME_RE.match(url)
    rest = match.group(2) if match else url
    if "@" in rest:
        secret = rest.rpartition("@")[0]
        pieces = [secret, *re.split(r"[:/?#@]", secret)]
        for piece in sorted(pieces, key=len, reverse=True):
            if len(piece) >= 2:
                out = out.replace(piece, "<redacted>")
    return _no_query(out)


def url_problem(url: str, check_accessibility: bool = False) -> Problems:
    """Why ``url`` can't be sent (None when it can), and any warnings.

    By default only the format is checked (an http or https scheme and a
    host), with no network access. ``check_accessibility`` also sends a
    HEAD request (no credentials, no cookies). Prints nothing: the caller
    reports both, once.
    """
    shown = display_url(url)
    warnings: list[str] = []
    try:
        parsed = _parsed(url)
        if parsed is None:
            return f"Not a valid URL: {shown}", warnings
        if parsed.scheme not in ("http", "https"):
            return f"URLs must start with http:// or https://: {shown}", warnings
        path = parsed.path.lower()
        if path:
            extension = Path(path).suffix
            if extension and extension not in SUPPORTED_AUDIO_EXTENSIONS:
                warnings.append(
                    f"{shown} doesn't look like an audio file ({extension}). "
                    f"Supported: {_supported()}."
                )
        if check_accessibility:
            problem, more = _url_reach_problem(url)
            return problem, warnings + more
        return None, warnings
    except Exception as e:
        return f"Not a valid URL: {shown} ({scrub_url_text(e, url)})", warnings


def validate_url(url: str, check_accessibility: bool = False) -> bool:
    """Validate URL format and optionally check accessibility.

    Prints the problem and warnings (see :func:`url_problem`).

    Args:
        url: URL to validate
        check_accessibility: Also send a HEAD request (no credentials, no
            cookies) to check the URL is reachable

    Returns:
        True if URL is valid, False otherwise
    """
    return _print(url_problem(url, check_accessibility))


def _no_query(text: object) -> str:
    """``text`` with any URL query string removed."""
    return re.sub(r"\?\S*", "", str(text))


_CHECK_TIMEOUT_S = 10.0


def _url_reach_problem(url: str) -> Problems:
    """``--check-url``: why ``url`` can't be reached (None when it can),
    and warnings about what it serves.

    The request carries no API key and keeps no cookies: it goes to a host
    the user named, not to Deepgram.
    """
    import httpx

    shown = display_url(url)
    warnings: list[str] = []

    def unreachable(why: str) -> Problems:
        return f"--check-url couldn't reach {shown} ({why})", warnings

    try:
        with httpx.Client(timeout=_CHECK_TIMEOUT_S) as client:
            from http.cookiejar import DefaultCookiePolicy

            client.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))
            # Try HEAD first; fall back to a ranged GET if the server
            # doesn't support HEAD (some CDNs/redirect services return
            # 404 or 405 for HEAD but serve GET fine).
            response = client.head(url, follow_redirects=True)

            if response.status_code not in range(200, 400):
                # Streamed, and the body is never read: a server that
                # ignores Range can't make this load the whole file.
                with client.stream(
                    "GET", url, follow_redirects=True, headers={"Range": "bytes=0-0"}
                ) as response:
                    pass

            if response.status_code not in (200, 206):
                return unreachable(f"HTTP {response.status_code}")
            content_type = response.headers.get("content-type", "").lower()
            if content_type and not any(
                mime in content_type for mime in AUDIO_MIME_TYPES
            ):
                warnings.append(
                    f"{shown} is served as {content_type}, which may not be audio."
                )
            content_length = response.headers.get("content-length")
            if content_length and content_length.isdigit():
                size_mb = int(content_length) / (1024 * 1024)
                if size_mb > 500:  # 500 MB
                    warnings.append(
                        f"Large file ({size_mb:.1f} MB): it may take longer to process."
                    )
            return None, warnings

    except httpx.TimeoutException:
        return unreachable(f"no answer within {_CHECK_TIMEOUT_S:g} s")
    except Exception as e:
        return unreachable(scrub_url_text(e, url) or type(e).__name__)


def _check_url_accessibility(url: str) -> bool:
    """Check if URL is accessible (see :func:`_url_reach_problem`); prints
    the problem and warnings."""
    return _print(_url_reach_problem(url))


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
    uuid_pattern = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    if not re.match(uuid_pattern, project_id, re.IGNORECASE):
        console.print(
            "[yellow]Warning:[/yellow] Project ID doesn't match expected UUID format"
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
            f"[yellow]Warning:[/yellow] Language '{language}' may not be supported"
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
        console.print(f"[yellow]Warning:[/yellow] Model '{model}' may not be supported")
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
            console.print(f"[red]Error:[/red] No read permission for file: {file_path}")
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
        console.print(f"[red]Error:[/red] Unsupported output format: {format_type}")
        console.print(
            f"[dim]Supported formats: {', '.join(sorted(supported_formats))}[/dim]"
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
