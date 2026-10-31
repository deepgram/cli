"""Request bodies read from disk while they are sent.

:class:`FileBody` streams a local file to the SDK in 1 MiB chunks instead of
reading it into memory. It opens the file once and keeps the descriptor:
every pass over the body reads from offset 0 of that descriptor, so the
SDK's retries (it sends the same content object again) resend the same
file, and a file swapped in at the path between retries is never sent.

Before each chunk the descriptor's size and mtime are compared with the
values at open. A change raises :class:`UploadFileError`
(``E-UPLOAD-FILE-CHANGED``); a failed read raises it with
``E-UPLOAD-FILE-UNREADABLE``. Both are ``invalid_argument``: the user fixes
the input. :func:`coded_result` reports one the way a transport error is
reported.
"""

from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from .models import CodedErrorResult

CHUNK_SIZE = 1 << 20

# ── Catalogue text (error_codes.json "strings") ──────────────────────

STRINGS: dict[str, str] = {
    "E-UPLOAD-FILE-CHANGED": "{path} changed while {prog} was uploading it.",
    "H-UPLOAD-FILE-CHANGED": (
        "Wait until the file is completely written, then try again."
    ),
    "A-UPLOAD-FILE-CHANGED": (
        "The file was still being written. Wait for it to finish, then retry once."
    ),
    "E-UPLOAD-FILE-UNREADABLE": "Couldn't read {path}: {os_error}.",
    "H-UPLOAD-FILE-UNREADABLE": (
        "Check that the file still exists and is readable, then try again."
    ),
}

# (error message ID, human hint, agent hint)
Messages = tuple[str, str, str]
FILE_CHANGED: Messages = (
    "E-UPLOAD-FILE-CHANGED",
    "H-UPLOAD-FILE-CHANGED",
    "A-UPLOAD-FILE-CHANGED",
)
FILE_UNREADABLE: Messages = (
    "E-UPLOAD-FILE-UNREADABLE",
    "H-UPLOAD-FILE-UNREADABLE",
    "H-UPLOAD-FILE-UNREADABLE",
)


class UploadFileError(Exception):
    """The file being uploaded changed, or couldn't be read."""

    code = "invalid_argument"

    def __init__(
        self, messages: Messages, path: str, os_error: OSError | None = None
    ) -> None:
        from .transport import _clean

        self.messages = messages
        self.path = _clean(path)
        self.values: dict[str, str] = {"path": self.path}
        if os_error is not None:
            reason = os_error.strerror or type(os_error).__name__
            self.values["os_error"] = _clean(reason)
        super().__init__(STRINGS[messages[0]].format(prog="dg", **self.values))


class FileBody:
    """A local file as a re-iterable request body with a known length.

    Give it to the SDK as ``request=`` with a ``Content-Length`` header of
    ``len(body)``; httpx then sends no ``Transfer-Encoding: chunked``. Use
    it as a context manager, or call :meth:`close`, to close the descriptor.
    It has no ``read`` method on purpose: httpx would read it once, as a
    file, and a retry would send nothing.
    """

    def __init__(
        self, path: str | Path, *, file_id: tuple[int, int] | None = None
    ) -> None:
        """Open ``path``.

        ``file_id`` is ``(st_dev, st_ino)`` from a stat taken when the path
        was validated: if the path now names another file, the upload stops
        with ``E-UPLOAD-FILE-CHANGED`` before anything is sent.
        """
        self.path = os.fspath(path)
        self._data: bytes | None = None
        try:
            self._fd: int | None = os.open(
                self.path, os.O_RDONLY | getattr(os, "O_BINARY", 0)
            )
        except OSError as e:
            raise UploadFileError(FILE_UNREADABLE, self.path, e) from e
        try:
            st = os.fstat(self._fd)
            if file_id is not None and (st.st_dev, st.st_ino) != file_id:
                self.close()
                raise UploadFileError(FILE_CHANGED, self.path)
            if not stat.S_ISREG(st.st_mode):
                # A pipe or device (``<(...)``) can be read only once: keep
                # its bytes, as 0.4 did, so a retry can resend them.
                self._data = self._read_all()
                self._size = len(self._data)
                self.close()
                return
        except OSError as e:
            self.close()
            raise UploadFileError(FILE_UNREADABLE, self.path, e) from e
        self._size = st.st_size
        self._stamp = (st.st_size, st.st_mtime_ns)

    def __len__(self) -> int:
        return self._size

    def __iter__(self) -> Iterator[bytes]:
        if self._data is not None:
            return iter((self._data,))
        return self._chunks()

    def __enter__(self) -> FileBody:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)

    def _chunks(self) -> Iterator[bytes]:
        offset = 0
        while offset < self._size:
            self._check()
            data = self._pread(min(CHUNK_SIZE, self._size - offset), offset)
            if not data:  # shorter than at open
                raise UploadFileError(FILE_CHANGED, self.path)
            yield data
            offset += len(data)
        self._check()

    def _check(self) -> None:
        if self._fd is None:
            raise UploadFileError(
                FILE_UNREADABLE, self.path, OSError(9, os.strerror(9))
            )
        try:
            st = os.fstat(self._fd)
        except OSError as e:
            raise UploadFileError(FILE_UNREADABLE, self.path, e) from e
        if (st.st_size, st.st_mtime_ns) != self._stamp:
            raise UploadFileError(FILE_CHANGED, self.path)

    def _pread(self, size: int, offset: int) -> bytes:
        assert self._fd is not None
        try:
            if hasattr(os, "pread"):
                return os.pread(self._fd, size, offset)
            # Windows has no pread; one pass reads at a time.
            os.lseek(self._fd, offset, os.SEEK_SET)
            return os.read(self._fd, size)
        except OSError as e:
            raise UploadFileError(FILE_UNREADABLE, self.path, e) from e

    def _read_all(self) -> bytes:
        assert self._fd is not None
        parts = []
        while chunk := os.read(self._fd, CHUNK_SIZE):
            parts.append(chunk)
        return b"".join(parts)


def coded_result(err: UploadFileError) -> CodedErrorResult:
    """Report ``err`` on stderr (two lines) and return its coded result.

    The same shape as a transport error, with ``next_step`` null: no
    command fixes a file that is still being written.
    """
    from rich.markup import escape

    from . import output, transport

    program = transport.prog()
    values = {**err.values, "prog": program or "dg"}
    agentic = bool(output._output_config["agentic"])
    error = STRINGS[err.messages[0]].format(**values)
    hint = STRINGS[err.messages[2 if agentic else 1]].format(**values)
    output.print_error(escape(error))
    output.print_hint(escape(hint))
    result = transport.result_class()(
        error=error,
        code=err.code,
        hint=hint,
        next_step=None,
        details={"path": err.path},
    )
    return cast("CodedErrorResult", result)
