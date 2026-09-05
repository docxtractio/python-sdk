"""Result and manifest wrappers.

Date: 2026-08-23 | Author: Alok | File: models.py
The API places metadata at the root level beside ``data``, so both are exposed without
pretending ``data`` has a fixed schema — its shape varies by document type.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class ExtractionResult:
    """An extraction result — covers both the sync response and the stitched multi-page one."""

    def __init__(self, data: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> None:
        self.data = data
        self.meta = meta or {}

    @classmethod
    def from_body(cls, body: Dict[str, Any]) -> "ExtractionResult":
        meta = {k: v for k, v in body.items() if k not in ("data", "success")}
        data = body.get("data") or {}
        if not isinstance(data, dict):
            data = {"value": data}
        return cls(data, meta)

    def get(self, path: str, default: Any = None) -> Any:
        """Dot-path read: get('Header.pages'), get('line_items.0.hsn')."""
        node: Any = self.data
        for seg in path.split("."):
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            elif isinstance(node, list) and seg.isdigit() and int(seg) < len(node):
                node = node[int(seg)]
            else:
                return default
        return node

    # ── metadata ──────────────────────────────────────────────────────────────
    @property
    def pages(self) -> Optional[int]:
        return self.meta.get("pages")

    @property
    def processing_time_ms(self) -> Optional[int]:
        return self.meta.get("processing_time_ms")

    @property
    def model_used(self) -> Optional[str]:
        return self.meta.get("model_used")

    @property
    def extraction_id(self) -> Optional[str]:
        """Present only when store_db was true, which is not the default server-side."""
        return self.meta.get("extraction_id")

    # ── multi-page ────────────────────────────────────────────────────────────
    @property
    def job_id(self) -> Optional[str]:
        return self.meta.get("job_id")

    @property
    def complete(self) -> bool:
        """A synchronous result is complete by definition; only a partial collect says no."""
        return self.meta.get("status", "complete") == "complete"

    @property
    def pending_pages(self) -> List[int]:
        return list(self.meta.get("pending_pages") or [])

    @property
    def failed_pages(self) -> List[int]:
        return list(self.meta.get("failed_pages") or [])

    @property
    def warnings(self) -> List[str]:
        return list(self.meta.get("warnings") or [])

    @property
    def credits_used(self) -> Optional[int]:
        return self.meta.get("credits_used")

    # ── convenience ───────────────────────────────────────────────────────────
    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __contains__(self, key: object) -> bool:
        return key in self.data

    def to_dict(self) -> Dict[str, Any]:
        return {"data": self.data, **self.meta}

    def to_dataframe(self, path: Optional[str] = None):
        """Row-shaped extractions (invoice line items, bank statement rows) as a DataFrame.

        Requires pandas: ``pip install 'docxtract[pandas]'``. Pass ``path`` to point at the
        list, or leave it out to use the first list of dicts found in ``data``.
        """
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ImportError(
                "to_dataframe() needs pandas. Install with: pip install 'docxtract[pandas]'"
            ) from exc

        rows = self.get(path) if path else None
        if rows is None:
            rows = next(
                (v for v in self.data.values()
                 if isinstance(v, list) and v and isinstance(v[0], dict)),
                None,
            )
        if rows is None:
            raise ValueError(
                "No row-shaped list found in the extracted data. Pass an explicit path, "
                "e.g. to_dataframe('line_items')."
            )
        return pd.DataFrame(rows)

    def __repr__(self) -> str:
        return f"ExtractionResult(fields={len(self.data)}, complete={self.complete})"


class Chunk:
    """One entry from the 202 split manifest."""

    __slots__ = ("job_id", "pages")

    def __init__(self, job_id: str, pages: str) -> None:
        self.job_id = job_id
        self.pages = pages

    def __repr__(self) -> str:
        return f"Chunk(job_id={self.job_id!r}, pages={self.pages!r})"


class SplitManifest:
    """The 202 response when a PDF exceeds the server's page threshold."""

    def __init__(self, job_id: str, pages: int, chunks: List[Chunk], expires_at: Optional[str]) -> None:
        self.job_id = job_id
        self.pages = pages
        self.chunks = chunks
        self.expires_at = expires_at

    @classmethod
    def from_body(cls, body: Dict[str, Any]) -> "SplitManifest":
        raw = body.get("data") or []
        chunks = [
            Chunk(str(c.get("job_id", "")), str(c.get("pages", "")))
            for c in raw
            if isinstance(c, dict)
        ]
        return cls(
            str(body.get("job_id", "")),
            int(body.get("pages") or 0),
            chunks,
            body.get("expires_at"),
        )

    @property
    def expires_at_unix(self) -> Optional[int]:
        if not self.expires_at:
            return None
        try:
            dt = datetime.fromisoformat(str(self.expires_at).replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())

    @property
    def expired(self) -> bool:
        """An absent expiry must not read as expired — that would abandon a valid job."""
        ts = self.expires_at_unix
        if ts is None:
            return False
        return datetime.now(timezone.utc).timestamp() >= ts

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    def __repr__(self) -> str:
        return f"SplitManifest(job_id={self.job_id!r}, pages={self.pages}, chunks={self.chunk_count})"
