import json
import os
import re
import time
import uuid
from dotenv import load_dotenv
from pinecone import Pinecone
from pinecone.exceptions import PineconeApiException

# ------------------------------------
# 1. Load Environment Variables
# ------------------------------------
load_dotenv()

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
JSON_PATH = r"D:\Newspaper_OCR_engine\temp_downloads\Loksatta_2026-08-17_ocr.json"

INDEX_NAME = "loksttapune"
NAMESPACE = "__default__"

if not PINECONE_API_KEY:
    raise ValueError("Missing PINECONE_API_KEY in .env file.")

# ------------------------------------
# 2. Text Chunking Function
# ------------------------------------
def split_text_smart(text: str, chunk_size: int = 700, overlap: int = 100) -> list[str]:
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

# ------------------------------------
# 3. Recursive Text Extractor (Universal)
# ------------------------------------
def extract_text_from_node(node) -> str:
    """Recursively collects all string values from nested dicts/lists."""
    if isinstance(node, str):
        return node
    elif isinstance(node, list):
        return "\n".join([extract_text_from_node(item) for item in node if item])
    elif isinstance(node, dict):
        text_parts = []
        for k, v in node.items():
            if k.lower() in ["page_number", "page", "date", "extraction_date", "_id"]:
                continue
            extracted = extract_text_from_node(v)
            if extracted.strip():
                text_parts.append(extracted.strip())
        return "\n\n".join(text_parts)
    return ""

# ------------------------------------
# 4. Upsert Retry Function
# ------------------------------------
def upsert_with_retry(index, namespace, batch, max_retries=6, base_delay=20):
    for attempt in range(1, max_retries + 1):
        try:
            index.upsert_records(namespace=namespace, records=batch)
            return
        except PineconeApiException as e:
            is_rate_limited = getattr(e, "status", None) == 429
            if is_rate_limited and attempt < max_retries:
                wait = base_delay * attempt
                print(f"Rate limited, waiting {wait}s (retry {attempt}/{max_retries})...")
                time.sleep(wait)
                continue
            raise

# ------------------------------------
# 5. Connect Pinecone
# ------------------------------------
pc = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index(INDEX_NAME)

# ------------------------------------
# 6. Load & Debug JSON File
# ------------------------------------
print(f"Loading JSON file: {JSON_PATH}")
with open(JSON_PATH, "r", encoding="utf-8") as f:
    data = json.load(f)

print(f"[Debug] Root JSON Type: {type(data)}")
if isinstance(data, dict):
    print(f"[Debug] Root JSON Keys: {list(data.keys())}")
elif isinstance(data, list):
    print(f"[Debug] Root JSON contains {len(data)} items.")

extraction_date = "unknown"
if isinstance(data, dict):
    extraction_date = data.get("extraction_date", data.get("date", "unknown"))

# ------------------------------------
# 7. Structure Resolution & Chunking
# ------------------------------------
pages_to_process = []

if isinstance(data, list):
    pages_to_process = data
elif isinstance(data, dict):
    if "pages" in data and isinstance(data["pages"], list):
        pages_to_process = data["pages"]
    elif "data" in data and isinstance(data["data"], list):
        pages_to_process = data["data"]
    else:
        # Check for key-based page structures (e.g., {"page_1": {...}, "page_2": {...}})
        page_keys = [k for k in data.keys() if "page" in k.lower()]
        if page_keys:
            for k in page_keys:
                pages_to_process.append({"page_key": k, "content": data[k]})
        else:
            pages_to_process = [data]

records = []

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
        records.append({
            "_id": str(uuid.uuid4()),
            "text": chunk,
            "page": page_number,
            "chunk": chunk_id,
            "date": extraction_date
        })

# ------------------------------------
# 8. Upload Records
# ------------------------------------
print(f"Generated {len(records)} chunks for upload.")

if len(records) == 0:
    print("[Error] No chunks were extracted. Please share the printed [Debug] output above.")
else:
    BATCH_SIZE = 96
    SLEEP_BETWEEN_BATCHES = 5

    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        upsert_with_retry(index, NAMESPACE, batch)
        print(f"Uploaded {min(i + BATCH_SIZE, len(records))}/{len(records)}")
        time.sleep(SLEEP_BETWEEN_BATCHES)

    print("Upload Completed Successfully!")