"""Official Python SDK for the DocXtract document extraction API.

Date: 2026-08-23 | Author: Alok | File: __init__.py

    from docxtract import DocXtract

    dx = DocXtract(os.environ["DOCXTRACT_API_KEY"])
    result = dx.extract("invoice.pdf", model="invoice")
    print(result["vendor"])

Standard library only — no runtime dependencies.
"""

from .client import DocXtract, __version__
from .errors import (
    AuthenticationError,
    DocXtractError,
    ExtractionFailedError,
    JobError,
    QuotaError,
    RateLimitError,
    RequestError,
    ServerError,
    TransportError,
    to_error,
)
from .models import Chunk, ExtractionResult, SplitManifest

__all__ = [
    "DocXtract",
    "ExtractionResult",
    "SplitManifest",
    "Chunk",
    "DocXtractError",
    "AuthenticationError",
    "QuotaError",
    "RateLimitError",
    "RequestError",
    "ExtractionFailedError",
    "JobError",
    "ServerError",
    "TransportError",
    "to_error",
    "__version__",
]
