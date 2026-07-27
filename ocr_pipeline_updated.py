import os
import sys
import cv2
import json
import time
import datetime
import numpy as np
import fitz  # PyMuPDF
from paddleocr import PaddleOCR
from drive_utils import upload_file_to_drive

# Google Drive Target Output Folder ID
OUTPUT_FOLDER_ID = "1IkXvx4xZUKrvP1qnGqepGLi8whgOBPEA"
TEMP_DIR = "./temp_downloads"


class MarathiNewspaperTwoDimPipeline:
    def __init__(self):
        print("[System] Initializing Two-Dimensional Marathi Extraction Core...")
        self.ocr_engine = PaddleOCR(
            use_angle_cls=False, 
            lang='devanagari', 
            use_gpu=False, 
            show_log=False,
            rec_batch_num=6
        )

    def _get_2d_layout_grid(self, high_res_bgr):
        """
        Slices the newspaper page horizontally (rows/stories) and
        vertically (columns) to prevent overlapping articles from merging.
        """
        h, w, _ = high_res_bgr.shape
        gray = cv2.cvtColor(high_res_bgr, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 220, 255, cv2.THRESH_BINARY_INV)
        
        # 1. Detect Horizontal Rows
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

        # 2. Localized Column Scan within each Row
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
        Differentiates headlines from paragraphs by calculating bounding box heights.
        """
        if not sorted_lines:
            return "", []

        line_heights = []
        for line in sorted_lines:
            bbox_coords = line[0]  # [[x1,y1],[x2,y1],[x2,y2],[x1,y2]]
            y_coords = [pt[1] for pt in bbox_coords]
            height = max(y_coords) - min(y_coords)
            line_heights.append(height)
            
        median_height = np.median(line_heights) if line_heights else 20
        headline_threshold = median_height * 1.4 
        
        heading_parts = []
        paragraph_parts = []
        
        for idx, line in enumerate(sorted_lines):
            text_str = line[1][0].strip()
            
            if len(text_str) <= 1:
                continue
                
            if line_heights[idx] >= headline_threshold and not paragraph_parts:
                heading_parts.append(text_str)
            else:
                paragraph_parts.append(text_str)
                
        heading_text = " ".join(heading_parts).strip()
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
        total_start_time = time.perf_counter()

        for page_num in range(len(doc)):
            page_start_time = time.perf_counter()
            page_key = f"page_{page_num + 1}"
            print(f"\n--- Processing {page_key.upper()} / {len(doc)} ---")

            page = doc[page_num]

            # Render page at 300 DPI
            zoom = 3.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)

            img_np = np.frombuffer(
                pix.samples, dtype=np.uint8
            ).reshape((pix.h, pix.w, 3))

            high_res_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)

            # Detect layout grid
            grid_blocks = self._get_2d_layout_grid(high_res_bgr)
            sorted_grid_blocks = sorted(
                grid_blocks,
                key=lambda b: (b[1] // 100, b[0])
            )

            print(f"[Processing] Isolated {len(sorted_grid_blocks)} article segments.")

            page_articles = []
            article_id_counter = 1

            for (x1, y1, x2, y2) in sorted_grid_blocks:
                article_crop = high_res_bgr[y1:y2, x1:x2]

                if article_crop.size == 0:
                    continue

                ocr_out = self.ocr_engine.ocr(article_crop, cls=False)

                if ocr_out and ocr_out[0]:
                    sorted_lines = sorted(
                        ocr_out[0],
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
            page_time = time.perf_counter() - page_start_time

            print(
                f"[Time] {page_key} processed in {page_time:.2f}s "
                f"({len(page_articles)} articles extracted)"
            )

        total_time = time.perf_counter() - total_start_time
        doc.close()

        print("\n==============================")
        print(f"Total Pages Processed : {len(all_pdf_results)}")
        print(f"Total Articles        : {sum(len(v) for v in all_pdf_results.values())}")
        print(f"Total Processing Time : {total_time:.2f}s")
        print("==============================")

        return all_pdf_results


def main():
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    os.makedirs(TEMP_DIR, exist_ok=True)

    # 1. Locate Scraped PDF File
    expected_pdf = os.path.join(TEMP_DIR, f"Loksatta_{today_str}.pdf")
    
    if not os.path.exists(expected_pdf):
        print(f"[Warning] Exact target '{expected_pdf}' not found. Searching '{TEMP_DIR}' for fallback PDFs...")
        pdf_files = [os.path.join(TEMP_DIR, f) for f in os.listdir(TEMP_DIR) if f.lower().endswith(".pdf")]
        
        if not pdf_files:
            print(f"[Error] No PDF files found in '{TEMP_DIR}'. Execution halted.")
            sys.exit(1)
            
        input_pdf = pdf_files[0]
        print(f"[System] Selected PDF: '{input_pdf}'")
    else:
        input_pdf = expected_pdf

    # 2. Output File Path (YYYY-MM-DD.json)
    output_json_name = f"{today_str}.json"
    local_json_path = os.path.join(TEMP_DIR, output_json_name)

    # 3. Execute Extraction Pipeline
    pipeline = MarathiNewspaperTwoDimPipeline()
    print(f"[System] Running OCR extraction pipeline on '{input_pdf}'...")
    extracted_data = pipeline.process_pdf(input_pdf)

    # 4. Save Extracted Results to Local JSON
    with open(local_json_path, "w", encoding="utf-8") as json_file:
        json.dump(extracted_data, json_file, ensure_ascii=False, indent=4)
    print(f"[Success] Extracted data written to: '{local_json_path}'")

    # 5. Upload to Google Drive Output Folder
    print(f"[System] Uploading '{output_json_name}' to Google Drive Output folder...")
    try:
        drive_file_id = upload_file_to_drive(local_json_path, OUTPUT_FOLDER_ID, custom_filename=output_json_name)
        print(f"[Success] Successfully uploaded JSON to Google Drive! File ID: {drive_file_id}")
    except Exception as e:
        print(f"[Error] Failed to upload JSON to Google Drive: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()