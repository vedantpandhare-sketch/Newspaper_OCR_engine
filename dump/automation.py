"""
automation.py

Pipeline:
1. Scrape TradingRef with Playwright
2. Download PDF(s) into INPUT_FOLDER
3. Run OCR_extraction.py
4. Wait until OCR finishes
"""

from pathlib import Path
import subprocess
import logging
from playwright.sync_api import sync_playwright

# ---------------- CONFIG ----------------
INPUT_FOLDER = Path(r"D:\OCR_Project\input")
OCR_SCRIPT = Path(r"D:\OCR_Project\OCR_extraction.py")

# TODO: Replace with the actual page URL
TRADINGREF_URL = "https://www.tradingref.com/"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

INPUT_FOLDER.mkdir(parents=True, exist_ok=True)


def scrape_tradingref():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=300
        )

        context = browser.new_context(
            accept_downloads=True
        )

        page = context.new_page()

        logging.info("Opening TradingRef...")
        page.goto(TRADINGREF_URL, wait_until="domcontentloaded", timeout=120000)
        page.screenshot(path="page.png")
        # ==========================================================
        # TODO:
        # Replace the examples below with the real selectors.
        #
        # page.select_option("select#exchange", "NSE")
        # page.select_option("select#symbol", "NIFTY")
        # page.click("button:text('Generate')")
        # ==========================================================

        logging.info("Waiting for download...")

        with page.expect_download() as download_info:

            # TODO: Replace selector
            page.click("text=Download")
            page.wait_for_timeout(10000)

        download = download_info.value

        save_path = INPUT_FOLDER / download.suggested_filename
        download.save_as(save_path)

        logging.info(f"Saved PDF -> {save_path}")

        browser.close()


def run_ocr():
    logging.info("Starting OCR...")
    subprocess.run(
        ["python", str(OCR_SCRIPT)],
        check=True
    )
    logging.info("OCR completed.")


def main():
    scrape_tradingref()
    run_ocr()
    logging.info("Pipeline finished successfully.")


if __name__ == "__main__":
    main()
