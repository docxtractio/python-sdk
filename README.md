# docxtract

Official Python client for the [DocXtract](https://docxtract.io) document extraction API.

## Requirements

Python 3.9+. **No dependencies** — standard library only (`urllib`), so `pip install
docxtract` pulls nothing and cannot conflict with your project's pinned `requests` or
`httpx`.

## Install

```bash
pip install docxtract-sdk
```

> **Note** — the package is `docxtract-sdk` but the import is `docxtract`. The shorter name
> was already taken on PyPI by an unrelated DOCX text extractor.

## Get an API key

The SDK is free and open source. The API it talks to needs an account.

1. Go to **[docxtract.io](https://docxtract.io)** and choose **Start Free Trial**
2. Credentials arrive by email — no card required, no sales call
3. Sign in at **[app.docxtract.io](https://app.docxtract.io)** and copy your key from **Settings**

Keep the key in an environment variable, never in source control:

```bash
export DOCXTRACT_API_KEY=sk_your_api_key
```

Two calls cost no credits, so you can confirm setup before spending anything:

```python
dx.authorised()   # is the key active?
dx.models()       # which document types may it use?
```

Need higher limits, more credits, or a custom document type? support@docxtract.io.

## Quickstart

```python
import os
from docxtract import DocXtract

dx = DocXtract(os.environ["DOCXTRACT_API_KEY"])

result = dx.extract("invoice.pdf", model="invoice")

print(result["vendor"])
print(result.get("line_items.0.hsn"))   # dot paths for nested values
print(result.pages, result.extraction_id)
```

> DocXtract keys use an **underscore** (`sk_`). A hyphen after `sk` means the key belongs to
> a different API provider — the SDK rejects it up front rather than letting you debug a 401.

## Large PDFs are the point of this SDK

The API does not process a PDF over 3 pages synchronously. It splits the document and returns
`202` with a chunk manifest; you then call `process` per chunk and `result` to
collect, handling retries, single-flight conflicts, a 2-hour job TTL, and partial results.

`extract()` does all of it:

```python
result = dx.extract("500-page-statement.pdf", model="bank_statement")
```

Same call, any page count. With progress:

```python
dx.extract("big.pdf", model="invoice",
           on_progress=lambda done, total, stage: print(f"{done}/{total}"))
```

### Why chunks run sequentially

The API's default rate limit is 10 requests per minute, so parallel chunk calls do not finish
sooner — they turn the work into `429`s. Pace them if your key is tighter:

```python
dx = DocXtract(key, chunk_pause_ms=500)
```

### Manual control

```python
manifest = dx.split_document("big.pdf", model="invoice")

for chunk in manifest.chunks:
    dx.process_chunk(chunk.job_id, model="invoice")   # safe to retry

result = dx.collect_result(manifest.job_id)

if not result.complete:
    print(result.failed_pages, result.pending_pages)
```

`collect_result()` is a pure read, re-fetchable within the TTL — usable as a progress poll
from a separate worker.

> **`collect_result(job_id, finalize=True)` is irreversible.** It permanently deletes the
> job's extracted data. Only pass it once the result is stored on your side.

## Tabular extractions

Invoice line items and bank statement rows come back as lists of dicts:

```python
df = result.to_dataframe("line_items")   # needs: pip install 'docxtract-sdk[pandas]'
```

With no argument it uses the first row-shaped list it finds in the data.

## Discovering document types

```python
dx.models()   # costs no credits — safe to call freely
```

## Error handling

```python
from docxtract import DocXtractError, RateLimitError, QuotaError

try:
    dx.extract("invoice.pdf", model="invoice")
except RateLimitError as exc:
    time.sleep(exc.retry_after or 30)     # from X-RateLimit-Reset
except QuotaError:
    pass                                   # out of credits — do not retry
except DocXtractError as exc:
    if exc.retryable:
        requeue()
    else:
        raise
```

Or branch on the code:

```python
except DocXtractError as exc:
    match exc.code:
        case "insufficient_credits": notify_billing()
        case "unknown_model":        report_bad_model(exc.details)
        case _:
            if not exc.retryable:
                raise
```

| Exception | Codes |
|---|---|
| `AuthenticationError` | `invalid_api_key`, `expired_api_key` |
| `QuotaError` | `usage_limit_exceeded`, `insufficient_credits` |
| `RateLimitError` | `rate_limit_exceeded`, `too_many_open_jobs` |
| `RequestError` | `invalid_request`, `invalid_file`, `invalid_file_type`, `file_too_large`, `invalid_options`, `unknown_model`, `page_limit_exceeded`, `method_not_allowed` |
| `ExtractionFailedError` | `extraction_failed` |
| `JobError` | `job_not_found`, `job_expired`, `chunk_in_progress`, `chunk_source_lost` |
| `ServerError` | `server_error`, `persist_failed`, `server_busy` |
| `TransportError` | network failure — the API never answered |

An unrecognised code falls back to `DocXtractError` rather than raising, so a new server-side
code cannot break a deployed copy.

> **Billing note.** `extraction_failed` on the synchronous path **still deducts 1 credit**. On
> the multi-page path the chunk stays available and is not charged until it succeeds.
> `persist_failed` charges nothing.

## Configuration

```python
dx = DocXtract(
    api_key=os.environ["DOCXTRACT_API_KEY"],
    base_url="https://api.docxtract.io",   # default — no /api prefix
    base_path="/v3.1",                     # default
    timeout=120,                           # seconds
    max_retries=3,
    chunk_pause_ms=0,
)
```

> If you see a `TransportError` about non-JSON output, the base URL is usually wrong. `/api`
> is the server's docroot, not part of the public path.

`base_path="/v3"` exists for customers still pinned to the old version. v3 has no
`models` and no multi-page support; `models()` raises a clear error rather than a
confusing 404.

## Tests

```bash
python3 -m unittest discover -s tests
```

Offline: no API key or network needed.

## Links

- Documentation — https://docs.docxtract.io
- Interactive API reference — https://app.docxtract.io/api-reference.php
- Support — support@docxtract.io

---

**Built by RPATech** | [docxtract.io](https://docxtract.io)
