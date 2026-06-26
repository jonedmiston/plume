# Using the `plume` OCR tool (instructions for an AI agent)

`plume` is a command-line tool that OCRs documents (PDFs and images) with the
Mistral OCR API and writes the results to disk. Use it whenever you need to turn
scanned PDFs or images into text/Markdown.

## Before you start

- **The command is `plume`.** If `plume` is not found, the equivalent is
  `python -m plume`.
- **An API key must be set** in the `MISTRAL_API_KEY` environment variable. Do
  not print, log, or echo this value. If it is missing, `plume` exits with the
  message `No API key. Set the MISTRAL_API_KEY environment variable.` — stop and
  tell the user to set it; do not try to invent one.
- **Supported input types:** `.pdf`, `.png`, `.jpg`, `.jpeg`, `.avif`. Anything
  else is ignored.

## The one rule that matters most: never run it interactively

By default `plume` asks interactive questions, which will hang an agent. **Always
pass `--yes`** so it never prompts, plus explicit flags for anything you don't
want left to defaults. With `--yes` the defaults are: realtime mode, current
directory only (no subdirectories), extract images on, confidence on.

## Output

For each input file, output is written under `./ocr-output/`, mirroring the
input's folder structure:

```
ocr-output/
  <name>.md               # the OCR text as Markdown (this is the main result)
  <name>.ocr.json         # full raw API response
  <name>.confidence.json  # per-page confidence + list of low-confidence pages
  <name>_images/          # any figures/images extracted from the document
```

Read the `.md` file for the transcribed text. Check `.confidence.json` ->
`low_confidence_pages` to know which pages may be unreliable and worth a human
review.

`plume` **skips files that already have output** unless you pass `--force`. This
makes re-runs safe and cheap.

## Mode 1: Realtime (default — use this unless told otherwise)

Processes synchronously and writes results before the command returns. Best for
small/medium jobs and when you need the text immediately.

```bash
# OCR every supported file in a folder (current dir only)
plume ./path/to/folder --yes

# Include subdirectories
plume ./path/to/folder --recursive --yes

# A single file
plume ./invoice.pdf --yes

# Only PDFs; skip image extraction; force re-processing
plume ./folder --types pdf --no-images --force --yes
```

After it finishes, read the `.md` files under `./ocr-output/`.

## Mode 2: Batch (asynchronous, ~50% cheaper, for large jobs)

Batch is **stateful and spans multiple runs.** It works like this:

1. **Submit.** Run from the directory where you want the tracking file kept:
   ```bash
   plume ./folder --mode batch --recursive --yes
   ```
   This uploads the documents, creates one batch job, and writes a tracking file
   named `.plume-batch.json` in the current working directory. The command
   returns immediately — results are NOT ready yet.

2. **Wait.** The job runs server-side. Do not poll in a tight loop; wait a while
   (minutes, depending on volume) before checking.

3. **Check / download.** Run `plume` again **in the same directory** (the one
   containing `.plume-batch.json`):
   ```bash
   plume --yes
   ```
   - Because a `.plume-batch.json` exists, `plume` enters resume mode instead of
     starting a new job.
   - If the job is finished, it downloads the results, writes them to
     `./ocr-output/`, and removes the tracking file.
   - If it is not finished, it prints the status (e.g. `QUEUED`/`RUNNING`) and
     exits without writing results. Wait longer and run `plume --yes` again.

   With `--yes`, the download is auto-confirmed and the tracking file is auto
   removed once everything is downloaded.

**Important batch caveats:**
- While `.plume-batch.json` exists in a directory, a plain `plume` run there will
  ALWAYS try to resume that job, not start a new one. To start a new job anyway,
  add `--new`.
- The tracking file records absolute paths. Keep working from the same directory
  and don't move the source files until results are downloaded.

## Full flag reference

| Flag | Effect | Default |
|------|--------|---------|
| `PATH` (positional) | File or directory to OCR | current dir |
| `--mode, -m {realtime,batch}` | Processing mode | `realtime` |
| `--recursive, -r` / `--no-recursive` | Include subdirectories | off |
| `--types, -t pdf,png` | Restrict to these extensions | all supported |
| `--images` / `--no-images` | Extract embedded images | on |
| `--confidence` / `--no-confidence` | Write confidence + flag low pages | on |
| `--force, -f` | Re-process even if output exists | off (skip done) |
| `--output-dir, -o DIR` | Output location | `./ocr-output` |
| `--model NAME` | OCR model id | `mistral-ocr-latest` |
| `--concurrency, -c N` | Parallel uploads in batch mode | 8 |
| `--yes, -y` | Never prompt; accept defaults | off — **always pass this** |
| `--download` / `--no-download` | Auto-answer the resume download prompt | prompt |
| `--new` | Start a new run even if a batch is pending | off |

## How to tell it worked

- Exit code `0` and new/updated `.md` files under `./ocr-output/`.
- `No matching documents found.` (exit 0) means nothing supported was in the
  path, or everything was already processed (use `--force` to redo).
- `No API key...` (exit 1) means `MISTRAL_API_KEY` is not set.

## Recommended default behavior

Unless the user specifies otherwise:
1. Use **realtime** mode.
2. Run `plume <path> --yes` (add `--recursive` if they want subfolders).
3. Report which files were written and call out any pages listed in a
   `.confidence.json` `low_confidence_pages` array as needing review.
