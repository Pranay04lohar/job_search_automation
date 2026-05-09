"""
Naukri job scraper using Playwright.

Uses a real headed/headless Chromium browser so Akamai Bot Manager sees a genuine
browser session — no cookie management or API token gymnastics required.

Output dicts match the shape expected by NaukriNormalizer (naukri_api source key).
"""

import logging
import random
import time
from typing import Any

import config

log = logging.getLogger(__name__)

# Map common city names to Naukri's URL slug format
_CITY_SLUGS: dict[str, str] = {
    "bengaluru": "bengaluru",
    "bangalore": "bengaluru",
    "bengaluru, india": "bengaluru",
    "hyderabad": "hyderabad",
    "pune": "pune",
    "noida": "noida",
    "gurgaon": "gurgaon",
    "gurugram": "gurgaon",
    "mumbai": "mumbai",
    "delhi": "delhi",
    "india": "",          # pan-India — no city slug
    "remote": "",
}

_PERSISTENT_PROFILE = "cookies/_playwright_naukri_profile"


def _city_slug(location: str) -> str:
    return _CITY_SLUGS.get(location.lower().strip(), "")


def _simulate_human(page) -> None:
    """Brief mouse + scroll interaction so Akamai's JS challenge completes."""
    try:
        vp = page.viewport_size or {"width": 1280, "height": 800}
        w, h = vp["width"], vp["height"]
        for _ in range(6):
            page.mouse.move(random.randint(80, w - 80), random.randint(80, h - 80))
            time.sleep(random.uniform(0.1, 0.25))
        for y in [200, 500, 300, 0]:
            page.evaluate(f"window.scrollTo({{top:{y},behavior:'smooth'}})")
            time.sleep(0.3)
    except Exception:
        pass


def _extract_jobs_from_page(page) -> list[dict]:
    """Extract job cards from a rendered Naukri search results page."""
    jobs: list[dict] = []
    try:
        page.wait_for_selector("div.srp-jobtuple-wrapper", timeout=10_000)
    except Exception:
        log.debug("[Naukri] No job cards found on this page.")
        return jobs

    cards = page.query_selector_all("div.srp-jobtuple-wrapper")
    for card in cards:
        try:
            def text(*selectors: str) -> str:
                for sel in selectors:
                    el = card.query_selector(sel)
                    if el:
                        t = el.inner_text().strip()
                        if t:
                            return t
                return ""

            title_el = card.query_selector("a.title")
            title = title_el.inner_text().strip() if title_el else ""
            apply_url = (title_el.get_attribute("href") or "") if title_el else ""
            if apply_url and not apply_url.startswith("http"):
                apply_url = "https://www.naukri.com" + apply_url

            company    = text("a.comp-name", "a.subTitle")
            experience = text("span.expwdth", "li.experience span")
            salary     = text("span.sal",     "li.salary span")
            location   = text("span.locWdth", "li.location span")
            posted     = text("span.job-post-day", "time")
            description = text("div.job-desc", "div.job-description", "div.jd-desc")

            skills_el = card.query_selector_all("ul.tags-gt li span, div.techSkill a")
            skills = [el.inner_text().strip() for el in skills_el if el.inner_text().strip()]

            # Extract job ID from URL
            job_id = ""
            if apply_url:
                for p in reversed(apply_url.rstrip("/").split("/")):
                    if p.isdigit():
                        job_id = p
                        break

            if not title or not company:
                continue

            jobs.append({
                "jobId": job_id,
                "title": title,
                "companyName": company,
                "placeholders": [
                    {"type": "experience", "label": experience},
                    {"type": "salary",     "label": salary},
                    {"type": "location",   "label": location},
                ],
                "tagsAndSkills": ", ".join(skills),
                "jobDescription": description,
                "footerPlaceholderLabel": posted,
                "jdURL": apply_url,
            })
        except Exception as e:
            log.debug(f"[Naukri] Card parse error: {e}")

    return jobs


def scrape_naukri(
    search_terms: list[str],
    location: str = "India",
    hours_old: int = 24,
    results_per_term: int = 20,
) -> list[dict[str, Any]]:
    """
    Scrape Naukri using a real Playwright browser.
    Returns raw dicts compatible with NaukriNormalizer.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        log.error("[Naukri] playwright not installed. Run: pip install playwright && playwright install chromium")
        return []

    try:
        from playwright_stealth import Stealth
        _stealth = Stealth()
    except Exception:
        _stealth = None

    city_slug = _city_slug(location)
    pages_per_term = max(1, results_per_term // 20)  # Naukri shows ~20 per page
    all_jobs: list[dict] = []
    seen_ids: set[str] = set()

    log.info(f"[Naukri] Playwright scraper starting — location='{location}', {len(search_terms)} terms")

    with sync_playwright() as pw:
        # Persistent profile so Akamai builds trust over time
        # Run headed (visible) so Akamai sees a real browser environment.
        # Headless Chromium is fingerprinted and blocked by Akamai regardless of stealth patches.
        context = pw.chromium.launch_persistent_context(
            user_data_dir=_PERSISTENT_PROFILE,
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--start-maximized",
            ],
            ignore_default_args=["--enable-automation"],
            no_viewport=True,
        )
        page = context.new_page()

        if _stealth:
            try:
                _stealth.use_sync(page)
            except Exception:
                pass

        # ── First-run check: ensure Naukri is accessible ──────────────────────
        log.info("[Naukri] Checking Naukri access ...")
        page.goto("https://www.naukri.com/", wait_until="domcontentloaded", timeout=30_000)
        time.sleep(3)
        _simulate_human(page)
        page_title = page.title().lower()
        if "access denied" in page_title or "error" in page_title:
            log.warning(
                "[Naukri] Akamai blocked access. The browser window is open — "
                "please log into naukri.com manually, then press Enter here to continue."
            )
            input("Press Enter after logging into Naukri in the browser window...")
        elif "login" in page_title or "sign in" in page_title:
            log.warning(
                "[Naukri] Naukri login page detected. "
                "Please log in manually in the browser window, then press Enter."
            )
            input("Press Enter after logging into Naukri in the browser window...")
        else:
            log.info(f"[Naukri] Access OK (title: {page.title()[:60]})")

        for term in search_terms:
            term_jobs: list[dict] = []
            term_slug = term.lower().replace(" ", "-")

            for pg in range(1, pages_per_term + 1):
                # Build Naukri search URL (SEO-friendly format)
                if city_slug:
                    url = (
                        f"https://www.naukri.com/{term_slug}-jobs-in-{city_slug}"
                        f"?experience=0&experience=1&experience=2&experience=3"
                        f"&jobAge={hours_old}&pg={pg}"
                    )
                else:
                    url = (
                        f"https://www.naukri.com/{term_slug}-jobs"
                        f"?experience=0&experience=1&experience=2&experience=3"
                        f"&jobAge={hours_old}&pg={pg}"
                    )

                try:
                    log.debug(f"[Naukri] GET {url}")
                    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    time.sleep(random.uniform(1.5, 2.5))
                    _simulate_human(page)

                    page_jobs = _extract_jobs_from_page(page)
                    if not page_jobs:
                        log.debug(f"[Naukri] '{term}' page {pg}: no results — stopping pagination")
                        break

                    new = 0
                    for j in page_jobs:
                        jid = str(j.get("jobId") or "")
                        key = jid if jid else f"{j.get('title','')}:{j.get('companyName','')}"
                        if key in seen_ids:
                            continue
                        seen_ids.add(key)
                        term_jobs.append(j)
                        new += 1

                    log.debug(f"[Naukri] '{term}' page {pg}: {new} new jobs")
                    time.sleep(random.uniform(2.0, 3.5))

                except PWTimeout:
                    log.warning(f"[Naukri] Timeout on '{term}' page {pg} — skipping")
                    break
                except Exception as e:
                    log.error(f"[Naukri] Error on '{term}' page {pg}: {e}")
                    break

            log.info(f"[Naukri] '{term}': {len(term_jobs)} jobs")
            all_jobs.extend(term_jobs)
            time.sleep(random.uniform(2.0, 4.0))

        context.close()

    log.info(f"[Naukri] Total: {len(all_jobs)} raw jobs")
    return all_jobs
