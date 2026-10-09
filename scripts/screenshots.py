"""Capture one screenshot per dashboard page (dashboard must be running on :8501).

Usage: python scripts/screenshots.py docs/screenshots
Set CHROMIUM_PATH to use an existing Chromium instead of the one installed by `playwright install`.
"""

import os
import sys
import time

from playwright.sync_api import sync_playwright

pages = [
    ("", "01-overview"),
    ("compare", "02-price-comparison"),
    ("history", "03-price-history"),
    ("competitiveness", "04-competitiveness"),
    ("promotions", "05-promotions"),
    ("trends", "06-trends-price-index"),
    ("basket", "07-cheapest-basket"),
    ("review", "08-match-review"),
    ("quality", "09-data-quality"),
]
out = sys.argv[1]
with sync_playwright() as p:
    b = p.chromium.launch(
        executable_path=os.environ.get("CHROMIUM_PATH") or None, args=["--no-sandbox"]
    )
    ctx = b.new_context(viewport={"width": 1500, "height": 950}, device_scale_factor=1)
    page = ctx.new_page()
    for path, name in pages:
        page.goto(f"http://localhost:8501/{path}", wait_until="networkidle")
        page.wait_for_selector("h1", timeout=30000)
        time.sleep(3)
        errs = page.locator('[data-testid="stException"]').count()
        print(path, "exceptions:", errs)
        if errs:
            print(page.locator('[data-testid="stException"]').first.inner_text()[:600])
        page.screenshot(path=f"{out}/{name}.png", full_page=True)
    b.close()
