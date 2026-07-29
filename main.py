# main.py
import os
import sys
import json
import time
import streamlit as st

# Environment setup
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

# Ensure local project path is accessible
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Core imports
from scraper import run_scraper
from ocr_pipeline_updated import process_pdf_to_json
from db_mongo import save_raw_ocr_json

# Configure Streamlit Page
st.set_page_config(
    page_title="Automated Newspaper OCR & Vector Pipeline",
    page_icon="📰",
    layout="wide"
)

# Custom Styling
st.markdown("""
    <style>
    .stButton>button {
        width: 100%;
        background-color: #0284c7;
        color: white;
        font-weight: bold;
        padding: 0.65rem;
        border-radius: 8px;
    }
    .stButton>button:hover {
        background-color: #0369a1;
        color: white;
    }
    </style>
""", unsafe_allow_html=True)


def run_pipeline_with_ui(status_box, log_area, progress_bar, manual_pdf_path=None):
    """Executes the full pipeline and updates the Streamlit interface in real time."""
    logs = []

    def log(msg):
        logs.append(msg)
        log_area.code("\n".join(logs), language="bash")

    try:
        # Step 1: Scraper Step
        status_box.info("⏳ [1/4] Running Newspaper Scraper...")
        progress_bar.progress(10)
        log("=" * 60)
        log("🚀 AUTOMATED NEWSPAPER SCRAPER, OCR & PINECONE PIPELINE")
        log("=" * 60)
        log("\n[1/4] Initiating Scraper step...")

        if manual_pdf_path and os.path.exists(manual_pdf_path):
            pdf_path = manual_pdf_path
            log(f"✓ Using local specified PDF path: {pdf_path}")
        else:
            pdf_path = run_scraper()
            if not pdf_path or not os.path.exists(pdf_path):
                fallback_path = "temp_downloads/Loksatta_2026-07-29.pdf"
                if os.path.exists(fallback_path):
                    pdf_path = fallback_path
                    log(f"⚠️ Scraper fallback used: {pdf_path}")
                else:
                    raise FileNotFoundError("❌ Scraper failed and fallback PDF was not found.")

        log(f"✓ Target PDF: {os.path.basename(pdf_path)}")
        progress_bar.progress(25)

        # Step 2: PaddleOCR Extraction
        status_box.info("⏳ [2/4] Running PaddleOCR Processing...")
        log(f"\n[2/4] Running PaddleOCR Pipeline on {pdf_path}...")

        output_json_path = pdf_path.replace(".pdf", "_ocr.json")

        if os.path.exists(output_json_path):
            log(f"✓ Found existing cached OCR JSON: {os.path.basename(output_json_path)}")
        else:
            ocr_json_data = process_pdf_to_json(pdf_path)
            with open(output_json_path, "w", encoding="utf-8") as f:
                json.dump(ocr_json_data, f, ensure_ascii=False, indent=2)
            log(f"✓ Saved backup OCR JSON: {os.path.basename(output_json_path)}")

        progress_bar.progress(55)

        # Step 3: MongoDB Atlas Daily Ingestion
        status_box.info("⏳ [3/4] Uploading Raw JSON to MongoDB Atlas...")
        log("\n[3/4] Uploading Raw JSON to MongoDB Atlas...")

        doc_id, collection_name = save_raw_ocr_json(output_json_path)
        log(f"✓ Saved in MongoDB!")
        log(f"  ├─ Dynamic Collection: '{collection_name}'")
        log(f"  └─ Document ID: '{doc_id}'")

        progress_bar.progress(75)

        # Step 4: Chunking, Local Embeddings (SentenceTransformers), & Pinecone Upsert
        status_box.info("⏳ [4/4] Generating Local BGE-M3 Embeddings & Ingesting to Pinecone...")
        log("\n[4/4] Generating Embeddings & Ingesting to Pinecone...")

        from pipeingest import upsert_collection_to_pinecone

        vector_count = upsert_collection_to_pinecone(doc_id=doc_id, collection_name=collection_name)
        log(f"✓ Pinecone Ingestion Complete! ({vector_count} vector chunks saved)")

        progress_bar.progress(100)
        log("\n" + "=" * 60)
        log("🎉 FULL PIPELINE COMPLETED SUCCESSFULLY!")
        log("=" * 60)

        status_box.success(f"🎉 Pipeline Completed! Saved {vector_count} vector chunks in '{collection_name}'.")
        return True, collection_name

    except Exception as e:
        progress_bar.progress(0)
        status_box.error(f"❌ Pipeline Execution Failed: {str(e)}")
        log(f"\n❌ Pipeline Error: {str(e)}")
        return False, None


# --- STREAMLIT UI ---
st.title("📰 Newspaper OCR & Vector Ingestion Engine")
st.caption("PDF Ingestion → PaddleOCR → Dynamic MongoDB Storage → SentenceTransformer BGE-M3 Embeddings → Pinecone")

col_left, col_right = st.columns([1, 2])

with col_left:
    st.subheader("⚙️ Control Panel")
    
    run_btn = st.button("🚀 Run Full Pipeline", use_container_width=True)

    with st.expander("🛠️ File Settings"):
        manual_path_input = st.text_input(
            "Target PDF Path", 
            value="temp_downloads/Loksatta_2026-07-29.pdf"
        )

    st.markdown("---")
    st.markdown("### 📋 Pipeline Flow")
    st.markdown("""
    1. **Scrape:** Fetch PDF newspaper file.
    2. **OCR:** PaddleOCR extracts text blocks and headlines.
    3. **MongoDB:** Archives output into a dynamic daily collection.
    4. **Pinecone:** Chunks text and generates `BAAI/bge-m3` vectors locally.
    """)

with col_right:
    st.subheader("📊 Execution Monitor")

    status_box = st.empty()
    status_box.info("System Ready. Click 'Run Full Pipeline' to initiate processing.")

    progress_bar = st.progress(0)

    st.markdown("**Live Execution Logs:**")
    log_area = st.empty()
    log_area.code("Logs will appear here during execution...", language="text")

# Trigger Run
if run_btn:
    success, col_used = run_pipeline_with_ui(
        status_box, 
        log_area, 
        progress_bar, 
        manual_pdf_path=manual_path_input.strip() if manual_path_input else None
    )
    if success:
        st.balloons()

st.markdown("---")

# Vector Search Interface with SentenceTransformers
st.subheader("🔍 Query Pinecone Vector Index")
with st.expander("Perform Search Query"):
    search_query = st.text_input("Enter search phrase or keyword:")
    if st.button("Search Vector DB") and search_query:
        try:
            from sentence_transformers import SentenceTransformer
            from pinecone import Pinecone

            st.info("Encoding query via SentenceTransformer...")
            model = SentenceTransformer("BAAI/bge-m3", trust_remote_code=True)
            query_vector = model.encode(search_query, normalize_embeddings=True).tolist()

            pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
            index = pc.Index(os.getenv("PINECONE_INDEX_NAME", "test"))

            results = index.query(
                vector=query_vector,
                top_k=3,
                include_metadata=True
            )

            matches = results.get("matches", [])
            if not matches:
                st.warning("No matching vectors found in index.")
            else:
                for idx, match in enumerate(matches):
                    st.markdown(f"**Match #{idx+1}** (Similarity: `{match['score']:.4f}`) - Page {match['metadata'].get('page', 'N/A')}")
                    st.write(match["metadata"].get("text", ""))
                    st.caption(f"Headline: {match['metadata'].get('heading', 'N/A')} | Collection: {match['metadata'].get('collection_source', 'N/A')}")
                    st.markdown("---")

        except Exception as search_err:
            st.error(f"Search failed: {search_err}")