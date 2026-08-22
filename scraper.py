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

# List of newspapers to scrape dynamically
PAPERS_CONFIG = [
    {
        "name": "Loksatta",
        "language": "marathi",
        "newspaper": "Loksatta",
        "edition": "Pune",
        "filename_prefix": "Loksatta",
    },
    {
        "name": "Lokmat",
        "language": "marathi",
        "newspaper": "Lokmat",
        "edition": "Pune main",
        "filename_prefix": "Lokmat",
    },
]


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
            if (
                "cloudflare" in frame.url
                or "challenges" in frame.url
                or "turnstile" in frame.url
            ):
                try:
                    box_locator = frame.locator(
                        "input[type='checkbox'], #challenge-stage, .ctp-checkbox-label"
                    ).first
                    if box_locator.is_visible(timeout=2000):
                        box = box_locator.bounding_box()
                        if box:
                            print(
                                "[Scraper] Turnstile checkbox located. Executing human-like click..."
                            )
                            target_x = box["x"] + random.uniform(8, 18)
                            target_y = box["y"] + random.uniform(8, 18)

                            page.mouse.move(target_x, target_y, steps=10)
                            page.wait_for_timeout(random.randint(300, 600))
                            page.mouse.click(target_x, target_y)

                            print(
                                "[Scraper] Clicked Turnstile checkbox. Waiting for verification..."
                            )
                            page.wait_for_timeout(4000)
                            return True
                except Exception:
                    pass

        page.wait_for_timeout(1000)

    return False


def dump_debug_state(page, tag: str):
    """
    Saves a screenshot + HTML snapshot of the current page so that a failed
    Cloudflare verification can be diagnosed after the fact (e.g. from a CI
    debug artifact) instead of guessing from a bare timeout message.
    """
    os.makedirs(TEMP_DIR, exist_ok=True)
    base = os.path.join(TEMP_DIR, f"debug_{tag}")
    try:
        page.screenshot(path=f"{base}.png", full_page=True)
    except Exception as e:
        print(f"[Scraper Debug] Could not save screenshot: {e}")
    try:
        with open(f"{base}.html", "w", encoding="utf-8") as f:
            f.write(page.content())
    except Exception as e:
        print(f"[Scraper Debug] Could not save HTML snapshot: {e}")
    try:
        frame_urls = [frame.url for frame in page.frames]
        with open(f"{base}_frames.json", "w", encoding="utf-8") as f:
            json.dump({"page_url": page.url, "frames": frame_urls}, f, indent=2)
    except Exception as e:
        print(f"[Scraper Debug] Could not save frame list: {e}")
    print(f"[Scraper Debug] Saved diagnostic snapshot: {base}.png / .html / _frames.json")


def select_dropdown_option(select_locator, target_text: str):
    """
    Robustly selects an option from a dropdown locator matching either value,
    label, or case-insensitive text.
    """
    select_locator.wait_for(state="visible", timeout=15000)

    # 1. Try exact label match
    try:
        select_locator.select_option(label=target_text)
        return
    except Exception:
        pass

    # 2. Try exact value match
    try:
        select_locator.select_option(value=target_text)
        return
    except Exception:
        pass

    # 3. Fallback: Search all options case-insensitively
    options = select_locator.locator("option").all()
    for opt in options:
        opt_val = opt.get_attribute("value") or ""
        opt_text = opt.text_content() or ""
        if (
            target_text.lower() in opt_val.lower()
            or target_text.lower() in opt_text.lower()
        ):
            select_locator.select_option(value=opt_val)
            return

    raise ValueError(f"Could not find matching option for '{target_text}' in dropdown.")


def download_paper(page, paper_cfg: dict, today_iso: str) -> str:
    """Fills out form controls and downloads a single newspaper PDF."""
    paper_name = paper_cfg["name"]
    print(f"\n[Scraper] --- Processing: {paper_name} ---")

    local_pdf_path = os.path.abspath(
        os.path.join(TEMP_DIR, f"{paper_cfg['filename_prefix']}_{today_iso}.pdf")
    )

    # 1. Date Selection via Flatpickr
    print(f"[{paper_name}] Setting date to {today_iso}...")
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
    page.wait_for_timeout(1000)

    # 2. Select Language
    print(f"[{paper_name}] Selecting language: {paper_cfg['language']}...")
    lang_select = page.get_by_label("Language")
    select_dropdown_option(lang_select, paper_cfg["language"])
    page.wait_for_timeout(1000)

    # 3. Select Newspaper
    print(f"[{paper_name}] Selecting newspaper: {paper_cfg['newspaper']}...")
    paper_select = page.get_by_label("Newspaper", exact=True)
    select_dropdown_option(paper_select, paper_cfg["newspaper"])
    page.wait_for_timeout(1200)

    # 4. Select Edition
    print(f"[{paper_name}] Selecting edition: {paper_cfg['edition']}...")
    edition_select = page.get_by_label("Edition")
    select_dropdown_option(edition_select, paper_cfg["edition"])
    page.wait_for_timeout(1000)

    # 5. Generate & Download PDF
    print(f"[{paper_name}] Triggering PDF Generation & Download...")
    with page.expect_download(timeout=90000) as download_info:
        page.get_by_role("button", name=" Generate & Download PDF").click()

    download = download_info.value
    download.save_as(local_pdf_path)
    print(f"[{paper_name} Success] Downloaded to: '{local_pdf_path}'")

    return local_pdf_path


def download_paper_with_retry(
    page, paper_cfg: dict, today_iso: str, max_attempts: int = 3
) -> str:
    """
    Wraps download_paper() with a reload-and-retry loop. The site's own ad
    slots (Google Ads iframes, a '.disabled-overlay' div, etc.) sometimes
    render on top of the "Generate & Download PDF" button after a paper is
    selected, blocking the click for the full 30s timeout - refreshing the
    page and re-filling the form clears that overlay far more reliably than
    just retrying the click in place.
    """
    paper_name = paper_cfg["name"]
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            return download_paper(page, paper_cfg, today_iso)
        except Exception as e:
            last_error = e
            print(f"[{paper_name}] Attempt {attempt}/{max_attempts} failed: {e}")
            dump_debug_state(page, f"{paper_name.lower()}_download_attempt_{attempt}")

            if attempt < max_attempts:
                print(f"[{paper_name}] Reloading page before retrying...")
                page.goto(
                    "https://www.tradingref.com/",
                    timeout=60000,
                    wait_until="domcontentloaded",
                )
                try:
                    page.wait_for_selector("#datePicker", state="visible", timeout=30000)
                except Exception:
                    # Cloudflare occasionally re-challenges on reload too.
                    auto_solve_turnstile(page)
                    page.wait_for_selector("#datePicker", state="visible", timeout=30000)
                page.wait_for_timeout(1500)

    raise RuntimeError(
        f"Failed to download {paper_name} after {max_attempts} attempts: {last_error}"
    )


def run_scraper() -> list[str]:
    """Scrapes all configured newspaper PDFs with stealth evasion."""
    today = datetime.datetime.now()
    today_iso = today.strftime("%Y-%m-%d")

    os.makedirs(TEMP_DIR, exist_ok=True)
    downloaded_files = []

    print(f"[Scraper] Starting automated scrape run for date: {today_iso}")

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

        # Cloudflare Turnstile is adversarial and can fail intermittently on
        # unattended/CI runs (cloud IPs are scrutinized harder than a home IP).
        # Retry the full navigate -> solve -> verify cycle a few times before
        # giving up, instead of failing the whole run on one bad attempt.
        MAX_VERIFY_ATTEMPTS = 3
        verified = False
        last_error = None

        for attempt in range(1, MAX_VERIFY_ATTEMPTS + 1):
            try:
                print(
                    f"[Scraper] Navigating to target portal "
                    f"(attempt {attempt}/{MAX_VERIFY_ATTEMPTS})..."
                )
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

                verified = True
                break
            except Exception as e:
                last_error = e
                print(f"[Scraper] Verification attempt {attempt} failed: {e}")
                dump_debug_state(page, f"verify_attempt_{attempt}")
                if attempt < MAX_VERIFY_ATTEMPTS:
                    wait_s = 5 * attempt
                    print(f"[Scraper] Retrying in {wait_s}s with a fresh page load...")
                    page.wait_for_timeout(wait_s * 1000)

        if not verified:
            context.close()
            raise RuntimeError(
                "Cloudflare verification failed after "
                f"{MAX_VERIFY_ATTEMPTS} attempts: {last_error}"
            )

        print("[Scraper] Access confirmed! Persisting fresh clearance cookies...")
        try:
            current_cookies = context.cookies()
            with open(COOKIE_FILE, "w") as f:
                json.dump(current_cookies, f)
        except Exception as e:
            print(f"[Scraper] Warning - couldn't save cookies: {e}")

        # Iterate through newspapers
        for paper_cfg in PAPERS_CONFIG:
            try:
                pdf_path = download_paper_with_retry(page, paper_cfg, today_iso)
                downloaded_files.append(pdf_path)
            except Exception as e:
                print(f"[Scraper Error] Failed to download {paper_cfg['name']}: {e}")

            # Brief pause between downloads to let site clear
            page.wait_for_timeout(2000)

        context.close()

    # Google Drive Backup
    if downloaded_files:
        try:
            from drive_utils import upload_file_to_drive

            print("\n[System] Uploading downloaded PDFs to Google Drive...")
            for file_path in downloaded_files:
                try:
                    drive_file_id = upload_file_to_drive(file_path, INPUT_FOLDER_ID)
                    print(
                        f"[Drive Success] Uploaded '{os.path.basename(file_path)}'! File ID: {drive_file_id}"
                    )
                except Exception as e:
                    print(f"[Drive Warning] Failed uploading {file_path}: {e}")
        except Exception as e:
            print(f"[Drive Warning] Drive module skipped or unavailable: {e}")

    return downloaded_files


def scrape_and_upload() -> list[str]:
    return run_scraper()


if __name__ == "__main__":
    run_scraper()