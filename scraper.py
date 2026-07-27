import os
import datetime
from playwright.sync_api import sync_playwright
from drive_utils import upload_file_to_drive

# Replace with your actual Google Drive INPUT Folder ID
INPUT_FOLDER_ID = "1fFMtXqlTyP7gZtJ0L39l1IcJE0Wh5vRf"
TEMP_DIR = "./temp_downloads"


def scrape_and_upload():
    today = datetime.datetime.now()
    today_iso = today.strftime("%Y-%m-%d")  # Format: "2026-07-27"

    os.makedirs(TEMP_DIR, exist_ok=True)
    local_pdf_path = os.path.join(TEMP_DIR, f"Loksatta_{today_iso}.pdf")

    print(f"[Scraper] Starting scrape process for date: {today_iso}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        print("[Scraper] Navigating to https://www.tradingref.com/...")
        # domcontentloaded is faster/more reliable than waiting for full load
        page.goto("https://www.tradingref.com/", timeout=60000, wait_until="domcontentloaded")

        # Instead of networkidle (unreliable on pages with ads/analytics/polling),
        # wait for the actual element we need to interact with.
        print("[Scraper] Waiting for date picker to be ready...")
        page.wait_for_selector("#datePicker", state="visible", timeout=30000)

        # 1. Date Selection via Flatpickr JS Instance
        print(f"[Scraper] Setting publication date to {today_iso} via Flatpickr API...")
        page.evaluate(f'''() => {{
            const fp = document.querySelector("#datePicker");
            if (fp && fp._flatpickr) {{
                fp._flatpickr.setDate("{today_iso}", true); // 'true' triggers onChange
            }} else if (fp) {{
                fp.value = "{today_iso}";
                fp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                fp.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }}
        }}''')

        # Short pause for dependent options to populate dynamically
        page.wait_for_timeout(1500)

        # 2. Select Language
        print("[Scraper] Selecting language: Marathi...")
        lang_select = page.get_by_label("Language")
        lang_select.wait_for(state="visible", timeout=10000)
        lang_select.select_option(value="marathi")

        page.wait_for_timeout(1000)

        # 3. Select Newspaper
        print("[Scraper] Selecting newspaper: Loksatta...")
        paper_select = page.get_by_label("Newspaper", exact=True)
        paper_select.wait_for(state="visible", timeout=10000)
        paper_select.select_option(value="Loksatta")

        page.wait_for_timeout(1000)

        # 4. Select Edition
        print("[Scraper] Selecting edition: Pune...")
        page.goto(
            "https://www.tradingref.com/#editionSelect",
            timeout=60000,
            wait_until="domcontentloaded",
        )
        edition_select = page.get_by_label("Edition")
        edition_select.wait_for(state="visible", timeout=10000)
        edition_select.select_option(value="Pune")

        # 5. Generate & Download PDF
        print("[Scraper] Generating & Downloading PDF...")
        with page.expect_download(timeout=90000) as download_info:
            page.get_by_role("button", name=" Generate & Download PDF").click()

        download = download_info.value
        download.save_as(local_pdf_path)

        context.close()
        browser.close()

    print(f"[Success] PDF downloaded and saved locally to: '{local_pdf_path}'")

    # 6. Upload PDF to Google Drive Input Folder
    print("[System] Uploading scraped PDF to Google Drive Input folder...")
    try:
        drive_file_id = upload_file_to_drive(local_pdf_path, INPUT_FOLDER_ID)
        print(f"[Success] File uploaded to Drive successfully! File ID: {drive_file_id}")
    except Exception as e:
        print(f"[Error] Failed to upload PDF to Google Drive: {e}")


if __name__ == "__main__":
    scrape_and_upload()