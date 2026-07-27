import json
import os
import re
from datetime import datetime
import pymongo


def get_mongo_collection(collection_name: str):
  """Connects to MongoDB Atlas and returns the requested collection."""
  mongo_uri = os.environ.get("MONGO_URI")
  if not mongo_uri:
    raise ValueError(
        "Missing 'MONGO_URI' environment variable! Set it in your terminal or"
        " environment."
    )

  client = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
  db = client["newspaper_ocr_db"]
  collection = db[collection_name]
  return client, collection


def save_raw_ocr_json(json_path: str) -> str:
  """Reads an OCR output JSON file and inserts it into a date-named MongoDB collection.

  Example collection name: '2026-07-27'
  """
  if not os.path.exists(json_path):
    raise FileNotFoundError(f"JSON output file not found: {json_path}")

  with open(json_path, "r", encoding="utf-8") as f:
    data = json.load(f)

  # 1. Extract date from source_filename (e.g., "Loksatta_2026-07-27.pdf" -> "2026-07-27")
  source_filename = data.get("source_filename", "")
  date_match = re.search(r"\d{4}-\d{2}-\d{2}", source_filename)

  if date_match:
    collection_name = date_match.group(0)  # Yields "2026-07-27"
  else:
    # Fallback to current date if not found in filename
    collection_name = datetime.now().strftime("%Y-%m-%d")

  # Optional prefix if you prefer: collection_name = f"articles_{collection_name}"

  # 2. Connect to the dynamic collection
  client, collection = get_mongo_collection(collection_name)

  try:
    result = collection.insert_one(data)
    doc_id = str(result.inserted_id)
    print(
        f"[MongoDB] Inserted into collection '{collection_name}' | ID: {doc_id}"
    )
    return doc_id
  finally:
    client.close()