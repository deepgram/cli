"""Models for shared utilities."""

from pydantic import BaseModel


class FileInfo(BaseModel):
    """File information for validation results."""

    path: str
    name: str | None = None
    extension: str | None = None
    size_bytes: int | None = None
    size_mb: float | None = None
    modified: float | None = None
    readable: bool | None = None
    exists: bool = False
    is_file: bool | None = None
    is_audio: bool | None = None
    error: str | None = None
