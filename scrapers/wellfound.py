"""
Wellfound (AngelList) Playwright scraper.

Strategy: use the persistent Chrome profile to load Wellfound role-search pages
(https://wellfound.com/role/l/{slug}/india) and extract all job data from the
Apollo GraphQL cache embedded in the page's __NEXT_DATA__ script tag.

This avoids the GraphQL API endpoint which DataDome specifically blocks for
programmatic requests — a real browser with a persistent, trusted session
loads the pages fine and the full job data is embedded in the HTML.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_PERSISTENT_PROFILE = str(Path("cookies") / "_playwright_profile")

# Wellfound role slugs that map to our target job categories.
# URL pattern: https://wellfound.com/role/l/{slug}/india
_ROLE_SLUGS = [
    "machine-learning-engineer",
    "ai-engineer",
    "python-developer",
    "mlops-engineer",
    "nlp-engineer",
    "backend-engineer",
    "data-scientist",
]


def _extract_apollo_state(page) -> dict:
    """Extract the Apollo GraphQL cache from the __NEXT_DATA__ script tag."""
    try:
        raw = page.evaluate(
            """() => {
                const el = document.getElementById('__NEXT_DATA__');
                return el ? el.textContent : null;
            }"""
        )
        if not raw:
            return {}
        data = json.loads(raw)
        # Path: props -> pageProps -> apolloState -> data
        return (
            data.get("props", {})
            .get("pageProps", {})
            .get("apolloState", {})
            .get("data", {})
        )
    except Exception as e:
        log.debug(f"[Wellfound] __NEXT_DATA__ extraction failed: {e}")
        return {}


def _parse_jobs_from_graph(graph: dict, role_slug: str) -> list[dict]:
    """
    Extract JobListingSearchResult nodes from the Apollo graph.
    Company info is resolved by following the StartupResult reference.
    """
    jobs: list[dict] = []

    for key, node in graph.items():
        if not key.startswith("JobListingSearchResult:"):
            continue
        if not isinstance(node, dict):
            continue

        title = str(node.get("title") or node.get("primaryRoleTitle") or "").strip()
        if not title:
            continue

        # Resolve company from StartupResult reference
        company = ""
        startup_slug = ""
        startup_ref = node.get("startup") or {}
        if isinstance(startup_ref, dict) and startup_ref.get("type") == "id":
            startup_node = graph.get(startup_ref["id"], {})
            company = str(startup_node.get("name", "")).strip()
            startup_slug = str(startup_node.get("slug", "")).strip()

        if not company:
            continue

        # Location: stored as {"type": "json", "json": ["Bengaluru", "Remote"]}
        loc_raw = node.get("locationNames") or {}
        if isinstance(loc_raw, dict) and "json" in loc_raw:
            location = ", ".join(str(l) for l in loc_raw["json"] if l)
        else:
            location = ""

        # Build the canonical job URL
        job_id = str(node.get("id", key.split(":")[-1]))
        if startup_slug and job_id:
            apply_url = f"https://wellfound.com/company/{startup_slug}/jobs/{job_id}"
        else:
            apply_url = f"https://wellfound.com/jobs/{job_id}"

        # Posted timestamp
        live_start = node.get("liveStartAt")
        posted_iso = None
        if live_start:
            try:
                from datetime import datetime as _dt, timezone as _tz
                posted_iso = _dt.fromtimestamp(
                    int(live_start), tz=_tz.utc
                ).isoformat()
            except Exception:
                pass

        jobs.append({
            "id": job_id,
            "title": title,
            # WellfoundNormalizer looks for "startups" list; keep compatible
            "startups": [{"name": company, "slug": startup_slug}],
            # Also include flat field for the updated normalizer path
            "company": company,
            "locations": [{"displayName": loc} for loc in (location.split(", ") if location else [])],
            "location": location,
            "remote": bool(node.get("remote", False)),
            "description": str(node.get("description") or ""),
            "slug": role_slug,
            "jdURL": apply_url,
            "apply_url": apply_url,
            "compensation": str(node.get("compensation") or ""),
            "jobType": str(node.get("jobType") or ""),
            "posted_at": posted_iso,
        })

    return jobs


def scrape_wellfound(search_terms: list[str]) -> list[dict[str, Any]]:
    """
    Scrape Wellfound by loading role-search pages with Playwright and extracting
    Apollo state from __NEXT_DATA__.

    `search_terms` is accepted for API compatibility but we use a fixed set of
    curated role slugs instead, since Wellfound's URL-based role search is more
    reliable than keyword search.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("[Wellfound] playwright not installed. Run: pip install playwright")
        return []

    all_jobs: list[dict] = []
    seen_ids: set[str] = set()

    log.info(
        f"[Wellfound] Playwright scraper starting — {len(_ROLE_SLUGS)} role pages"
    )

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=_PERSISTENT_PROFILE,
            headless=False,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
            no_viewport=True,
            ignore_default_args=["--enable-automation"],
        )
        page = context.new_page()

        # Apply stealth to reduce automation fingerprint
        try:
            from playwright_stealth import Stealth
            Stealth().use_sync(page)
        except Exception:
            pass

        # Warm up: visit the jobs home page to let DataDome validate the session
        try:
            page.goto(
                "https://wellfound.com/jobs",
                wait_until="domcontentloaded",
                timeout=45_000,
            )
            time.sleep(3)
            page_title = page.title().lower()
            if "access denied" in page_title or "blocked" in page_title:
                log.warning(
                    "[Wellfound] Access denied on warm-up — DataDome challenge not resolved. "
                    "Try running refresh_cookies.py manually first."
                )
                context.close()
                return []
            log.info(f"[Wellfound] Session warm-up OK (title: {page.title()[:60]})")
        except Exception as e:
            log.warning(f"[Wellfound] Warm-up failed: {e}")

        for slug in _ROLE_SLUGS:
            url = f"https://wellfound.com/role/l/{slug}/india"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                time.sleep(3)

                graph = _extract_apollo_state(page)
                if not graph:
                    log.debug(f"[Wellfound] No Apollo state for slug '{slug}'")
                    continue

                jobs = _parse_jobs_from_graph(graph, slug)
                new_count = 0
                for job in jobs:
                    jid = job.get("id", "")
                    if jid and jid in seen_ids:
                        continue
                    if jid:
                        seen_ids.add(jid)
                    all_jobs.append(job)
                    new_count += 1

                log.info(f"[Wellfound] '{slug}': {new_count} jobs")
                time.sleep(2)

            except Exception as e:
                log.warning(f"[Wellfound] Failed for slug '{slug}': {type(e).__name__}: {e}")

        context.close()

    log.info(f"[Wellfound] Total raw jobs: {len(all_jobs)}")
    return all_jobs
