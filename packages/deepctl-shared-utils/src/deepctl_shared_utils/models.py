"""Models for shared utilities."""

from typing import Optional
from pydantic import BaseModel


class FileInfo(BaseModel):
    """File information for validation results."""

    path: str
    name: Optional[str] = None
    extension: Optional[str] = None
    size_bytes: Optional[int] = None
    size_mb: Optional[float] = None
    modified: Optional[float] = None
    readable: Optional[bool] = None
    exists: bool = False
    is_file: Optional[bool] = None
    is_audio: Optional[bool] = None
    error: Optional[str] = None
