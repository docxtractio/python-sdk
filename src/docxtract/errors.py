"""Typed exceptions mirroring the API's stable error codes.

Date: 2026-08-23 | Author: Alok | File: errors.py
Branch on the exception class or ``code``, never on the message — wording is not stable.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, Type

# Retrying these could plausibly succeed. Deliberately conservative: caller input errors,
# dead jobs and exhausted credits are excluded.
_RETRYABLE = frozenset({
    "rate_limit_exceeded",
    "chunk_in_progress",
    "extraction_failed",
    "persist_failed",
    "server_error",
    "server_busy",
})


class DocXtractError(Exception):
    """Base for every API error."""

    def __init__(
        self,
        message: str,
        code: str = "server_error",
        status: int = 0,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.details = details or {}

    @property
    def retryable(self) -> bool:
        return self.code in _RETRYABLE

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, status={self.status}, message={self.message!r})"


class AuthenticationError(DocXtractError):
    """invalid_api_key, expired_api_key (401)."""


class QuotaError(DocXtractError):
    """usage_limit_exceeded, insufficient_credits (402)."""


class RequestError(DocXtractError):
    """Caller input: invalid_request, invalid_file, invalid_file_type, file_too_large,
    invalid_options, unknown_model, page_limit_exceeded, method_not_allowed."""


class ExtractionFailedError(DocXtractError):
    """extraction_failed (422). Note this is billed on the synchronous path."""


class JobError(DocXtractError):
    """job_not_found, job_expired, chunk_in_progress, chunk_source_lost."""


class ServerError(DocXtractError):
    """server_error, persist_failed, server_busy (5xx)."""


class RateLimitError(DocXtractError):
    """rate_limit_exceeded, too_many_open_jobs (429)."""

    def __init__(self, *args: Any, reset_at: Optional[int] = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.reset_at = reset_at

    @property
    def retry_after(self) -> Optional[int]:
        """Seconds to wait, or None when the server sent no reset header.

        Returns None rather than a guess so callers use their own backoff knowingly.
        """
        if self.reset_at is None:
            return None
        return max(0, self.reset_at - int(time.time()))


class TransportError(DocXtractError):
    """Network-level failure or a non-JSON body — the API never answered."""

    @property
    def retryable(self) -> bool:
        return True


_MAP: Dict[str, Type[DocXtractError]] = {
    "invalid_api_key": AuthenticationError,
    "expired_api_key": AuthenticationError,
    "usage_limit_exceeded": QuotaError,
    "insufficient_credits": QuotaError,
    "rate_limit_exceeded": RateLimitError,
    "too_many_open_jobs": RateLimitError,
    "invalid_request": RequestError,
    "invalid_file": RequestError,
    "invalid_file_type": RequestError,
    "file_too_large": RequestError,
    "invalid_options": RequestError,
    "unknown_model": RequestError,
    "page_limit_exceeded": RequestError,
    "method_not_allowed": RequestError,
    "extraction_failed": ExtractionFailedError,
    "job_not_found": JobError,
    "job_expired": JobError,
    "chunk_in_progress": JobError,
    "chunk_source_lost": JobError,
    "server_error": ServerError,
    "persist_failed": ServerError,
    "server_busy": ServerError,
}


def to_error(
    message: str,
    code: str,
    status: int,
    details: Optional[Dict[str, Any]] = None,
    reset_at: Optional[int] = None,
) -> DocXtractError:
    """Build the typed exception for an API error code.

    An unrecognised code falls back to the base class rather than raising, so a new
    server-side code cannot break a deployed copy of this SDK.
    """
    cls = _MAP.get(code, DocXtractError)
    if cls is RateLimitError:
        return RateLimitError(message, code=code, status=status, details=details, reset_at=reset_at)
    return cls(message, code=code, status=status, details=details)
