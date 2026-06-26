"""Find input documents under a directory."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


def find_documents(
    root: Path,
    *,
    recursive: bool,
    extensions: Iterable[str],
    output_dir: Path,
) -> list[Path]:
    """Return supported input files under ``root``.

    The configured ``output_dir`` is always skipped so plume never tries to
    OCR its own output on a re-run.
    """
    exts = {e.lower() for e in extensions}
    output_dir = output_dir.resolve()

    candidates = root.rglob("*") if recursive else root.glob("*")
    found: list[Path] = []
    for p in candidates:
        if not p.is_file():
            continue
        if p.suffix.lower() not in exts:
            continue
        resolved = p.resolve()
        if resolved == output_dir or output_dir in resolved.parents:
            continue
        found.append(p)

    return sorted(found)
