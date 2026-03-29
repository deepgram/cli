"""Transcribe command — deprecated alias for `dg listen`.

.. deprecated::
    ``dg transcribe`` has been superseded by ``dg listen``, which handles
    files, URLs, microphone, and stdin streaming in a single unified command.
    This package and class are kept for backwards compatibility only.
    New code should use ``dg listen`` / :class:`deepctl_cmd_listen.command.ListenCommand`.
"""

from __future__ import annotations

from deepctl_cmd_listen.command import ListenCommand


class TranscribeCommand(ListenCommand):
    """Deprecated alias for :class:`~deepctl_cmd_listen.command.ListenCommand`.

    .. deprecated::
        Use ``dg listen`` instead.  ``dg transcribe`` is functionally
        identical — it is hidden from ``dg --help`` but continues to work so
        existing scripts are not broken.  This class will be removed in a
        future major release.
    """

    name = "transcribe"
    # Hidden from `dg --help` — command still works but is not advertised.
    hidden = True
    help = (
        "Transcribe audio files or URLs using Deepgram.\n\n"
        "This command is a deprecated alias for `dg listen`. "
        "Use `dg listen` for all new scripts."
    )
    short_help = "Transcribe audio (deprecated alias for dg listen)"
