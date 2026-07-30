import os
import cv2
import json
import numpy as np
import time
import fitz  # PyMuPDF
from paddleocr import PaddleOCR
import datetime
import json
import os
from drive_utils import upload_file_to_drive

# Replace with your actual Google Drive Output Folder ID
OUTPUT_FOLDER_ID = "1IkXvx4xZUKrvP1qnGqepGLi8whgOBPEA"

class MarathiNewspaperTwoDimPipeline:
    def __init__(self):
        print("[System] Initializing Two-Dimensional Marathi Extraction Core...")
        self.ocr_engine = PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False, 
            text_detection_model_name='PP-OCRv5_mobile_det',        # lightweight vs default 'server' det model
            text_recognition_model_name='devanagari_PP-OCRv5_mobile_rec',  # must be pinned explicitly:
            # once ANY model name is set, `lang=` is silently ignored, so we
            # have to name the Marathi/devanagari recognizer ourselves or it
            # falls back to a generic model that isn't tuned for this script
            #use_gpu=False, 
            #show_log=False,
            text_recognition_batch_size=6,
            cpu_threads=os.cpu_count() or 4,
        )

    def _get_2d_layout_grid(self, high_res_bgr):
        """
        Slices the newspaper page both horizontally (into rows/stories) and
        vertically (into columns) to prevent overlapping articles from merging.
        """
        h, w, _ = high_res_bgr.shape
        gray = cv2.cvtColor(high_res_bgr, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 220, 255, cv2.THRESH_BINARY_INV)
        
        # 1. Detect Horizontal Rows (Horizontal cuts across the layout grid)
        horizontal_projection = np.sum(thresh, axis=1)
        row_whitespace_threshold = w * 255 * 0.01  # Less than 1% ink presence
        is_row_whitespace = horizontal_projection < row_whitespace_threshold
        
        rows = []
        in_row = False
        start_y = 0
        min_row_height = int(h * 0.04) # Drop noise rows under 4% of page height
        
        for y in range(h):
            if not is_row_whitespace[y] and not in_row:
                start_y = max(0, y - 8)
                in_row = True
            elif is_row_whitespace[y] and in_row:
                end_y = min(h, y + 8)
                if (end_y - start_y) > min_row_height:
                    rows.append((start_y, end_y))
                in_row = False
        if in_row and (h - start_y) > min_row_height:
            rows.append((start_y, h))
            
        if not rows:
            rows = [(0, h)] # Fallback if no distinct horizontal breaks are found

        # 2. Within each row, perform a localized column scan
        final_grid_blocks = []
        for r_start, r_end in rows:
            row_strip = thresh[r_start:r_end, 0:w]
            vertical_projection = np.sum(row_strip, axis=0)
            
            col_whitespace_threshold = (r_end - r_start) * 255 * 0.015
            is_col_whitespace = vertical_projection < col_whitespace_threshold
            
            in_col = False
            start_x = 0
            min_col_width = int(w * 0.05)
            
            for x in range(w):
                if not is_col_whitespace[x] and not in_col:
                    start_x = max(0, x - 10)
                    in_col = True
                elif is_col_whitespace[x] and in_col:
                    end_x = min(w, x + 10)
                    if (end_x - start_x) > min_col_width:
                        final_grid_blocks.append((start_x, r_start, end_x, r_end))
                    in_col = False
            if in_col and (w - start_x) > min_col_width:
                final_grid_blocks.append((start_x, r_start, w, r_end))
                
        return final_grid_blocks

    def _separate_heading_and_paragraphs(self, sorted_lines):
        """
        Differentiates headlines from paragraphs by calculating line box heights.
        Newspaper headlines use a significantly larger font size/height than body text.
        """
        if not sorted_lines:
            return "", []

        # Calculate bounding box height for each text line to identify the font scale
        line_heights = []
        for line in sorted_lines:
            bbox_coords = line[0] # [[x1,y1],[x2,y1],[x2,y2],[x1,y2]]
            y_coords = [pt[1] for pt in bbox_coords]
            height = max(y_coords) - min(y_coords)
            line_heights.append(height)
            
        median_height = np.median(line_heights) if line_heights else 20
        # Headlines are typically at least 1.4x larger than the average body text line height
        headline_threshold = median_height * 1.4 
        
        heading_parts = []
        paragraph_parts = []
        
        for idx, line in enumerate(sorted_lines):
            text_str = line[1][0].strip()
            
            # Skip empty lines or low-confidence noise drops
            if len(text_str) <= 1:
                continue
                
            if line_heights[idx] >= headline_threshold and not paragraph_parts:
                heading_parts.append(text_str)
            else:
                paragraph_parts.append(text_str)
                
        heading_text = " ".join(heading_parts).strip()
        
        # Format paragraphs cleanly by joining lines, splitting by common sentence markers
        full_body = " ".join(paragraph_parts).strip()
        paragraphs = [p.strip() for p in full_body.split("  ") if p.strip()]
        if not paragraphs and full_body:
            paragraphs = [full_body]
            
        return heading_text, paragraphs

    def process_pdf(self, pdf_path):
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"Input PDF not found: {pdf_path}")

        doc = fitz.open(pdf_path)
        all_pdf_results = {}

        # Start total timer
        total_start_time = time.perf_counter()

        for page_num in range(len(doc)):

            # Start page timer
            page_start_time = time.perf_counter()

            page_key = f"page_{page_num + 1}"
            print(f"\n--- Processing {page_key.upper()} / {len(doc)} ---")

            page = doc[page_num]

            # Render full page at 300 DPI
            zoom = 3.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)

            img_np = np.frombuffer(
                pix.samples, dtype=np.uint8
            ).reshape((pix.h, pix.w, 3))

            high_res_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)

            # Detect article grid
            grid_blocks = self._get_2d_layout_grid(high_res_bgr)

            # Sort blocks
            sorted_grid_blocks = sorted(
                grid_blocks,
                key=lambda b: (b[1] // 100, b[0])
            )

            print(f"[Processing] Isolated {len(sorted_grid_blocks)} independent article segments.")

            page_articles = []
            article_id_counter = 1

            # Build the list of valid crops first, keeping their grid coordinates
            # aligned by index, so we can send ALL crops to the model in ONE call
            # instead of one call per crop (which was the main source of overhead).
            valid_crops = []
            for (x1, y1, x2, y2) in sorted_grid_blocks:
                article_crop = high_res_bgr[y1:y2, x1:x2]
                if article_crop.size == 0:
                    continue
                valid_crops.append(article_crop)

            print(f"  [OCR] Running batched OCR on {len(valid_crops)} segments...", flush=True)
            batch_start = time.perf_counter()

            batch_results = self.ocr_engine.predict(valid_crops) if valid_crops else []

            print(f"  [OCR] Batch complete ({time.perf_counter() - batch_start:.1f}s total, "
                  f"{(time.perf_counter() - batch_start) / max(len(valid_crops), 1):.2f}s/segment avg)",
                  flush=True)

            for page_res in batch_results:

                # New PaddleOCR 3.x returns one result dict per image,
                # e.g. {'rec_texts': [...], 'rec_polys': [...], 'rec_scores': [...]}
                # instead of the old list of [bbox, (text, score)] pairs.
                rec_texts = page_res.get("rec_texts", [])
                rec_polys = page_res.get(
                    "rec_polys", page_res.get("dt_polys", [])
                )
                rec_scores = page_res.get(
                    "rec_scores", [1.0] * len(rec_texts)
                )

                if rec_texts:

                    # Rebuild the old-style line structure: [bbox, (text, score)]
                    # so the rest of the pipeline doesn't need to change.
                    formatted_lines = [
                        [poly, (text, score)]
                        for poly, text, score in zip(rec_polys, rec_texts, rec_scores)
                    ]

                    sorted_lines = sorted(
                        formatted_lines,
                        key=lambda line: line[0][0][1]
                    )

                    heading, paragraphs = self._separate_heading_and_paragraphs(
                        sorted_lines
                    )

                    if not heading and paragraphs:
                        words = paragraphs[0].split()
                        heading = " ".join(words[:5]) + "..."

                    if heading or paragraphs:
                        page_articles.append({
                            "article_id": article_id_counter,
                            "heading": heading,
                            "paragraphs": paragraphs
                        })

                        article_id_counter += 1

            all_pdf_results[page_key] = page_articles

            # End page timer
            page_time = time.perf_counter() - page_start_time

            print(
                f"[Time] {page_key} processed in {page_time:.2f} seconds "
                f"({len(page_articles)} articles)"
            )

        # End total timer
        total_time = time.perf_counter() - total_start_time

        doc.close()

        print("\n==============================")
        print(f"Total pages processed : {len(all_pdf_results)}")
        print(f"Total articles        : {sum(len(v) for v in all_pdf_results.values())}")
        print(f"Total extraction time : {total_time:.2f} seconds")
        print(f"Average time per page : {total_time / len(all_pdf_results):.2f} seconds")
        print("==============================")

        return all_pdf_results


# --- Module-level convenience API used by main.py / the Streamlit app ---
#
# main.py does: from ocr_pipeline import process_pdf_to_json
# We keep a single cached pipeline instance so the (slow, model-loading)
# constructor only runs once per process, not once per PDF/click.
_pipeline_singleton = None


def _get_pipeline():
    global _pipeline_singleton
    if _pipeline_singleton is None:
        _pipeline_singleton = MarathiNewspaperTwoDimPipeline()
    return _pipeline_singleton


def process_pdf_to_json(pdf_path):
    """
    Convenience wrapper: runs the full 2D article-extraction pipeline on a
    single PDF and returns the result as a plain dict (JSON-serializable),
    e.g. {"page_1": [{"article_id": 1, "heading": ..., "paragraphs": [...]}]}.
    """
    pipeline = _get_pipeline()
    return pipeline.process_pdf(pdf_path)


if __name__ == "__main__":
    INPUT_PDF = "test.pdf"
    OUTPUT_JSON = "test_extracted_articles.json"

    if os.path.exists(INPUT_PDF):
        extracted_data = process_pdf_to_json(INPUT_PDF)
        
        with open(OUTPUT_JSON, "w", encoding="utf-8") as json_file:
            json.dump(extracted_data, json_file, ensure_ascii=False, indent=4)
            
        print(f"\n[Success] 2D extraction complete! Output file: '{OUTPUT_JSON}'")
    else:
        print(f"[Error] Please place your newspaper PDF named '{INPUT_PDF}' in this folder.")