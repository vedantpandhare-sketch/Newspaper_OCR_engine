import datetime
import json
import os
import tempfile
import time
from playwright.sync_api import sync_playwright

TEMP_DIR = "./temp_downloads"
COOKIE_FILE = "./temp_downloads/cloudflare_cookies.json"
INPUT_FOLDER_ID = "1fFMtXqlTyP7gZtJ0L39l1IcJE0Wh5vRf"


def auto_solve_turnstile(page):
    """
    Attempts to automatically find and click the Turnstile challenge widget 
    with human-like cursor behavior to pass without human intervention.
    """
    try:
        page.wait_for_timeout(3000)
        # Look for the Cloudflare iframe box
        frames = page.frames
        for frame in frames:
            if "cloudflare" in frame.url or "challenges" in frame.url:
                print("[Scraper] Detected Cloudflare iframe, attempting automated click...")
                
                # Move mouse naturally toward the checkbox area
                box = frame.locator("input[type='checkbox'], .mark, #challenge-stage").first
                if box.is_visible(timeout=3000):
                    bounding_box = box.bounding_box()
                    if bounding_box:
                        # Move mouse with slight offsets to emulate human interaction
                        page.mouse.move(bounding_box["x"] + 10, bounding_box["y"] + 10)
                        page.wait_for_timeout(500)
                        page.mouse.click(bounding_box["x"] + 10, bounding_box["y"] + 10)
                        print("[Scraper] Clicked Turnstile checkbox automatically.")
                        page.wait_for_timeout(4000)
                        return True
    except Exception as e:
        print(f"[Scraper] Automated click skipped or not needed: {e}")
    return False


def run_scraper() -> str:
    """Scrapes the newspaper PDF fully automatically with persistent session cookies."""
    today = datetime.datetime.now()
    today_iso = today.strftime("%Y-%m-%d")

    os.makedirs(TEMP_DIR, exist_ok=True)
    local_pdf_path = os.path.abspath(
        os.path.join(TEMP_DIR, f"Loksatta_{today_iso}.pdf")
    )

    print(f"[Scraper] Starting automated scrape process for date: {today_iso}")

    user_data_dir = os.path.join(tempfile.gettempdir(), "playwright_chrome_profile")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            channel="chrome",
            headless=False,
            ignore_default_args=["--enable-automation"],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--start-maximized",
            ],
            viewport=None,
            accept_downloads=True,
        )

        # Inject previously saved Cloudflare cookies if available
        if os.path.exists(COOKIE_FILE):
            try:
                with open(COOKIE_FILE, "r") as f:
                    cookies = json.load(f)
                    context.add_cookies(cookies)
                print("[Scraper] Loaded existing Cloudflare clearance cookies.")
            except Exception as e:
                print(f"[Scraper] Failed to load cookies: {e}")

        page = context.pages[0] if context.pages else context.new_page()

        print("[Scraper] Navigating to https://www.tradingref.com/...")
        page.goto(
            "https://www.tradingref.com/",
            timeout=60000,
            wait_until="domcontentloaded",
        )

        # Try automatic turnstile bypass
        auto_solve_turnstile(page)

        # Wait for main page form to confirm challenge completion
        print("[Scraper] Waiting for date picker target element...")
        try:
            page.wait_for_selector("#datePicker", state="visible", timeout=30000)
        except Exception:
            # If state not visible yet, attempt one extra auto-click fallback
            auto_solve_turnstile(page)
            page.wait_for_selector("#datePicker", state="visible", timeout=30000)

        print("[Scraper] Access confirmed! Saving updated clearance cookies...")
        
        # Save valid session cookies (including cf_clearance) to disk
        try:
            current_cookies = context.cookies()
            with open(COOKIE_FILE, "w") as f:
                json.dump(current_cookies, f)
        except Exception as e:
            print(f"[Scraper] Warning - couldn't persist cookies: {e}")

        # 1. Date Selection via Flatpickr JS Instance
        print(f"[Scraper] Setting publication date to {today_iso} via Flatpickr API...")
        page.evaluate(f"""() => {{
            const fp = document.querySelector("#datePicker");
            if (fp && fp._flatpickr) {{
                fp._flatpickr.setDate("{today_iso}", true);
            }} else if (fp) {{
                fp.value = "{today_iso}";
                fp.dispatchEvent(new Event('input', {{ bubbles: true }}));
                fp.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }}
        }}""")

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

    print(f"[Success] PDF downloaded and saved locally to: '{local_pdf_path}'")

    # 6. Backup to Google Drive
    try:
        from drive_utils import upload_file_to_drive

        print("[System] Uploading scraped PDF to Google Drive Input folder...")
        drive_file_id = upload_file_to_drive(local_pdf_path, INPUT_FOLDER_ID)
        print(f"[Drive Success] File uploaded to Drive successfully! File ID: {drive_file_id}")
    except Exception as e:
        print(f"[Drive Warning] Skipped Drive upload or failed: {e}")

    return local_pdf_path


def scrape_and_upload() -> str:
    return run_scraper()


if __name__ == "__main__":
    run_scraper()