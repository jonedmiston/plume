"""Thin wrapper around the Mistral SDK for OCR, file upload and batch jobs."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any, Callable

try:  # SDK 1.x exposes the client at the top level
    from mistralai import Mistral
except ImportError:  # SDK 2.x moved it under mistralai.client
    from mistralai.client import Mistral

from .config import IMAGE_EXTS

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".avif": "image/avif",
    ".pdf": "application/pdf",
}


#: Per-call upload timeout (ms) and how many times to retry a flaky request.
UPLOAD_TIMEOUT_MS = 300_000
MAX_ATTEMPTS = 4


def make_client(api_key: str) -> Mistral:
    return Mistral(api_key=api_key)


def _with_retry(action: Callable[[], Any], *, what: str) -> Any:
    """Run ``action`` with exponential backoff for transient network errors.

    The SDK retries on 429/5xx status codes already, but a dropped/aborted
    connection surfaces as an exception, so we retry those ourselves.
    """
    last: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 - retry any transport error
            last = exc
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"{what} failed after {MAX_ATTEMPTS} attempts: {last}") from last


def _data_uri(path: Path) -> str:
    mime = _MIME_BY_EXT.get(path.suffix.lower(), "application/octet-stream")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def upload_and_sign(client: Mistral, path: Path) -> str:
    """Upload a file for OCR and return a short-lived signed URL to it."""
    def _do() -> str:
        uploaded = client.files.upload(
            file={"file_name": path.name, "content": path.read_bytes()},
            purpose="ocr",
            timeout_ms=UPLOAD_TIMEOUT_MS,
        )
        # Keep URLs valid well beyond the 24h default so a queued batch that
        # runs slowly doesn't hit expired links.
        signed = client.files.get_signed_url(file_id=uploaded.id, expiry=168)
        return signed.url

    return _with_retry(_do, what=f"upload {path.name}")


def upload_file_id(client: Mistral, path: Path) -> str:
    """Upload a file for OCR and return its file id."""
    def _do() -> str:
        uploaded = client.files.upload(
            file={"file_name": path.name, "content": path.read_bytes()},
            purpose="ocr",
            timeout_ms=UPLOAD_TIMEOUT_MS,
        )
        return uploaded.id

    return _with_retry(_do, what=f"upload {path.name}")


def build_document(client: Mistral, path: Path) -> dict[str, Any]:
    """Build the ``document`` payload for a realtime OCR request.

    Images are embedded inline as base64 (one small request); PDFs are uploaded
    and referenced by a short-lived signed URL.
    """
    if path.suffix.lower() in IMAGE_EXTS:
        return {"type": "image_url", "image_url": _data_uri(path)}
    return {"type": "document_url", "document_url": upload_and_sign(client, path)}


def build_batch_document(client: Mistral, path: Path) -> dict[str, Any]:
    """Build the ``document`` payload for a batch OCR request.

    The batch endpoint requires files be referenced by ``file_id`` (it rejects
    inline base64 and signed URLs), so every file is uploaded first. This also
    keeps the batch JSONL tiny regardless of input count or size.
    """
    return {"type": "file", "file_id": upload_file_id(client, path)}


def ocr_realtime(
    client: Mistral,
    *,
    model: str,
    document: dict[str, Any],
    include_images: bool,
    confidence_granularity: str | None = None,
) -> dict[str, Any]:
    """Run a synchronous OCR request and return the response as a plain dict."""
    kwargs: dict[str, Any] = {
        "model": model,
        "document": document,
        "include_image_base64": include_images,
    }
    if confidence_granularity:
        kwargs["confidence_scores_granularity"] = confidence_granularity
    response = client.ocr.process(**kwargs)
    return response.model_dump()


# --- Batch -----------------------------------------------------------------


def build_batch_jsonl(
    entries: list[dict[str, Any]],
    include_images: bool,
    confidence_granularity: str | None = None,
) -> bytes:
    """Serialize batch entries (``{"custom_id", "document"}``) to JSONL bytes."""
    lines = []
    for entry in entries:
        body: dict[str, Any] = {
            "document": entry["document"],
            "include_image_base64": include_images,
        }
        if confidence_granularity:
            body["confidence_scores_granularity"] = confidence_granularity
        line = {"custom_id": entry["custom_id"], "body": body}
        lines.append(json.dumps(line, ensure_ascii=False))
    return ("\n".join(lines) + "\n").encode("utf-8")


def create_batch_job(client: Mistral, *, jsonl: bytes, model: str) -> str:
    """Upload the JSONL and create a ``/v1/ocr`` batch job. Returns the job id."""
    def _upload() -> str:
        uploaded = client.files.upload(
            file={"file_name": "plume-batch.jsonl", "content": jsonl},
            purpose="batch",
            timeout_ms=UPLOAD_TIMEOUT_MS,
        )
        return uploaded.id

    input_id = _with_retry(_upload, what="upload batch job file")
    job = _with_retry(
        lambda: client.batch.jobs.create(
            input_files=[input_id],
            model=model,
            endpoint="/v1/ocr",
            metadata={"tool": "plume"},
        ),
        what="create batch job",
    )
    return job.id


def get_batch_job(client: Mistral, job_id: str) -> Any:
    return client.batch.jobs.get(job_id=job_id)


def download_batch_results(client: Mistral, output_file_id: str) -> dict[str, dict[str, Any]]:
    """Download a finished job's output file and map ``custom_id`` -> OCR body."""
    raw = client.files.download(file_id=output_file_id).read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")

    results: dict[str, dict[str, Any]] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        results[record["custom_id"]] = record
    return results
