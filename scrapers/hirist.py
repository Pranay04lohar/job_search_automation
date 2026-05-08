"""Hirist.tech direct HTTP JSON API scraper using curl_cffi."""

import logging
import random
import time
from typing import Any

log = logging.getLogger(__name__)

HIRIST_API = "https://www.hirist.tech/api/job/search"
MAX_PAGES = 3

STEALTH_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://www.hirist.tech/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "DNT": "1",
}


def scrape_hirist(
    search_terms: list[str],
    location: str = "bangalore",
) -> list[dict[str, Any]]:
    """
    Scrape Hirist.tech jobs for each search term.

    Uses curl_cffi with chrome124 TLS fingerprint impersonation.
    Paginates up to MAX_PAGES per term.
    Returns raw job dicts from the JSON API.
    """
    try:
        from curl_cffi import requests as cffi_requests  # type: ignore[import]
    except ImportError:
        log.error("[Hirist] 'curl_cffi' not installed. Run: pip install curl-cffi")
        return []

    all_jobs: list[dict] = []
    seen_ids: set[str] = set()

    for term in search_terms:
        try:
            for page in range(MAX_PAGES):
                params = {"q": term, "loc": location, "page": page}

                response = cffi_requests.get(
                    HIRIST_API,
                    params=params,
                    headers=STEALTH_HEADERS,
                    impersonate="chrome124",
                    timeout=20,
                )

                if response.status_code in (403, 429):
                    sleep_time = random.uniform(60, 120)
                    log.warning(
                        f"[Hirist] Rate limited ({response.status_code}). "
                        f"Sleeping {sleep_time:.0f}s, retrying..."
                    )
                    time.sleep(sleep_time)
                    response = cffi_requests.get(
                        HIRIST_API,
                        params=params,
                        headers=STEALTH_HEADERS,
                        impersonate="chrome124",
                        timeout=20,
                    )
                    if response.status_code in (403, 429):
                        log.error(f"[Hirist] Blocked for '{term}' page {page}")
                        break

                if response.status_code != 200:
                    log.warning(
                        f"[Hirist] Unexpected status {response.status_code} for '{term}' page {page}"
                    )
                    break

                data = response.json()

                # Hirist may return data under 'jobs', 'results', or the root list
                if isinstance(data, list):
                    jobs_on_page = data
                elif isinstance(data, dict):
                    jobs_on_page = (
                        data.get("jobs")
                        or data.get("results")
                        or data.get("data")
                        or []
                    )
                else:
                    jobs_on_page = []

                if not jobs_on_page:
                    log.debug(f"[Hirist] No more results for '{term}' at page {page}")
                    break

                new_count = 0
                for job in jobs_on_page:
                    job_id = str(job.get("job_id") or job.get("id") or "")
                    if job_id and job_id in seen_ids:
                        continue
                    if job_id:
                        seen_ids.add(job_id)
                    all_jobs.append(job)
                    new_count += 1

                log.debug(f"[Hirist] '{term}' page {page}: {new_count} jobs")

                # Sleep between pages
                time.sleep(random.uniform(2.0, 4.0))

            log.info(f"[Hirist] '{term}': accumulated {len(all_jobs)} total so far")

        except Exception as e:
            log.error(f"[Hirist] Failed for '{term}': {type(e).__name__}: {e}")

        # Sleep between search terms
        time.sleep(random.uniform(5.0, 10.0))

    log.info(f"[Hirist] Total raw jobs: {len(all_jobs)}")
    return all_jobs
