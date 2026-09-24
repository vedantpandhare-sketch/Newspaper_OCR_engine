# Newspaper OCR Engine

Automated pipeline that scrapes daily Marathi e-paper editions (Loksatta, Lokmat), extracts article text with PaddleOCR, archives the raw extraction in MongoDB, and indexes chunked, searchable vectors in Pinecone. Includes a Streamlit control panel for manual runs and ad-hoc semantic search over the indexed papers.

## How it works

The pipeline is split into two halves that run on **different machines**, because the source site (`tradingref.com`) puts its PDF generator behind Cloudflare, and Cloudflare reliably blocks GitHub Actions' datacenter IPs. The split works around that:

```
┌─────────────────────────────┐        ┌──────────────────────────────────────────┐
│   LOCAL MACHINE (Windows)    │        │        GITHUB ACTIONS (cloud)             │
│   residential IP             │        │                                            │
│                               │        │                                            │
│  1. scraper.py                │        │  3. run_process_only_cli.py               │
│     Playwright + stealth      │        │     Polls the Drive folder for today's    │
│     passes Cloudflare,        │        │     PDFs                                  │
│     downloads Loksatta &      │  Drive │                                            │
│     Lokmat PDFs               │───────▶│  4. ocr_pipeline.py (PaddleOCR)           │
│                               │ upload │     Extracts headline + paragraph text     │
│  2. local_scrape_and_notify.py│        │     per article, per page                 │
│     fires a GitHub             │────────▶  5. db_mongo.py                          │
│     repository_dispatch event  │ trigger│     Stores raw OCR JSON per newspaper     │
│     so step 3 starts instantly │        │     in a date-named MongoDB collection    │
└─────────────────────────────┘        │                                            │
                                         │  6. pipeingest.py                         │
                                         │     Chunks article text and upserts       │
                                         │     vectors into a dedicated Pinecone     │
                                         │     index per newspaper                   │
                                         └──────────────────────────────────────────┘
```

If the `repository_dispatch` trigger doesn't fire (network hiccup, script not run, etc.), the GitHub Actions workflow also runs on a daily cron schedule as a fallback, polling Drive for the day's files.

`main.py` (Streamlit) and `run_pipeline_cli.py` can instead run the **entire** pipeline (scrape → OCR → Mongo → Pinecone) end-to-end on one machine — useful for local testing, backfills, or if you don't want the two-machine split at all. They just need to run somewhere Cloudflare will actually let the scraper through (i.e., not a cloud/datacenter IP).

## Project structure

| File | Purpose |
|---|---|
| `main.py` | Streamlit UI: run the full pipeline with live logs/progress, plus a Pinecone semantic search box. |
| `scraper.py` | Playwright-based scraper for `tradingref.com` (Cloudflare Turnstile handling, stealth evasion, retry logic). Downloads Loksatta & Lokmat PDFs and uploads them to Google Drive. |
| `ocr_pipeline.py` | PaddleOCR (Marathi/Devanagari model) extraction. Renders each PDF page at high DPI, segments it into a 2D article/column grid, batches OCR across segments, and splits each block into heading + paragraphs. |
| `db_mongo.py` | MongoDB helpers: saves raw OCR JSON into a date-named collection, and tracks which documents have already been vectorized (to avoid burning Pinecone embedding quota on retries). |
| `pipeingest.py` | Chunks extracted article text (word-boundary aware, with overlap), resolves a per-article date, and upserts records into a per-newspaper Pinecone index with retry/backoff on rate limits. |
| `pipeline_core.py` | Shared Phase 2–4 logic (OCR → Mongo → Pinecone) used by both `run_pipeline_cli.py` and `run_process_only_cli.py`, so the two entrypoints can't drift apart. |
| `drive_utils.py` | Google Drive OAuth + upload/download helpers, used to hand scraped PDFs off from the local machine to the CI job. |
| `run_pipeline_cli.py` | Headless CLI: full local run (scrape + process). Run this on a machine with a normal residential IP. |
| `run_process_only_cli.py` | Headless CLI: CI-side run (fetch from Drive + process only). This is what GitHub Actions runs. Never touches the scraper/Cloudflare. |
| `local_scrape_and_notify.py` | Scrape-only entrypoint meant for a local scheduled task: scrapes, uploads to Drive, then fires a `repository_dispatch` event to kick off the GitHub Actions job immediately. |
| `new_scraper.py` | Standalone experimental scraper that pulls Loksatta's high-res page images directly via its epaper API and assembles them into a PDF, bypassing the `tradingref.com` form flow. Not yet wired into the main pipeline. |
| `scripts/run_daily_scrape.ps1` | Wrapper invoked by Windows Task Scheduler: runs `local_scrape_and_notify.py` with the project venv, logs output to `logs/`. |
| `scripts/setup_scheduled_task.ps1` | One-time setup script that registers the daily Windows scheduled task (wakes from sleep, AC-power only, runs in your interactive session since the scraper opens a visible Chrome window). |
| `scripts/check_github_access.py` | Diagnostic for `GITHUB_PAT`/`GITHUB_REPO` — checks token identity, repo visibility, and whether the dispatch call actually succeeds. |
| `.github/workflows/pipeline.yml` | GitHub Actions workflow: triggered by `repository_dispatch`, a daily cron fallback, or manually; runs `run_process_only_cli.py`. |

## Prerequisites

- **Python 3.10** (pinned — see note in `requirements.txt` about `numpy`/`opencv` builds needing 3.11+, which conflicts with the tested `paddlepaddle`/`paddleocr` combo on 3.10)
- **Google Chrome** installed locally (the scraper launches via `channel="chrome"`, not Playwright's bundled Chromium)
- A **MongoDB Atlas** cluster (or any reachable MongoDB instance)
- A **Pinecone** account/API key, with indexes created for each newspaper (see [Index mapping](#index-mapping))
- A **Google Cloud project** with the Drive API enabled and an OAuth Desktop client (for `drive_utils.py`)
- Only the scraping step (`scraper.py`) needs to run on a residential IP — the OCR/Mongo/Pinecone steps can run anywhere, including GitHub Actions

## Setup

### 1. Clone and create a virtual environment

```powershell
git clone <repo-url> Newspaper_OCR_engine
cd Newspaper_OCR_engine
py -3.10 -m venv venv310
venv310\Scripts\activate
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
playwright install chromium
```

> `channel="chrome"` in `scraper.py` requires a real Google Chrome install on the machine, in addition to Playwright's own browsers.

If you plan to use the experimental `new_scraper.py`, also install its one extra dependency (not in `requirements.txt`):

```powershell
pip install img2pdf
```

### 3. Configure environment variables

Copy the example file and fill in your own values:

```powershell
copy .env.example .env
```

```
MONGO_URI=          # MongoDB Atlas connection string
MONGO_DB_NAME=       # Database name (defaults to "newspaper_ocr" if unset)
GEMINI_API_KEY=      # Reserved for future use
PINECONE_API_KEY=    # Pinecone API key
INDEX_NAME=          # Not required for the current per-paper index routing; safe to leave blank

# for CI/CD (local machine only — used by local_scrape_and_notify.py)
GITHUB_PAT=          # GitHub PAT with 'repo' scope (classic) or Contents+Actions read/write (fine-grained)
GITHUB_REPO=         # "owner/repo", e.g. "vedantpandhare-sketch/Newspaper_OCR_engine"
```

`.env` is git-ignored — never commit it.

### 4. Set up Pinecone indexes

Create one index per newspaper before your first run. The current mapping (`INDEX_MAPPING` in `main.py` / `pipeline_core.py`):

| Paper | Index name |
|---|---|
| Loksatta | `loksttapune` |
| Lokmat | `lokmatpune` |

Any newspaper not in the mapping falls back to `<papername>-index`. Records are upserted via Pinecone's hosted embedding (`upsert_records`), so the index must be configured for hosted embeddings matching that API.

### 5. Set up Google Drive access

1. In Google Cloud Console, enable the Drive API and create an **OAuth 2.0 Desktop app** client. Download it as `credentials.json` into the project root (already git-ignored).
2. On first run of anything that touches Drive (`scraper.py`, `local_scrape_and_notify.py`), a browser window will open for a one-time interactive login. This creates `token.json` locally, which is reused (and auto-refreshed) afterward.
3. For the GitHub Actions job, generate `token.json` locally first, then paste its contents into a repository secret named `GDRIVE_TOKEN_JSON` (see [CI/CD setup](#cicd-setup)) — CI has no browser to complete an interactive login.
4. The Drive folder ID scraped PDFs are uploaded to / fetched from is hardcoded as `INPUT_FOLDER_ID` in `scraper.py` and `run_process_only_cli.py` — update both if you use a different folder.

### 6. Set up MongoDB

Any reachable MongoDB works (Atlas free tier is fine). No manual collection setup is needed — `db_mongo.py` creates a new collection per calendar date (e.g. `2026-09-24`) automatically on first insert.

## Running it

### Option A — Streamlit control panel (recommended for manual/interactive use)

```powershell
streamlit run main.py
```

Runs the full pipeline (scrape → OCR → Mongo → Pinecone) across all configured newspapers with live progress, and includes a "Search Vector DB" panel to query an existing index directly.

### Option B — Full local pipeline, headless

```powershell
python run_pipeline_cli.py
```

Same end-to-end flow as the Streamlit app, without the UI — good for a scheduled/unattended full run on a residential-IP machine.

### Option C — Split pipeline (production setup)

**On your local machine** (needs to pass Cloudflare, so residential IP):

```powershell
python local_scrape_and_notify.py
```

Scrapes both papers, uploads them to Drive, and (if `GITHUB_PAT`/`GITHUB_REPO` are set) instantly triggers the GitHub Actions job via `repository_dispatch`. Without those env vars it still scrapes and uploads fine — the workflow's scheduled cron fallback will pick the files up a bit later.

To automate this daily on Windows, register the scheduled task once:

```powershell
.\scripts\setup_scheduled_task.ps1
```

This runs `scripts\run_daily_scrape.ps1` daily at 06:45 (before the CI cron fallback), wakes the machine from sleep, only runs on AC power, and logs to `logs\`. The scraper opens a visible (non-headless) Chrome window on purpose, so the task runs in your interactive logon session rather than as a background service.

**On GitHub Actions** — handled automatically by `.github/workflows/pipeline.yml`, which runs `run_process_only_cli.py`. You normally don't invoke this manually, but you can for testing:

```powershell
python run_process_only_cli.py
```

## CI/CD setup

`.github/workflows/pipeline.yml` triggers on:
- `repository_dispatch` (type `newspaper-scraped`) — fired by `local_scrape_and_notify.py`
- a daily cron (`0 3 * * *`, ≈08:30 IST) as a fallback
- manual `workflow_dispatch`

Add these repository secrets (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `MONGO_URI` | Same as your local `.env` |
| `MONGO_DB_NAME` | Same as your local `.env` |
| `PINECONE_API_KEY` | Same as your local `.env` |
| `GEMINI_API_KEY` | Same as your local `.env` |
| `GDRIVE_TOKEN_JSON` | Contents of a locally-generated `token.json` |

The workflow disables PaddleOCR's oneDNN backend (`OCR_DISABLE_MKLDNN=1`) because GitHub-hosted runner CPUs can lack the instruction set oneDNN's fused attention kernel assumes, which crashes with `SIGILL` rather than a catchable exception. Leave it enabled locally (default) for the speedup.

If `repository_dispatch` returns a 404 or otherwise fails, run the diagnostic:

```powershell
python scripts\check_github_access.py
```

It reports token identity, repo visibility/permissions, and the actual dispatch call's response — without ever printing the token itself.

## Troubleshooting

- **Cloudflare Turnstile keeps failing / `datePicker` never appears**: the scraper retries the verify-and-navigate cycle 3 times and dumps a screenshot + HTML snapshot to `temp_downloads/debug_*` on each failure — check those first. This almost always means you're running from a non-residential IP (e.g. a VM/cloud box), which is why scraping is kept off GitHub Actions entirely.
- **`SIGILL` / "Illegal instruction" during OCR**: your CPU likely lacks the instruction set PaddlePaddle's oneDNN backend expects. Set `OCR_DISABLE_MKLDNN=1` in the environment before running (already set in CI).
- **Pinecone `429` rate limit errors**: `pipeingest.py` retries with exponential backoff (`upsert_with_retry`) automatically; persistent failures usually mean the batch size or request rate needs to be lowered further.
- **Re-running a day's pipeline re-spends Pinecone embedding quota**: `db_mongo.has_already_ingested_to_pinecone` / `mark_pinecone_ingested` guard against this in the CLI/CI flow (`pipeline_core.py`) — a paper already ingested for the day is skipped, not re-embedded.
- **No PDFs found in Drive (CI)**: `run_process_only_cli.py` polls Drive up to 3 times, 5 minutes apart (tune via `DRIVE_POLL_ATTEMPTS`/`DRIVE_POLL_INTERVAL_SECONDS`), in case the local scrape hadn't finished uploading yet when the cron fired.
