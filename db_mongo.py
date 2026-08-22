import os
import json
import datetime
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

# MongoDB Configuration from .env or fallback
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://<username>:<password>@cluster.mongodb.net/")
DB_NAME = os.getenv("MONGO_DB_NAME", "newspaper_ocr")


def get_mongo_client():
    """Initializes and returns a MongoDB client instance."""
    return MongoClient(MONGO_URI)


def get_today_collection_name():
    """
    Generates a dynamic collection name based on the current date.
    Example output: '2026-07-29'
    """
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    return f"{today_str}"


def get_mongo_collection(collection_name=None):
    """
    Returns the PyMongo collection object.
    If no collection_name is provided, it dynamically defaults to today's date collection.
    """
    client = get_mongo_client()
    db = client[DB_NAME]
    
    if not collection_name:
        collection_name = get_today_collection_name()

    # Returns strictly the PyMongo collection object (no tuples!)
    return db[collection_name]


def save_raw_ocr_json(json_file_path, collection_name=None):
    """
    Reads the OCR output JSON file and inserts it into MongoDB under a dynamic collection name.
    Returns: (inserted_doc_id_str, collection_name_used)
    """
    if not os.path.exists(json_file_path):
        raise FileNotFoundError(f"❌ JSON file not found at path: {json_file_path}")

    # Determine dynamic collection name
    if not collection_name:
        collection_name = get_today_collection_name()

    collection = get_mongo_collection(collection_name)

    # Read OCR JSON content
    with open(json_file_path, "r", encoding="utf-8") as f:
        ocr_data = json.load(f)

    # Add execution timestamp metadata
    if isinstance(ocr_data, dict):
        ocr_data["processed_at"] = datetime.datetime.utcnow().isoformat()
        ocr_data["source_file"] = os.path.basename(json_file_path)

    # Insert into dynamic daily collection
    result = collection.insert_one(ocr_data)
    doc_id_str = str(result.inserted_id)

    print(f"✓ Saved OCR data to MongoDB collection '{collection_name}' with ID: {doc_id_str}")

    # Return BOTH strings to pass smoothly into pipeingest.py
    return doc_id_str, collection_name


def has_already_ingested_to_pinecone(source_filename: str, collection_name=None) -> bool:
    """
    Checks whether this exact OCR output (by filename, e.g.
    'Loksatta_2026-08-21_ocr.json') has already been successfully embedded
    into Pinecone today. Used to skip redundant re-embedding on retries/
    re-runs, since Pinecone's hosted embedding model burns quota on every
    upsert_records() call regardless of whether the content is new.
    """
    collection = get_mongo_collection(collection_name)
    existing = collection.find_one({
        "source_file": source_filename,
        "pinecone_ingested": True,
    })
    return existing is not None


def mark_pinecone_ingested(doc_id: str, collection_name=None):
    """Flags a saved OCR document as already vectorized, so future runs skip it."""
    from bson import ObjectId  # provided by pymongo itself, not the standalone bson pkg

    collection = get_mongo_collection(collection_name)
    collection.update_one(
        {"_id": ObjectId(doc_id)},
        {"$set": {"pinecone_ingested": True}},
    )


if __name__ == "__main__":
    # Test script standalone
    col_name = get_today_collection_name()
    print(f"Today's dynamic collection name: {col_name}")