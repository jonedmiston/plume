"""Settings model and discovery defaults for plume."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

#: Environment variable that must hold the Mistral API key.
API_KEY_ENV = "MISTRAL_API_KEY"

#: Default OCR model. ``mistral-ocr-latest`` tracks the newest OCR model.
#: For a guaranteed-v4 pin, pass ``--model mistral-ocr-4-0``.
DEFAULT_MODEL = "mistral-ocr-latest"

#: Default folder (relative to the working directory) for written output.
DEFAULT_OUTPUT_DIR = "ocr-output"

#: Filename of the batch-tracking file written into the working directory.
TRACKING_FILENAME = ".plume-batch.json"

#: Pages whose mean confidence falls below this are flagged for review.
LOW_CONFIDENCE_THRESHOLD = 0.85

# Supported input extensions grouped by how plume submits them to the API.
# Images are sent inline as base64; documents are uploaded and referenced by
# a signed URL.
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".avif"}
DOCUMENT_EXTS = {".pdf"}
SUPPORTED_EXTS = IMAGE_EXTS | DOCUMENT_EXTS


@dataclass
class Settings:
    """Resolved run configuration after merging flags, prompts and defaults."""

    path: Path
    mode: str = "realtime"  # "realtime" | "batch"
    recursive: bool = False
    extensions: set[str] = field(default_factory=lambda: set(SUPPORTED_EXTS))
    include_images: bool = True
    include_confidence: bool = True
    force: bool = False
    output_dir: Path = field(default_factory=lambda: Path(DEFAULT_OUTPUT_DIR))
    model: str = DEFAULT_MODEL

    def resolved_output_dir(self) -> Path:
        return self.output_dir if self.output_dir.is_absolute() else self.path / self.output_dir


def get_api_key() -> str | None:
    """Return the Mistral API key from the environment, or ``None`` if unset."""
    key = os.environ.get(API_KEY_ENV, "").strip()
    return key or None
