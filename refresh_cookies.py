"""
refresh_cookies.py — No longer needed for regular pipeline runs.

Wellfound is now scraped directly via Playwright in scrapers/wellfound.py,
which uses the persistent Chrome profile and handles DataDome within its own
browser session — no separate cookie refresh step required.

Naukri is also scraped directly via Playwright in scrapers/naukri.py.

This script is kept as a manual utility in case you need to pre-warm the
Wellfound session (e.g. after a long gap or if the scraper hits DataDome).

Usage (manual only):
    .venv\\Scripts\\python.exe refresh_cookies.py
"""

import logging
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("refresh_cookies")

_PERSISTENT_PROFILE = str(Path("cookies") / "_playwright_profile")


def main() -> None:
    """Open a headed Chrome window to pre-warm the Wellfound DataDome session."""
    from playwright.sync_api import sync_playwright

    log.info("Opening Chrome to warm up the Wellfound DataDome session ...")
    log.info("This is only needed if scrapers/wellfound.py is being blocked.")

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=_PERSISTENT_PROFILE,
            headless=False,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
            ],
            no_viewport=True,
            ignore_default_args=["--enable-automation"],
        )
        page = context.new_page()

        try:
            from playwright_stealth import Stealth
            Stealth().use_sync(page)
            log.info("Stealth mode applied ✓")
        except Exception as e:
            log.warning(f"playwright-stealth could not be applied: {e}")

        log.info("Navigating to wellfound.com/jobs ...")
        page.goto(
            "https://wellfound.com/jobs",
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        time.sleep(6)

        title = page.title()
        log.info(f"Page title: {title}")
        if "access denied" in title.lower() or "blocked" in title.lower():
            log.warning(
                "Access blocked — DataDome challenge not resolved. "
                "Try browsing manually in the opened window, then close it."
            )
            input("Press Enter when done browsing manually...")
        else:
            log.info("Session warm-up complete ✓ — wellfound.com loaded successfully.")

        context.close()

    log.info("Done. The persistent profile now has an active Wellfound session.")


if __name__ == "__main__":
    main()
