import os
# Force pure-python protobuf implementation to prevent version conflict
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
import sys
import json
from scraper import run_scraper  # Scrapes PDF
from ocr_pipeline_updated import process_pdf_to_json  # Your PaddleOCR script
from db_mongo import save_raw_ocr_json  # MongoDB upload function


def run_pipeline():
    print("=" * 60)
    print("🚀 AUTOMATED NEWSPAPER SCRAPER & PADDLEOCR PIPELINE")
    print("=" * 60)

    # 1. Run Scraper
#    print("\n[1/3] Running Scraper...")
#    pdf_path = run_scraper()
#    if not pdf_path or not os.path.exists(pdf_path):
#        print("❌ Scraper failed to download PDF.")
#        sys.exit(1)

    # 2. Run PaddleOCR Pipeline
#    print(f"\n[2/3] Running PaddleOCR Pipeline on {pdf_path}...")
#    ocr_json_data = process_pdf_to_json(pdf_path)

    # Save a local JSON backup
#    output_json_path = pdf_path.replace(".pdf", "_ocr.json")
#    with open(output_json_path, "w", encoding="utf-8") as f:
#       json.dump(ocr_json_data, f, ensure_ascii=False, indent=2)

    output_json_path = (
        r"temp_downloads/Loksatta_2026-07-27_ocr.json"  # or "extracted_articles.json"
    )
    # 3. Store Output in MongoDB Atlas
    print("\n[3/3] Uploading Raw JSON to MongoDB Atlas...")
    doc_id = save_raw_ocr_json(output_json_path)
    print(f"✓ Document ID: {doc_id}")

    print("=" * 60)
    print("🎉 PIPELINE COMPLETED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    run_pipeline()