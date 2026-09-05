"""Offline suite — no API key, no network.

Date: 2026-08-23 | Author: Alok | File: test_sdk.py
Run with: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import email
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from docxtract import (  # noqa: E402
    AuthenticationError, DocXtract, DocXtractError, ExtractionFailedError, ExtractionResult,
    JobError, QuotaError, RateLimitError, RequestError, ServerError, SplitManifest, to_error,
)
from docxtract._http import encode_multipart  # noqa: E402

KEY = "sk_0000000000000000000000000000abcd"

CODE_MAP = {
    "invalid_api_key": AuthenticationError, "expired_api_key": AuthenticationError,
    "usage_limit_exceeded": QuotaError, "insufficient_credits": QuotaError,
    "rate_limit_exceeded": RateLimitError, "too_many_open_jobs": RateLimitError,
    "invalid_request": RequestError, "invalid_file": RequestError,
    "invalid_file_type": RequestError, "file_too_large": RequestError,
    "invalid_options": RequestError, "unknown_model": RequestError,
    "page_limit_exceeded": RequestError, "method_not_allowed": RequestError,
    "extraction_failed": ExtractionFailedError,
    "job_not_found": JobError, "job_expired": JobError,
    "chunk_in_progress": JobError, "chunk_source_lost": JobError,
    "server_error": ServerError, "persist_failed": ServerError, "server_busy": ServerError,
}


class TestErrorMapping(unittest.TestCase):
    def test_all_documented_codes_map(self):
        self.assertEqual(len(CODE_MAP), 22)
        for code, cls in CODE_MAP.items():
            with self.subTest(code=code):
                err = to_error("m", code, 400)
                self.assertIsInstance(err, cls)
                self.assertEqual(err.code, code)

    def test_unknown_code_falls_back_to_base(self):
        # A server adding a code must not break deployed copies of the SDK.
        err = to_error("brand new", "some_future_code", 418)
        self.assertIs(type(err), DocXtractError)
        self.assertEqual(err.status, 418)

    def test_retry_after_from_reset_header(self):
        err = to_error("slow", "rate_limit_exceeded", 429, reset_at=int(time.time()) + 30)
        self.assertIsInstance(err, RateLimitError)
        self.assertGreater(err.retry_after, 25)
        self.assertLessEqual(err.retry_after, 30)

    def test_retry_after_is_none_without_header(self):
        # Must not invent a backoff the server did not specify.
        self.assertIsNone(to_error("slow", "rate_limit_exceeded", 429).retry_after)

    def test_retryability_is_conservative(self):
        for code in ("rate_limit_exceeded", "chunk_in_progress", "extraction_failed",
                     "persist_failed", "server_busy"):
            self.assertTrue(to_error("m", code, 400).retryable, code)
        for code in ("invalid_file_type", "job_expired", "chunk_source_lost",
                     "insufficient_credits", "invalid_api_key"):
            self.assertFalse(to_error("m", code, 400).retryable, code)

    def test_details_preserved(self):
        err = to_error("bad", "invalid_file_type", 400, {"uploaded_type": "image/gif"})
        self.assertEqual(err.details, {"uploaded_type": "image/gif"})


SYNC_BODY = {
    "success": True,
    "data": {"vendor": "ABC Suppliers", "total": 5900.0, "line_items": [{"hsn": "8471"}]},
    "processing_time_ms": 5577, "model_used": "invoice", "pages": 1, "extraction_id": "abc123",
}


class TestExtractionResult(unittest.TestCase):
    def test_separates_data_from_root_metadata(self):
        r = ExtractionResult.from_body(SYNC_BODY)
        self.assertEqual(r["vendor"], "ABC Suppliers")
        self.assertEqual(r.processing_time_ms, 5577)
        self.assertEqual(r.model_used, "invoice")
        self.assertEqual(r.extraction_id, "abc123")
        self.assertNotIn("success", r.meta)
        self.assertNotIn("data", r.meta)

    def test_extraction_id_none_without_store_db(self):
        self.assertIsNone(ExtractionResult.from_body({"success": True, "data": {}}).extraction_id)

    def test_dot_paths(self):
        r = ExtractionResult.from_body(SYNC_BODY)
        self.assertEqual(r.get("line_items.0.hsn"), "8471")
        self.assertEqual(r.get("nope.missing", "dflt"), "dflt")
        self.assertIsNone(r.get("a.b.c"))

    def test_sync_result_is_complete(self):
        r = ExtractionResult.from_body(SYNC_BODY)
        self.assertTrue(r.complete)
        self.assertEqual(r.pending_pages, [])
        self.assertIsNone(r.job_id)

    def test_partial_collect_surfaces_pending_and_failed(self):
        r = ExtractionResult.from_body({
            "success": True, "data": {}, "job_id": "parent99", "status": "partial",
            "pending_pages": [46, 47], "failed_pages": [12],
            "warnings": ["mixed_models"], "credits_used": 50,
        })
        self.assertFalse(r.complete)
        self.assertEqual(r.job_id, "parent99")
        self.assertEqual(r.pending_pages, [46, 47])
        self.assertEqual(r.failed_pages, [12])
        self.assertEqual(r.credits_used, 50)

    def test_to_dict_reproduces_envelope(self):
        d = ExtractionResult.from_body(SYNC_BODY).to_dict()
        self.assertEqual(d["data"]["vendor"], "ABC Suppliers")
        self.assertEqual(d["processing_time_ms"], 5577)


def manifest(expires_at):
    return SplitManifest.from_body({
        "success": True,
        "data": [{"job_id": "a1", "pages": "1-3"}, {"job_id": "b2", "pages": "4-6"}],
        "job_id": "parent99", "pages": 6, "expires_at": expires_at,
    })


class TestSplitManifest(unittest.TestCase):
    @staticmethod
    def _iso(offset):
        from datetime import datetime, timedelta, timezone
        return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()

    def test_parses_parent_and_chunks(self):
        m = manifest(self._iso(7200))
        self.assertEqual(m.job_id, "parent99")
        self.assertEqual(m.chunk_count, 2)
        self.assertEqual(m.chunks[0].job_id, "a1")
        self.assertEqual(m.chunks[1].pages, "4-6")

    def test_ttl_future_and_past(self):
        self.assertFalse(manifest(self._iso(3600)).expired)
        self.assertTrue(manifest(self._iso(-60)).expired)

    def test_absent_expiry_is_not_expired(self):
        # Otherwise a valid job would be abandoned.
        m = SplitManifest.from_body({"success": True, "data": [], "job_id": "p"})
        self.assertIsNone(m.expires_at_unix)
        self.assertFalse(m.expired)

    def test_unparseable_expiry_is_not_expired(self):
        m = SplitManifest.from_body({"data": [], "job_id": "p", "expires_at": "not-a-date"})
        self.assertIsNone(m.expires_at_unix)
        self.assertFalse(m.expired)

    def test_malformed_chunks_skipped(self):
        m = SplitManifest.from_body({
            "data": [{"job_id": "a1", "pages": "1-3"}, "garbage", None], "job_id": "p"})
        self.assertEqual(m.chunk_count, 1)


class TestMultipart(unittest.TestCase):
    def test_body_parses_as_real_multipart(self):
        body, ctype = encode_multipart(
            {"options": '{"model":"invoice"}'},
            ("file", b"%PDF-1.4\n%%EOF\n", "/tmp/deep/dir/invoice.pdf"))

        msg = email.message_from_bytes(
            f"Content-Type: {ctype}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body)
        self.assertTrue(msg.is_multipart())

        parts = {p.get_param("name", header="content-disposition"): p for p in msg.get_payload()}
        self.assertEqual(parts["options"].get_payload(decode=True), b'{"model":"invoice"}')
        # Only the basename may be sent — never the caller's local directory structure.
        self.assertEqual(parts["file"].get_filename(), "invoice.pdf")
        self.assertEqual(parts["file"].get_content_type(), "application/pdf")


class TestClientPreflight(unittest.TestCase):
    def test_missing_key_rejected(self):
        with self.assertRaises(RequestError):
            DocXtract("")

    def test_hyphenated_key_caught_with_guidance(self):
        with self.assertRaises(RequestError) as ctx:
            DocXtract("sk-proj-abcdef")
        self.assertIn("underscore", str(ctx.exception))

    def test_missing_file_fails_before_request(self):
        with self.assertRaises(RequestError) as ctx:
            DocXtract(KEY).extract("/nonexistent/invoice.pdf")
        self.assertEqual(ctx.exception.code, "invalid_file")

    def test_unsupported_type_fails_locally(self):
        with tempfile.NamedTemporaryFile(suffix=".gif", delete=False) as fh:
            fh.write(b"GIF89a")
            path = fh.name
        try:
            with self.assertRaises(RequestError) as ctx:
                DocXtract(KEY).extract(path)
            self.assertEqual(ctx.exception.code, "invalid_file_type")
        finally:
            os.unlink(path)

    def test_oversized_file_fails_locally(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
            fh.truncate(11 * 1024 * 1024)   # sparse
            path = fh.name
        try:
            with self.assertRaises(RequestError) as ctx:
                DocXtract(KEY).extract(path)
            self.assertEqual(ctx.exception.code, "file_too_large")
        finally:
            os.unlink(path)

    def test_models_refused_when_pinned_to_v3(self):
        # v3 has no models; failing loudly beats a confusing 404.
        with self.assertRaises(RequestError) as ctx:
            DocXtract(KEY, base_path="/v3").models()
        self.assertIn("v3.1", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
