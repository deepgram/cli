"""``python -m deepctl``: the same entry point as the ``dg`` console script.

Usage lines say ``dg`` (not ``python -m deepctl``) unless
``DEEPCTL_PROG_NAME`` names another program.

``python -m`` puts the working directory first on ``sys.path`` (unless
``-P``/``-I`` or ``PYTHONSAFEPATH`` is in effect). It is dropped here, so a
``*.dist-info`` or module in the directory dg runs in can't register or
shadow a plugin.
"""

import os
import sys

if not getattr(sys.flags, "safe_path", False) and sys.path:
    try:
        if sys.path[0] in ("", os.getcwd()):
            del sys.path[0]
    except OSError:  # the working directory is gone
        del sys.path[0]

from deepctl.main import main

main(prog="dg")
