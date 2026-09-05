"""DocXtract API client.

Date: 2026-08-23 | Author: Alok | File: client.py
``extract()`` is transparent over the sync and multi-page paths; the raw three calls stay
public for callers who want to drive the flow themselves.
"""

from __future__ import annotations

import json
import mimetypes
import os
import time
from typing import Any, Callable, Dict, List, Optional, Union

from ._http import Transport, encode_multipart
from .errors import DocXtractError, JobError, RateLimitError, RequestError
from .models import ExtractionResult, SplitManifest

__version__ = "1.0.0"

# Mirrors the server's ALLOWED_FILE_TYPES so a bad file fails before the network call.
_ACCEPTED = frozenset({"application/pdf", "image/jpeg", "image/png"})
_MAX_BYTES = 10 * 1024 * 1024

ProgressCallback = Callable[[int, int, str], None]


class DocXtract:
    """Client for the DocXtract v3.1 API.

    ``base_path`` defaults to ``/v3.1``. Set ``/v3`` only if pinned to the older version —
    it has no ``models`` and no multi-page support, and accepts PDF only.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.docxtract.io",
        base_path: str = "/v3.1",
        timeout: int = 120,
        max_retries: int = 3,
        chunk_pause_ms: int = 0,
        user_agent: Optional[str] = None,
    ) -> None:
        if not api_key:
            raise RequestError("An api_key is required.", code="invalid_api_key")
        if api_key.startswith("sk-"):
            # One-character mistake that would otherwise surface as an opaque 401. Several
            # other API providers use the sk- form.
            raise RequestError(
                "DocXtract keys start with an underscore: sk_... The value given uses a "
                "hyphen, so it belongs to a different provider.",
                code="invalid_api_key",
            )

        self.base_path = base_path.rstrip("/")
        self.max_retries = max(0, max_retries)
        self.chunk_pause_ms = chunk_pause_ms
        self._http = Transport(
            api_key,
            base_url,
            timeout,
            user_agent or f"docxtract-python/{__version__} (+https://docxtract.io)",
        )

    # ── the one call most integrations need ───────────────────────────────────

    def extract(
        self,
        file: str,
        model: Optional[str] = None,
        on_progress: Optional[ProgressCallback] = None,
        **options: Any,
    ) -> ExtractionResult:
        """Extract a document, transparently handling the multi-page split.

        A PDF over the server's page threshold is split into chunks by the API; this method
        processes every chunk and returns the stitched result, so one call works at any page
        count.
        """
        opts = self._options(model, options)
        status, body, _ = self._upload(file, opts)

        if status != 202:
            if on_progress:
                on_progress(1, 1, "complete")
            return ExtractionResult.from_body(body)

        manifest = SplitManifest.from_body(body)
        self.process_manifest(manifest, model=model, on_progress=on_progress, **options)
        return self.collect_result(manifest.job_id)

    # ── raw three-call path ───────────────────────────────────────────────────

    def split_document(self, file: str, model: Optional[str] = None, **options: Any) -> SplitManifest:
        """POST documents and require the split branch."""
        status, body, _ = self._upload(file, self._options(model, options))
        if status != 202:
            raise RequestError(
                "Document was processed synchronously (under the page threshold) and produced "
                "no split manifest. Use extract().",
                code="invalid_request",
                status=status,
            )
        return SplitManifest.from_body(body)

    def process_chunk(self, chunk_job_id: str, model: Optional[str] = None, **options: Any) -> Dict[str, Any]:
        """POST process for one chunk.

        Idempotent — replaying a completed chunk is neither an error nor charged again.
        """
        body, ctype = encode_multipart({"options": json.dumps(self._options(model, options))})
        _, parsed, _ = self._http.request(
            "POST", self._path("process"),
            query={"job_id": chunk_job_id}, body=body, content_type=ctype,
        )
        return {k: v for k, v in parsed.items() if k not in ("success", "data")}

    def collect_result(self, job_id: str, finalize: bool = False) -> ExtractionResult:
        """GET result for the parent job.

        ``finalize=True`` is IRREVERSIBLE: it permanently deletes all extracted data for the
        job after responding. Only pass it once the result is stored on your side.
        """
        query: Dict[str, Any] = {"job_id": job_id}
        if finalize:
            query["finalize"] = "true"
        _, body, _ = self._http.request("GET", self._path("result"), query=query)
        return ExtractionResult.from_body(body)

    def process_manifest(
        self,
        manifest: SplitManifest,
        model: Optional[str] = None,
        on_progress: Optional[ProgressCallback] = None,
        **options: Any,
    ) -> None:
        """Process every chunk in page order, with retry.

        Sequential by design: the API's default limit is 10 requests/minute, so parallel
        chunk calls do not finish sooner — they turn the work into 429s. Use
        ``chunk_pause_ms`` to pace calls on a tighter key.
        """
        total = manifest.chunk_count

        for i, chunk in enumerate(manifest.chunks):
            if manifest.expired:
                raise JobError(
                    f"Job {manifest.job_id} expired before all chunks were processed "
                    f"({i} of {total} done). Re-upload the document.",
                    code="job_expired",
                    status=410,
                    details={"job_id": manifest.job_id, "chunks_done": i, "chunks_total": total},
                )

            self._with_retry(lambda c=chunk: self.process_chunk(c.job_id, model, **options))
            if on_progress:
                on_progress(i + 1, total, "chunk")

            if self.chunk_pause_ms > 0 and i + 1 < total:
                time.sleep(self.chunk_pause_ms / 1000)

    # ── discovery ─────────────────────────────────────────────────────────────

    def models(self) -> List[Any]:
        """Document types this key may use. Costs no credits — safe to call freely."""
        if self.base_path != "/v3.1":
            raise RequestError(
                "models exists only in v3.1. Remove the base_path override to use it.",
                code="method_not_allowed",
            )
        _, body, _ = self._http.request("GET", self._path("models"))
        return list((body.get("data") or {}).get("models") or [])

    def health(self) -> Dict[str, Any]:
        _, body, _ = self._http.request("GET", self._path("health"))
        return body

    def authorised(self) -> bool:
        """Whether the configured key is currently active."""
        try:
            _, body, _ = self._http.request("GET", self._path("authorised"))
            return body.get("success") is True
        except DocXtractError:
            return False

    # ── internals ─────────────────────────────────────────────────────────────

    def _path(self, endpoint: str) -> str:
        return f"{self.base_path}/{endpoint}"

    @staticmethod
    def _options(model: Optional[str], extra: Dict[str, Any]) -> Dict[str, Any]:
        opts = dict(extra)
        if model:
            opts["model"] = model
        return opts

    def _upload(self, file: str, options: Dict[str, Any]):
        content, filename = self._read_file(file)
        body, ctype = encode_multipart(
            {"options": json.dumps(options)}, ("file", content, filename)
        )
        return self._http.request(
            "POST", self._path("documents"), body=body, content_type=ctype
        )

    def _read_file(self, file: str):
        """Validate locally so an unsupported file never costs a round trip."""
        if not os.path.isfile(file):
            raise RequestError(f"File not found or not readable: {file}", code="invalid_file")

        size = os.path.getsize(file)
        if size > _MAX_BYTES:
            raise RequestError(
                f"File is {size / 1048576:.1f} MB; the limit is {_MAX_BYTES // 1048576} MB.",
                code="file_too_large",
                details={"size": size},
            )

        guessed = mimetypes.guess_type(file)[0]
        if guessed not in _ACCEPTED:
            raise RequestError(
                f"Unsupported file type {guessed or 'unknown'!r}. Accepted: PDF, JPG, PNG.",
                code="invalid_file_type",
                details={"uploaded_type": guessed, "accepted_types": sorted(_ACCEPTED)},
            )

        with open(file, "rb") as fh:
            return fh.read(), file

    def _with_retry(self, call: Callable[[], Any]) -> Any:
        """Retry retryable failures, honouring X-RateLimit-Reset over a guessed backoff."""
        for attempt in range(self.max_retries + 1):
            try:
                return call()
            except DocXtractError as exc:
                if attempt >= self.max_retries or not exc.retryable:
                    raise
                wait = exc.retry_after if isinstance(exc, RateLimitError) and exc.retry_after is not None else 2 ** attempt
                time.sleep(min(wait, 60))
