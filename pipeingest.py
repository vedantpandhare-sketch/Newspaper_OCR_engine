import datetime
import json
import os
import re
import time
import uuid
from collections import Counter

from dotenv import load_dotenv
from pinecone import Pinecone
from pinecone.exceptions import PineconeApiException

# Load Environment Variables
load_dotenv()


# ---------------------------------------------------------------------------
# Date parsing / normalization
# ---------------------------------------------------------------------------
# Every chunk's "date" used to be a single root-level extraction_date copied
# onto ALL chunks in the file, even when different pages/articles carried
# their own (possibly different) date fields. These helpers resolve a date
# per page/article node instead, and normalize whatever format shows up
# (2026-08-18, 18/8/26, 18-08-2026, ...) to a consistent "YYYY-MM-DD".

_DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d-%m-%y",
    "%d/%m/%y",
    "%Y%m%d",
    "%d_%m_%Y",
    "%d_%m_%y",
    "%m-%d-%Y",
    "%m/%d/%Y",
]


def parse_date_value(value) -> "datetime.date | None":
    """Best-effort parse of a date-like value into a datetime.date, or None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def normalize_date_value(value) -> str:
    """Normalizes any recognized date format to 'YYYY-MM-DD'. Empty string if unparseable."""
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        return value.strip()
    parsed = parse_date_value(value)
    return parsed.strftime("%Y-%m-%d") if parsed else ""


def infer_date_from_path(path: str) -> str:
    """
    Falls back to deriving a date from the source filename when the JSON
    payload doesn't carry one at the page/article level, e.g.
    'Loksatta_2026-08-21_ocr.json' -> '2026-08-21'.
    """
    stem = os.path.splitext(os.path.basename(path))[0]

    patterns = [
        r"(20\d{2}-\d{1,2}-\d{1,2})",
        r"(\d{1,2}[-_/]\d{1,2}[-_/](?:\d{2}|\d{4}))",
        r"(\d{4}\d{2}\d{2})",
    ]
    for pattern in patterns:
        m = re.search(pattern, stem)
        if m:
            normalized = normalize_date_value(m.group(1))
            if normalized:
                return normalized
    return ""


def find_date_in_node(node) -> str:
    """
    Searches a page/article node for the first plausible date field,
    preferring keys whose names suggest they actually are a date
    (extraction_date, article_date, published_date, ...), before falling
    back to a looser "contains 'date'" match. Recurses into nested
    dicts/lists so a date on the specific article, not just the page or
    the root, is preferred and found.
    """
    if isinstance(node, dict):
        preferred_keys = (
            "extraction_date",
            "article_date",
            "published_date",
            "publish_date",
            "pub_date",
            "date",
            "issue_date",
        )

        for key in preferred_keys:
            for k, v in node.items():
                if str(k).lower() == key:
                    normalized = normalize_date_value(v)
                    if normalized:
                        return normalized

        for k, v in node.items():
            key = str(k).lower()
            if any(token in key for token in ("date", "publish", "issue")):
                normalized = normalize_date_value(v)
                if normalized:
                    return normalized

        for v in node.values():
            if isinstance(v, (dict, list)):
                found = find_date_in_node(v)
                if found:
                    return found

    elif isinstance(node, list):
        for item in node:
            found = find_date_in_node(item)
            if found:
                return found

    return ""


# ---------------------------------------------------------------------------
# Chunking / text extraction
# ---------------------------------------------------------------------------

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


def resolve_page_number(page_item, fallback: int) -> int:
    if isinstance(page_item, dict):
        for key in ("page_number", "page", "page_no", "pageNum"):
            value = page_item.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.strip().isdigit():
                return int(value.strip())
    return fallback


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
    a specified Pinecone index. Each chunk's date is resolved from its own
    page/article node first (falling back to the filename's date, then
    "unknown"), instead of one root-level date being stamped onto everything.
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

    # Root-level date is now only a fallback, not the answer for every chunk.
    root_date = find_date_in_node(data) or infer_date_from_path(json_path)
    print(f"[Ingest] Fallback date (root/filename): {root_date or 'unknown'}")

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
    date_counter = Counter()

    for idx, page_item in enumerate(pages_to_process, start=1):
        page_number = resolve_page_number(page_item, idx)

        # Resolve THIS page/article's own date, falling back to the
        # file-level date only when the node itself doesn't carry one.
        page_date = find_date_in_node(page_item) or root_date

        full_text = extract_text_from_node(page_item)
        if not full_text.strip():
            continue

        chunks = split_text_smart(full_text, chunk_size=700, overlap=100)

        for chunk_id, chunk in enumerate(chunks):
            date_counter[page_date or "unknown"] += 1
            records.append(
                {
                    "_id": str(uuid.uuid4()),
                    "text": chunk,
                    "page": page_number,
                    "chunk": chunk_id,
                    "date": page_date or "",
                    "source_paper": paper_identifier,
                }
            )

    print(f"[Ingest] Generated {len(records)} total text chunks.")
    if date_counter:
        print("[Ingest] Chunk counts by resolved date:")
        for d, count in sorted(date_counter.items()):
            print(f"    {d}: {count}")

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
