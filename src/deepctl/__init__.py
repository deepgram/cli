"""deepctl - Official command-line interface for Deepgram's speech
recognition API.

Stdlib only, and nothing is imported here: ``dg --version`` and the ``dg mcp``
handshake run from this package without loading Click or ``deepctl_core``.
The console scripts call ``deepctl.main:main``; this package deliberately
exports no ``main`` (``deepctl.main`` is the submodule).
"""

__version__ = "0.5.0"  # x-release-please-version
__author__ = "Deepgram"
__email__ = "devrel@deepgram.com"
__license__ = "MIT"

__all__ = ["__version__"]
