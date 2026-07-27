from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)

    page = browser.new_page()

    try:
        page.goto(
            "https://www.tradingref.com",
            wait_until="domcontentloaded",
            timeout=120000
        )
        page.wait_for_timeout(3000)

        print("SUCCESS")
        print(page.title())
        print(page.url)

        page.screenshot(path="test.png")

    except Exception as e:
        print(e)

    input("Press Enter...")
    browser.close()