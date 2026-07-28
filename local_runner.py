import os
import sys
from scraper import run_scraper

def main():
    print("=== Starting Local Daily Scraper ===")
    try:
        # 1. Runs Playwright with persistent cookies on your home/office IP
        pdf_path = run_scraper()
        
        if os.path.exists(pdf_path):
            print(f"[Success] Daily PDF ready: {pdf_path}")
            print("[System] Upload complete. Ready for OCR processing!")
        else:
            print("[Error] PDF download failed.")
            sys.exit(1)
            
    except Exception as e:
        print(f"[Fatal Error] Local runner failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()