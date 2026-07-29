import os
import sys
import json

# Fix C++ / OpenMP runtime conflicts on Windows
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

from scraper import run_scraper  # Scrapes PDF
from ocr_pipeline_updated import process_pdf_to_json  # PaddleOCR processing
from db_mongo import save_raw_ocr_json  # MongoDB upload function


def run_pipeline():
    print("=" * 60)
    print("🚀 AUTOMATED NEWSPAPER SCRAPER, OCR & PINECONE PIPELINE")
    print("=" * 60)

    # 1. Run Scraper
    #print("\n[1/4] Running Scraper...")
    #pdf_path = run_scraper()
    #if not pdf_path or not os.path.exists(pdf_path):
    #    print("❌ Scraper failed to download PDF.")
    #    sys.exit(1)

    # 2. Run PaddleOCR Pipeline
    #print(f"\n[2/4] Running PaddleOCR Pipeline on {pdf_path}...")
    #ocr_json_data = process_pdf_to_json(pdf_path)

    # Save local backup JSON
    #with open(output_json_path, "w", encoding="utf-8") as f:
    #    json.dump(ocr_json_data, f, ensure_ascii=False, indent=2)

    pdf_path="temp_downloads/Loksatta_2026-07-29.pdf"
    output_json_path = pdf_path.replace(".pdf", "_ocr.json")
    # 3. Store Output in MongoDB Atlas
    print("\n[3/4] Uploading Raw JSON to MongoDB Atlas...")
    doc_id, collection_name = save_raw_ocr_json(output_json_path)
    print(f"✓ Document saved in MongoDB. ID: {doc_id}")

    # 4. Ingest MongoDB Document into Pinecone
    print("\n[4/4] Generating Embeddings & Ingesting to Pinecone...")
    try:
        # Dynamically import pipeingest only at execution time
        from pipeingest import upsert_collection_to_pinecone
        
        vector_count = upsert_collection_to_pinecone(doc_id=doc_id, collection_name=collection_name)
        print(f"✓ Pinecone Ingestion Complete! ({vector_count} vectors saved)")
    except Exception as e:
        print(f"❌ Pinecone Ingestion Failed: {e}")
        sys.exit(1)

    print("=" * 60)
    print("🎉 FULL PIPELINE COMPLETED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    run_pipeline()