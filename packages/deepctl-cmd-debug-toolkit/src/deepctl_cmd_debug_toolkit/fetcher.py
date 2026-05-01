"""GitHub fetch, blob-SHA integrity verification, and local cache management."""

from __future__ import annotations

import base64
import hashlib
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import platformdirs
from rich.console import Console

from .models import ToolkitManifest, ToolkitScript

if TYPE_CHECKING:
    pass

GITHUB_REPO = "deepgram/support-toolkit"
GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_REPO}/contents"

_CACHE_DIR = Path(platformdirs.user_cache_dir("deepctl")) / "toolkit"
_MANIFEST_CACHE = _CACHE_DIR / "manifest.json"
_MANIFEST_AGE = _CACHE_DIR / "manifest.age"
_SCRIPTS_CACHE = _CACHE_DIR / "scripts"
_MANIFEST_TTL = 86400  # 24 hours


def git_blob_sha(content: bytes) -> str:
    """Compute the git blob SHA1 for integrity verification.

    GitHub's Contents API returns this hash alongside file content.
    Re-deriving it from the downloaded bytes lets us confirm we received
    exactly what GitHub has, using their own object model as a trust anchor.
    """
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content).hexdigest()


def local_toolkit_path() -> Path | None:
    """Return a local override path if DEEPGRAM_TOOLKIT_PATH is set."""
    p = os.getenv("DEEPGRAM_TOOLKIT_PATH")
    return Path(p) if p else None


def _fetch_github_contents(path: str) -> tuple[str, bytes]:
    """Fetch a file from the GitHub Contents API.

    Returns (blob_sha, raw_bytes). Raises on any HTTP error or SHA mismatch.
    """
    url = f"{GITHUB_API_BASE}/{path}"
    resp = httpx.get(
        url,
        headers={"Accept": "application/vnd.github+json"},
        timeout=30.0,
        follow_redirects=True,
    )
    resp.raise_for_status()
    data = resp.json()
    sha: str = data["sha"]
    content = base64.b64decode(data["content"])

    # Verify the download is exactly what GitHub reported.
    if git_blob_sha(content) != sha:
        raise RuntimeError(
            f"Integrity check failed for {path}: "
            f"downloaded content does not match GitHub blob SHA."
        )
    return sha, content


def get_cached_manifest() -> ToolkitManifest | None:
    """Return the cached manifest if it exists and is fresh, else None."""
    if not _MANIFEST_CACHE.exists():
        return None
    if _MANIFEST_AGE.exists():
        try:
            age = float(_MANIFEST_AGE.read_text().strip())
            if time.time() - age > _MANIFEST_TTL:
                return None  # Stale — caller should refresh
        except Exception:
            return None
    try:
        return ToolkitManifest.model_validate_json(_MANIFEST_CACHE.read_text())
    except Exception:
        return None


def refresh_manifest(console: Console) -> ToolkitManifest:
    """Fetch the toolkit manifest, verify it, cache it, and return it."""
    local = local_toolkit_path()
    if local:
        manifest_file = local / "toolkit.json"
        content = manifest_file.read_bytes()
        console.print(f"[dim]Loading toolkit manifest from {local}[/dim]")
    else:
        console.print(
            f"[dim]Fetching toolkit manifest from github.com/{GITHUB_REPO}...[/dim]"
        )
        _sha, content = _fetch_github_contents("toolkit.json")

    manifest = ToolkitManifest.model_validate_json(content)

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _MANIFEST_CACHE.write_bytes(content)
    _MANIFEST_AGE.write_text(str(time.time()))

    return manifest


def get_or_fetch_script(entry: ToolkitScript, console: Console) -> Path:
    """Return the path to a verified local copy of the script.

    Uses a local override if DEEPGRAM_TOOLKIT_PATH is set, otherwise
    fetches from GitHub and caches by blob SHA. Re-downloads only when the
    cached SHA no longer matches the stored value.
    """
    local = local_toolkit_path()
    if local:
        return local / entry.script

    # Stable cache filename derived from the script path.
    safe_name = entry.script.replace("/", "__")
    cache_file = _SCRIPTS_CACHE / safe_name
    sha_file = _SCRIPTS_CACHE / f"{safe_name}.sha"

    # Use cache if content still matches its stored SHA.
    if cache_file.exists() and sha_file.exists():
        stored_sha = sha_file.read_text().strip()
        if git_blob_sha(cache_file.read_bytes()) == stored_sha:
            return cache_file

    # Download from GitHub with integrity verification.
    script_name = Path(entry.script).name
    console.print(f"[blue]Downloading[/blue] {script_name} from support-toolkit...")
    sha, content = _fetch_github_contents(entry.script)

    _SCRIPTS_CACHE.mkdir(parents=True, exist_ok=True)
    cache_file.write_bytes(content)
    sha_file.write_text(sha)

    console.print(f"[green]✓[/green] {script_name} verified (sha: {sha[:12]}…)")
    return cache_file
