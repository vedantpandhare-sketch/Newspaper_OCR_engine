import os
import re
import uuid
from dotenv import load_dotenv
from pymongo import MongoClient
from sentence_transformers import SentenceTransformer
from pinecone import Pinecone

# ------------------------------------
# 1. Load Environment Variables
# ------------------------------------
load_dotenv()

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "newspaper_ocr_db")

INDEX_NAME = "test"  # BAAI/bge-m3 produces 1024-dimensional vectors

if not PINECONE_API_KEY or not MONGO_URI:
    raise ValueError("Missing PINECONE_API_KEY or MONGO_URI in .env file.")

# ------------------------------------
# 2. Text Chunking Function
# ------------------------------------
def split_text(text, chunk_size=700, overlap=100):
    """Split text into overlapping chunks."""
    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap

    return chunks

# ------------------------------------
# 3. Initialize Embedding Model & Pinecone
# ------------------------------------
print("[Model] Loading BAAI/bge-m3 embedding model...")
model = SentenceTransformer(
    "BAAI/bge-m3",
    trust_remote_code=True
)

print("[Pinecone] Connecting to Pinecone...")
pc = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index(INDEX_NAME)

# ------------------------------------
# 4. Connect to MongoDB & Select Latest Dated Collection
# ------------------------------------
print(f"[MongoDB] Connecting to database '{MONGO_DB_NAME}'...")
mongo_client = MongoClient(MONGO_URI)
db = mongo_client[MONGO_DB_NAME]

all_collections = [c for c in db.list_collection_names() if not c.startswith("system.")]

if not all_collections:
    raise ValueError(f"No collections found inside database '{MONGO_DB_NAME}'!")

# Filter for dated collections starting with digits (e.g. '2026-07-27')
date_collections = [c for c in all_collections if re.match(r"^\d", c)]

if date_collections:
    date_collections.sort()
    latest_collection_name = date_collections[-1]
else:
    filtered = [c for c in all_collections if c != "extracted_articles"]
    if filtered:
        filtered.sort()
        latest_collection_name = filtered[-1]
    else:
        latest_collection_name = sorted(all_collections)[-1]

print(f"[MongoDB] Target Collection selected -> '{latest_collection_name}'")

target_collection = db[latest_collection_name]
mongo_docs = list(target_collection.find({}))
print(f"[MongoDB] Retrieved {len(mongo_docs)} documents from '{latest_collection_name}'.")

# ------------------------------------
# 5. Extract Text & Generate Embeddings
# ------------------------------------
vectors = []

for doc in mongo_docs:
    # 1. Grab pages array or dict
    pages_data = doc.get("pages", doc.get("data", doc))

    # Normalize pages_data into an iterable list of page items
    pages_list = []
    if isinstance(pages_data, list):
        pages_list = pages_data
    elif isinstance(pages_data, dict):
        # Convert dict {"page_1": [...]} to list format
        for k, v in pages_data.items():
            pages_list.append({"page_key": k, "content": v})

    for idx, page_item in enumerate(pages_list, start=1):
        page_number = idx
        articles = []

        # Case A: page_item is dict like {"page": 1, "articles": [...]}
        if isinstance(page_item, dict):
            if "page" in page_item and isinstance(page_item["page"], int):
                page_number = page_item["page"]

            if "articles" in page_item and isinstance(page_item["articles"], list):
                articles = page_item["articles"]
            elif "content" in page_item and isinstance(page_item["content"], list):
                articles = page_item["content"]
            else:
                # Check for "page_1", "page_2" internal keys
                for k, v in page_item.items():
                    if k.startswith("page_") and isinstance(v, list):
                        page_number = int(k.replace("page_", "")) if k.replace("page_", "").isdigit() else page_number
                        articles = v
                        break

        # Case B: page_item is directly a list of articles
        elif isinstance(page_item, list):
            articles = page_item

        # Extract articles from page
        for article in articles:
            if not isinstance(article, dict):
                continue

            heading = article.get("heading", article.get("title", ""))
            paragraphs = article.get("paragraphs", article.get("content", []))

            if isinstance(paragraphs, list):
                body_text = "\n".join([str(p) for p in paragraphs if p])
            else:
                body_text = str(paragraphs)

            full_text = f"{heading}\n\n{body_text}".strip()

            if not full_text:
                continue

            chunks = split_text(full_text, chunk_size=700, overlap=100)

            for chunk_id, chunk in enumerate(chunks):
                embedding = model.encode(chunk, normalize_embeddings=True).tolist()

                vectors.append({
                    "id": str(uuid.uuid4()),
                    "values": embedding,
                    "metadata": {
                        "mongo_id": str(doc.get("_id", "")),
                        "collection_source": latest_collection_name,
                        "page": page_number,
                        "article_id": str(article.get("article_id", "")),
                        "heading": str(heading)[:500],
                        "chunk": chunk_id,
                        "text": chunk
                    }
                })

# ------------------------------------
# 6. Upload Vector Embeddings to Pinecone
# ------------------------------------
print(f"[Pinecone] Generated {len(vectors)} vector chunks ready for upload.")

if len(vectors) == 0:
    print("[Warning] No chunks generated! Check document structure.")
else:
    BATCH_SIZE = 100
    for i in range(0, len(vectors), BATCH_SIZE):
        batch = vectors[i:i + BATCH_SIZE]
        index.upsert(vectors=batch)
        print(f"Uploaded {min(i + BATCH_SIZE, len(vectors))}/{len(vectors)} chunks...")

    print("Upload Completed Successfully!")