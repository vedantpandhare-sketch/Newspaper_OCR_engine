import os
import sys
import time
import streamlit as st

# 1. Dynamically add project root directory to Python path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 2. Local module imports (Handles varying scraper script names dynamically)
try:
    import scraper as scraper_module
except ModuleNotFoundError:
    try:
        import pipeline as scraper_module
    except ModuleNotFoundError:
        try:
            import download_newspaper as scraper_module
        except ModuleNotFoundError:
            scraper_module = None

import ocr_pipeline_updated
import db_mongo
import pipeingest

# 3. Streamlit Page Config & Theme
st.set_page_config(
    page_title="Newspaper OCR & Embedding Engine",
    page_icon="📰",
    layout="wide"
)

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


def run_pipeline_execution(status_box, log_area, progress_bar):
    """Executes the full pipeline sequentially with live Streamlit UI updates."""
    logs = []

    def log(msg):
        logs.append(msg)
        log_area.code("\n".join(logs), language="bash")

    try:
        # Step 1: Scrape / Download Newspaper PDF
        status_box.info("⏳ [1/4] Running Scraper to fetch newspaper PDF...")
        progress_bar.progress(10)
        log("[Step 1/4] Launching PDF scraper...")

        if scraper_module and hasattr(scraper_module, "run_scraper"):
            pdf_path = scraper_module.run_scraper()
        elif scraper_module and hasattr(scraper_module, "download_newspaper"):
            pdf_path = scraper_module.download_newspaper()
        else:
            raise ImportError("Could not find `run_scraper()` or `download_newspaper()` function in scraper script.")

        if not pdf_path or not os.path.exists(pdf_path):
            raise FileNotFoundError("❌ Scraper failed to download or locate the PDF file.")

        log(f"✓ PDF Downloaded: {os.path.basename(pdf_path)}")
        progress_bar.progress(30)

        # Step 2: PaddleOCR Extraction
        status_box.info("⏳ [2/4] Running PaddleOCR layout analysis and text extraction...")
        log("[Step 2/4] Processing layout and text blocks...")

        # Supports both method naming conventions in ocr_pipeline_updated
        if hasattr(ocr_pipeline_updated, "process_pdf_and_save_json"):
            output_json_path = ocr_pipeline_updated.process_pdf_and_save_json(pdf_path)
        elif hasattr(ocr_pipeline_updated, "process_pdf_to_json"):
            output_json_data = ocr_pipeline_updated.process_pdf_to_json(pdf_path)
            output_json_path = pdf_path.replace(".pdf", "_ocr.json")
            import json
            with open(output_json_path, "w", encoding="utf-8") as f:
                json.dump(output_json_data, f, ensure_ascii=False, indent=2)

        if not output_json_path or not os.path.exists(output_json_path):
            raise FileNotFoundError("❌ OCR processing failed to generate JSON file.")

        log(f"✓ OCR Complete! Output: {os.path.basename(output_json_path)}")
        progress_bar.progress(55)

        # Step 3: MongoDB Daily Collection Ingestion
        status_box.info("⏳ [3/4] Uploading raw OCR records to dynamic MongoDB collection...")
        log("[Step 3/4] Storing JSON in MongoDB Atlas...")

        doc_id, collection_name = db_mongo.save_raw_ocr_json(output_json_path)

        log(f"✓ MongoDB Upload Complete!")
        log(f"  └─ Dynamic Collection: '{collection_name}'")
        log(f"  └─ Document ID: '{doc_id}'")
        progress_bar.progress(75)

        # Step 4: Pinecone Ingestion
        status_box.info("⏳ [4/4] Generating BAAI/bge-m3 embeddings & Ingesting to Pinecone...")
        log("[Step 4/4] Embedding text via Pinecone Server 'bge-m3'...")

        vector_count = pipeingest.upsert_collection_to_pinecone(doc_id=doc_id, collection_name=collection_name)

        log(f"✓ Pinecone Ingestion Complete! ({vector_count} vectors saved)")
        progress_bar.progress(100)

        status_box.success(f"🎉 Pipeline Completed Successfully! Ingested {vector_count} text blocks into collection '{collection_name}'.")
        return True

    except Exception as e:
        progress_bar.progress(0)
        status_box.error(f"❌ Pipeline Failed: {str(e)}")
        log(f"\n[CRITICAL ERROR]: {str(e)}")
        return False


# --- STREAMLIT UI LAYOUT ---
st.title("📰 Newspaper OCR & Vector Pipeline Engine")
st.caption("Automated PDF Scraper → PaddleOCR Layout Engine → MongoDB Daily Store → Pinecone BGE-M3 Embeddings")

col_control, col_monitor = st.columns([1, 2])

with col_control:
    st.subheader("⚙️ Control Panel")
    run_btn = st.button("🚀 Run Full Automation", use_container_width=True)

    st.markdown("---")
    st.markdown("### 📋 Pipeline Flow")
    st.markdown("""
    1. **Scraper:** Downloads latest daily PDF.
    2. **PaddleOCR:** Extracts text, headers, and page coordinates.
    3. **MongoDB:** Archives document into daily collection (`ocr_YYYY-MM-DD`).
    4. **Pinecone:** Generates `bge-m3` embeddings and updates index.
    """)

with col_monitor:
    st.subheader("📊 Real-time Execution Monitor")

    status_box = st.empty()
    status_box.info("System Ready. Click 'Run Full Automation' to initiate execution.")

    progress_bar = st.progress(0)

    st.markdown("**Live Logs:**")
    log_area = st.empty()
    log_area.code("Logs will stream here during execution...", language="text")

# Trigger Run
if run_btn:
    success = run_pipeline_execution(status_box, log_area, progress_bar)
    if success:
        st.balloons()

st.markdown("---")

# Optional Vector Search Interface
st.subheader("🔍 Query Pinecone Index")
with st.expander("Search Vectors"):
    query_text = st.text_input("Enter search phrase or keyword:")
    if st.button("Search Index") and query_text:
        try:
            from pinecone import Pinecone
            pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
            index = pc.Index(os.getenv("PINECONE_INDEX_NAME", "newspaper-index"))

            query_emb = pc.inference.embed(
                model="bge-m3",
                inputs=[query_text],
                parameters={"input_type": "query"}
            )

            results = index.query(
                vector=query_emb[0]["values"],
                top_k=3,
                include_metadata=True
            )

            for idx, match in enumerate(results.get("matches", [])):
                st.markdown(f"**Match #{idx+1}** (Score: `{match['score']:.4f}`) - Page {match['metadata'].get('page', 'N/A')}")
                st.write(match["metadata"].get("text", ""))
                st.caption(f"Headline: {match['metadata'].get('headline', 'N/A')}")
                st.markdown("---")

        except Exception as query_err:
            st.error(f"Search Query Failed: {query_err}")