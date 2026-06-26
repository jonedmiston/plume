# plume

A command-line tool that OCRs documents with the [Mistral OCR API](https://mistral.ai/news/ocr-4/)
(`mistral-ocr-latest`). It works on a folder of files, supports
both realtime and asynchronous **batch** processing, and writes clean Markdown
alongside the full JSON response.

## Features

- **Realtime or batch.** Realtime processes synchronously; batch submits one
  async job (50% cheaper) and lets you collect results on a later run.
- **Resumable batches.** A batch run writes `.plume-batch.json` into the current
  directory. Run `plume` again there and it checks the job and offers to
  download the finished transcriptions.
- **Directory aware.** Processes the current directory and asks whether to
  include subdirectories (or pass `--recursive`).
- **Prompts *and* flags.** Every setting is asked interactively, and every
  question has a flag so you can skip it. `--yes` runs fully non-interactive.
- **Rich output.** For each document: `<name>.md` (all pages), `<name>.ocr.json`
  (full API response), extracted images, and `<name>.confidence.json` flagging
  any low-confidence pages.

## Install

```bash
pipx install .
# or, without pipx:
pip install .
```

Then set your API key (the only required configuration):

```bash
# PowerShell
$env:MISTRAL_API_KEY = "your-key"
# bash
export MISTRAL_API_KEY="your-key"
```

## Usage

```bash
# Interactive: asks for mode, subdirectories, images, confidence
plume

# OCR a specific folder, non-interactively, in batch mode, recursively
plume ./scans --mode batch --recursive --yes

# A single file
plume invoice.pdf

# Re-run in a folder with a pending batch -> checks status, offers download
plume
```

### Options

| Flag | Question it answers | Default |
|------|--------------------|---------|
| `--mode, -m` | realtime or batch | prompt -> `realtime` |
| `--recursive, -r / --no-recursive` | include subdirectories | prompt -> no |
| `--types, -t` | which extensions (e.g. `pdf,png`) | all supported |
| `--images / --no-images` | extract embedded images | prompt -> yes |
| `--confidence / --no-confidence` | save confidence + flag low pages | prompt -> yes |
| `--force, -f` | reprocess files that already have output | off (skip done) |
| `--output-dir, -o` | where output is written | `./ocr-output` |
| `--model` | OCR model id | `mistral-ocr-latest` |
| `--yes, -y` | accept defaults, never prompt | off |
| `--download / --no-download` | answer the resume download prompt | prompt |
| `--new` | start a new run even if a batch is pending | off |

## Supported inputs

`.pdf`, `.png`, `.jpg`, `.jpeg`, `.avif`. Images are sent inline; PDFs are
uploaded and referenced by a signed URL.

## Output layout

```
ocr-output/
  sub/report.md            # all pages, with images relinked
  sub/report.ocr.json      # full Mistral response
  sub/report.confidence.json
  sub/report_images/       # extracted figures
```
