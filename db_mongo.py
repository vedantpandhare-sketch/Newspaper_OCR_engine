import os
import json
import datetime
from pymongo import MongoClient
from bson.objectid import ObjectId

# Paste your MongoDB connection string here (replace <password> with your actual user password)
# Best practice: Load this from an environment variable!
MONGO_URI = os.getenv(
    "MONGO_URI",
    "mongodb+srv://vedantpandhare_db_user:<db_password>@cluster0.w9kmyhe.mongodb.net/?appName=Cluster0"
)

def get_mongo_client():
    """Initializes and returns the MongoDB Atlas client."""
    client = MongoClient(MONGO_URI)
    return client

def save_raw_ocr_json(json_file_path: str) -> str:
    """Reads a local OCR JSON file and inserts it into the raw_ocr_documents collection."""
    if not os.path.exists(json_file_path):
        raise FileNotFoundError(f"File not found: {json_file_path}")

    with open(json_file_path, 'r', encoding='utf-8') as f:
        ocr_data = json.load(f)

    # Attach database metadata
    ocr_data["_metadata"] = {
        "inserted_at": datetime.datetime.utcnow().isoformat(),
        "status": "raw_unprocessed"
    }

    client = get_mongo_client()
    db = client["newspaper_database"]
    collection = db["raw_ocr_documents"]

    result = collection.insert_one(ocr_data)
    inserted_id = str(result.inserted_id)
    print(f"[MongoDB] Raw OCR JSON successfully saved! Document ID: {inserted_id}")
    return inserted_id

def get_unprocessed_raw_documents(limit: int = 5):
    """Retrieves raw OCR documents that have not been preprocessed yet."""
    client = get_mongo_client()
    db = client["newspaper_database"]
    collection = db["raw_ocr_documents"]

    # Query for documents where status is raw_unprocessed
    query = {"_metadata.status": "raw_unprocessed"}
    documents = list(collection.find(query).limit(limit))

    print(f"[MongoDB] Found {len(documents)} unprocessed raw document(s).")
    return documents

def mark_document_as_processed(doc_id: str):
    """Updates status flag after preprocessing pipeline finishes."""
    client = get_mongo_client()
    db = client["newspaper_database"]
    collection = db["raw_ocr_documents"]

    collection.update_one(
        {"_id": ObjectId(doc_id)},
        {"$set": {"_metadata.status": "preprocessed", "_metadata.processed_at": datetime.datetime.utcnow().isoformat()}}
    )
    print(f"[MongoDB] Document {doc_id} marked as 'preprocessed'.")


if __name__ == "__main__":
    # Example Usage: Save local OCR JSON to MongoDB
    test_json_path = "./temp_outputs/Loksatta_2026-07-27_ocr.json"
    
    if os.path.exists(test_json_path):
        doc_id = save_raw_ocr_json(test_json_path)
        
        # Retrieve unprocessed docs
        raw_docs = get_unprocessed_raw_documents()
        for doc in raw_docs:
            print(f"Doc ID: {doc['_id']} | Source: {doc.get('source_file', 'N/A')}")