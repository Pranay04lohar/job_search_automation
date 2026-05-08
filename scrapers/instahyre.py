"""Instahyre job scraper using httpx with session cookies."""

import json
import logging
import random
import time
from pathlib import Path
from typing import Any

import config

log = logging.getLogger(__name__)

INSTAHYRE_SEARCH_URL = (
    "https://www.instahyre.com/api/v1/opportunity/?format=json&q={term}&page={page}"
)
MAX_PAGES = 3

STEALTH_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://www.instahyre.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "DNT": "1",
}


def _load_cookies() -> dict[str, str]:
    """Load cookies from cookies/instahyre_cookies.json."""
    cookie_path = Path(config.COOKIES_DIR) / "instahyre_cookies.json"
    if not cookie_path.exists():
        log.warning(
            f"[Instahyre] Cookie file not found at {cookie_path}. "
            "Export cookies manually to enable Instahyre scraping."
        )
        return {}
    try:
        with open(cookie_path) as f:
            data = json.load(f)
        if isinstance(data, list):
            return {c["name"]: c["value"] for c in data if "name" in c}
        if isinstance(data, dict):
            return data
    except Exception as e:
        log.warning(f"[Instahyre] Failed to load cookies: {e}")
    return {}


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

    with httpx.Client(
        headers=STEALTH_HEADERS,
        cookies=cookies,
        timeout=20,
        follow_redirects=True,
    ) as client:
        for term in search_terms:
            try:
                for page in range(1, MAX_PAGES + 1):
                    url = INSTAHYRE_SEARCH_URL.format(term=term, page=page)

                    response = client.get(url)

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
