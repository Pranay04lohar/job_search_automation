"""
scrapers/naukri.py — Naukri.com direct JSON API scraper.

Uses the internal Naukri search API (same endpoint the browser calls).
No Selenium required. Optionally loads cookies from cookies/naukri_cookies.*
for better success rate if Akamai bot-protection kicks in.
"""

import logging
import random
import time
from pathlib import Path
from typing import Any

import config

log = logging.getLogger(__name__)

NAUKRI_API = "https://www.naukri.com/jobapi/v3/search"
RESULTS_PER_PAGE = 20
MAX_PAGES = 3  # 3 pages × 20 results = up to 60 per search term

# Naukri uses its own location slugs (lowercased, no spaces)
_LOCATION_SLUG_MAP: dict[str, str] = {
    "bengaluru": "bengaluru",
    "bangalore": "bengaluru",
    "hyderabad": "hyderabad",
    "pune": "pune",
    "noida": "noida",
    "gurgaon": "gurgaon",
    "gurugram": "gurgaon",
    "mumbai": "mumbai",
    "delhi": "delhi",
    "india": "",           # empty = pan-India, no location filter
    "remote": "",
}

_BASE_HEADERS = {
    "authority": "www.naukri.com",
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9,en-IN;q=0.8",
    "accept-encoding": "gzip, deflate, br",
    "appid": "109",
    "clientid": "d3skt0p",
    "content-type": "application/json",
    "gid": "LOCATION,INDUSTRY,EDUCATION,FAREA_ROLE",
    "systemid": "Naukri",
    "sec-ch-ua": '"Google Chrome";v="124", "Chromium";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "origin": "https://www.naukri.com",
    "referer": "https://www.naukri.com/jobs-in-india",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


def _load_naukri_cookies() -> dict[str, str]:
    """
    Optionally load Naukri cookies from cookies/naukri_cookies.{json,txt,cookies}.
    If none found, returns empty dict (unauthenticated request will still work
    for most queries, but may be blocked by Akamai on heavy usage).
    """
    cookies_dir = Path(config.COOKIES_DIR)
    candidates = [
        cookies_dir / "naukri_cookies.json",
        cookies_dir / "naukri_cookies.txt",
        cookies_dir / "naukri_cookies.cookies",
    ]
    for path in candidates:
        if path.exists():
            try:
                from scrapers.cookie_loader import load_cookies_any
                return load_cookies_any(path)
            except Exception as exc:
                log.warning(f"[Naukri] Could not parse cookies from {path}: {exc}")
    return {}


def _job_age_param(hours_old: int) -> str:
    """Map hours_old to Naukri's jobAge filter (1=1day, 3=3days, 7=1week, 15=2weeks)."""
    if hours_old <= 24:
        return "1"
    if hours_old <= 72:
        return "3"
    if hours_old <= 168:
        return "7"
    return "15"


def scrape_naukri(
    search_terms: list[str],
    location: str = "India",
    hours_old: int = 24,
    results_per_term: int = 20,
) -> list[dict[str, Any]]:
    """
    Scrape Naukri.com jobs for each search term via the internal JSON API.

    Args:
        search_terms: List of keyword strings (e.g. ["ML engineer", "LLM engineer"]).
        location:     Location string (e.g. "Bengaluru, India" or "India").
        hours_old:    How many hours back to look for job postings.
        results_per_term: Target results per term (capped at RESULTS_PER_PAGE × MAX_PAGES).

    Returns:
        List of raw job dicts as returned by the Naukri API.
    """
    try:
        from curl_cffi import requests as cffi_requests  # type: ignore[import]
    except ImportError:
        log.error("[Naukri] 'curl_cffi' not installed. Run: pip install curl-cffi")
        return []

    all_jobs: list[dict] = []
    seen_ids: set[str] = set()

    cookies = _load_naukri_cookies()
    if cookies:
        log.info("[Naukri] Loaded cookies — authenticated requests")
    else:
        log.info("[Naukri] No cookies found — using unauthenticated requests")

    loc_primary = location.lower().split(",")[0].strip()
    naukri_loc = _LOCATION_SLUG_MAP.get(loc_primary, "")

    pages_needed = min(
        MAX_PAGES,
        max(1, (results_per_term + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE),
    )
    job_age = _job_age_param(hours_old)

    for term in search_terms:
        try:
            for page in range(1, pages_needed + 1):
                params: dict[str, str] = {
                    "noOfResults": str(RESULTS_PER_PAGE),
                    "urlType": "search_by_keyword",
                    "searchType": "adv",
                    "keyword": term,
                    "pageNo": str(page),
                    "sort": "r",
                    "k": term,
                    "src": "jobsearchDesk",
                    "latLong": "",
                    "jobAge": job_age,
                }
                if naukri_loc:
                    params["l"] = naukri_loc
                    params["loc"] = naukri_loc

                resp = cffi_requests.get(
                    NAUKRI_API,
                    params=params,
                    headers=_BASE_HEADERS,
                    cookies=cookies,
                    impersonate="chrome124",
                    timeout=25,
                )

                if resp.status_code in (403, 406):
                    log.warning(
                        f"[Naukri] HTTP {resp.status_code} — Naukri is blocking requests "
                        "even with cookies. Your cookies may be expired or missing Akamai "
                        "tokens (_abck, bm_sv). Re-export fresh cookies after logging in."
                    )
                    return all_jobs

                if resp.status_code == 429:
                    wait = random.uniform(30, 60)
                    log.warning(f"[Naukri] Rate limited. Sleeping {wait:.0f}s…")
                    time.sleep(wait)
                    continue

                if resp.status_code != 200:
                    log.warning(f"[Naukri] HTTP {resp.status_code} for '{term}' page {page}")
                    break

                data = resp.json()
                jobs_on_page: list[dict] = data.get("jobDetails", [])

                if not jobs_on_page:
                    log.debug(f"[Naukri] No more results for '{term}' at page {page}")
                    break

                added = 0
                for job in jobs_on_page:
                    job_id = str(job.get("jobId", "") or "")
                    if job_id and job_id in seen_ids:
                        continue
                    if job_id:
                        seen_ids.add(job_id)
                    all_jobs.append(job)
                    added += 1

                log.debug(f"[Naukri] '{term}' page {page}: +{added} jobs")
                time.sleep(random.uniform(1.5, 3.0))

            log.info(f"[Naukri] '{term}' done — {len(all_jobs)} total so far")

        except Exception as exc:
            log.error(f"[Naukri] Error for '{term}': {type(exc).__name__}: {exc}")

        time.sleep(random.uniform(3.0, 5.0))

    log.info(f"[Naukri] Total raw jobs collected: {len(all_jobs)}")
    return all_jobs
