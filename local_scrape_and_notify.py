"""
Run this LOCALLY (Windows Task Scheduler, cron, etc.) - it's the one piece
of the pipeline that still needs to run somewhere with a normal residential
IP, since that's what lets it pass tradingref.com's Cloudflare check.

What it does:
  1. Runs the scraper (downloads PDFs, uploads them to Drive - both already
     handled inside scraper.run_scraper()).
  2. On success, fires a GitHub `repository_dispatch` event so the CI
     workflow (run_process_only_cli.py on GitHub Actions) starts
     immediately instead of waiting for its scheduled/fallback cron time.

Requires two local-only env vars (put them in a local .env, NOT committed):
  GITHUB_PAT   - a GitHub Personal Access Token with 'repo' scope (classic)
                 or 'Contents: read/write' + 'Actions: read/write' (fine-grained)
  GITHUB_REPO  - "owner/repo", e.g. "vedantpandhare-sketch/Newspaper_OCR_engine"

If those aren't set, the script still scrapes+uploads fine - it just skips
the instant-trigger step, and the CI workflow's own scheduled fallback run
(with its built-in Drive polling) will pick the files up a bit later.
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()


def notify_github(event_type: str = "newspaper-scraped") -> bool:
    token = os.environ.get("GITHUB_PAT")
    repo = os.environ.get("GITHUB_REPO")

    if not token or not repo:
        print("[Notify] GITHUB_PAT/GITHUB_REPO not set - skipping instant CI trigger. "
              "The CI workflow's scheduled fallback + Drive polling will still pick this up.")
        return False

    import requests

    url = f"https://api.github.com/repos/{repo}/dispatches"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
    }
    resp = requests.post(url, headers=headers, json={"event_type": event_type}, timeout=15)

    if resp.status_code == 204:
        print(f"[Notify] Triggered GitHub Actions via repository_dispatch ('{event_type}').")
        return True

    print(f"[Notify] Failed to trigger CI (status {resp.status_code}): {resp.text}")
    return False


def main():
    print("=== Local Scrape + Notify ===")
    from scraper import run_scraper

    try:
        pdf_paths = run_scraper()
    except Exception as e:
        print(f"[Fatal Error] Scraper failed: {e}")
        sys.exit(1)

    if not pdf_paths:
        print("[Error] No PDFs downloaded - not notifying CI.")
        sys.exit(1)

    print(f"[Success] {len(pdf_paths)} PDF(s) downloaded and uploaded to Drive.")
    notify_github()


if __name__ == "__main__":
    main()
