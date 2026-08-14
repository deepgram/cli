"""Shared fixtures for deepctl-core tests."""

from __future__ import annotations

import pytest
from deepctl_core import output as _output

# Snapshot the pristine output config at import time, before any test (or a
# test that invokes the CLI) mutates it.
_OUTPUT_DEFAULTS = dict(_output._output_config)


@pytest.fixture(autouse=True)
def _reset_output_config():
    """Isolate the process-global output config per test.

    ``deepctl_core.output._output_config`` is mutable module state that
    ``setup_output`` (called by the CLI entrypoint and some tests) overwrites,
    with nothing resetting it afterwards. That let a format set by one test leak
    into the next — e.g. the ``output_result`` JSON tests only passed because an
    earlier CLI-invoking test left the global on ``"json"`` (they fail in
    isolation). Restore the pristine defaults around every test so
    ``output_result`` — which reads this global via ``get_output_format`` — sees
    a deterministic format regardless of ordering.
    """
    _output._output_config.clear()
    _output._output_config.update(_OUTPUT_DEFAULTS)
    yield
    _output._output_config.clear()
    _output._output_config.update(_OUTPUT_DEFAULTS)
