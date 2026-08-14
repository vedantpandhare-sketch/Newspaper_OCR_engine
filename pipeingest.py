import json
import os
import re
import time
import uuid
from dotenv import load_dotenv
from pinecone import Pinecone
from pinecone.exceptions import PineconeApiException
from pymongo import MongoClient

# ------------------------------------
# 1. Load Environment Variables
# ------------------------------------
load_dotenv()

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "newspaper_ocr_db")

INDEX_NAME = "test"  # Must be an index configured with Integrated Inference
NAMESPACE = "__default__"

if not PINECONE_API_KEY or not MONGO_URI:
    raise ValueError("Missing PINECONE_API_KEY or MONGO_URI in .env file.")


# ------------------------------------
# 2. Text Chunking Function (Word-Aware)
# ------------------------------------
def split_text_smart(
    text: str, chunk_size: int = 700, overlap: int = 100
) -> list[str]:
    """Splits text into overlapping chunks on whitespace boundaries."""
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
# 3. Upsert One Batch with Retry Logic
# ------------------------------------
def upsert_with_retry(
    index, namespace, batch, max_retries=6, base_delay=20
):
    """Sends raw text records to Pinecone's server-side embedding engine with rate-limit handling."""
    for attempt in range(1, max_retries + 1):
        try:
            index.upsert_records(namespace=namespace, records=batch)
            return
        except PineconeApiException as e:
            is_rate_limited = getattr(e, "status", None) == 429

            if is_rate_limited and attempt < max_retries:
                wait = base_delay * attempt
                print(
                    f"Rate limited by server-side model, waiting {wait}s (retry {attempt}/{max_retries})..."
                )
                time.sleep(wait)
                continue

            raise


# ------------------------------------
# 4. Connect to Pinecone & MongoDB
# ------------------------------------
print("[Pinecone] Connecting to Pinecone...")
pc = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index(INDEX_NAME)

print(f"[MongoDB] Connecting to database '{MONGO_DB_NAME}'...")
mongo_client = MongoClient(MONGO_URI)
db = mongo_client[MONGO_DB_NAME]

all_collections = [
    c for c in db.list_collection_names() if not c.startswith("system.")
]

if not all_collections:
    raise ValueError(f"No collections found inside database '{MONGO_DB_NAME}'!")

# Automatically pick the latest collection (e.g. '2026-08-14')
date_collections = [c for c in all_collections if re.match(r"^\d", c)]

if date_collections:
    date_collections.sort()
    latest_collection_name = date_collections[-1]
else:
    filtered = [c for c in all_collections if c != "extracted_articles"]
    latest_collection_name = (
        sorted(filtered)[-1] if filtered else sorted(all_collections)[-1]
    )

print(f"[MongoDB] Selected Target Collection -> '{latest_collection_name}'")

target_collection = db[latest_collection_name]
mongo_docs = list(target_collection.find({}))
print(
    f"[MongoDB] Retrieved {len(mongo_docs)} documents from '{latest_collection_name}'."
)

# ------------------------------------
# 5. Extract Text & Build Record Objects
# ------------------------------------
records = []

for doc in mongo_docs:
    extraction_date = doc.get(
        "extraction_date", doc.get("date", latest_collection_name)
    )

    pages_data = doc.get("pages", doc.get("data", doc))

    pages_list = []
    if isinstance(pages_data, list):
        pages_list = pages_data
    elif isinstance(pages_data, dict):
        for k, v in pages_data.items():
            pages_list.append({"page_key": k, "content": v})

    for idx, page_item in enumerate(pages_list, start=1):
        page_number = idx
        articles = []

        if isinstance(page_item, dict):
            if "page" in page_item and isinstance(page_item["page"], int):
                page_number = page_item["page"]

            if "articles" in page_item and isinstance(
                page_item["articles"], list
            ):
                articles = page_item["articles"]
            elif "content" in page_item and isinstance(
                page_item["content"], list
            ):
                articles = page_item["content"]
            else:
                for k, v in page_item.items():
                    if k.startswith("page_") and isinstance(v, list):
                        page_number = (
                            int(k.replace("page_", ""))
                            if k.replace("page_", "").isdigit()
                            else page_number
                        )
                        articles = v
                        break

        elif isinstance(page_item, list):
            articles = page_item

        for article in articles:
            if not isinstance(article, dict):
                continue

            heading = article.get("heading", article.get("title", ""))
            paragraphs = article.get("paragraphs", article.get("content", []))

            body_text = (
                "\n".join([str(p) for p in paragraphs if p])
                if isinstance(paragraphs, list)
                else str(paragraphs)
            )
            full_text = f"{heading}\n\n{body_text}".strip()

            if not full_text:
                continue

            chunks = split_text_smart(full_text, chunk_size=700, overlap=100)

            for chunk_id, chunk in enumerate(chunks):
                # Build the record matching your exact target dictionary schema
                records.append(
                    {
                        "_id": str(uuid.uuid4()),
                        "text": chunk,
                        "page": page_number,
                        "chunk": chunk_id,
                        "date": extraction_date,
                    }
                )

# ------------------------------------
# 6. Upload Records to Pinecone
# ------------------------------------
print(f"[Pinecone] Built {len(records)} record chunks for server-side embedding...")

if len(records) == 0:
    print("[Warning] No records generated! Check document structure.")
else:
    BATCH_SIZE = 96  # Pinecone integrated-embedding record limit
    SLEEP_BETWEEN_BATCHES = 5  # Keeps execution well under token limits

    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]

        upsert_with_retry(index, NAMESPACE, batch)

        print(
            f"Uploaded {min(i + BATCH_SIZE, len(records))}/{len(records)} records..."
        )

        time.sleep(SLEEP_BETWEEN_BATCHES)

    print("Upload Completed Successfully!")