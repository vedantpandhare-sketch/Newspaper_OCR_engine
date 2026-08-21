"""
Headless CLI entrypoint for the full pipeline: Scrape -> OCR -> MongoDB -> Pinecone.

This mirrors the 4-phase logic in main.py's `run_pipeline_with_ui`, minus the
Streamlit UI, so it can run unattended on a scheduler (GitHub Actions, cron,
etc.) where no one is present to click a button.

Each newspaper is processed independently: if one paper fails at any phase,
its error is logged and the run continues with the remaining papers. The
process exits non-zero only if EVERY paper failed end-to-end.
"""

import json
import os
import sys
import traceback

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

from db_mongo import save_raw_ocr_json
from ocr_pipeline import process_pdf_to_json
from pipeingest import ingest_json_to_pinecone
from scraper import run_scraper

INDEX_MAPPING = {
    "loksatta": "loksttapune",
    "lokmat": "lokmatpune",
}


def log(msg: str):
    print(msg, flush=True)


def main():
    log("=" * 60)
    log("BATCH MULTI-NEWSPAPER OCR & VECTOR PIPELINE (headless run)")
    log("=" * 60)

    # ---------------------------------------------------------
    # PHASE 1: SCRAPE ALL NEWSPAPERS
    # ---------------------------------------------------------
    log("\n[Phase 1/4] Downloading all target newspapers...")
    try:
        pdf_paths = run_scraper()
        if isinstance(pdf_paths, str):
            pdf_paths = [pdf_paths]
    except Exception as e:
        log(f"[FATAL] Scraper crashed entirely: {e}")
        traceback.print_exc()
        pdf_paths = []

    if not pdf_paths:
        log("[FATAL] No PDFs were downloaded. Aborting run.")
        sys.exit(1)

    log(f"Acquired {len(pdf_paths)} PDF(s):")
    for p in pdf_paths:
        log(f"  - {os.path.basename(p)}")

    # ---------------------------------------------------------
    # PHASES 2-4 PER PAPER (isolated failures)
    # ---------------------------------------------------------
    results = []

    for pdf_path in pdf_paths:
        paper_name = os.path.basename(pdf_path).split("_")[0]
        log(f"\n{'='*60}\nProcessing: {paper_name} ({pdf_path})\n{'='*60}")

        try:
            # Phase 2: OCR
            log(f"[Phase 2/4] Running PaddleOCR for {paper_name}...")
            output_json_path = pdf_path.replace(".pdf", "_ocr.json")
            ocr_json_data = process_pdf_to_json(pdf_path)
            with open(output_json_path, "w", encoding="utf-8") as f:
                json.dump(ocr_json_data, f, ensure_ascii=False, indent=2)
            log(f"  -> OCR complete: {output_json_path}")

            # Phase 3: MongoDB
            log(f"[Phase 3/4] Uploading {paper_name} JSON to MongoDB...")
            doc_id, collection_name = save_raw_ocr_json(output_json_path)
            log(f"  -> Saved to collection '{collection_name}' (id={doc_id})")

            # Phase 4: Pinecone
            target_index = INDEX_MAPPING.get(
                paper_name.lower(), f"{paper_name.lower()}-index"
            )
            log(f"[Phase 4/4] Ingesting {paper_name} vectors into '{target_index}'...")
            vector_count = ingest_json_to_pinecone(
                json_path=output_json_path, index_name=target_index
            )
            log(f"  -> Ingested {vector_count} chunks into '{target_index}'")

            results.append({
                "paper": paper_name,
                "status": "success",
                "collection": collection_name,
                "index": target_index,
                "vectors": vector_count,
            })

        except Exception as e:
            log(f"[ERROR] {paper_name} pipeline failed: {e}")
            traceback.print_exc()
            results.append({"paper": paper_name, "status": "failed", "error": str(e)})

    # ---------------------------------------------------------
    # SUMMARY
    # ---------------------------------------------------------
    log("\n" + "=" * 60)
    log("RUN SUMMARY")
    log("=" * 60)
    any_success = False
    for r in results:
        if r["status"] == "success":
            any_success = True
            log(f"  OK   {r['paper']}: {r['vectors']} vectors -> '{r['index']}'")
        else:
            log(f"  FAIL {r['paper']}: {r['error']}")

    if not any_success:
        log("\n[FATAL] All papers failed. Exiting with error status.")
        sys.exit(1)

    log("\nDone.")


if __name__ == "__main__":
    main()
