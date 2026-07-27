import datetime
import gc
import json
import os
import time
import cv2
import fitz  # PyMuPDF
import numpy as np
from paddleocr import PaddleOCR


class MarathiNewspaperTwoDimPipeline:

    def __init__(self):
        print(
            "[System] Initializing Two-Dimensional Marathi Extraction Core..."
        )
        # Optimized for CPU execution in headless environments
        self.ocr_engine = PaddleOCR(
            use_angle_cls=False,
            lang="devanagari",  # Marathi Devanagari script
            use_gpu=False,
            show_log=False,
            rec_batch_num=6,
        )

    def _get_2d_layout_grid(self, high_res_bgr):
        """Slices the newspaper page both horizontally (into rows/stories) and

        vertically (into columns) to prevent overlapping articles from merging.
        """
        h, w, _ = high_res_bgr.shape
        gray = cv2.cvtColor(high_res_bgr, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 220, 255, cv2.THRESH_BINARY_INV)

        # 1. Detect Horizontal Rows
        horizontal_projection = np.sum(thresh, axis=1)
        row_whitespace_threshold = w * 255 * 0.01
        is_row_whitespace = horizontal_projection < row_whitespace_threshold

        rows = []
        in_row = False
        start_y = 0
        min_row_height = int(h * 0.04)

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
            rows = [(0, h)]

        # 2. Localized Column Scan within each row
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
                        final_grid_blocks.append(
                            (start_x, r_start, end_x, r_end)
                        )
                    in_col = False
            if in_col and (w - start_x) > min_col_width:
                final_grid_blocks.append((start_x, r_start, w, r_end))

        return final_grid_blocks

    def _separate_heading_and_paragraphs(self, sorted_lines):
        """Differentiates headlines from body paragraphs using bounding box line

        heights.
        """
        if not sorted_lines:
            return "", []

        line_heights = []
        for line in sorted_lines:
            bbox_coords = line[0]
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

    def process_pdf(self, pdf_path: str) -> dict:
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"Input PDF not found: {pdf_path}")

        doc = fitz.open(pdf_path)
        all_pdf_results = {}
        filename = os.path.basename(pdf_path)

        total_start_time = time.perf_counter()

        for page_num in range(len(doc)):
            page_start_time = time.perf_counter()
            page_key = f"page_{page_num + 1}"
            print(f"\n--- Processing {page_key.upper()} / {len(doc)} ---")

            page = doc[page_num]
            zoom = 3.0  # 300 DPI high resolution
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)

            img_np = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                (pix.h, pix.w, 3)
            )
            high_res_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)

            grid_blocks = self._get_2d_layout_grid(high_res_bgr)
            sorted_grid_blocks = sorted(
                grid_blocks, key=lambda b: (b[1] // 100, b[0])
            )

            print(
                f"[Processing] Isolated {len(sorted_grid_blocks)} independent article segments."
            )

            page_articles = []
            article_id_counter = 1

            for x1, y1, x2, y2 in sorted_grid_blocks:
                article_crop = high_res_bgr[y1:y2, x1:x2]
                if article_crop.size == 0:
                    continue

                ocr_out = self.ocr_engine.ocr(article_crop, cls=False)

                if ocr_out and ocr_out[0]:
                    sorted_lines = sorted(
                        ocr_out[0], key=lambda line: line[0][0][1]
                    )
                    heading, paragraphs = (
                        self._separate_heading_and_paragraphs(sorted_lines)
                    )

                    if not heading and paragraphs:
                        words = paragraphs[0].split()
                        heading = " ".join(words[:5]) + "..."

                    if heading or paragraphs:
                        page_articles.append({
                            "article_id": article_id_counter,
                            "heading": heading,
                            "paragraphs": paragraphs,
                        })
                        article_id_counter += 1

            all_pdf_results[page_key] = page_articles
            page_time = time.perf_counter() - page_start_time
            print(
                f"[Time] {page_key} processed in {page_time:.2f}s ({len(page_articles)} articles)"
            )

            # Garbage collect per page to keep memory usage low on cloud runners
            gc.collect()

        total_time = time.perf_counter() - total_start_time
        doc.close()

        print("\n==============================")
        print(f"Total pages processed : {len(all_pdf_results)}")
        print(
            f"Total articles        : {sum(len(v) for v in all_pdf_results.values())}"
        )
        print(f"Total extraction time : {total_time:.2f} seconds")
        print("==============================")

        # Wrap output with metadata for MongoDB
        return {
            "source_filename": filename,
            "processed_at": datetime.datetime.utcnow().isoformat(),
            "total_pages": len(all_pdf_results),
            "pages": all_pdf_results,
        }


# Global helper function for main.py integration
def process_pdf_to_json(pdf_path: str) -> dict:
    pipeline = MarathiNewspaperTwoDimPipeline()
    return pipeline.process_pdf(pdf_path)


if __name__ == "__main__":
    # Local manual test execution
    sample_pdf = "Loksatta_Pune_20260727.pdf"
    if os.path.exists(sample_pdf):
        result = process_pdf_to_json(sample_pdf)
        with open(
            "extracted_articles_output.json", "w", encoding="utf-8"
        ) as f:
            json.dump(result, f, ensure_ascii=False, indent=4)
        print("[Success] OCR test run complete!")