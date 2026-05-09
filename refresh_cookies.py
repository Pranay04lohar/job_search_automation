"""
refresh_cookies.py — Refresh short-lived bot-protection cookies for Wellfound.

Run this ONCE before main.py each day (or whenever Wellfound returns 403):

    .\.venv\Scripts\python.exe .\refresh_cookies.py

What it does:
  1. Opens a VISIBLE Chrome window so DataDome sees a real browser.
  2. Injects existing Wellfound session cookies (so you stay logged in).
  3. Browses wellfound.com briefly — this refreshes the DataDome token.
  4. Saves updated cookies back to cookies/wellfound_cookies.txt.

Note: Naukri is now scraped directly via Playwright (scrapers/naukri.py) —
no separate cookie refresh needed for Naukri.
"""

import json
import time
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("refresh_cookies")

COOKIES_DIR = Path("cookies")

# ── Helpers ────────────────────────────────────────────────────────────────────

def _read_existing_cookies(path: Path) -> list[dict]:
    """Load existing cookies so we can inject them and stay logged in."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        return []

    # JSON list-of-objects
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
    except Exception:
        pass

    # Netscape format → convert to list of dicts for Playwright
    cookies = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            parts = line.split()
        if len(parts) < 7:
            continue
        domain, flag, path_val, secure, expiry, name, value = (
            parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], parts[6]
        )
        try:
            expiry_int = int(float(expiry))
        except ValueError:
            expiry_int = 0
        cookies.append({
            "name": name,
            "value": value,
            "domain": domain.lstrip(".") if not domain.startswith(".") else domain,
            "path": path_val,
            "expires": expiry_int,
            "httpOnly": False,
            "secure": secure.upper() == "TRUE",
            "sameSite": "None",
        })
    return cookies


def _save_cookies_netscape(cookies: list, path: Path, domain_filter: str = "") -> None:
    """Save Playwright cookie list back to Netscape format.
    If domain_filter is set, only save cookies whose domain contains that string.
    """
    lines = [
        "# Netscape HTTP Cookie File",
        "# https://curl.haxx.se/rfc/cookie_spec.html",
        "# Refreshed by refresh_cookies.py",
    ]
    saved = 0
    for c in cookies:
        domain = c.get("domain", "")
        if domain_filter and domain_filter not in domain:
            continue
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        path_val = c.get("path", "/")
        secure = "TRUE" if c.get("secure") else "FALSE"
        expiry = str(int(c.get("expires") or 0))
        name = c.get("name", "")
        value = c.get("value", "")
        if not name:
            continue
        lines.append(f"{domain}\t{flag}\t{path_val}\t{secure}\t{expiry}\t{name}\t{value}")
        saved += 1
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info(f"Saved {saved} cookies (domain={domain_filter or 'all'}) → {path}")


# ── Per-site refreshers ────────────────────────────────────────────────────────

def _refresh_wellfound(page, context) -> None:
    """Navigate Wellfound so DataDome refreshes its session cookie."""
    log.info("[Wellfound] Navigating to wellfound.com ...")
    page.goto("https://wellfound.com/jobs", wait_until="networkidle", timeout=45_000)
    time.sleep(4)

    cookies = context.cookies()
    has_datadome = any(c["name"] == "datadome" for c in cookies)
    if has_datadome:
        log.info("[Wellfound] datadome cookie present ✓")
    else:
        log.warning("[Wellfound] datadome cookie NOT found — DataDome challenge may not have resolved.")

    _save_cookies_netscape(cookies, COOKIES_DIR / "wellfound_cookies.txt", domain_filter="wellfound.com")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    from playwright.sync_api import sync_playwright

    COOKIES_DIR.mkdir(exist_ok=True)

    with sync_playwright() as pw:
        # Use a PERSISTENT context so Chrome remembers its own fingerprint across runs.
        # This makes Akamai/DataDome trust the browser more over time.
        user_data_dir = str(Path("cookies") / "_playwright_profile")
        log.info("Opening Chrome (headed) — do NOT close the window until the script finishes.")

        context = pw.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,               # Must be visible for Akamai/DataDome to pass
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",  # Hide webdriver flag
            ],
            no_viewport=True,
            ignore_default_args=["--enable-automation"],          # Remove automation banner
        )
        page = context.new_page()

        # Apply stealth patches to hide Playwright fingerprint from Akamai/DataDome
        # playwright-stealth v2 API uses Stealth().use_sync(page)
        try:
            from playwright_stealth import Stealth
            stealth = Stealth()
            stealth.use_sync(page)
            log.info("Stealth mode applied ✓")
        except Exception as e:
            log.warning(f"playwright-stealth could not be applied ({e}) — Akamai may still detect automation.")

        # ── Wellfound ─────────────────────────────────────────────────────────
        wellfound_cookie_path = COOKIES_DIR / "wellfound_cookies.txt"
        existing_wellfound = _read_existing_cookies(wellfound_cookie_path)
        if existing_wellfound:
            log.info(f"[Wellfound] Injecting {len(existing_wellfound)} existing cookies ...")
            try:
                context.add_cookies(existing_wellfound)
            except Exception as e:
                log.warning(f"[Wellfound] Could not inject some cookies: {e}")

        _refresh_wellfound(page, context)

        context.close()

    log.info("Cookie refresh complete. You can now run main.py.")


if __name__ == "__main__":
    main()
