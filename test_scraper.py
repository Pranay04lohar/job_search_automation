"""Quick standalone test for the JobSpy scraper."""

import logging

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    from scrapers.jobspy_scraper import scrape_jobspy

    results = scrape_jobspy(
        ["machine learning engineer"],
        "Bengaluru, India",
        hours_old=168,
        results_per_term=10,
    )
    print(f"\nGot {len(results)} raw jobs")
    for r in results[:3]:
        print(
            f"  • {r.get('title', '?')} | "
            f"{r.get('company', '?')} | "
            f"{r.get('job_url', r.get('apply_url', '?'))}"
        )
