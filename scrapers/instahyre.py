"""Instahyre job scraper using httpx with session cookies."""

import json
import logging
import random
import time
from pathlib import Path
from typing import Any

import config
from scrapers.cookie_loader import load_cookies_first_existing

log = logging.getLogger(__name__)

# v1 endpoint returns 404 as of May 2026 — try v2 variant
INSTAHYRE_SEARCH_URL = (
    "https://www.instahyre.com/api/v2/opportunity/?format=json&q={term}&page={page}"
)
_INSTAHYRE_FALLBACK_URL = (
    "https://www.instahyre.com/api/v1/opportunity/?format=json&q={term}&page={page}"
)
MAX_PAGES = 3

STEALTH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://www.instahyre.com/",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "DNT": "1",
    "X-Requested-With": "XMLHttpRequest",
}


def _load_cookies() -> dict[str, str]:
    """Load cookies from cookies/instahyre_cookies.(json|txt)."""
    cookie_paths = [
        Path(config.COOKIES_DIR) / "instahyre_cookies.json",
        Path(config.COOKIES_DIR) / "instahyre_cookies.txt",
        Path(config.COOKIES_DIR) / "instahyre_cookies.cookies",
    ]
    cookies = load_cookies_first_existing(cookie_paths)
    if not cookies:
        cookie_path = cookie_paths[0]
        log.warning(
            f"[Instahyre] Cookie file not found at {cookie_path}. "
            "Export cookies and save to cookies/instahyre_cookies.json (or .txt for Netscape format)."
        )
        return {}
    return cookies


def scrape_instahyre(search_terms: list[str]) -> list[dict[str, Any]]:
    """
    Scrape Instahyre jobs for each search term using session cookies.

    Paginates up to MAX_PAGES per term.
    Returns [] gracefully if cookies are missing or auth fails.
    """
    try:
        import httpx  # type: ignore[import]
    except ImportError:
        log.error("[Instahyre] 'httpx' not installed. Run: pip install httpx")
        return []

    cookies = _load_cookies()
    if not cookies:
        return []

    all_jobs: list[dict] = []
    seen_ids: set[str] = set()

    # Detect working base URL on first request (v2 preferred, v1 fallback)
    active_url_template: str = INSTAHYRE_SEARCH_URL

    with httpx.Client(
        headers=STEALTH_HEADERS,
        cookies=cookies,
        timeout=20,
        follow_redirects=True,
    ) as client:
        for term in search_terms:
            try:
                for page in range(1, MAX_PAGES + 1):
                    url = active_url_template.format(term=term, page=page)
                    response = client.get(url)

                    # v2 returned 404 — try v1 once, abort entire scraper if that also fails
                    if response.status_code == 404:
                        if active_url_template == INSTAHYRE_SEARCH_URL:
                            fb = _INSTAHYRE_FALLBACK_URL.format(term=term, page=page)
                            log.debug("[Instahyre] v2 404 — probing v1 fallback...")
                            r2 = client.get(fb)
                            if r2.status_code == 200:
                                active_url_template = _INSTAHYRE_FALLBACK_URL
                                log.info("[Instahyre] v1 endpoint works — switching.")
                                response = r2
                            else:
                                log.warning(
                                    "[Instahyre] Both v1 and v2 return 404. "
                                    "API endpoint has changed — disabling for this run."
                                )
                                return all_jobs
                        else:
                            log.warning("[Instahyre] 404 — aborting.")
                            return all_jobs

                    if response.status_code in (401, 403):
                        log.warning(
                            f"[Instahyre] Auth failed (HTTP {response.status_code}) for '{term}'. "
                            "Cookie may be expired."
                        )
                        break

                    if response.status_code == 429:
                        sleep_time = random.uniform(60, 120)
                        log.warning(
                            f"[Instahyre] Rate limited. Sleeping {sleep_time:.0f}s, retrying..."
                        )
                        time.sleep(sleep_time)
                        response = client.get(url)
                        if response.status_code == 429:
                            log.error(f"[Instahyre] Still rate-limited for '{term}'")
                            break

                    if response.status_code != 200:
                        log.warning(
                            f"[Instahyre] HTTP {response.status_code} for '{term}' page {page}"
                        )
                        break

                    data = response.json()

                    if isinstance(data, dict):
                        jobs_on_page = (
                            data.get("results")
                            or data.get("opportunities")
                            or data.get("data")
                            or []
                        )
                        next_page = data.get("next")
                    elif isinstance(data, list):
                        jobs_on_page = data
                        next_page = None
                    else:
                        jobs_on_page = []
                        next_page = None

                    if not jobs_on_page:
                        log.debug(f"[Instahyre] No more results for '{term}' at page {page}")
                        break

                    new_count = 0
                    for job in jobs_on_page:
                        job_id = str(
                            job.get("id")
                            or job.get("opportunity_id")
                            or ""
                        )
                        if job_id and job_id in seen_ids:
                            continue
                        if job_id:
                            seen_ids.add(job_id)
                        all_jobs.append(job)
                        new_count += 1

                    log.debug(f"[Instahyre] '{term}' page {page}: {new_count} jobs")

                    if not next_page:
                        break

                    time.sleep(random.uniform(2.0, 4.0))

                log.info(f"[Instahyre] '{term}': done")

            except Exception as e:
                log.error(f"[Instahyre] Failed for '{term}': {type(e).__name__}: {e}")

            time.sleep(random.uniform(5.0, 10.0))

    log.info(f"[Instahyre] Total raw jobs: {len(all_jobs)}")
    return all_jobs
