import os
import img2pdf
import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://epaper.loksatta.com/",
}


def download_loksatta_hd_pdf(
    issue_id: str = "4190022", date_str: str = "2026-08-21"
) -> str:
    manifest_url = f"https://epaper.loksatta.com/pagemeta/get/{issue_id}/1-50"
    print(f"Fetching issue manifest: {manifest_url}")

    res = requests.get(manifest_url, headers=HEADERS, timeout=15)
    if res.status_code != 200:
        raise ConnectionError(
            f"Failed to fetch manifest. Status: {res.status_code}"
        )

    data = res.json()
    pages_dict = data.get("pages", data)

    temp_dir = f"temp_images_{issue_id}"
    os.makedirs(temp_dir, exist_ok=True)
    image_paths = []

    for page_key, page_info in pages_dict.items():
        if not isinstance(page_info, dict) or "levels" not in page_info:
            continue

        levels = page_info["levels"]

        # 1. Find the highest available resolution level (prefer highest keys)
        selected_level = None
        selected_level_data = None

        # Check keys in order of preference (highest resolution down to lowest)
        for level_key in [
            "level3",
            "level2",
            "level1",
            "3",
            "2",
            "1",
            "level0",
            "thumbs",
        ]:
            if level_key in levels and "chunks" in levels[level_key]:
                selected_level = level_key
                selected_level_data = levels[level_key]
                break

        if not selected_level_data:
            continue

        base_chunk_url = selected_level_data["chunks"][0]["url"]
        if base_chunk_url.startswith("//"):
            base_chunk_url = "https:" + base_chunk_url

        # 2. Get width & height for this specific level to build the exact URL path
        width = selected_level_data.get("width")
        height = selected_level_data.get("height")

        # Swap the thumbnail string (150x241) with this level's exact dimensions
        if width and height:
            target_dim = f"{width}x{height}-{width}x{height}"
            # Replace whatever low-res dim string exists in the URL
            img_url = base_chunk_url.replace("150x241-150x241", target_dim)
            img_url = img_url.replace("300x482-300x482", target_dim)
        else:
            img_url = base_chunk_url

        # 3. Download image
        img_res = requests.get(img_url, headers=HEADERS, timeout=15)

        if (
            img_res.status_code != 200
            or "image" not in img_res.headers.get("Content-Type", "").lower()
        ):
            # Fallback to base chunk URL if replaced dimension URL failed
            img_res = requests.get(base_chunk_url, headers=HEADERS, timeout=15)

        page_num = page_info.get("pagenum", page_key)
        img_path = os.path.join(temp_dir, f"page_{int(page_num):03d}.jpg")

        with open(img_path, "wb") as f:
            f.write(img_res.content)

        image_paths.append(img_path)
        print(
            f"✓ Page {page_num}: Loaded level '{selected_level}' ({len(img_res.content)} bytes)"
        )

    if not image_paths:
        raise ValueError("No images were downloaded.")

    image_paths.sort()

    output_pdf = f"temp_downloads/Loksatta_{date_str}.pdf"
    os.makedirs("temp_downloads", exist_ok=True)

    with open(output_pdf, "wb") as f:
        f.write(img2pdf.convert(image_paths))

    print(f"\n Saved High-Res PDF: {output_pdf}")
    return output_pdf


if __name__ == "__main__":
    download_loksatta_hd_pdf(issue_id="4190022", date_str="2026-08-21")