import datetime
import json
import os
import random
import tempfile
import time
from playwright.sync_api import sync_playwright

# Correct import for playwright-stealth
import playwright_stealth

TEMP_DIR = "./temp_downloads"
COOKIE_FILE = "./temp_downloads/cloudflare_cookies.json"
INPUT_FOLDER_ID = "1fFMtXqlTyP7gZtJ0L39l1IcJE0Wh5vRf"


def apply_stealth(page):
    """Applies stealth evasion to Playwright page safely."""
    try:
        if hasattr(playwright_stealth, "stealth_sync"):
            playwright_stealth.stealth_sync(page)
        elif hasattr(playwright_stealth, "stealth_page_sync"):
            playwright_stealth.stealth_page_sync(page)
        else:
            # Native JS evasion fallback if stealth wrapper method differs
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
            """)
    except Exception as e:
        print(f"[Scraper Warning] Stealth hook fallback applied: {e}")


def auto_solve_turnstile(page):
    """Detects Cloudflare Turnstile iframe and clicks checkbox if needed."""
    print("[Scraper] Checking for Cloudflare Turnstile iframe...")

    start_time = time.time()
    while time.time() - start_time < 25:
        if page.locator("#datePicker").is_visible():
            print("[Scraper] Access verified! Main controls visible.")
            return True

        for frame in page.frames:
            if "cloudflare" in frame.url or "challenges" in frame.url or "turnstile" in frame.url:
                try:
                    box_locator = frame.locator("input[type='checkbox'], #challenge-stage, .ctp-checkbox-label").first
                    if box_locator.is_visible(timeout=2000):
                        box = box_locator.bounding_box()
                        if box:
                            print("[Scraper] Turnstile checkbox located. Executing human-like click...")
                            target_x = box["x"] + random.uniform(8, 18)
                            target_y = box["y"] + random.uniform(8, 18)

                            page.mouse.move(target_x, target_y, steps=10)
                            page.wait_for_timeout(random.randint(300, 600))
                            page.mouse.click(target_x, target_y)

                            print("[Scraper] Clicked Turnstile checkbox. Waiting for verification...")
                            page.wait_for_timeout(4000)
                            return True
                except Exception:
                    pass

        page.wait_for_timeout(1000)

    return False


def run_scraper() -> str:
    """Scrapes the newspaper PDF with stealth evasion."""
    today = datetime.datetime.now()
    today_iso = today.strftime("%Y-%m-%d")

    os.makedirs(TEMP_DIR, exist_ok=True)
    local_pdf_path = os.path.abspath(
        os.path.join(TEMP_DIR, f"Loksatta_{today_iso}.pdf")
    )

    print(f"[Scraper] Starting automated scrape process for date: {today_iso}")

    user_data_dir = os.path.join(tempfile.gettempdir(), "clean_chrome_stealth_profile")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            channel="chrome",
            headless=False,
            ignore_default_args=["--enable-automation"],
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--start-maximized",
            ],
            viewport=None,
            accept_downloads=True,
        )

        page = context.pages[0] if context.pages else context.new_page()

        # Apply stealth rules safely
        apply_stealth(page)

        print("[Scraper] Navigating to target portal...")
        page.goto(
            "https://www.tradingref.com/",
            timeout=60000,
            wait_until="domcontentloaded",
        )

        auto_solve_turnstile(page)

        print("[Scraper] Waiting for main form controls...")
        try:
            page.wait_for_selector("#datePicker", state="visible", timeout=30000)
        except Exception:
            print("[Scraper] Secondary verification attempt...")
            auto_solve_turnstile(page)
            page.wait_for_selector("#datePicker", state="visible", timeout=30000)

        print("[Scraper] Access confirmed! Persisting fresh clearance cookies...")
        try:
            current_cookies = context.cookies()
            with open(COOKIE_FILE, "w") as f:
                json.dump(current_cookies, f)
        except Exception as e:
            print(f"[Scraper] Warning - couldn't save cookies: {e}")

        # 1. Date Selection via Flatpickr
        print(f"[Scraper] Setting date to {today_iso}...")
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
        lang_select.wait_for(state="visible", timeout=30000)
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

    # 6. Drive Backup
    try:
        from drive_utils import upload_file_to_drive

        print("[System] Uploading PDF to Google Drive...")
        drive_file_id = upload_file_to_drive(local_pdf_path, INPUT_FOLDER_ID)
        print(f"[Drive Success] Uploaded! File ID: {drive_file_id}")
    except Exception as e:
        print(f"[Drive Warning] Drive upload skipped or failed: {e}")

    return local_pdf_path


def scrape_and_upload() -> str:
    return run_scraper()


if __name__ == "__main__":
    run_scraper()