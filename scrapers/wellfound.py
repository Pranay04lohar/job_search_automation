"""Wellfound (AngelList) GraphQL job scraper using curl_cffi."""

import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Any

import config
from scrapers.cookie_loader import load_cookies_first_existing

log = logging.getLogger(__name__)

WELLFOUND_GQL_ENDPOINT = "https://wellfound.com/graphql"

JOBS_QUERY = """
query JobSearchResults($query: String!, $remote: Boolean) {
  talent {
    jobListings(query: $query, remote: $remote) {
      totalCount
      edges {
        node {
          id
          title
          description
          compensation
          remote
          jobType
          slug
          startups {
            name
            websiteUrl
          }
          locations { displayName }
        }
      }
    }
  }
}
"""

STEALTH_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Content-Type": "application/json",
    "Connection": "keep-alive",
    "Origin": "https://wellfound.com",
    "Referer": "https://wellfound.com/jobs",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "DNT": "1",
}


def _load_cookies() -> dict[str, str]:
    """Load cookies from cookies/wellfound_cookies.(json|txt) if it exists."""
    cookie_paths = [
        Path(config.COOKIES_DIR) / "wellfound_cookies.json",
        Path(config.COOKIES_DIR) / "wellfound_cookies.txt",
        Path(config.COOKIES_DIR) / "wellfound_cookies.cookies",
    ]
    cookies = load_cookies_first_existing(cookie_paths)
    if not cookies:
        cookie_path = cookie_paths[0]
        log.warning(
            f"[Wellfound] Cookie file not found at {cookie_path}. "
            "Export cookies and save to cookies/wellfound_cookies.json (or .txt for Netscape format) for auth."
        )
        return {}
    return cookies


def scrape_wellfound(
    search_terms: list[str],
    remote: bool = True,
) -> list[dict[str, Any]]:
    """
    Query the Wellfound GraphQL endpoint for each search term.

    Requires valid session cookies in cookies/wellfound_cookies.json (or .txt Netscape format).
    Returns raw job node dicts from edges[].node.
    Gracefully returns [] if cookies are missing or auth fails.
    """
    try:
        from curl_cffi import requests as cffi_requests  # type: ignore[import]
    except ImportError:
        log.error("[Wellfound] 'curl_cffi' not installed. Run: pip install curl-cffi")
        return []

    cookies = _load_cookies()
    if not cookies:
        return []

    # Extract CSRF token from cookies if present
    headers = dict(STEALTH_HEADERS)
    csrf_token = cookies.get("_csrf_token") or cookies.get("csrf_token") or ""
    if csrf_token:
        headers["x-csrf-token"] = csrf_token

    all_jobs: list[dict] = []
    seen_ids: set[str] = set()

    for term in search_terms:
        try:
            payload = {
                "query": JOBS_QUERY,
                "variables": {"query": term, "remote": remote},
            }

            response = cffi_requests.post(
                WELLFOUND_GQL_ENDPOINT,
                json=payload,
                headers=headers,
                cookies=cookies,
                impersonate="chrome124",
                timeout=30,
            )

            if response.status_code == 403:
                # 403 = auth failure — cookies are expired or invalid.
                # Retrying won't help; abort entire scraper to save time.
                log.error(
                    "[Wellfound] 403 Forbidden — session cookies are expired or invalid. "
                    "Re-export your Wellfound cookies and save to "
                    "cookies/wellfound_cookies.txt, then retry."
                )
                return all_jobs

            if response.status_code == 429:
                sleep_time = random.uniform(30, 60)
                log.warning(f"[Wellfound] Rate limited (429). Sleeping {sleep_time:.0f}s, retrying...")
                time.sleep(sleep_time)
                response = cffi_requests.post(
                    WELLFOUND_GQL_ENDPOINT,
                    json=payload,
                    headers=headers,
                    cookies=cookies,
                    impersonate="chrome124",
                    timeout=30,
                )
                if response.status_code != 200:
                    log.error(f"[Wellfound] Still blocked after retry for '{term}'. Skipping.")
                    continue

            data = response.json()
            edges = (
                data.get("data", {})
                .get("talent", {})
                .get("jobListings", {})
                .get("edges", [])
            )

            new_count = 0
            for edge in edges:
                node = edge.get("node", {})
                node_id = str(node.get("id", ""))
                if node_id in seen_ids:
                    continue
                seen_ids.add(node_id)
                all_jobs.append(node)
                new_count += 1

            log.info(f"[Wellfound] '{term}': {new_count} jobs")

        except Exception as e:
            log.error(f"[Wellfound] Failed for '{term}': {type(e).__name__}: {e}")

        time.sleep(random.uniform(3.0, 7.0))

    log.info(f"[Wellfound] Total raw jobs: {len(all_jobs)}")
    return all_jobs
