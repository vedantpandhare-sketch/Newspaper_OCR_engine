"""
Shared Phase 2-4 logic (OCR -> MongoDB -> Pinecone) used by both:
  - run_pipeline_cli.py   (full local run: scrape + process)
  - run_process_only_cli.py (CI run: pull already-scraped PDFs from Drive + process)

Kept in one place so the two entrypoints can't drift out of sync.
"""

import json
import os
import traceback

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

from db_mongo import save_raw_ocr_json
from ocr_pipeline import process_pdf_to_json
from pipeingest import ingest_json_to_pinecone

INDEX_MAPPING = {
    "loksatta": "loksttapune",
    "lokmat": "lokmatpune",
}


def log(msg: str):
    print(msg, flush=True)


def process_and_ingest(pdf_paths: list[str]) -> list[dict]:
    """
    Runs OCR -> MongoDB -> Pinecone for each PDF, independently. One paper's
    failure does not stop the others. Returns a list of per-paper result
    dicts with a "status" key ("success" or "failed").
    """
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

    return results


def print_summary(results: list[dict]) -> bool:
    """Prints a run summary. Returns True if at least one paper succeeded."""
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
    return any_success
