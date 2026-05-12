"""
Wellfound (AngelList) Playwright scraper.

Strategy: open each /role/l/{slug}/india page in the persistent browser,
wait for it to fully load (network idle + scroll), then extract jobs via:
  1. Any __NEXT_DATA__ or Apollo-like global that contains JobListing nodes.
  2. DOM scraping of rendered job cards (title, company, URL).
  3. Network interception of any JSON response from any host (broad filter).

Debug: on every slug page the scraper logs a sample of all anchor hrefs and
all window globals — this helps diagnose selector mismatches without a manual
DevTools session.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_PERSISTENT_PROFILE = str(Path("cookies") / "_playwright_profile")

_ROLE_SLUGS = [
    "machine-learning-engineer",
    "ai-engineer",
    "llm-engineer",
    "nlp-engineer",
    "mlops-engineer",
    "python-developer",
    "backend-engineer",
    "data-scientist",
]

# ── JS snippets ────────────────────────────────────────────────────────────────

# Returns every <a href> on the page (up to 200), for diagnostics.
_JS_ALL_HREFS = """
() => Array.from(document.querySelectorAll('a[href]'))
        .slice(0, 200)
        .map(a => a.getAttribute('href'))
"""

# Returns all top-level window property names that look data-ish.
_JS_WINDOW_GLOBALS = """
() => Object.keys(window).filter(k =>
    k.startsWith('__') ||
    k.toLowerCase().includes('apollo') ||
    k.toLowerCase().includes('next') ||
    k.toLowerCase().includes('job') ||
    k.toLowerCase().includes('talent')
).slice(0, 30)
"""

# Returns the __NEXT_DATA__ JSON string (or null).
_JS_NEXT_DATA = """
() => {
    const el = document.getElementById('__NEXT_DATA__');
    return el ? el.textContent : null;
}
"""

# Walks window for any Apollo/Next global that might hold job data; returns JSON.
_JS_APOLLO_VARIANTS = """
() => {
    const candidates = [
        window.__APOLLO_STATE__,
        window.__NEXT_DATA__,
        window.__APOLLO_CACHE__,
        window.__RELAY_STORE__,
    ];
    for (const c of candidates) {
        if (c && typeof c === 'object') {
            const keys = Object.keys(c);
            const jobKey = keys.find(k =>
                k.startsWith('JobListing') || k.startsWith('JobListingSearchResult')
            );
            if (jobKey) return JSON.stringify(c);
        }
    }
    return null;
}
"""

# DOM scraper — tries multiple selector patterns for Wellfound job cards.
_JS_DOM_JOBS = """
() => {
    const jobs = [];
    const seen = new Set();

    // Pattern 1: job links that include /company/<slug>/jobs/<id>
    // Pattern 2: any href containing /jobs/
    // Pattern 3: data-href or href-less elements with job-like text near a company link
    const selectors = [
        'a[href*="/company/"][href*="/jobs/"]',
        'a[href*="/jobs/"]',
        'a[href*="/role/"]',
    ];

    for (const sel of selectors) {
        const links = Array.from(document.querySelectorAll(sel));
        for (const link of links) {
            const href = link.href || link.getAttribute('href') || '';
            if (!href || seen.has(href)) continue;
            seen.add(href);

            const rawText = (link.innerText || link.textContent || '').trim();
            // Skip very short text and navigation links
            if (rawText.length < 4 || rawText.length > 200) continue;

            // Walk up the DOM tree to find a card container with a company name
            let company = '';
            let location = '';
            let el = link.parentElement;
            for (let depth = 0; depth < 10 && el; depth++) {
                // Look for a company link (different from the job link)
                const compLinks = Array.from(el.querySelectorAll('a[href]')).filter(a => {
                    const h = a.href || '';
                    return h !== href && (
                        h.includes('/company/') ||
                        h.includes('/startup/') ||
                        h.includes('/jobs') === false
                    );
                });
                if (compLinks.length > 0) {
                    company = (compLinks[0].innerText || compLinks[0].textContent || '').trim();
                    if (company.length > 1) {
                        // Also grab location text from the card
                        const spans = Array.from(el.querySelectorAll('span, p'));
                        for (const s of spans) {
                            const t = (s.innerText || '').trim();
                            if (t.match(/(India|Remote|Bangalore|Bengaluru|Mumbai|Pune|Hyderabad|Delhi|Noida|Gurgaon)/i)
                                && t.length < 100) {
                                location = t;
                                break;
                            }
                        }
                        break;
                    }
                }
                el = el.parentElement;
            }

            // Even without a company link, emit if the text looks like a job title
            jobs.push({
                title: rawText,
                company: company || '',
                apply_url: href.startsWith('http') ? href : 'https://wellfound.com' + href,
                location,
            });
        }
    }
    return jobs;
}
"""

# ── Helpers ────────────────────────────────────────────────────────────────────

def _walk_for_jobs(obj: Any, found: list) -> None:
    if isinstance(obj, dict):
        typename = obj.get("__typename", "")
        has_title = bool(obj.get("title") or obj.get("primaryRoleTitle"))
        has_company = (
            isinstance(obj.get("startup"), dict)
            or isinstance(obj.get("company"), dict)
        )
        if typename in ("JobListing", "JobListingSearchResult") or (has_title and has_company):
            found.append(obj)
            return
        for v in obj.values():
            _walk_for_jobs(v, found)
    elif isinstance(obj, list):
        for item in obj:
            _walk_for_jobs(item, found)


def _node_to_flat(node: dict, slug: str) -> dict | None:
    title = str(node.get("title") or node.get("primaryRoleTitle") or "").strip()
    if not title:
        return None

    company = ""
    startup_slug = ""
    for ckey in ("startup", "company"):
        sub = node.get(ckey)
        if isinstance(sub, dict):
            company = str(sub.get("name") or "").strip()
            startup_slug = str(sub.get("slug") or "").strip()
            if company:
                break
    if not company:
        return None

    loc_raw = node.get("locationNames") or {}
    if isinstance(loc_raw, dict) and "json" in loc_raw:
        location = ", ".join(str(x) for x in loc_raw["json"] if x)
    elif isinstance(loc_raw, list):
        location = ", ".join(str(x) for x in loc_raw if x)
    else:
        location = str(node.get("location") or "")

    job_id = str(node.get("id") or "")
    if startup_slug and job_id:
        apply_url = f"https://wellfound.com/company/{startup_slug}/jobs/{job_id}"
    elif job_id:
        apply_url = f"https://wellfound.com/jobs/{job_id}"
    else:
        apply_url = ""

    posted_iso = None
    ts = node.get("liveStartAt")
    if ts:
        try:
            from datetime import datetime as _dt, timezone as _tz
            posted_iso = _dt.fromtimestamp(int(ts), tz=_tz.utc).isoformat()
        except Exception:
            pass

    return {
        "id": job_id,
        "title": title,
        "company": company,
        "startups": [{"name": company, "slug": startup_slug}],
        "location": location,
        "locations": [{"displayName": p} for p in location.split(", ") if p],
        "remote": bool(node.get("remote", False)),
        "description": str(node.get("description") or ""),
        "slug": slug,
        "jdURL": apply_url,
        "apply_url": apply_url,
        "compensation": str(node.get("compensation") or ""),
        "jobType": str(node.get("jobType") or ""),
        "posted_at": posted_iso,
    }


def _dom_to_flat(item: dict, slug: str) -> dict | None:
    title = str(item.get("title") or "").strip()
    company = str(item.get("company") or "").strip()
    if not title:
        return None
    apply_url = str(item.get("apply_url") or "")
    job_id = apply_url.rstrip("/").split("/")[-1] if apply_url else ""
    location = str(item.get("location") or "")
    return {
        "id": job_id,
        "title": title,
        "company": company,
        "startups": [{"name": company, "slug": ""}],
        "location": location,
        "locations": [{"displayName": location}] if location else [],
        "remote": "remote" in location.lower(),
        "description": "",
        "slug": slug,
        "jdURL": apply_url,
        "apply_url": apply_url,
        "compensation": "",
        "jobType": "",
        "posted_at": None,
    }


# ── Main scraper ───────────────────────────────────────────────────────────────

def scrape_wellfound(search_terms: list[str]) -> list[dict[str, Any]]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("[Wellfound] playwright not installed. Run: pip install playwright")
        return []

    all_jobs: list[dict] = []
    seen_ids: set[str] = set()

    # Network interception: broad filter — any JSON response from any host
    intercepted: list[dict] = []

    def _on_response(response) -> None:
        try:
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            url = response.url
            # Log all JSON urls (debug) so we can see what's being fetched
            log.debug(f"[Wellfound] JSON response: {url[:120]}")
            body = response.json()
            if body:
                intercepted.append(body)
        except Exception:
            pass

    log.info(f"[Wellfound] Playwright scraper starting — {len(_ROLE_SLUGS)} role pages")

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

        try:
            from playwright_stealth import Stealth
            Stealth().use_sync(page)
        except Exception:
            pass

        # Register response listener BEFORE any navigation
        page.on("response", _on_response)

        # Warm up
        try:
            page.goto(
                "https://wellfound.com/jobs",
                wait_until="domcontentloaded",
                timeout=45_000,
            )
            time.sleep(5)
            title_lower = page.title().lower()
            if "access denied" in title_lower or "blocked" in title_lower:
                log.warning(
                    "[Wellfound] Access denied on warm-up. "
                    "Run refresh_cookies.py to pre-warm the session."
                )
                context.close()
                return []
            log.info(f"[Wellfound] Session warm-up OK (title: {page.title()[:60]})")
        except Exception as e:
            log.warning(f"[Wellfound] Warm-up failed: {e}")
            context.close()
            return []

        for slug in _ROLE_SLUGS:
            intercepted.clear()
            slug_count = 0
            url = f"https://wellfound.com/role/l/{slug}/india"

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)

                # Wait for network to settle (jobs load via XHR after initial render)
                try:
                    page.wait_for_load_state("networkidle", timeout=12_000)
                except Exception:
                    pass  # networkidle can time out on pages with persistent connections
                time.sleep(4)

                # Scroll down to trigger any lazy loading
                page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                time.sleep(2)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)

                # ── DIAGNOSTIC: log what's on the page ──────────────────────
                try:
                    globals_found = page.evaluate(_JS_WINDOW_GLOBALS)
                    log.info(f"[Wellfound] '{slug}' window globals: {globals_found}")
                except Exception:
                    pass

                try:
                    hrefs = page.evaluate(_JS_ALL_HREFS)
                    job_hrefs = [h for h in hrefs if h and ("/jobs" in h or "/role" in h or "/company" in h)]
                    log.info(
                        f"[Wellfound] '{slug}' job-like hrefs (sample): "
                        f"{job_hrefs[:8]}"
                    )
                except Exception:
                    pass

                # ── Strategy 1: __NEXT_DATA__ ────────────────────────────────
                try:
                    raw_next = page.evaluate(_JS_NEXT_DATA)
                    if raw_next:
                        nd = json.loads(raw_next)
                        nd_keys = list(nd.get("props", {}).get("pageProps", {}).keys())
                        log.info(f"[Wellfound] '{slug}' __NEXT_DATA__ pageProps keys: {nd_keys[:15]}")
                        page_props = nd.get("props", {}).get("pageProps", {})
                        # Try any key that might hold a jobs list
                        for key, val in page_props.items():
                            if isinstance(val, list) and val:
                                nodes: list[dict] = []
                                _walk_for_jobs(val, nodes)
                                for node in nodes:
                                    flat = _node_to_flat(node, slug)
                                    if not flat:
                                        continue
                                    jid = flat["id"] or (flat["title"] + flat["company"])
                                    if jid in seen_ids:
                                        continue
                                    seen_ids.add(jid)
                                    all_jobs.append(flat)
                                    slug_count += 1
                            elif isinstance(val, dict):
                                nodes = []
                                _walk_for_jobs(val, nodes)
                                for node in nodes:
                                    flat = _node_to_flat(node, slug)
                                    if not flat:
                                        continue
                                    jid = flat["id"] or (flat["title"] + flat["company"])
                                    if jid in seen_ids:
                                        continue
                                    seen_ids.add(jid)
                                    all_jobs.append(flat)
                                    slug_count += 1
                except Exception as e:
                    log.debug(f"[Wellfound] __NEXT_DATA__ failed for '{slug}': {e}")

                # ── Strategy 2: Apollo / global state variants ────────────────
                try:
                    apollo_raw = page.evaluate(_JS_APOLLO_VARIANTS)
                    if apollo_raw:
                        apollo = json.loads(apollo_raw)
                        nodes = []
                        _walk_for_jobs(apollo, nodes)
                        log.info(f"[Wellfound] '{slug}' Apollo state: {len(nodes)} job nodes")
                        for node in nodes:
                            flat = _node_to_flat(node, slug)
                            if not flat:
                                continue
                            jid = flat["id"] or (flat["title"] + flat["company"])
                            if jid in seen_ids:
                                continue
                            seen_ids.add(jid)
                            all_jobs.append(flat)
                            slug_count += 1
                    else:
                        log.debug(f"[Wellfound] '{slug}' no Apollo-like global found")
                except Exception as e:
                    log.debug(f"[Wellfound] Apollo check failed for '{slug}': {e}")

                # ── Strategy 3: Network interception ─────────────────────────
                net_nodes: list[dict] = []
                for body in intercepted:
                    _walk_for_jobs(body, net_nodes)
                if net_nodes:
                    log.info(f"[Wellfound] '{slug}' network: {len(net_nodes)} job nodes from {len(intercepted)} JSON responses")
                for node in net_nodes:
                    flat = _node_to_flat(node, slug)
                    if not flat:
                        continue
                    jid = flat["id"] or (flat["title"] + flat["company"])
                    if jid in seen_ids:
                        continue
                    seen_ids.add(jid)
                    all_jobs.append(flat)
                    slug_count += 1

                # ── Strategy 4: DOM scraping ──────────────────────────────────
                try:
                    dom_items: list[dict] = page.evaluate(_JS_DOM_JOBS)
                    dom_added = 0
                    for item in dom_items:
                        flat = _dom_to_flat(item, slug)
                        if not flat:
                            continue
                        jid = flat["id"] or (flat["title"] + flat["company"])
                        if jid in seen_ids:
                            continue
                        seen_ids.add(jid)
                        all_jobs.append(flat)
                        slug_count += 1
                        dom_added += 1
                    if dom_items:
                        log.info(f"[Wellfound] '{slug}' DOM: found {len(dom_items)} links, {dom_added} new jobs added")
                except Exception as e:
                    log.debug(f"[Wellfound] DOM scraping failed for '{slug}': {e}")

                log.info(f"[Wellfound] '{slug}': {slug_count} total jobs")

            except Exception as e:
                log.warning(f"[Wellfound] Failed for slug '{slug}': {type(e).__name__}: {e}")

            time.sleep(2)

        context.close()

    log.info(f"[Wellfound] Total raw jobs: {len(all_jobs)}")
    return all_jobs
