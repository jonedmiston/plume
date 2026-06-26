"""Write OCR results to disk: markdown, full JSON, images and confidence."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import LOW_CONFIDENCE_THRESHOLD


@dataclass
class WriteResult:
    markdown_path: Path
    json_path: Path
    image_count: int
    low_confidence_pages: list[int]


def output_paths(source: Path, root: Path, output_dir: Path) -> dict[str, Path]:
    """Compute mirrored output paths for a source file."""
    try:
        rel = source.resolve().relative_to(root.resolve())
    except ValueError:
        rel = Path(source.name)

    base = output_dir / rel
    stem_dir = base.parent
    return {
        "markdown": base.with_suffix(".md"),
        "json": base.with_suffix(".ocr.json"),
        "confidence": base.with_suffix(".confidence.json"),
        "images": stem_dir / f"{base.stem}_images",
    }


def already_done(source: Path, root: Path, output_dir: Path) -> bool:
    return output_paths(source, root, output_dir)["markdown"].exists()


def _decode_image(image_b64: str) -> bytes:
    if "base64," in image_b64:
        image_b64 = image_b64.split("base64,", 1)[1]
    return base64.b64decode(image_b64)


def _find_confidences(node: Any) -> list[float]:
    """Recursively collect numeric values under any 'confidence' key.

    OCR 4 returns confidence at page and word granularity; field names vary by
    response shape, so this is a best-effort sweep over the page object.
    """
    found: list[float] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if "confidence" in key.lower() and isinstance(value, (int, float)):
                found.append(float(value))
            else:
                found.extend(_find_confidences(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_find_confidences(item))
    return found


def write_outputs(
    source: Path,
    root: Path,
    output_dir: Path,
    body: dict[str, Any],
    *,
    include_images: bool,
    include_confidence: bool,
) -> WriteResult:
    paths = output_paths(source, root, output_dir)
    paths["markdown"].parent.mkdir(parents=True, exist_ok=True)

    pages = body.get("pages", []) or []
    image_count = 0
    low_confidence_pages: list[int] = []
    confidence_summary: list[dict[str, Any]] = []
    md_chunks: list[str] = []

    for page in pages:
        index = page.get("index", len(md_chunks))
        markdown = page.get("markdown", "") or ""

        if include_images:
            saved = _save_page_images(page, paths["images"], page_index=index)
            image_count += len(saved)
            for image_id, rel_link in saved.items():
                markdown = _relink_image(markdown, image_id, rel_link)

        if include_confidence:
            scores = _find_confidences(page)
            if scores:
                mean = sum(scores) / len(scores)
                low = mean < LOW_CONFIDENCE_THRESHOLD
                if low:
                    low_confidence_pages.append(index)
                confidence_summary.append(
                    {
                        "page": index,
                        "mean_confidence": round(mean, 4),
                        "min_confidence": round(min(scores), 4),
                        "samples": len(scores),
                        "below_threshold": low,
                    }
                )

        header = f"<!-- page {index} -->"
        md_chunks.append(f"{header}\n\n{markdown}".rstrip())

    paths["markdown"].write_text("\n\n---\n\n".join(md_chunks) + "\n", encoding="utf-8")
    paths["json"].write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")

    if include_confidence:
        paths["confidence"].write_text(
            json.dumps(
                {
                    "threshold": LOW_CONFIDENCE_THRESHOLD,
                    "low_confidence_pages": low_confidence_pages,
                    "pages": confidence_summary,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    return WriteResult(
        markdown_path=paths["markdown"],
        json_path=paths["json"],
        image_count=image_count,
        low_confidence_pages=low_confidence_pages,
    )


def _save_page_images(page: dict[str, Any], images_dir: Path, *, page_index: int) -> dict[str, str]:
    """Save a page's embedded images. Returns {image_id: markdown_relative_link}."""
    links: dict[str, str] = {}
    for image in page.get("images", []) or []:
        b64 = image.get("image_base64")
        if not b64:
            continue
        image_id = image.get("id") or f"img-{len(links)}"
        filename = f"p{page_index}-{image_id}"
        if not Path(filename).suffix:
            filename += ".png"

        images_dir.mkdir(parents=True, exist_ok=True)
        (images_dir / filename).write_bytes(_decode_image(b64))
        links[image_id] = f"{images_dir.name}/{filename}"
    return links


def _relink_image(markdown: str, image_id: str, rel_link: str) -> str:
    """Point a markdown image reference at the saved file."""
    # Replace the target inside ](...) for this id, plus any bare occurrences.
    pattern = re.compile(r"(!\[[^\]]*\]\()" + re.escape(image_id) + r"(\))")
    markdown = pattern.sub(lambda m: m.group(1) + rel_link + m.group(2), markdown)
    return markdown
