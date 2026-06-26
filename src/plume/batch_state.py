"""Read and write the batch-tracking file in the working directory."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import TRACKING_FILENAME


@dataclass
class BatchItem:
    custom_id: str
    source: str  # path relative to the run root, for display + re-mirroring


@dataclass
class BatchJob:
    job_id: str
    model: str
    created_at: str
    root: str
    output_dir: str
    include_images: bool
    include_confidence: bool
    items: list[BatchItem] = field(default_factory=list)
    completed: bool = False


def tracking_path(cwd: Path) -> Path:
    return cwd / TRACKING_FILENAME


def exists(cwd: Path) -> bool:
    return tracking_path(cwd).exists()


def load(cwd: Path) -> list[BatchJob]:
    data = json.loads(tracking_path(cwd).read_text(encoding="utf-8"))
    jobs = []
    for raw in data.get("jobs", []):
        items = [BatchItem(**i) for i in raw.pop("items", [])]
        jobs.append(BatchJob(items=items, **raw))
    return jobs


def save(cwd: Path, jobs: list[BatchJob]) -> None:
    payload = {"jobs": [_job_to_dict(j) for j in jobs]}
    tracking_path(cwd).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append(cwd: Path, job: BatchJob) -> None:
    jobs = load(cwd) if exists(cwd) else []
    jobs.append(job)
    save(cwd, jobs)


def remove_file(cwd: Path) -> None:
    tracking_path(cwd).unlink(missing_ok=True)


def _job_to_dict(job: BatchJob) -> dict[str, Any]:
    d = asdict(job)
    return d
