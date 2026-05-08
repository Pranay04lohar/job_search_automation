"""
scrapers/hirist.py — Hirist.com job scraper.

Uses the jobseeker-api.hirist.com endpoint discovered from the open-source
Scrapy spider at github.com/silentkinght25/Scraping-Job-Portals-with-Scrapy.

Strategy:
  1. Query several tech-category feeds (covers Python/ML/Backend roles).
  2. Post-filter results by keyword to keep only relevant jobs.
  3. Paginate with the hasMore flag up to MAX_PAGES.
"""

import logging
import random
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Base URL for the working Hirist category-feed API
_HIRIST_BASE = "https://jobseeker-api.hirist.com/v2/jobfeed/-1/v3/catJobs"

# Category IDs that cover Python / ML / Backend / Data Science roles.
# Hirist has 13 categories (1–13); these tend to cover tech/dev roles.
# We fetch the ones most likely to contain our target roles.
_CATEGORY_IDS = [1, 2, 3, 4, 5, 6, 7, 8]

MAX_PAGES = 3        # pages per category
RESULTS_PER_PAGE = 20

_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Origin": "https://www.hirist.com",
    "Referer": "https://www.hirist.com/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "cross-site",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}


def _keyword_matches(job: dict, keywords: list[str]) -> bool:
    """Return True if any keyword appears in job title or skills."""
    title = str(job.get("title", "") or "").lower()
    skills_raw = job.get("skills") or []
    if isinstance(skills_raw, list):
        skills_text = " ".join(str(s) for s in skills_raw).lower()
    else:
        skills_text = str(skills_raw).lower()
    text = f"{title} {skills_text}"
    return any(kw.lower() in text for kw in keywords)


def scrape_hirist(
    search_terms: list[str],
    location: str = "bangalore",
    max_experience: int = 3,
) -> list[dict[str, Any]]:
    """
    Scrape Hirist.com by fetching tech-category feeds and keyword-filtering locally.

    Args:
        search_terms:   Keywords used for post-fetch filtering.
        location:       Preferred location filter passed to the API (optional).
        max_experience: Drop jobs requiring more than this many years.

    Returns:
        List of raw job dicts matching at least one keyword.
    """
    all_jobs: list[dict] = []
    seen_ids: set[str] = set()

    # Derive a flat keyword list from search terms for fast local matching
    keywords = list({kw.lower().strip() for term in search_terms for kw in term.split()})

    # Also keep the original multi-word terms for better matching
    keywords += [t.lower() for t in search_terms]

    loc_param = location.lower().split(",")[0].strip() if location else ""

    with httpx.Client(headers=_HEADERS, timeout=25, follow_redirects=True) as client:
        for cat_id in _CATEGORY_IDS:
            url = f"{_HIRIST_BASE}/{cat_id}"
            try:
                for page in range(MAX_PAGES):
                    params: dict[str, Any] = {
                        "pageNo": page,
                        "loc": loc_param,
                        "minexp": 0,
                        "maxexp": max_experience,
                        "boostJobs": "false",
                    }

                    resp = client.get(url, params=params)

                    if resp.status_code == 503:
                        log.warning("[Hirist] Server unavailable (503) — skipping all categories.")
                        return all_jobs

                    if resp.status_code == 404:
                        log.debug(f"[Hirist] Category {cat_id} not found (404)")
                        break

                    if resp.status_code in (403, 429):
                        wait = random.uniform(15, 30)
                        log.warning(
                            f"[Hirist] Rate limited on cat {cat_id} page {page}. "
                            f"Sleeping {wait:.0f}s…"
                        )
                        time.sleep(wait)
                        break

                    if resp.status_code != 200:
                        log.warning(
                            f"[Hirist] HTTP {resp.status_code} for cat {cat_id} page {page}"
                        )
                        break

                    data = resp.json()
                    count = data.get("count", 0)
                    if count == 0:
                        break

                    jobs_on_page: list[dict] = data.get("jobs") or []
                    if not jobs_on_page:
                        break

                    added = 0
                    for job in jobs_on_page:
                        # Deduplicate by job ID (or title+company hash)
                        job_id = str(
                            job.get("jobId")
                            or job.get("id")
                            or job.get("job_id")
                            or ""
                        )
                        if not job_id:
                            # Fallback key: title + company
                            company_name = (
                                (job.get("companyData") or {}).get("companyName", "")
                                or job.get("companyName", "")
                            )
                            job_id = f"{job.get('title','')}|{company_name}"

                        if job_id in seen_ids:
                            continue
                        seen_ids.add(job_id)

                        if _keyword_matches(job, keywords):
                            all_jobs.append(job)
                            added += 1

                    log.debug(
                        f"[Hirist] Cat {cat_id} page {page}: "
                        f"{len(jobs_on_page)} fetched, {added} keyword-matched"
                    )

                    if not data.get("hasMore", False):
                        break

                    time.sleep(random.uniform(1.0, 2.5))

                log.info(
                    f"[Hirist] Category {cat_id} done — {len(all_jobs)} total matched so far"
                )

            except httpx.RequestError as exc:
                log.error(f"[Hirist] Network error for category {cat_id}: {exc}")
            except Exception as exc:
                log.error(
                    f"[Hirist] Unexpected error for category {cat_id}: "
                    f"{type(exc).__name__}: {exc}"
                )

            time.sleep(random.uniform(2.0, 4.0))

    log.info(f"[Hirist] Total keyword-matched jobs: {len(all_jobs)}")
    return all_jobs
