"""Optional SageMaker transport support.

The published ``deepgram-sagemaker`` package is Python 3.12+ and async-only.
Keep its imports here so ordinary CLI commands neither require AWS packages nor
fail to import on Python 3.10 and 3.11.
"""

from __future__ import annotations

import os
import sys
from typing import Any


class SageMakerTransportError(RuntimeError):
    """The optional SageMaker streaming transport cannot be used."""


def endpoint_name(value: str | None) -> str | None:
    """Resolve an explicit endpoint, then the standard SageMaker environment."""
    return (
        value
        or os.getenv("DG_SAGEMAKER_ENDPOINT")
        or os.getenv("SAGEMAKER_ENDPOINT_NAME")
        or os.getenv("SAGEMAKER_ENDPOINT")
    )


def region_name(value: str | None) -> str:
    """Resolve an explicit region, then the AWS default used by the transport."""
    return value or os.getenv("AWS_REGION") or "us-west-2"


def create_sagemaker_client(endpoint: str, region: str) -> Any:
    """Create the async SDK client configured for SageMaker HTTP/2 streaming."""
    if sys.version_info < (3, 12):
        raise SageMakerTransportError(
            "SageMaker streaming requires Python 3.12+ because "
            "deepgram-sagemaker depends on AWS CRT."
        )

    try:
        from deepgram import AsyncDeepgramClient
        from deepgram_sagemaker import (  # type: ignore[import-not-found]
            SageMakerTransportFactory,
        )
    except ImportError as exc:
        raise SageMakerTransportError(
            "SageMaker streaming requires the optional transport. Install it with "
            "`pip install 'deepctl[sagemaker]'` (or install deepgram-sagemaker)."
        ) from exc

    factory = SageMakerTransportFactory(endpoint_name=endpoint, region=region)
    # SageMaker signs requests with the normal AWS credential chain. The SDK
    # constructor still requires this value, but the transport never sends it.
    return AsyncDeepgramClient(api_key="unused", transport_factory=factory)
