"""Show text from plugins and their metadata safely in Rich output."""

from rich.markup import escape
from rich.text import Text


def _one_line(text: object) -> str:
    return "".join(ch if ch.isprintable() else "?" for ch in str(text))


def printable(text: object) -> str:
    """``text`` as one line of literal Rich output.

    Every character that isn't printable (newlines, ESC and so the start of
    any CSI or OSC sequence, BEL, other control and format characters) is
    replaced with ``?``, and Rich markup is escaped.
    """
    return escape(_one_line(text))


def literal(text: object) -> Text:
    """``text`` as a one-line Rich ``Text``, for table cells.

    Non-printable characters become ``?``. A ``Text`` is never parsed for
    markup or ``:emoji:`` codes, so it renders exactly as given.
    """
    return Text(_one_line(text))
