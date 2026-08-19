import asyncio
import json
import os
import sys
import time
import streamlit as st

# Environment setup
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

# --- FIX FOR PLAYWRIGHT + STREAMLIT ON WINDOWS ---
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
# --------------------------------------------------

# Ensure local project path is accessible
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Core imports
from db_mongo import save_raw_ocr_json
from ocr_pipeline import process_pdf_to_json
from pipeingest import ingest_json_to_pinecone
from scraper import run_scraper

# Index Mapping Configuration
INDEX_MAPPING = {
    "loksatta": "loksttapune",
    "lokmat": "lokmatpune",
}

# Configure Streamlit Page
st.set_page_config(
    page_title="Automated Newspaper OCR & Vector Pipeline",
    page_icon="📰",
    layout="wide",
)

# Custom Styling
st.markdown(
    """
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
""",
    unsafe_allow_html=True,
)


def run_pipeline_with_ui(
    status_box, log_area, progress_bar, manual_pdf_path=None, force_reocr=False
):
    """Executes the pipeline in 4 distinct phases across ALL newspapers."""
    logs = []

    def log(msg):
        logs.append(msg)
        log_area.code("\n".join(logs), language="bash")

    try:
        log("=" * 60)
        log("🚀 BATCH MULTI-NEWSPAPER OCR & DEDICATED VECTOR PIPELINE")
        log("=" * 60)

        # ---------------------------------------------------------
        # PHASE 1: SCRAPE ALL NEWSPAPERS
        # ---------------------------------------------------------
        status_box.info("⏳ [Phase 1/4] Running Scraper for all newspapers...")
        progress_bar.progress(10)
        log("\n[Phase 1/4] Downloading all target newspapers...")

        if manual_pdf_path and os.path.exists(manual_pdf_path):
            pdf_paths = [manual_pdf_path]
            log(f"✓ Using local specified PDF path: {manual_pdf_path}")
        else:
            pdf_paths = run_scraper()

            if isinstance(pdf_paths, str):
                pdf_paths = [pdf_paths]

            if not pdf_paths:
                fallback_path = "temp_downloads/Loksatta_2026-08-19.pdf"
                if os.path.exists(fallback_path):
                    pdf_paths = [fallback_path]
                    log(f"⚠️ Scraper fallback used: {fallback_path}")
                else:
                    raise FileNotFoundError(
                        "❌ Scraper failed to retrieve any PDFs and no fallback was found."
                    )

        log(f"✓ Total PDFs acquired for processing: {len(pdf_paths)}")
        for idx, path in enumerate(pdf_paths, start=1):
            log(f"   ├─ PDF #{idx}: {os.path.basename(path)}")

        progress_bar.progress(25)

        # ---------------------------------------------------------
        # PHASE 2: OCR EXTRACTION ON ALL PAPERS FIRST
        # ---------------------------------------------------------
        status_box.info("⏳ [Phase 2/4] Running PaddleOCR on ALL papers...")
        log("\n[Phase 2/4] Executing PaddleOCR extraction across all PDFs...")

        ocr_records = []

        for idx, pdf_path in enumerate(pdf_paths, start=1):
            paper_name = os.path.basename(pdf_path).split("_")[0]
            output_json_path = pdf_path.replace(".pdf", "_ocr.json")

            log(f"\n► [{idx}/{len(pdf_paths)}] OCR Extraction for {paper_name}...")

            if os.path.exists(output_json_path) and not force_reocr:
                log(f"   └─ Found cached OCR file: {os.path.basename(output_json_path)}")
            else:
                if force_reocr and os.path.exists(output_json_path):
                    log("   └─ Force Re-OCR enabled. Overwriting existing cached JSON...")

                ocr_json_data = process_pdf_to_json(pdf_path)
                with open(output_json_path, "w", encoding="utf-8") as f:
                    json.dump(ocr_json_data, f, ensure_ascii=False, indent=2)
                log(f"   └─ OCR complete! Saved {os.path.basename(output_json_path)}")

            ocr_records.append({
                "paper_name": paper_name,
                "pdf_path": pdf_path,
                "json_path": output_json_path,
            })

        progress_bar.progress(50)

        # ---------------------------------------------------------
        # PHASE 3: MONGODB UPLOAD FOR ALL PAPERS
        # ---------------------------------------------------------
        status_box.info("⏳ [Phase 3/4] Uploading raw JSONs to MongoDB...")
        log("\n[Phase 3/4] Uploading all extracted JSONs to MongoDB Atlas...")

        for idx, record in enumerate(ocr_records, start=1):
            paper_name = record["paper_name"]
            json_path = record["json_path"]

            log(f"\n► [{idx}/{len(ocr_records)}] Saving {paper_name} to MongoDB...")
            doc_id, collection_name = save_raw_ocr_json(json_path)
            log(f"   ├─ Collection: '{collection_name}'")
            log(f"   └─ Document ID: '{doc_id}'")

            record["doc_id"] = doc_id
            record["collection_name"] = collection_name

        progress_bar.progress(75)

        # ---------------------------------------------------------
        # PHASE 4: PINECONE VECTOR INGESTION TO DEDICATED INDEXES
        # ---------------------------------------------------------
        status_box.info("⏳ [Phase 4/4] Ingesting vectors to dedicated Pinecone indexes...")
        log("\n[Phase 4/4] Ingesting vectors into separate Pinecone indexes...")

        summary_results = []

        for idx, record in enumerate(ocr_records, start=1):
            paper_name = record["paper_name"]
            json_path = record["json_path"]
            
            # Resolve target Pinecone index name
            target_index = INDEX_MAPPING.get(paper_name.lower(), f"{paper_name.lower()}-index")

            log(f"\n► [{idx}/{len(ocr_records)}] Vectorizing {paper_name} -> Index: '{target_index}'...")

            vector_count = ingest_json_to_pinecone(
                json_path=json_path,
                index_name=target_index
            )

            log(f"   └─ Ingested {vector_count} vector chunks into '{target_index}'.")

            summary_results.append({
                "paper": paper_name,
                "index": target_index,
                "collection": record["collection_name"],
                "vectors": vector_count
            })

        progress_bar.progress(100)
        log("\n" + "=" * 60)
        log("🎉 ALL NEWSPAPERS PROCESSED SUCCESSFULLY!")
        for res in summary_results:
            log(f" ├─ {res['paper']}: {res['vectors']} vectors -> Index '{res['index']}'")
        log("=" * 60)

        status_box.success(f"🎉 Pipeline Completed! Processed {len(summary_results)} paper(s).")
        return True, summary_results

    except Exception as e:
        progress_bar.progress(0)
        status_box.error(f"❌ Pipeline Execution Failed: {str(e)}")
        return False, []


# --- STREAMLIT UI ---
st.title("📰 Newspaper OCR & Dedicated Multi-Vector Engine")
st.caption(
    "Automated Multi-Paper Scraper → Sequential OCR → MongoDB Storage → Targeted Pinecone Indexes"
)

col_left, col_right = st.columns([1, 2])

with col_left:
    st.subheader("⚙️ Control Panel")

    force_reocr_check = st.checkbox("Force Re-run OCR (Ignore cached JSONs)", value=False)
    run_btn = st.button("🚀 Run Full Pipeline", use_container_width=True)

    with st.expander("🛠️ Single File Override"):
        manual_path_input = st.text_input(
            "Target PDF Path (Optional)", value=""
        )

    st.markdown("---")
    st.markdown("### 📋 Pipeline Flow")
    st.markdown("""
    1. **Scrape:** Fetch all target newspapers (`Loksatta`, `Lokmat`).
    2. **Batch OCR:** Run PaddleOCR on **all** downloaded PDFs.
    3. **MongoDB:** Store raw JSON files into daily collections.
    4. **Pinecone:** Chunk text and route to dedicated indexes (`loksttapune`, `lokmatpune`).
    """)

with col_right:
    st.subheader("📊 Execution Monitor")

    status_box = st.empty()
    status_box.info(
        "System Ready. Click 'Run Full Pipeline' to process all newspapers."
    )

    progress_bar = st.progress(0)

    st.markdown("**Live Execution Logs:**")
    log_area = st.empty()
    log_area.code("Logs will appear here during execution...", language="text")

# Trigger Pipeline Execution
if run_btn:
    success, summary = run_pipeline_with_ui(
        status_box,
        log_area,
        progress_bar,
        manual_pdf_path=manual_path_input.strip() if manual_path_input else None,
        force_reocr=force_reocr_check,
    )
    if success:
        st.balloons()

st.markdown("---")

# Vector Search Interface
st.subheader("🔍 Query Pinecone Vector Index")
with st.expander("Perform Search Query"):
    selected_paper = st.selectbox("Select Target Paper / Index", options=["Loksatta (loksttapune)", "Lokmat (lokmatpune)"])
    search_query = st.text_input("Enter search phrase or keyword:")

    if st.button("Search Vector DB") and search_query:
        try:
            from pinecone import Pinecone
            from sentence_transformers import SentenceTransformer

            target_idx_name = "loksttapune" if "Loksatta" in selected_paper else "lokmatpune"

            st.info(f"Encoding query & searching '{target_idx_name}'...")
            model = SentenceTransformer("BAAI/bge-m3", trust_remote_code=True)
            query_vector = model.encode(
                search_query, normalize_embeddings=True
            ).tolist()

            pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
            index = pc.Index(target_idx_name)

            results = index.query(
                vector=query_vector, top_k=5, include_metadata=True
            )

            matches = results.get("matches", [])
            if not matches:
                st.warning(f"No matching vectors found in index '{target_idx_name}'.")
            else:
                for idx, match in enumerate(matches):
                    st.markdown(
                        f"**Match #{idx+1}** (Similarity: `{match['score']:.4f}`) - Page {match['metadata'].get('page', 'N/A')}"
                    )
                    st.write(match["metadata"].get("text", ""))
                    st.caption(
                        f"Date: {match['metadata'].get('date', 'N/A')} | Chunk ID: {match['metadata'].get('chunk', 'N/A')}"
                    )
                    st.markdown("---")

        except Exception as search_err:
            st.error(f"Search failed: {search_err}")