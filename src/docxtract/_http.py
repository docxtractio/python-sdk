"""HTTP transport built on urllib — no third-party dependency.

Date: 2026-08-23 | Author: Alok | File: _http.py
Multipart bodies are encoded by hand because urllib has no equivalent of requests' `files=`.
Keeping the SDK dependency-free means `pip install docxtract` cannot conflict with a
project's pinned requests/httpx.
"""

from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, Optional, Tuple

from .errors import TransportError, to_error


def encode_multipart(
    fields: Dict[str, str],
    file: Optional[Tuple[str, bytes, str]] = None,
) -> Tuple[bytes, str]:
    """Encode a multipart/form-data body.

    ``file`` is ``(field_name, contents, filename)``. Returns ``(body, content_type)``.
    """
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []

    for name, value in fields.items():
        parts += [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            f"{value}\r\n".encode(),
        ]

    if file is not None:
        name, content, filename = file
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        parts += [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"; filename="{os.path.basename(filename)}"\r\n'.encode(),
            f"Content-Type: {ctype}\r\n\r\n".encode(),
            content,
            b"\r\n",
        ]

    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class Transport:
    def __init__(self, api_key: str, base_url: str, timeout: int, user_agent: str) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.user_agent = user_agent

    def request(
        self,
        method: str,
        path: str,
        query: Optional[Dict[str, Any]] = None,
        body: Optional[bytes] = None,
        content_type: Optional[str] = None,
    ) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            clean = {k: str(v) for k, v in query.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)

        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Accept", "application/json")
        # urllib's default UA is "Python-urllib/3.x", which WAFs treat as suspicious and
        # which tells you nothing in server logs.
        req.add_header("User-Agent", self.user_agent)
        if content_type:
            req.add_header("Content-Type", content_type)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status, raw, headers = resp.status, resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            # 4xx/5xx still carry a JSON body we want.
            status, raw, headers = exc.code, exc.read(), dict(exc.headers or {})
        except urllib.error.URLError as exc:
            raise TransportError(
                f"Request to {url} failed: {exc.reason}", code="transport_error"
            ) from exc
        except TimeoutError as exc:
            raise TransportError(
                f"Request to {url} timed out after {self.timeout}s", code="transport_error"
            ) from exc

        try:
            parsed = json.loads(raw.decode("utf-8", "replace"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            snippet = raw.decode("utf-8", "replace").strip()[:120] or "an empty body"
            raise TransportError(
                f"Expected JSON from {url} but got {snippet} (HTTP {status}). "
                "Check the base URL — note there is no /api prefix.",
                code="transport_error",
                status=status,
            ) from exc

        if not isinstance(parsed, dict):
            raise TransportError(
                f"Expected a JSON object from {url}, got {type(parsed).__name__}.",
                code="transport_error",
                status=status,
            )

        if parsed.get("success") is False or status >= 400:
            err = parsed.get("error") or {}
            reset = _header_int(headers, "X-RateLimit-Reset")
            raise to_error(
                err.get("message") or f"Request failed with HTTP {status}",
                err.get("code") or "server_error",
                status,
                err.get("details") or {},
                reset,
            )

        return status, parsed, headers


def _header_int(headers: Dict[str, str], name: str) -> Optional[int]:
    """Header lookup that is case-insensitive, since servers vary."""
    for key, value in headers.items():
        if key.lower() == name.lower():
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None
