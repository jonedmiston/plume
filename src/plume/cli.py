"""Command-line interface for plume."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn
from rich.prompt import Confirm, Prompt

from . import batch_state, client, output
from .config import (
    API_KEY_ENV,
    DEFAULT_MODEL,
    DEFAULT_OUTPUT_DIR,
    SUPPORTED_EXTS,
    Settings,
    get_api_key,
)
from .discovery import find_documents

app = typer.Typer(
    add_completion=False,
    help="OCR documents with the Mistral OCR API, in realtime or batch.",
)
console = Console()

# Statuses the Mistral batch API reports as finished-with-output.
_DONE_OK = {"SUCCESS"}
_DONE_BAD = {"FAILED", "TIMEOUT_EXCEEDED", "CANCELLED"}


def _parse_types(types: Optional[str]) -> set[str]:
    if not types:
        return set(SUPPORTED_EXTS)
    exts = set()
    for token in types.replace(" ", "").split(","):
        if not token:
            continue
        exts.add(token if token.startswith(".") else f".{token.lower()}")
    return exts


@app.command()
def main(
    path: Path = typer.Argument(Path("."), help="File or directory to OCR (default: current directory)."),
    mode: Optional[str] = typer.Option(None, "--mode", "-m", help="realtime | batch."),
    recursive: Optional[bool] = typer.Option(None, "--recursive/--no-recursive", "-r", help="Include subdirectories."),
    types: Optional[str] = typer.Option(None, "--types", "-t", help="Comma list of extensions, e.g. pdf,png. Default: all supported."),
    include_images: Optional[bool] = typer.Option(None, "--images/--no-images", help="Extract embedded images."),
    include_confidence: Optional[bool] = typer.Option(None, "--confidence/--no-confidence", help="Write confidence summary + flag low-confidence pages."),
    force: bool = typer.Option(False, "--force", "-f", help="Re-process files even if output already exists."),
    output_dir: Path = typer.Option(Path(DEFAULT_OUTPUT_DIR), "--output-dir", "-o", help="Where to write output."),
    model: str = typer.Option(DEFAULT_MODEL, "--model", help="OCR model id."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept defaults; never prompt."),
    download: Optional[bool] = typer.Option(None, "--download/--no-download", help="In resume mode, answer the download prompt without asking."),
    new: bool = typer.Option(False, "--new", help="Start a new run even if a pending batch file exists."),
) -> None:
    """Process documents, or resume a pending batch when one is tracked here."""
    cwd = Path.cwd()

    # Resume flow takes priority: a tracking file means there may be results to fetch.
    if batch_state.exists(cwd) and not new:
        _resume(cwd, download=download, yes=yes)
        return

    api_key = get_api_key()
    if not api_key:
        console.print(f"[red]No API key.[/red] Set the [bold]{API_KEY_ENV}[/bold] environment variable.")
        raise typer.Exit(code=1)

    settings = _resolve_settings(
        path=path,
        mode=mode,
        recursive=recursive,
        types=types,
        include_images=include_images,
        include_confidence=include_confidence,
        force=force,
        output_dir=output_dir,
        model=model,
        yes=yes,
    )

    documents = _collect_documents(settings)
    if not documents:
        console.print("[yellow]No matching documents found.[/yellow]")
        raise typer.Exit()

    mc = client.make_client(api_key)
    if settings.mode == "batch":
        _submit_batch(mc, settings, documents, cwd)
    else:
        _run_realtime(mc, settings, documents)


# --- Settings resolution ---------------------------------------------------


def _resolve_settings(*, path, mode, recursive, types, include_images, include_confidence, force, output_dir, model, yes) -> Settings:
    is_dir = path.is_dir()

    if mode is None:
        mode = "realtime" if yes else Prompt.ask(
            "Mode", choices=["realtime", "batch"], default="realtime"
        )
    if mode not in ("realtime", "batch"):
        console.print(f"[red]Invalid mode '{mode}'. Use realtime or batch.[/red]")
        raise typer.Exit(code=1)

    if recursive is None and is_dir:
        recursive = False if yes else Confirm.ask("Include subdirectories?", default=False)
    recursive = bool(recursive)

    if include_images is None:
        include_images = True if yes else Confirm.ask("Extract embedded images?", default=True)
    if include_confidence is None:
        include_confidence = True if yes else Confirm.ask("Save confidence + flag low-confidence pages?", default=True)

    return Settings(
        path=path,
        mode=mode,
        recursive=recursive,
        extensions=_parse_types(types),
        include_images=bool(include_images),
        include_confidence=bool(include_confidence),
        force=force,
        output_dir=output_dir,
        model=model,
    )


def _collect_documents(settings: Settings) -> list[Path]:
    if settings.path.is_file():
        docs = [settings.path]
        root = settings.path.parent
    else:
        docs = find_documents(
            settings.path,
            recursive=settings.recursive,
            extensions=settings.extensions,
            output_dir=settings.resolved_output_dir(),
        )
        root = settings.path

    if settings.force:
        return docs

    out_dir = settings.resolved_output_dir()
    kept, skipped = [], 0
    for d in docs:
        if output.already_done(d, root, out_dir):
            skipped += 1
        else:
            kept.append(d)
    if skipped:
        console.print(f"[dim]Skipping {skipped} already-processed file(s). Use --force to redo.[/dim]")
    return kept


# --- Realtime --------------------------------------------------------------


def _run_realtime(mc, settings: Settings, documents: list[Path]) -> None:
    root = settings.path.parent if settings.path.is_file() else settings.path
    out_dir = settings.resolved_output_dir()
    flagged: list[tuple[Path, list[int]]] = []
    errors: list[tuple[Path, str]] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("OCR", total=len(documents))
        for doc in documents:
            progress.update(task, description=f"OCR {doc.name}")
            try:
                document = client.build_document(mc, doc)
                body = client.ocr_realtime(
                    mc,
                    model=settings.model,
                    document=document,
                    include_images=settings.include_images,
                    confidence_granularity="page" if settings.include_confidence else None,
                )
                result = output.write_outputs(
                    doc, root, out_dir, body,
                    include_images=settings.include_images,
                    include_confidence=settings.include_confidence,
                )
                if result.low_confidence_pages:
                    flagged.append((doc, result.low_confidence_pages))
            except Exception as exc:  # noqa: BLE001 - report per file, keep going
                errors.append((doc, str(exc)))
            finally:
                progress.advance(task)

    console.print(f"[green]Done.[/green] Wrote output to [bold]{out_dir}[/bold].")
    _report_flags_and_errors(flagged, errors)


# --- Batch -----------------------------------------------------------------


def _submit_batch(mc, settings: Settings, documents: list[Path], cwd: Path) -> None:
    root = settings.path.parent if settings.path.is_file() else settings.path
    entries = []
    items = []
    upload_errors: list[tuple[Path, str]] = []

    # For batch we upload every file and reference it by signed URL, so the job
    # file stays tiny regardless of how many (or how large) the inputs are.
    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
        BarColumn(), MofNCompleteColumn(), console=console,
    ) as progress:
        task = progress.add_task("Uploading", total=len(documents))
        for i, doc in enumerate(documents):
            progress.update(task, description=f"Uploading {doc.name}")
            try:
                document = client.build_document(mc, doc, upload_images=True)
            except Exception as exc:  # noqa: BLE001 - skip the file, keep going
                upload_errors.append((doc, str(exc)))
                progress.advance(task)
                continue
            custom_id = f"doc-{i:04d}"
            entries.append({"custom_id": custom_id, "document": document})
            items.append(batch_state.BatchItem(custom_id=custom_id, source=_rel(doc, root)))
            progress.advance(task)

    if upload_errors:
        console.print(f"[yellow]Skipped {len(upload_errors)} file(s) that failed to upload:[/yellow]")
        for doc, msg in upload_errors[:10]:
            console.print(f"  {doc.name}: {msg}")
        if len(upload_errors) > 10:
            console.print(f"  ...and {len(upload_errors) - 10} more.")

    if not entries:
        console.print("[red]Nothing was uploaded successfully; no batch job created.[/red]")
        raise typer.Exit(code=1)

    jsonl = client.build_batch_jsonl(
        entries,
        include_images=settings.include_images,
        confidence_granularity="page" if settings.include_confidence else None,
    )
    try:
        job_id = client.create_batch_job(mc, jsonl=jsonl, model=settings.model)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Failed to create the batch job:[/red] {exc}")
        raise typer.Exit(code=1)

    job = batch_state.BatchJob(
        job_id=job_id,
        model=settings.model,
        created_at=datetime.now(timezone.utc).isoformat(),
        root=str(root.resolve()),
        output_dir=str(settings.resolved_output_dir().resolve()),
        include_images=settings.include_images,
        include_confidence=settings.include_confidence,
        items=items,
    )
    batch_state.append(cwd, job)

    console.print(Panel.fit(
        f"Submitted [bold]{len(items)}[/bold] document(s) as batch job [bold]{job_id}[/bold].\n"
        f"Tracking saved to [bold]{batch_state.TRACKING_FILENAME}[/bold].\n\n"
        "Run [bold]plume[/bold] again in this folder to check status and download results.",
        title="Batch submitted", border_style="green",
    ))


# --- Resume / download -----------------------------------------------------


def _resume(cwd: Path, *, download: Optional[bool], yes: bool) -> None:
    api_key = get_api_key()
    if not api_key:
        console.print(f"[red]No API key.[/red] Set the [bold]{API_KEY_ENV}[/bold] environment variable.")
        raise typer.Exit(code=1)

    mc = client.make_client(api_key)
    jobs = batch_state.load(cwd)
    pending = [j for j in jobs if not j.completed]

    if not pending:
        console.print("[green]All tracked batch jobs are already downloaded.[/green]")
        if yes or Confirm.ask(f"Remove {batch_state.TRACKING_FILENAME}?", default=True):
            batch_state.remove_file(cwd)
        return

    any_changed = False
    for job in pending:
        remote = client.get_batch_job(mc, job.job_id)
        status = getattr(remote, "status", "UNKNOWN")
        console.print(f"Job [bold]{job.job_id}[/bold]: [cyan]{status}[/cyan] ({len(job.items)} document(s))")

        if status in _DONE_BAD:
            console.print(f"  [red]Job ended as {status}; nothing to download.[/red]")
            continue
        if status not in _DONE_OK:
            console.print("  [yellow]Not finished yet. Run plume again later.[/yellow]")
            continue

        do_download = download if download is not None else (yes or Confirm.ask("  Download transcriptions?", default=True))
        if not do_download:
            continue

        output_file = getattr(remote, "output_file", None)
        if not output_file:
            console.print("  [red]Job reports success but has no output file.[/red]")
            continue

        _download_job(mc, job, output_file)
        job.completed = True
        any_changed = True

    if any_changed:
        batch_state.save(cwd, jobs)

    if all(j.completed for j in jobs):
        if yes or Confirm.ask(f"All jobs done. Remove {batch_state.TRACKING_FILENAME}?", default=True):
            batch_state.remove_file(cwd)


def _download_job(mc, job: batch_state.BatchJob, output_file_id: str) -> None:
    results = client.download_batch_results(mc, output_file_id)
    root = Path(job.root)
    out_dir = Path(job.output_dir)
    by_id = {item.custom_id: item for item in job.items}

    flagged: list[tuple[Path, list[int]]] = []
    errors: list[tuple[Path, str]] = []

    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
        BarColumn(), MofNCompleteColumn(), console=console,
    ) as progress:
        task = progress.add_task("Writing", total=len(by_id))
        for custom_id, item in by_id.items():
            source = root / item.source
            record = results.get(custom_id)
            try:
                body = _extract_body(record)
                if body is None:
                    raise ValueError("no successful response for this document")
                result = output.write_outputs(
                    source, root, out_dir, body,
                    include_images=job.include_images,
                    include_confidence=job.include_confidence,
                )
                if result.low_confidence_pages:
                    flagged.append((source, result.low_confidence_pages))
            except Exception as exc:  # noqa: BLE001
                errors.append((source, str(exc)))
            finally:
                progress.advance(task)

    console.print(f"  [green]Wrote results to[/green] [bold]{out_dir}[/bold].")
    _report_flags_and_errors(flagged, errors)


def _extract_body(record) -> Optional[dict]:
    if not record:
        return None
    response = record.get("response") or {}
    if response.get("status_code") not in (None, 200):
        return None
    return response.get("body")


# --- Shared helpers --------------------------------------------------------


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return path.name


def _report_flags_and_errors(flagged, errors) -> None:
    if flagged:
        console.print("[yellow]Low-confidence pages flagged:[/yellow]")
        for doc, pages in flagged:
            console.print(f"  {doc.name}: pages {', '.join(map(str, pages))}")
    if errors:
        console.print("[red]Errors:[/red]")
        for doc, msg in errors:
            console.print(f"  {doc.name}: {msg}")


if __name__ == "__main__":
    app()
