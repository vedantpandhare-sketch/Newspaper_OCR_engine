"""
Headless CLI entrypoint for the CI-side pipeline: Fetch from Drive -> OCR -> MongoDB -> Pinecone.

Designed to run on GitHub Actions (or any cloud runner). It deliberately
never touches tradingref.com / Cloudflare - that step happens locally
(see run_pipeline_cli.py / local_scrape_and_notify.py) on a machine with a
residential IP, which uploads the scraped PDFs to a Google Drive folder.
This script just picks today's PDFs up from that folder and runs the heavy
part of the pipeline unattended.
"""

import datetime
import os
import sys

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

from drive_utils import download_files_from_drive_by_date
from pipeline_core import log, print_summary, process_and_ingest

# Same folder scraper.py uploads scraped PDFs into (see scraper.py: INPUT_FOLDER_ID)
INPUT_FOLDER_ID = "1fFMtXqlTyP7gZtJ0L39l1IcJE0Wh5vRf"
TEMP_DIR = "./temp_downloads"

# How many times (and how far apart) to re-check Drive if today's PDFs
# haven't shown up yet - covers the case where this workflow's schedule
# fires slightly before the local scrape has finished uploading.
MAX_POLL_ATTEMPTS = int(os.environ.get("DRIVE_POLL_ATTEMPTS", "3"))
POLL_INTERVAL_SECONDS = int(os.environ.get("DRIVE_POLL_INTERVAL_SECONDS", "300"))


def fetch_todays_pdfs() -> list[str]:
    today_iso = datetime.datetime.now().strftime("%Y-%m-%d")
    os.makedirs(TEMP_DIR, exist_ok=True)

    import time

    for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
        log(f"[Fetch] Checking Drive for '{today_iso}' PDFs (attempt {attempt}/{MAX_POLL_ATTEMPTS})...")
        pdf_paths = download_files_from_drive_by_date(
            folder_id=INPUT_FOLDER_ID, date_str=today_iso, dest_dir=TEMP_DIR
        )
        pdf_paths = [p for p in pdf_paths if p.lower().endswith(".pdf")]

        if pdf_paths:
            return pdf_paths

        if attempt < MAX_POLL_ATTEMPTS:
            log(f"[Fetch] Nothing there yet. Waiting {POLL_INTERVAL_SECONDS}s before retrying...")
            time.sleep(POLL_INTERVAL_SECONDS)

    return []


def main():
    log("=" * 60)
    log("CI PIPELINE: DRIVE FETCH -> OCR -> MONGODB -> PINECONE")
    log("=" * 60)

    log("\n[Phase 1/4] Fetching today's scraped PDFs from Google Drive...")
    pdf_paths = fetch_todays_pdfs()

    if not pdf_paths:
        log(
            "[FATAL] No PDFs found in Drive for today. "
            "Did the local scrape run and upload successfully?"
        )
        sys.exit(1)

    log(f"Acquired {len(pdf_paths)} PDF(s):")
    for p in pdf_paths:
        log(f"  - {os.path.basename(p)}")

    results = process_and_ingest(pdf_paths)

    if not print_summary(results):
        log("\n[FATAL] All papers failed. Exiting with error status.")
        sys.exit(1)

    log("\nDone.")


if __name__ == "__main__":
    main()
