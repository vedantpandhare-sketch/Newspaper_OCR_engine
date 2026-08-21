import json
import os
import re
import time
import uuid
from dotenv import load_dotenv
from pinecone import Pinecone
from pinecone.exceptions import PineconeApiException

# Load Environment Variables
load_dotenv()


def split_text_smart(
    text: str, chunk_size: int = 700, overlap: int = 100
) -> list[str]:
    """Splits text on word boundaries without cutting words mid-sentence."""
    words = text.split()
    if not words:
        return []

    chunks = []
    current_chunk = []
    current_length = 0

    for word in words:
        word_len = len(word) + 1
        if current_length + word_len > chunk_size and current_chunk:
            chunks.append(" ".join(current_chunk))
            overlap_words = []
            overlap_len = 0
            for w in reversed(current_chunk):
                if overlap_len + len(w) + 1 <= overlap:
                    overlap_words.insert(0, w)
                    overlap_len += len(w) + 1
                else:
                    break
            current_chunk = overlap_words
            current_length = overlap_len

        current_chunk.append(word)
        current_length += word_len

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks


def extract_text_from_node(node) -> str:
    """Recursively collects all string values from nested dicts/lists."""
    if isinstance(node, str):
        return node
    elif isinstance(node, list):
        return "\n".join(
            [extract_text_from_node(item) for item in node if item]
        )
    elif isinstance(node, dict):
        text_parts = []
        for k, v in node.items():
            if k.lower() in [
                "page_number",
                "page",
                "date",
                "extraction_date",
                "_id",
            ]:
                continue
            extracted = extract_text_from_node(v)
            if extracted.strip():
                text_parts.append(extracted.strip())
        return "\n\n".join(text_parts)
    return ""


def upsert_with_retry(index, namespace, batch, max_retries=6, base_delay=20):
    """Upserts records to Pinecone with exponential backoff on HTTP 429 rate limits."""
    for attempt in range(1, max_retries + 1):
        try:
            index.upsert_records(namespace=namespace, records=batch)
            return
        except PineconeApiException as e:
            is_rate_limited = getattr(e, "status", None) == 429
            if is_rate_limited and attempt < max_retries:
                wait = base_delay * attempt
                print(
                    f"Rate limited on index '{index.name}', waiting {wait}s (retry {attempt}/{max_retries})..."
                )
                time.sleep(wait)
                continue
            raise


def ingest_json_to_pinecone(
    json_path: str,
    index_name: str,
    namespace: str = "__default__",
    batch_size: int = 96,
    sleep_between_batches: int = 5,
) -> int:
    """
    Parses an OCR JSON file, chunks the extracted text, and streams records into
    a specified Pinecone index.
    """
    api_key = os.getenv("PINECONE_API_KEY")
    if not api_key:
        raise ValueError("Missing PINECONE_API_KEY in environment variables.")

    if not os.path.exists(json_path):
        raise FileNotFoundError(f"Target OCR JSON file not found: '{json_path}'")

    print(f"\n[Ingest] Reading OCR JSON: {json_path}")
    print(f"[Ingest] Target Index: '{index_name}' | Namespace: '{namespace}'")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Resolve extraction date
    extraction_date = "unknown"
    if isinstance(data, dict):
        extraction_date = data.get("extraction_date", data.get("date", "unknown"))

    # Determine structural format of pages
    pages_to_process = []
    if isinstance(data, list):
        pages_to_process = data
    elif isinstance(data, dict):
        if "pages" in data and isinstance(data["pages"], list):
            pages_to_process = data["pages"]
        elif "data" in data and isinstance(data["data"], list):
            pages_to_process = data["data"]
        else:
            page_keys = [k for k in data.keys() if "page" in k.lower()]
            if page_keys:
                for k in page_keys:
                    pages_to_process.append({"page_key": k, "content": data[k]})
            else:
                pages_to_process = [data]

    # Generate chunked records
    records = []
    paper_identifier = os.path.basename(json_path).split("_")[0]

    for idx, page_item in enumerate(pages_to_process, start=1):
        page_number = idx

        if isinstance(page_item, dict):
            if "page_number" in page_item:
                page_number = page_item["page_number"]
            elif "page" in page_item and isinstance(page_item["page"], int):
                page_number = page_item["page"]

        full_text = extract_text_from_node(page_item)
        if not full_text.strip():
            continue

        chunks = split_text_smart(full_text, chunk_size=700, overlap=100)

        for chunk_id, chunk in enumerate(chunks):
            records.append(
                {
                    "_id": str(uuid.uuid4()),
                    "text": chunk,
                    "page": page_number,
                    "chunk": chunk_id,
                    "date": extraction_date,
                    "source_paper": paper_identifier,
                }
            )

    print(f"[Ingest] Generated {len(records)} total text chunks.")

    if not records:
        print("[Warning] No valid text chunks generated for upload.")
        return 0

    # Initialize Pinecone index connection
    pc = Pinecone(api_key=api_key)
    index = pc.Index(index_name)

    # Upload in batches
    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        upsert_with_retry(index, namespace, batch)
        uploaded_count = min(i + batch_size, len(records))
        print(f"[Ingest] Ingested {uploaded_count}/{len(records)} chunks into '{index_name}'")
        time.sleep(sleep_between_batches)

    print(f"[Ingest] Completed ingestion for '{json_path}' -> '{index_name}' successfully!")
    return len(records)


# Standalone runner for testing local files directly
if __name__ == "__main__":
    # Specify paper JSON paths and target index mappings here for testing
    TEST_FILES = [
        {
            "json_path": r"D:\Newspaper_OCR_engine\temp_downloads\Loksatta_2026-08-21_ocr.json",
            "index_name": "loksttapune",  # Replace with your actual index name
        },
        {
            "json_path": r"D:\Newspaper_OCR_engine\temp_downloads\Lokmat_2026-08-21_ocr.json",
            "index_name": "lokmatpune",   # Replace with your actual index name
        },
    ]

    for item in TEST_FILES:
        if os.path.exists(item["json_path"]):
            ingest_json_to_pinecone(
                json_path=item["json_path"],
                index_name=item["index_name"]
            )
        else:
            print(f"[Skip] File not found: {item['json_path']}")