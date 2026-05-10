"""Primary scraper using the jobspy library (LinkedIn, Indeed, Naukri)."""

import logging
import random
import time
from typing import Any

log = logging.getLogger(__name__)


def scrape_jobspy(
    search_terms: list[str],
    location: str,
    hours_old: int = 24,
    results_per_term: int = 50,
) -> list[dict[str, Any]]:
    """
    Scrape LinkedIn, Indeed, and Naukri via jobspy for each search term.

    Returns a deduplicated list of raw job dicts (deduplicated by job_url within
    this function). Each dict has a 'site' key set to the source platform.
    """
    try:
        from jobspy import scrape_jobs  # type: ignore[import]
    except ImportError:
        log.error(
            "[JobSpy] 'jobspy' is not installed. Run: pip install jobspy"
        )
        return []

    all_results: list[dict] = []
    seen_urls: set[str] = set()

    for term in search_terms:
        try:
            log.info(f"[JobSpy] Searching: '{term}' @ {location}")
            df = scrape_jobs(
                site_name=["indeed", "linkedin"],
                search_term=term,
                location=location,
                results_wanted=results_per_term,
                hours_old=hours_old,
                country_indeed="India",
                linkedin_fetch_description=False,
            )

            if df is None or df.empty:
                log.info(f"[JobSpy] '{term}': 0 jobs")
            else:
                records = df.to_dict(orient="records")
                new_count = 0
                for r in records:
                    url = str(r.get("job_url") or r.get("apply_url") or "")
                    if url and url in seen_urls:
                        continue
                    if url:
                        seen_urls.add(url)
                    all_results.append(r)
                    new_count += 1
                log.info(f"[JobSpy] '{term}': {new_count} jobs")

        except Exception as e:
            log.error(f"[JobSpy] Failed for '{term}': {type(e).__name__}: {e}")

        sleep_time = random.uniform(2.0, 4.0)
        log.debug(f"[JobSpy] Sleeping {sleep_time:.1f}s before next term")
        time.sleep(sleep_time)

    log.info(f"[JobSpy] Total raw jobs scraped: {len(all_results)}")
    return all_results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = scrape_jobspy(
        ["machine learning engineer"],
        "Bengaluru, India",
        hours_old=24,
        results_per_term=10,
    )
    print(f"Got {len(results)} raw jobs")
    for r in results[:3]:
        print(r.get("title"), "|", r.get("company"), "|", r.get("job_url"))
