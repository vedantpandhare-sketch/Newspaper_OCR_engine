"""
Headless CLI entrypoint for the FULL local pipeline: Scrape -> OCR -> MongoDB -> Pinecone.

Run this on your own machine (or anywhere Cloudflare will actually pass,
i.e. NOT a CI/cloud IP - see run_process_only_cli.py for that). It scrapes
fresh PDFs, then hands them to the same OCR/Mongo/Pinecone logic the CI
pipeline uses.
"""

import os
import sys

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

from pipeline_core import log, print_summary, process_and_ingest
from scraper import run_scraper


def main():
    log("=" * 60)
    log("FULL LOCAL PIPELINE: SCRAPE -> OCR -> MONGODB -> PINECONE")
    log("=" * 60)

    log("\n[Phase 1/4] Downloading all target newspapers...")
    try:
        pdf_paths = run_scraper()
        if isinstance(pdf_paths, str):
            pdf_paths = [pdf_paths]
    except Exception as e:
        log(f"[FATAL] Scraper crashed entirely: {e}")
        pdf_paths = []

    if not pdf_paths:
        log("[FATAL] No PDFs were downloaded. Aborting run.")
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
