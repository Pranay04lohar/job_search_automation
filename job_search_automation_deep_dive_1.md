# Deep Technical Breakdown: Automated Personal Job Search Aggregation System

> **Scope:** Personal use only. Python-comfortable solo developer. Targets: LinkedIn, Naukri, Wellfound, Indeed, Hirist, Instahyre. Goal: maximum leverage, minimum maintenance.

---

## Table of Contents

1. [Mental Model & Architecture Philosophy](#1-mental-model--architecture-philosophy)
2. [Data Extraction Layer](#2-data-extraction-layer)
3. [Anti-Bot, Stealth & Evasion](#3-anti-bot-stealth--evasion)
4. [Authentication & Session Handling](#4-authentication--session-handling)
5. [Browser Automation Deep Dive](#5-browser-automation-deep-dive)
6. [API Interception & Reverse Engineering](#6-api-interception--reverse-engineering)
7. [Data Parsing & Normalization Pipeline](#7-data-parsing--normalization-pipeline)
8. [Storage & Deduplication](#8-storage--deduplication)
9. [Resume Matching & Ranking Logic](#9-resume-matching--ranking-logic)
10. [Alerting & Notification Systems](#10-alerting--notification-systems)
11. [Scheduling & Automation Workflows](#11-scheduling--automation-workflows)
12. [Proxy, Cookie & User-Agent Handling](#12-proxy-cookie--user-agent-handling)
13. [CAPTCHA Handling](#13-captcha-handling)
14. [Deployment Methods](#14-deployment-methods)
15. [Monitoring & Maintenance](#15-monitoring--maintenance)
16. [Key GitHub Projects Analyzed](#16-key-github-projects-analyzed)
17. [Comparison Matrix](#17-comparison-matrix)
18. [Recommended Architecture for Your Use Case](#18-recommended-architecture-for-your-use-case)
19. [What NOT to Build](#19-what-not-to-build)
20. [The Minimum Viable Implementation (MVI)](#20-the-minimum-viable-implementation-mvi)

---

## 1. Mental Model & Architecture Philosophy

### Full-stack app vs. lightweight scripts

The most important decision you'll make is resisting the temptation to build a SaaS. For personal job search automation, a **full-stack app** (React frontend, REST API, user auth, multi-user DB, deployment pipeline) adds no value over a well-structured set of Python scripts. You have no concurrent users, no uptime SLAs, and no customers. Every engineering decision should optimize for: **time-to-first-alert**.

The right mental model is a **pipeline of composable scripts** glued by a lightweight scheduler, writing to a single SQLite file, alerting via Telegram. No web server, no frontend, no Docker unless you choose to. You can optionally add a tiny read-only Streamlit dashboard later if you want visual browsing — but it's not required.

**Architecture layers (from scrape to alert):**

```
[Scheduler: cron / APScheduler]
         ↓
[Scraper Layer: per-platform modules]
  LinkedIn | Naukri | Indeed | Wellfound | Hirist | Instahyre
         ↓
[Raw JSON → Normalizer → Deduplicator]
         ↓
[SQLite: jobs table with hashes]
         ↓
[Resume Matcher / Scorer: sentence-transformers or LLM]
         ↓
[Alert Filter: score > threshold AND not seen]
         ↓
[Telegram Bot / Email → You]
```

Each layer is independently testable and replaceable. This is the architecture.

---

## 2. Data Extraction Layer

### 2.1 The Extraction Strategy Decision Tree

Not every platform requires the same technique. You should tier your approach:

| Platform | Preferred Method | Why |
|---|---|---|
| **LinkedIn** | Public search page scrape (no login) OR LinkedIn API (limited) | Login scraping is fragile; public `/jobs/search/` works |
| **Naukri** | Direct HTTP with session cookies OR `JobSpy` library | Has undocumented JSON API |
| **Indeed** | `JobSpy` library (wraps Indeed's internal API) | Most reliable extraction |
| **Wellfound** | GraphQL API interception | Has a proper GraphQL endpoint |
| **Hirist** | Direct HTTP requests | Lightweight site, minimal bot protection |
| **Instahyre** | Direct HTTP + BeautifulSoup | Light JS, parseable HTML |

### 2.2 Direct HTTP Scraping (`requests` + `httpx`)

**What it does:** Sends HTTP GET/POST requests directly to the target server, without running a browser.

**Why use it:** 10–100x faster than browser automation; zero memory overhead; can run in serverless functions; much harder to fingerprint if you match TLS/header signatures correctly.

**How it works internally:**
- `requests` builds an HTTP request with headers, cookies, and optionally a proxy
- The TCP connection is established (optionally via CONNECT tunnel for proxies)
- TLS handshake happens (this produces the JA3 fingerprint — see §3)
- The server returns HTML/JSON which you parse directly

**Tradeoff:** Fails completely on JS-heavy sites that render content client-side. Many modern job boards (LinkedIn, Indeed) render initial HTML server-side but load additional data via XHR — you can often intercept those XHR endpoints directly (see §6).

**Library choices:**
- `requests` — synchronous, simple, battle-tested. Fine for <50 requests/run.
- `httpx` — adds async support and HTTP/2; better TLS fingerprint matching; drop-in replacement.
- `curl_cffi` — Python bindings for curl with impersonation mode. Can produce **identical TLS fingerprints to real Chrome/Firefox**. This is the strongest option for avoiding TLS-level fingerprinting without a browser.

```python
# curl_cffi impersonating Chrome 120
from curl_cffi import requests as cfi_requests

resp = cfi_requests.get(
    "https://www.naukri.com/jobapi/v3/search",
    params={"noOfResults": 20, "urlType": "search_by_key_loc",
            "searchType": "adv", "keyword": "machine learning", "location": "bangalore"},
    impersonate="chrome120",
    headers={"x-http-method-override": "GET"}
)
jobs = resp.json()
```

**Scaling:** For personal use, sequential requests with 2–5s random sleep between them is sufficient and safe. Async is only needed at >100 concurrent requests.

**OSS alternatives:** `aiohttp` (async), `urllib3` (low-level), `treq` (Twisted-based).

### 2.3 HTML Parsing with BeautifulSoup and lxml

**What it does:** Parses the raw HTML returned by HTTP requests into a navigable tree, letting you extract specific elements with CSS selectors or XPath.

**How it works:** `BeautifulSoup` builds a DOM tree from raw HTML using a configurable parser backend. `lxml` is the fastest backend and supports XPath — always use `lxml` when speed matters.

```python
from bs4 import BeautifulSoup

soup = BeautifulSoup(html_content, "lxml")
job_cards = soup.select("div.job-listing-card")
for card in job_cards:
    title = card.select_one("h2.job-title").get_text(strip=True)
    company = card.select_one("span.company-name").get_text(strip=True)
```

**Tradeoff:** CSS selectors break when the platform redesigns. Use the most stable selectors (data attributes like `data-job-id` are more stable than class names which get minified). Always wrap in try/except and log parse failures.

**lxml XPath alternative** (faster for complex documents):
```python
from lxml import html
tree = html.fromstring(html_content)
titles = tree.xpath('//h2[@class="job-title"]/text()')
```

### 2.4 JSON API Extraction

Many job platforms have undocumented or semi-public JSON APIs used by their own frontend. These are the gold standard — structured data with no parsing ambiguity.

**How to find them:** Open DevTools → Network tab → filter by `XHR/Fetch` → search for jobs → watch which requests return JSON with job data.

**Naukri's internal API** (confirmed working as of 2025):
```
GET https://www.naukri.com/jobapi/v3/search
  ?noOfResults=20
  &urlType=search_by_key_loc
  &searchType=adv
  &keyword=machine+learning+engineer
  &location=bangalore
  &experience=0
  &areaTypeID=0
  &wfhType=2    # 2 = work from home
```
Returns structured JSON with `jobDetails[]` array. No auth required for basic search.

**Hirist API** (undocumented):
```
GET https://www.hirist.tech/api/job/search
  ?q=machine+learning
  &loc=bangalore
  &page=0
```

**Instahyre** requires a session token obtained via login, stored in cookies.

**Wellfound GraphQL** (see §6 for full details).

---

## 3. Anti-Bot, Stealth & Evasion

### 3.1 The Detection Stack (What You're Fighting)

Modern anti-bot systems layer multiple signals into a "bot score." Understanding each signal lets you neutralize them:

**Layer 1: IP Reputation**
- Datacenter IPs (AWS, GCP, Hetzner ASNs) are immediately suspicious
- Residential IPs from real ISPs have high trust scores
- IP velocity (too many requests from one IP) triggers rate limiting
- **Mitigation:** Residential proxy rotation, or simply running from your home machine

**Layer 2: TLS/JA3 Fingerprinting**
- Every HTTPS connection exposes a fingerprint based on cipher suites, extensions, and protocol version
- Python's `requests` produces a JA3 different from Chrome — sites can detect this at the TCP layer before even looking at headers
- **Mitigation:** `curl_cffi` with impersonation, or use a real browser (Playwright/Selenium)

**Layer 3: HTTP Header Analysis**
- `requests` sends `User-Agent: python-requests/2.x` by default — instantly flagged
- Real browsers send 15+ headers in a specific order; `requests` doesn't
- **Mitigation:** Set complete, consistent headers matching a real browser fingerprint

**Layer 4: JavaScript Fingerprinting**
- `navigator.webdriver` is `true` in Playwright/Selenium by default
- Missing browser APIs: `window.chrome`, `navigator.plugins`, WebGL renderer strings
- Canvas fingerprint anomalies (headless Chrome produces a different canvas hash)
- **Mitigation:** `playwright-stealth` patches most of these; for harder sites use Camoufox

**Layer 5: Behavioral Analysis**
- Bots navigate linearly at constant speed with no mouse movement
- Real users scroll, pause, hover, mis-click, go back
- Inter-request timing is perfectly regular in bots; variable in humans
- **Mitigation:** Random delays (`time.sleep(random.uniform(2, 7))`), scroll simulation, occasional back-navigation

**Layer 6: CAPTCHA Challenges**
- Triggered when other signals push the bot score above a threshold
- See §13 for full CAPTCHA handling

### 3.2 The Stealth Toolkit

**`playwright-stealth` (Python)**

```python
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=[
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
    ])
    context = browser.new_context(
        viewport={"width": 1366, "height": 768},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        locale="en-IN",
        timezone_id="Asia/Kolkata",
    )
    page = context.new_page()
    stealth_sync(page)  # patches navigator.webdriver, chrome runtime, plugins, etc.
    page.goto("https://www.linkedin.com/jobs/search/")
```

`playwright-stealth` patches:
- `navigator.webdriver` → `undefined`
- `window.chrome` → populated object
- `navigator.plugins` → realistic plugin list
- `navigator.languages` → `["en-US", "en"]`
- Canvas fingerprint → consistent value
- WebGL vendor/renderer → real GPU strings

**Camoufox** — Firefox-based, patches at C++ level (strongest available OSS option):
```bash
pip install camoufox
python -m camoufox fetch
```
```python
from camoufox.sync_api import Camoufox

with Camoufox(headless=True, geoip=True) as browser:
    page = browser.new_page()
    page.goto("https://www.linkedin.com/jobs/")
```
Rotates fingerprints automatically using BrowserForge's fingerprint database. Best for sites with DataDome or Akamai protection.

**`curl_cffi`** — For HTTP-only scraping with proper TLS fingerprinting:
```python
from curl_cffi import requests
resp = requests.get(url, impersonate="chrome124")
```
Supports impersonating: `chrome99`, `chrome110`, `chrome120`, `chrome124`, `firefox102`, `safari15_3`, `edge99`.

### 3.3 Per-Platform Stealth Profile

| Platform | Bot Protection Level | Recommended Approach |
|---|---|---|
| **LinkedIn** (public, not logged in) | Medium (Cloudflare) | `curl_cffi` with `chrome124` + proper headers |
| **LinkedIn** (logged in) | High | Playwright + stealth + your own session cookies |
| **Naukri** | Low-Medium | `curl_cffi` or plain `requests` with good headers |
| **Indeed** | Medium | `JobSpy` library handles this |
| **Wellfound** | Low-Medium | GraphQL API with session token |
| **Hirist** | Low | Plain `requests` + headers |
| **Instahyre** | Low | Plain `requests` + cookies |

### 3.4 Rate Limiting Strategy

The most important anti-detection measure for personal use is **going slow**:

```python
import random, time

def polite_get(url, session, min_delay=2.0, max_delay=6.0):
    resp = session.get(url)
    time.sleep(random.uniform(min_delay, max_delay))
    return resp
```

For personal use running 2–4 times/day with 20–50 results per platform, you will not trigger rate limits if you add these delays. No proxies needed for personal use from a residential IP.

---

## 4. Authentication & Session Handling

### 4.1 Cookie-Based Session Reuse (Recommended)

The cleanest approach for personal use: log in manually once in a real browser, export your cookies, and reuse them in your scraper. This is undetectable because the cookies are genuinely yours.

**Step 1: Export cookies using browser extension** (`Cookie-Editor`, `EditThisCookie`, or `Get cookies.txt LOCALLY`)

**Step 2: Load in Python:**
```python
import json, http.cookiejar
import requests

# From Netscape format (most extensions export this)
jar = http.cookiejar.MozillaCookieJar("cookies.txt")
jar.load()
session = requests.Session()
session.cookies = jar

# Or from JSON format
with open("cookies.json") as f:
    cookies = {c["name"]: c["value"] for c in json.load(f)}
session = requests.Session()
session.cookies.update(cookies)
```

**Validity:** LinkedIn cookies last 1 year typically. Naukri cookies last 30 days. Schedule a reminder to refresh them.

**Playwright with saved cookies:**
```python
context = browser.new_context()
context.add_cookies(json.load(open("cookies.json")))
page = context.new_page()
# Already "logged in"
```

### 4.2 Automated Login (Fragile, Use Only When Necessary)

```python
page.goto("https://www.naukri.com/nlogin/login")
page.fill('input[placeholder="Enter your active Email ID / Username"]', EMAIL)
page.fill('input[placeholder="Enter your password"]', PASSWORD)
page.click('button[type="submit"]')
page.wait_for_url("**/mnjuser/homepage**")

# Save session for reuse
cookies = context.cookies()
json.dump(cookies, open("naukri_cookies.json", "w"))
```

**Why it's fragile:** Login flows change; CAPTCHAs appear; 2FA blocks automation. Cookie reuse is much more reliable.

### 4.3 Token Storage and Refresh

Store session state in a local JSON file:
```python
SESSION_FILE = "~/.jobsearch/sessions.json"

def load_session(platform):
    sessions = json.load(open(SESSION_FILE))
    return sessions.get(platform, {})

def save_session(platform, cookies, expiry):
    sessions = json.load(open(SESSION_FILE)) if os.path.exists(SESSION_FILE) else {}
    sessions[platform] = {"cookies": cookies, "expiry": expiry}
    json.dump(sessions, open(SESSION_FILE, "w"))
```

---

## 5. Browser Automation Deep Dive

### 5.1 Playwright vs Selenium vs Puppeteer — Full Comparison

**Playwright (Microsoft, Python/Node/Java/.NET)**

*How it works internally:* Uses the Chrome DevTools Protocol (CDP) to control Chromium, Firefox, or WebKit. Unlike Selenium which uses the W3C WebDriver protocol, Playwright connects directly to the browser via CDP over a WebSocket, giving it lower latency and richer control over browser internals (network interception, geolocation, permissions, etc.).

*Key advantages:*
- Native async support in Python (`asyncio`)
- Cross-browser: Chromium, Firefox, WebKit (Safari-ish)
- Built-in network interception (you can intercept XHR and extract JSON before the page renders)
- `browser.new_context()` creates isolated browser profiles — run multiple scrapers in parallel contexts
- Actively maintained (2025: latest releases monthly)
- `page.wait_for_selector()`, `page.wait_for_response()` handle dynamic content better than explicit sleeps

*Limitations:* Larger memory footprint than HTTP scraping; stealth requires extra patches; some sites specifically fingerprint CDP connections.

```python
# Intercept API responses instead of parsing HTML
async def intercept_jobs(page):
    results = []
    async def handle_response(response):
        if "jobapi" in response.url and response.status == 200:
            try:
                data = await response.json()
                results.extend(data.get("jobDetails", []))
            except: pass
    page.on("response", handle_response)
    await page.goto("https://www.naukri.com/machine-learning-jobs")
    await page.wait_for_load_state("networkidle")
    return results
```

**Selenium (Selenium Project, Python/Java/etc.)**

*How it works:* Uses the W3C WebDriver protocol — a separate `chromedriver` binary acts as a bridge between your script and the Chrome browser. Commands are HTTP requests to `localhost:4444`.

*Why it's worse for scraping in 2026:* Slower than Playwright (extra HTTP hop per command); `navigator.webdriver` is easier to detect; `chromedriver` must be manually kept in sync with Chrome version (though `selenium-manager` now handles this); less idiomatic Python. The one advantage: SeleniumBase UC Mode has excellent CAPTCHA bypass for some sites.

*When to use it:* If you already know it well and are scraping low-protection sites. Otherwise use Playwright.

**Puppeteer (Google, Node.js only)**

*How it works:* Also uses CDP, but Node.js only. The `puppeteer-extra-plugin-stealth` plugin is well-maintained and widely used. If you prefer TypeScript/Node.js, Puppeteer is excellent. For Python, Playwright is equivalent.

**Verdict for your use case:**
- **Playwright (Python)** is the correct default for all browser automation tasks.
- Use **`curl_cffi`** for platforms with parseable JSON APIs (faster, lighter).
- Use **`JobSpy`** library for LinkedIn/Indeed/Naukri (skip writing scrapers entirely).

### 5.2 Headless vs Headed Mode

**Headless** (`headless=True`): No visible browser window. Runs in background. Required for server/cron use. Slightly easier to detect (some anti-bot systems check rendering differences in headless Chrome).

**Headed** (`headless=False`): Shows browser window. Useful for debugging and for sites with the highest bot protection (visual CAPTCHA). Cannot run on headless servers without Xvfb.

**New headless mode** (Playwright `channel="chrome"` with `--headless=new`): Chromium's new headless mode (introduced in Chrome 112) renders pages identically to headed mode, eliminating some headless-specific fingerprints:
```python
browser = p.chromium.launch(headless=True, args=["--headless=new"])
```

---

## 6. API Interception & Reverse Engineering

### 6.1 The Network Tab Method

The most powerful technique. Instead of parsing HTML, you intercept the XHR/fetch calls the page's JavaScript makes, and call those API endpoints directly with `requests`/`httpx`.

**Process:**
1. Open DevTools → Network tab → check "XHR" filter
2. Navigate to a job search page in the browser
3. Watch which requests fire — look for endpoints returning JSON with job data
4. Copy the request as `curl` (right-click → Copy as cURL)
5. Convert to Python: use `curlconverter.com` or do it manually

**Wellfound GraphQL reverse engineering:**

Wellfound (formerly AngelList) uses GraphQL. You can intercept the query in DevTools:

```python
import requests

WELLFOUND_GQL = "https://wellfound.com/graphql"

query = """
query JobSearchResults($query: String!, $locationNames: [String!], $jobTypes: [String!], $remote: Boolean) {
  talent {
    jobListings(query: $query, locationNames: $locationNames, jobTypes: $jobTypes, remote: $remote) {
      totalCount
      edges {
        node {
          id
          title
          description
          compensation
          remote
          jobType
          startups {
            name
            websiteUrl
            markets { displayName }
          }
        }
      }
    }
  }
}
"""

variables = {
    "query": "machine learning",
    "locationNames": ["India", "Remote"],
    "remote": True
}

resp = requests.post(
    WELLFOUND_GQL,
    json={"query": query, "variables": variables},
    headers={
        "Content-Type": "application/json",
        "x-csrf-token": CSRF_TOKEN,  # Get from cookies after login
        "cookie": SESSION_COOKIE,
    }
)
```

**LinkedIn's hidden API:**

LinkedIn's public job search page hits an internal API:
```
GET https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search
  ?keywords=machine+learning+engineer
  &location=India
  &f_TPR=r604800    # last 7 days
  &f_E=2            # entry level
  &start=0          # pagination offset
```

This endpoint returns HTML fragments (not JSON), but it works without authentication. Increment `start` by 25 for pagination.

For the full JSON API (requires LinkedIn cookies):
```
GET https://www.linkedin.com/voyager/api/jobs/jobPostings/{jobId}
  Headers: csrf-token: <from cookie>, x-li-lang: en_US
```

### 6.2 Mobile API Interception

Mobile apps often use simpler, better-documented APIs. You can intercept them using:
- **mitmproxy** (open-source HTTP/HTTPS proxy): Route your phone through it, browse the app, capture API calls
- **Charles Proxy** (paid but popular)

For Naukri's mobile app: the API is `https://www.naukri.com/jobapi/v3/search` — same as web, but the mobile app sometimes gets different rate limiting treatment.

### 6.3 Monitoring for API Changes

Platform APIs change. Build a simple contract test:
```python
def validate_naukri_response(data):
    required_keys = ["jobDetails", "noOfJobs", "typeLabel"]
    for key in required_keys:
        assert key in data, f"API contract broken: missing '{key}'"
    assert len(data["jobDetails"]) > 0, "Empty job results — check search params or API endpoint"
```

Run this after every scrape. If it fails, you know the API changed and need to reinvestigate.

---

## 7. Data Parsing & Normalization Pipeline

### 7.1 The Normalization Problem

Each platform returns job data in completely different schemas. Your goal is to normalize everything into a single canonical `Job` model:

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List

@dataclass
class Job:
    id: str                          # platform-namespaced: "linkedin:4408829907"
    title: str
    company: str
    location: str
    is_remote: bool
    employment_type: str             # full_time | internship | contract | part_time
    description: str
    apply_url: str
    posted_at: Optional[datetime]
    platform: str                    # linkedin | naukri | indeed | wellfound | hirist | instahyre
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    salary_currency: str = "INR"
    skills: List[str] = field(default_factory=list)
    experience_min: Optional[int] = None    # years
    experience_max: Optional[int] = None
    raw: dict = field(default_factory=dict) # original payload, for debugging
```

### 7.2 Platform-Specific Normalizers

```python
class NaukriNormalizer:
    WORK_TYPE_MAP = {"1": "full_time", "2": "part_time", "3": "internship", "4": "contract"}

    def normalize(self, raw: dict) -> Job:
        exp = raw.get("experience", {})
        sal = raw.get("placeholders", [{}])[0] if raw.get("placeholders") else {}
        return Job(
            id=f"naukri:{raw['jobId']}",
            title=raw["title"],
            company=raw.get("companyName", ""),
            location=", ".join(raw.get("locations", [])),
            is_remote="work from home" in [l.lower() for l in raw.get("tagsAndSkills", [])],
            employment_type=self.WORK_TYPE_MAP.get(str(raw.get("jobType", "1")), "full_time"),
            description=raw.get("jobDescription", ""),
            apply_url=f"https://www.naukri.com{raw.get('jdURL', '')}",
            posted_at=datetime.fromtimestamp(raw["modifiedOn"] / 1000) if "modifiedOn" in raw else None,
            platform="naukri",
            salary_min=int(sal.get("label", "0").replace("L", "").split("-")[0].strip() or 0) * 100000 if sal else None,
            skills=raw.get("tagsAndSkills", "").split(", ") if raw.get("tagsAndSkills") else [],
            experience_min=exp.get("min"),
            experience_max=exp.get("max"),
            raw=raw
        )
```

### 7.3 Description Cleaning

Job descriptions are full of HTML, Unicode garbage, and excess whitespace. Clean them before storing:

```python
import re
from bs4 import BeautifulSoup

def clean_description(raw_text: str) -> str:
    # Strip HTML
    soup = BeautifulSoup(raw_text, "lxml")
    text = soup.get_text(separator="\n")
    # Remove excessive whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    # Remove non-printable chars
    text = ''.join(c for c in text if c.isprintable() or c == '\n')
    return text.strip()
```

### 7.4 Salary Parsing

Salary data is stored in wildly different formats. A regex-based parser:

```python
def parse_salary_inr(salary_str: str) -> tuple[Optional[int], Optional[int]]:
    """Parses '4-8 LPA', '₹40,000/month', '8L-15L' etc. into annual INR."""
    if not salary_str:
        return None, None
    s = salary_str.lower().replace(",", "").replace("₹", "")
    # LPA format
    m = re.search(r'(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*lpa', s)
    if m:
        return int(float(m.group(1)) * 100000), int(float(m.group(2)) * 100000)
    # Monthly format
    m = re.search(r'(\d+)\s*[-–]\s*(\d+)\s*/month', s)
    if m:
        return int(m.group(1)) * 12, int(m.group(2)) * 12
    return None, None
```

---

## 8. Storage & Deduplication

### 8.1 SQLite vs PostgreSQL

**For personal use, SQLite is the correct answer, period.** It's a single file, needs no server, has zero maintenance, handles millions of rows without issue, and `pandas` can query it directly.

Use PostgreSQL only if:
- You need concurrent writes from multiple processes (SQLite has file-level locking)
- You're storing >10 GB of data (unlikely for job search)
- You need full-text search across millions of rows (FTS5 extension in SQLite handles this)

**SQLite with WAL mode** handles concurrent reads (dashboard + scraper simultaneously):
```python
import sqlite3
conn = sqlite3.connect("jobs.db")
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
```

### 8.2 Schema Design

```sql
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,              -- "naukri:12345678"
    content_hash TEXT NOT NULL,       -- SHA256 of (title+company+description)
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    is_remote INTEGER DEFAULT 0,
    employment_type TEXT,
    description TEXT,
    description_clean TEXT,
    apply_url TEXT,
    posted_at TEXT,                   -- ISO8601 datetime
    scraped_at TEXT NOT NULL,         -- ISO8601 datetime
    platform TEXT NOT NULL,
    salary_min INTEGER,
    salary_max INTEGER,
    salary_currency TEXT DEFAULT 'INR',
    skills TEXT,                      -- JSON array stored as text
    experience_min INTEGER,
    experience_max INTEGER,
    match_score REAL,                 -- filled by resume matcher
    alerted INTEGER DEFAULT 0,        -- 0 = not alerted yet, 1 = alerted
    applied INTEGER DEFAULT 0,        -- 0 = not applied, 1 = applied
    status TEXT DEFAULT 'new',        -- new | seen | applied | rejected
    raw TEXT                          -- JSON blob of original payload
);

CREATE INDEX IF NOT EXISTS idx_platform ON jobs(platform);
CREATE INDEX IF NOT EXISTS idx_posted_at ON jobs(posted_at);
CREATE INDEX IF NOT EXISTS idx_match_score ON jobs(match_score);
CREATE INDEX IF NOT EXISTS idx_alerted ON jobs(alerted);
CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(
    title, company, description, skills, content=jobs, content_rowid=rowid
);
```

### 8.3 Deduplication Strategy

**Problem:** The same job appears on LinkedIn, Indeed, and the company's own page. You don't want three notifications for the same role.

**Method 1: Platform ID dedup** (simplest): Use `platform:external_id` as primary key. Prevents duplicate inserts from the same platform. Fast but doesn't cross-platform dedup.

**Method 2: Content hash** (cross-platform):
```python
import hashlib, json

def content_hash(job: Job) -> str:
    canonical = {
        "title": job.title.lower().strip(),
        "company": job.company.lower().strip(),
        # Don't include description — it varies slightly between platforms
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()[:16]
```

**Method 3: Fuzzy dedup with `rapidfuzz`** (catches typos and slight variations):
```python
from rapidfuzz import fuzz

def is_duplicate(new_job: Job, existing_jobs: list[Job], threshold=85) -> bool:
    for existing in existing_jobs:
        title_sim = fuzz.token_sort_ratio(new_job.title, existing.title)
        company_sim = fuzz.ratio(new_job.company, existing.company)
        if title_sim > threshold and company_sim > threshold:
            return True
    return False
```

Fuzzy matching is expensive at scale. Cache recent jobs in memory and only fuzzy-match against jobs from the last 7 days.

**Recommended for personal use:** Platform ID as primary key + content hash stored for cross-platform awareness. Query `SELECT * FROM jobs WHERE content_hash = ?` before alerting to suppress cross-platform duplicates.

---

## 9. Resume Matching & Ranking Logic

### 9.1 Why You Need This

Without scoring, you get 100–200 jobs/day and need to manually review all of them. With scoring, you get 5–10 alerts/day for jobs you actually care about. This is the highest-leverage single feature.

### 9.2 Approach 1: TF-IDF Cosine Similarity (Baseline, Fast, Offline)

**How it works:** Converts your resume and each job description into term-frequency vectors, then computes cosine similarity. No LLM, no API calls, runs instantly.

```python
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

class TFIDFMatcher:
    def __init__(self, resume_text: str):
        self.vectorizer = TfidfVectorizer(
            ngram_range=(1, 2),  # unigrams + bigrams
            stop_words="english",
            max_features=10000
        )
        self.resume_vec = self.vectorizer.fit_transform([resume_text])

    def score(self, job_description: str) -> float:
        job_vec = self.vectorizer.transform([job_description])
        return float(cosine_similarity(self.resume_vec, job_vec)[0][0])
```

**Tradeoff:** Keyword-matching only. Doesn't understand semantics — "LLM engineer" and "large language model developer" will score differently. Good as a fast pre-filter.

### 9.3 Approach 2: Sentence Transformers (Semantic Similarity, Best OSS Option)

**How it works:** Uses a pre-trained transformer model to encode text into dense embeddings, then computes cosine similarity in embedding space. "LLM engineer" and "large language model developer" produce similar embeddings.

```python
from sentence_transformers import SentenceTransformer
import numpy as np

class SemanticMatcher:
    def __init__(self, resume_text: str):
        self.model = SentenceTransformer("all-MiniLM-L6-v2")  # 80MB, fast
        self.resume_embedding = self.model.encode(resume_text, normalize_embeddings=True)

    def score(self, job_description: str) -> float:
        job_embedding = self.model.encode(job_description, normalize_embeddings=True)
        return float(np.dot(self.resume_embedding, job_embedding))  # cosine similarity (both normalized)

    def batch_score(self, descriptions: list[str]) -> list[float]:
        job_embeddings = self.model.encode(descriptions, normalize_embeddings=True, batch_size=32, show_progress_bar=False)
        return [float(np.dot(self.resume_embedding, e)) for e in job_embeddings]
```

**Model choices:**
- `all-MiniLM-L6-v2`: 80MB, fastest, good quality. Recommended.
- `all-mpnet-base-v2`: 420MB, higher quality, slower.
- `BAAI/bge-small-en-v1.5`: 130MB, optimized for retrieval tasks, excellent for this use case.
- `thenlper/gte-small`: Similar to BGE, strong performance.

All run entirely locally, no API calls, no cost.

**Scoring interpretation:** Scores typically range 0.2–0.7. Set alert threshold at ~0.45 and adjust based on false positives/negatives.

### 9.4 Approach 3: LLM Scoring (Most Intelligent, Has Cost)

For jobs that pass the semantic filter (score > 0.4), you can run them through an LLM for a nuanced match score and explanation.

```python
import anthropic

client = anthropic.Anthropic()

MATCH_PROMPT = """
You are evaluating job fit for a candidate. Score on a scale 0-100 and explain briefly.

RESUME SUMMARY:
{resume_summary}

JOB POSTING:
Title: {title}
Company: {company}
Description: {description}

Return JSON: {{"score": int, "strengths": ["..."], "gaps": ["..."], "verdict": "apply|skip|maybe"}}
"""

def llm_score(job: Job, resume_summary: str) -> dict:
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",  # cheapest, fast
        max_tokens=300,
        messages=[{"role": "user", "content": MATCH_PROMPT.format(
            resume_summary=resume_summary,
            title=job.title, company=job.company,
            description=job.description_clean[:2000]  # truncate
        )}]
    )
    import json
    return json.loads(response.content[0].text)
```

**Cost:** Claude Haiku at ~$0.25/M input tokens. If you process 50 jobs/day post-filter, that's ~100K tokens/day → ~$0.025/day → ~$9/year. Negligible.

**Recommended pipeline:** TF-IDF quick filter (score > 0.15) → Semantic filter (score > 0.40) → LLM score for top candidates → alert on LLM score > 70.

### 9.5 Skill Extraction

Augment semantic scoring with explicit skill matching:

```python
# From codebasics/job-scrapper: 977 skills with regex patterns
# Or build your own from your resume

YOUR_SKILLS = {
    "python", "fastapi", "flask", "langchain", "llm", "large language model",
    "rag", "retrieval augmented generation", "faiss", "vector database",
    "aws lambda", "docker", "azure", "postgresql", "redis",
    "sentence transformers", "hugging face", "pytorch", "nlp",
    "machine learning", "deep learning", "bert", "gpt", "openai",
    "prompt engineering", "agentic", "voice ai", "stt", "tts", "deepgram",
    "elevenlabs", "crm", "automation", "rest api"
}

def skill_overlap_score(job: Job) -> float:
    desc_lower = (job.title + " " + job.description_clean).lower()
    matched = sum(1 for skill in YOUR_SKILLS if skill in desc_lower)
    return matched / len(YOUR_SKILLS)
```

**Composite score:**
```python
def composite_score(job: Job, matcher: SemanticMatcher) -> float:
    semantic = matcher.score(job.description_clean)
    skill_overlap = skill_overlap_score(job)
    # Weighted average
    return 0.7 * semantic + 0.3 * skill_overlap
```

---

## 10. Alerting & Notification Systems

### 10.1 Telegram Bot (Recommended — Best UX for Personal Use)

**Why Telegram:** Free, reliable, excellent Python library, supports inline buttons (mark as applied/skip directly from the message), markdown formatting, instant delivery, no email spam folder.

**Setup:**
1. Chat with `@BotFather` → `/newbot` → get API token
2. Start a chat with your bot → get your `chat_id` from `https://api.telegram.org/bot{TOKEN}/getUpdates`

```python
import httpx

class TelegramAlerter:
    def __init__(self, token: str, chat_id: str):
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id

    def send_job_alert(self, job: Job, score: float, strengths: list[str]):
        remote_tag = "🌐 Remote" if job.is_remote else f"📍 {job.location.split(',')[0]}"
        score_bar = "🟩" * int(score / 10) + "⬜" * (10 - int(score / 10))
        
        text = f"""🔔 *New Job Match* — Score: {score}/100

*{job.title}*
🏢 {job.company} | {remote_tag}
📅 Posted: {job.posted_at.strftime('%b %d') if job.posted_at else 'Unknown'}
💰 {f'{job.salary_min//100000}–{job.salary_max//100000} LPA' if job.salary_min else 'Salary not specified'}

{score_bar}

✅ *Strengths:* {', '.join(strengths[:3])}

[Apply Here]({job.apply_url}) | Source: {job.platform}"""

        inline_keyboard = {
            "inline_keyboard": [[
                {"text": "✅ Applied", "callback_data": f"applied:{job.id}"},
                {"text": "❌ Skip", "callback_data": f"skip:{job.id}"},
                {"text": "🔖 Save", "callback_data": f"save:{job.id}"},
            ]]
        }
        
        httpx.post(f"{self.base_url}/sendMessage", json={
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "reply_markup": inline_keyboard,
            "disable_web_page_preview": False
        })
```

### 10.2 Email Alerts (Fallback)

```python
import smtplib
from email.mime.text import MIMEText

def send_email_digest(jobs: list[Job], to_email: str):
    # Use Gmail app password (not main password) or SendGrid free tier
    body = "\n\n".join([
        f"[{j.title}] at {j.company} — Score: {j.match_score:.0%}\n{j.apply_url}"
        for j in jobs
    ])
    msg = MIMEText(body)
    msg["Subject"] = f"Job Digest: {len(jobs)} new matches"
    msg["From"] = "yourbot@gmail.com"
    msg["To"] = to_email
    
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login("yourbot@gmail.com", GMAIL_APP_PASSWORD)
        server.send_message(msg)
```

### 10.3 Slack (If You Live in Slack)

```python
import httpx

def send_slack_alert(job: Job, score: float, webhook_url: str):
    httpx.post(webhook_url, json={
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": f"🔔 {job.title} — {score}/100"}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Company:* {job.company}"},
                {"type": "mrkdwn", "text": f"*Location:* {job.location}"},
            ]},
            {"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "Apply"}, "url": job.apply_url}
            ]}
        ]
    })
```

---

## 11. Scheduling & Automation Workflows

### 11.1 Cron Jobs (Simplest)

**What:** Unix cron is a time-based job scheduler that runs commands at specified intervals.

**Why use it:** Zero dependencies, always available on Linux/macOS, no overhead. Perfect for simple periodic tasks.

```crontab
# Run every 6 hours, 7am–10pm
0 7,13,19 * * * /home/user/.venv/bin/python /home/user/jobsearch/main.py >> /home/user/jobsearch/logs/scrape.log 2>&1

# Run daily at 9am
0 9 * * * /home/user/.venv/bin/python /home/user/jobsearch/main.py
```

**Tradeoff:** No error handling, no retry logic, no dashboard, no alerting on failure. If cron silently fails, you won't know. Add health checks:

```python
# At end of main.py
import httpx
# Ping healthchecks.io (free tier) — if this doesn't fire, you get an email
httpx.get("https://hc-ping.com/YOUR-UUID", timeout=5)
```

### 11.2 APScheduler (Python-native, In-Process)

```python
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

scheduler = BlockingScheduler()

@scheduler.scheduled_job(IntervalTrigger(hours=6))
def run_job_search():
    try:
        jobs = scrape_all_platforms()
        new_jobs = deduplicate_and_store(jobs)
        score_and_alert(new_jobs)
        logger.info(f"Found {len(new_jobs)} new jobs")
    except Exception as e:
        logger.error(f"Scrape failed: {e}")
        send_error_alert(str(e))  # Telegram you about the failure

scheduler.start()
```

**Why use APScheduler over cron:** Stays in-process (no subprocess startup overhead), handles errors with callbacks, supports more complex schedules (e.g., "every 4 hours between 8am and 10pm"), stores job state in memory.

**Tradeoff:** Stops when your machine sleeps/reboots. Fine for a personal laptop that's usually on. For 24/7 operation, combine with systemd or run on a cheap VPS.

### 11.3 n8n (No-Code Orchestration)

**What it is:** Open-source workflow automation (like Zapier, but self-hosted, free, and code-capable). You visually wire together nodes: HTTP Request → Code → SQLite → Telegram.

**Why it's relevant:** Pre-built LinkedIn scraping workflows exist in the n8n community template library. You can import a workflow JSON, configure your credentials, and have a working job alert system in an hour without writing scraper code.

**Real workflow from the community** (GitHub: AloysJehwin/job-app):
- Accepts resume upload
- Calls Apify API for Naukri scraping
- Rewrites resume with DeepSeek via LangChain
- Stores in Google Drive + Google Sheets
- All in a visual no-code workflow

**When to use n8n:** If you want visual orchestration, easy connection to Google Sheets/Notion/Telegram without writing integration code. The downside: less control over scraping quality, harder to debug, another thing to maintain.

**Self-host with Docker:**
```yaml
# docker-compose.yml
services:
  n8n:
    image: n8nio/n8n
    ports:
      - "5678:5678"
    volumes:
      - n8n_data:/home/node/.n8n
    environment:
      - N8N_BASIC_AUTH_ACTIVE=true
      - N8N_BASIC_AUTH_USER=admin
      - N8N_BASIC_AUTH_PASSWORD=yourpassword
```

### 11.4 Cron vs APScheduler vs n8n

| | Cron | APScheduler | n8n |
|---|---|---|---|
| Setup effort | Minimal | Low | Medium |
| Observability | None | Logging | Visual |
| Error handling | None built-in | Built-in callbacks | Visual |
| Flexibility | High | High | Medium |
| Dependencies | 0 | 1 pip package | Docker |
| Best for | Simple, rock-solid | Python-native | Visual orchestration |

**Recommendation:** Start with cron + healthchecks.io. Upgrade to APScheduler if you want retry logic. Skip n8n unless you prefer visual workflows.

---

## 12. Proxy, Cookie & User-Agent Handling

### 12.1 Do You Even Need Proxies?

For personal use running from a home residential IP: **probably not.** Your home IP is inherently residential, which is what proxy services charge premium for.

You only need proxies if:
- You're running from a cloud server (VPS, Lambda, etc.) — datacenter IP gets flagged
- You're hitting rate limits even with polite delays
- You need to appear as different geographic locations

### 12.2 Free Proxy Sources (Avoid for Production)

Free proxy lists (`proxyscrape.com`, `free-proxy-list.net`) are 90% dead, 9% slow, 1% useful. Never use them for anything reliability-sensitive.

### 12.3 Residential Proxy Services (If Needed)

| Service | Cost | Notes |
|---|---|---|
| Bright Data | ~$15/GB | Most reliable, expensive |
| Oxylabs | ~$15/GB | Enterprise grade |
| Smartproxy | ~$7/GB | Good for personal use |
| **ProxyScrape Premium** | ~$3/GB | Budget option, decent quality |
| **Geonode** | ~$5/GB | Budget |

For personal use hitting 4 platforms 4x/day at ~1MB/scrape: ~16MB/day → ~500MB/month → ~$2.50–7.50/month.

### 12.4 Proxy Rotation in Python

```python
import random

PROXIES = [
    "http://user:pass@proxy1:port",
    "http://user:pass@proxy2:port",
]

def get_session_with_proxy():
    proxy = random.choice(PROXIES)
    session = requests.Session()
    session.proxies = {"http": proxy, "https": proxy}
    return session
```

### 12.5 User-Agent Rotation

Use a realistic, updated UA list:
```python
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

headers = {
    "User-Agent": random.choice(USER_AGENTS),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}
```

---

## 13. CAPTCHA Handling

### 13.1 Types of CAPTCHAs

| Type | Examples | Difficulty to Bypass |
|---|---|---|
| reCAPTCHA v2 checkbox | Google's "I'm not a robot" | Medium — solvable |
| reCAPTCHA v3 (score-based) | Invisible, JS-based | Hard — need perfect behavioral signals |
| hCaptcha | Used by Cloudflare | Medium |
| Cloudflare Turnstile | Modern replacement for hCaptcha | Medium |
| Image CAPTCHAs | Old-style text/image | Easy — AI solves well |
| FunCAPTCHA | Arkose Labs | Hard |

### 13.2 Approach 1: Avoid Triggering CAPTCHAs

The best CAPTCHA bypass is not hitting one:
- Run from residential IP (home machine)
- Slow down requests (3–7s between pages)
- Reuse authenticated session cookies
- Don't scrape during peak hours (3–5pm IST when bot detection is most aggressive)

### 13.3 Approach 2: 2captcha / Anti-Captcha (Paid, $0.001/solve)

**2captcha.com** provides an API where you submit the CAPTCHA image/sitekey and get the solution token back.

```python
import requests, time

def solve_recaptcha(site_key: str, page_url: str, api_key: str) -> str:
    # Submit CAPTCHA
    resp = requests.post("http://2captcha.com/in.php", data={
        "key": api_key, "method": "userrecaptcha",
        "googlekey": site_key, "pageurl": page_url, "json": 1
    }).json()
    captcha_id = resp["request"]
    
    # Poll for solution (takes 15–30s)
    for _ in range(20):
        time.sleep(5)
        result = requests.get(
            f"http://2captcha.com/res.php?key={api_key}&action=get&id={captcha_id}&json=1"
        ).json()
        if result["status"] == 1:
            return result["request"]
    raise TimeoutError("CAPTCHA solve timeout")

# Inject the token into the page
page.evaluate(f'document.getElementById("g-recaptcha-response").innerHTML = "{token}"')
page.click('button[type="submit"]')
```

**Cost:** $1 per 1000 reCAPTCHA solves → essentially free for personal use.

**OSS CAPTCHA solvers** (quality varies): `CapMonster` (self-hosted), `capsolver.com`.

### 13.4 Approach 3: SeleniumBase UC Mode

SeleniumBase's Undetected ChromeDriver mode handles many CAPTCHAs automatically by mimicking human browser signals:

```python
from seleniumbase import SB

with SB(uc=True, headless=False) as sb:
    sb.open("https://www.linkedin.com/jobs/search/")
    sb.sleep(3)
    # Often passes Cloudflare Turnstile automatically
```

---

## 14. Deployment Methods

### 14.1 Local Script + Cron (Recommended for Personal Use)

**Pros:** Free, zero infrastructure, your residential IP is inherently less suspicious, no cloud setup.

**Cons:** Machine must be on; no 24/7 coverage; harder to monitor.

```bash
# ~/.local/share/jobsearch/venv/bin/python /home/you/jobsearch/main.py
# Add to crontab: crontab -e
0 8,14,20 * * * /home/you/jobsearch/.venv/bin/python /home/you/jobsearch/main.py 2>&1 | tee -a /home/you/jobsearch/logs/$(date +\%Y-\%m-\%d).log
```

**Systemd timer** (more reliable than cron on Linux, survives reboots better):
```ini
# /etc/systemd/system/jobsearch.timer
[Unit]
Description=Job Search Scraper Timer

[Timer]
OnBootSec=5min
OnUnitActiveSec=6h

[Install]
WantedBy=timers.target
```

### 14.2 VPS Deployment ($3–6/month)

**When to use:** You want 24/7 operation without keeping your laptop on. Best option for reliability.

**Cheapest options:**
- **Hetzner Cloud CX11:** €3.29/month, 2 vCPU, 2GB RAM, Germany-based
- **Oracle Cloud Free Tier:** Actually free forever — 1 OCPU, 1GB RAM. Surprising but legitimate.
- **fly.io Free Tier:** 3 shared VMs free, works well for lightweight scripts
- **Railway:** $5 free credits/month, auto-deploys from GitHub

**Setup on Hetzner/Oracle:**
```bash
# Install Python 3.11+, clone repo, setup venv, install deps
# Add cron job as above
# Install chromium for Playwright: playwright install chromium
# Use systemd timer for reliability
```

**Playwright on VPS** requires additional packages:
```bash
playwright install-deps chromium
```

### 14.3 GitHub Actions (Free, Scheduled)

Use GitHub Actions for zero-infrastructure scheduled scraping. 2000 free minutes/month on public repos (500 on private).

```yaml
# .github/workflows/scrape.yml
name: Job Search Scraper
on:
  schedule:
    - cron: '0 3,9,15 * * *'  # 3am, 9am, 3pm UTC = 8:30am, 2:30pm, 8:30pm IST
  workflow_dispatch:  # Manual trigger

jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
      - run: pip install -r requirements.txt
      - run: playwright install chromium
      - run: python main.py
        env:
          TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
```

**Problem:** No persistent storage — SQLite resets every run, so you can't track which jobs you've already seen. **Solution:** Commit the SQLite file back to the repo, or use GitHub Actions artifacts, or store only job IDs in a simple text file, or use a lightweight cloud DB (Turso — free SQLite in the cloud).

### 14.4 AWS Lambda (Serverless)

You already know Lambda from your Nurix work. For scraping without a browser, Lambda is excellent. For Playwright-based scraping, you need a custom Lambda layer with Chromium.

**`playwright-aws-lambda`** package provides a pre-built Chromium binary that fits within Lambda's 250MB unzipped limit:
```python
import asyncio
from playwright.async_api import async_playwright
import boto3

async def handler(event, context):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            args=["--disable-gpu", "--no-sandbox", "--single-process"]
        )
        # ... scrape
```

**Cost:** Lambda free tier is 1M requests + 400K GB-seconds/month. For 3 scrapes/day at 30s each = 90 invocations/day → essentially free.

---

## 15. Monitoring & Maintenance

### 15.1 What Breaks (And When)

| What breaks | Why | How often | Fix |
|---|---|---|---|
| CSS selectors | Platform redesigns HTML | Every 2–4 months | Re-inspect DevTools |
| API endpoints | Platform migrates internally | Every 6–12 months | Reverse-engineer again |
| Session cookies | Expire | Every 30–365 days | Re-export from browser |
| Bot detection | Platform updates anti-bot | Every 1–3 months | Update stealth config |
| Python dependencies | Breaking changes in `playwright`, `httpx` | Rare | Pin versions in requirements.txt |

### 15.2 Minimal Monitoring

```python
import logging, functools, time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/jobsearch.log"),
        logging.StreamHandler()
    ]
)

def with_retry(max_attempts=3, delay=60):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        logging.error(f"{func.__name__} failed after {max_attempts} attempts: {e}")
                        raise
                    logging.warning(f"{func.__name__} failed (attempt {attempt+1}): {e}. Retrying in {delay}s")
                    time.sleep(delay)
        return wrapper
    return decorator

@with_retry(max_attempts=3, delay=30)
def scrape_naukri(query, location):
    # ... scraping code
```

### 15.3 Health Checks

```python
# healthchecks.io — free tier, 20 checks
import httpx, os

HEALTHCHECK_URL = os.getenv("HEALTHCHECK_URL")  # https://hc-ping.com/YOUR-UUID

def ping_healthcheck(start=False):
    suffix = "/start" if start else ""
    try:
        httpx.get(f"{HEALTHCHECK_URL}{suffix}", timeout=5)
    except: pass  # Don't let monitoring failure break the scraper

# At start of run
ping_healthcheck(start=True)
# ... scraping ...
# At successful end
ping_healthcheck()
```

If the end ping doesn't fire within 2x the schedule interval, healthchecks.io sends you an email.

---

## 16. Key GitHub Projects Analyzed

### 16.1 `speedyapply/JobSpy` ⭐⭐⭐⭐⭐
**URL:** https://github.com/speedyapply/JobSpy
**Stars:** ~20,000+ | **Status:** Actively maintained (commits weekly)
**What it does:** Single Python library that scrapes LinkedIn, Indeed, Glassdoor, Google Jobs, ZipRecruiter, Naukri, Bayt concurrently. Returns a normalized pandas DataFrame.

```python
from jobspy import scrape_jobs
jobs = scrape_jobs(
    site_name=["indeed", "linkedin", "naukri"],
    search_term="machine learning engineer",
    location="Bengaluru, India",
    results_wanted=50,
    hours_old=168,  # last 7 days
    country_indeed="India",
)
```

**Architecture:** Each site has its own module using direct HTTP + `curl_cffi` for TLS impersonation. No browser required for most sites. Naukri support was added in 2024.

**Strengths:** Dramatically reduces scraping complexity; handles deduplication within a single run; actively maintained; the most practical starting point.

**Weaknesses:** No persistence/storage layer; no scoring; no alerting; depends on maintainers to fix when platforms change. Won't cover Wellfound, Hirist, or Instahyre without custom additions.

**Verdict:** **Use this as your foundation.** It replaces 60% of the scraping work you'd otherwise write yourself. Add Wellfound/Hirist/Instahyre as custom modules.

### 16.2 `codebasics/job-scrapper` ⭐⭐⭐⭐
**URL:** https://github.com/codebasics/job-scrapper
**Stars:** ~500 | **Status:** Active
**What it does:** Full stack: Playwright-based scrapers for LinkedIn and Naukri, SQLite storage, skill extraction with 977 skills regex patterns, Streamlit dashboard.

**Architecture:** Playwright → BeautifulSoup → SQLite → Streamlit. Cookie-based auth (`save_linkedin_cookies.py`). 977-skill regex map is genuinely useful.

**Strengths:** Most complete end-to-end implementation. The skills JSON (`skills_reference_2025.json`) and roles JSON are highly reusable.

**Weaknesses:** Uses Playwright for Naukri (overkill — it has a JSON API); Streamlit dashboard is optional complexity.

**What to reuse:** The skills/roles JSON files. The cookie-save pattern. The SQLite schema.

### 16.3 `cwwmbm/linkedinscraper` ⭐⭐⭐
**URL:** https://github.com/cwwmbm/linkedinscraper
**Stars:** ~200 | **Status:** Active
**What it does:** LinkedIn-only scraper with Flask web UI, SQLite storage, OpenAI integration for cover letters, cron scheduling.

**Architecture:** HTTP requests → JSON parsing → SQLite → Flask UI. Rounds-based scraping (runs multiple times to catch all results since LinkedIn randomizes results).

**Strengths:** Good production patterns (multiple rounds, cron-ready).

**Weaknesses:** Only LinkedIn; OpenAI integration adds cost; Flask UI is unnecessary complexity for personal use.

### 16.4 `srbhr/Resume-Matcher` ⭐⭐⭐⭐
**URL:** https://github.com/srbhr/Resume-Matcher
**Stars:** ~7,000 | **Status:** Very active
**What it does:** Takes your master resume and scores it against job descriptions. Supports Ollama (local LLMs) and OpenAI/Anthropic APIs.

**Architecture:** PDF parsing → LLM analysis → match score + keyword gaps + improvement suggestions → PDF export.

**Strengths:** Actively maintained, beautiful output, works fully offline with Ollama.

**What to reuse:** The prompt engineering for resume matching. The PDF parsing workflow.

**Integration:** Run this as a post-processing step on your top-scored jobs from the semantic filter.

### 16.5 `gopiashokan/AI-Resume-Analyzer-and-LinkedIn-Scraper` ⭐⭐⭐
**URL:** https://github.com/gopiashokan/AI-Resume-Analyzer-and-LinkedIn-Scraper-using-Generative-AI
**Stars:** ~100 | **Status:** Moderate
**What it does:** RAG-based resume analysis + Selenium LinkedIn scraping + recommendation engine.

**Strengths:** Shows how to combine RAG with job scraping end-to-end.

**Weaknesses:** Uses Selenium (outdated), OpenAI-only, not actively maintained.

### 16.6 n8n Workflow Templates
**Community templates:** n8n.io/workflows (search "job")
Pre-built workflows from the community:
- LinkedIn + OpenAI + Telegram: Daily LinkedIn scrape → AI match against resume → Telegram alert
- Naukri via Apify + Google Sheets: No-code version of the full pipeline
- Gemini AI + Notion tracking: Scores jobs, writes to Notion DB, Telegram alerts

**When to use:** If you prefer visual workflow editing over code. The n8n community has solved most of the integration problems already.

---

## 17. Comparison Matrix

### Browser Automation vs Direct HTTP

| Dimension | Browser (Playwright) | Direct HTTP (`curl_cffi`/`requests`) |
|---|---|---|
| Speed | 5–30 req/min | 100–500 req/min |
| Memory | 200–500MB per browser | <50MB |
| Detectability | Medium (with stealth patches) | Low (with TLS impersonation) |
| JS-heavy sites | ✅ Works | ❌ Fails |
| JSON API sites | Overkill but works | ✅ Ideal |
| Setup complexity | Medium | Low |
| Maintenance | High (stealth patches update) | Low |

**Decision:** Use direct HTTP (`JobSpy`/`curl_cffi`) for Naukri, Indeed, Hirist, Instahyre. Use Playwright only for LinkedIn (which has the strongest JS-based bot detection).

### Python vs Node.js for Scraping

| | Python | Node.js |
|---|---|---|
| Ecosystem | `requests`, `httpx`, `curl_cffi`, `bs4`, `jobspy`, `sentence-transformers` | `axios`, `puppeteer`, `cheerio`, `linkedin-api` |
| ML/NLP | ✅ Unbeatable | ❌ Limited |
| Playwright quality | ✅ Excellent | ✅ Excellent |
| Solo developer speed | ✅ Faster iteration | Slower |

**Verdict:** Python, no contest, because of `sentence-transformers`, `JobSpy`, and the broader ML ecosystem.

### SQLite vs PostgreSQL

For personal job search: **SQLite wins, definitively.** No network overhead, no process to manage, files are portable, and SQLite FTS5 handles full-text search on millions of rows.

Use PostgreSQL only if you decide to share the system with others or need concurrent writes from multiple machines.

### Local vs Cloud Deployment

| | Local (Laptop/Raspi) | VPS (Hetzner/Oracle) | GitHub Actions | AWS Lambda |
|---|---|---|---|---|
| Cost | Free | $0–6/month | Free (2K min/month) | Free |
| Reliability | Depends on uptime | High | High | High |
| IP reputation | ✅ Residential | ⚠️ Datacenter | ⚠️ Datacenter | ⚠️ Datacenter |
| Playwright support | ✅ Full | ✅ Full | ✅ Full (ubuntu) | Limited |
| Setup effort | Minimal | Low | Low | Medium |

**Verdict for personal use:** Run locally first. If you want 24/7, Oracle Cloud Free Tier VPS.

---

## 18. Recommended Architecture for Your Use Case

Given your profile (Python/FastAPI/AWS experience, AI/ML background, personal use only, efficiency over perfection), here is the exact architecture I'd build:

```
project/
├── main.py                 # Entry point: runs all scrapers → normalize → score → alert
├── scrapers/
│   ├── jobspy_scraper.py   # LinkedIn + Indeed + Naukri via JobSpy
│   ├── wellfound.py        # GraphQL API scraper
│   ├── hirist.py           # Direct HTTP
│   └── instahyre.py        # Direct HTTP + cookies
├── pipeline/
│   ├── normalizer.py       # Platform-specific → Job dataclass
│   ├── dedup.py            # Content hash + SQLite insert-or-ignore
│   ├── scorer.py           # Semantic + skill + LLM scoring
│   └── alerter.py          # Telegram alerts with inline buttons
├── db.py                   # SQLite connection + schema
├── config.py               # Search params, thresholds, credentials
├── jobs.db                 # SQLite database
└── requirements.txt
```

**The full pipeline in `main.py`:**

```python
from scrapers.jobspy_scraper import scrape_jobspy
from scrapers.wellfound import scrape_wellfound
from scrapers.hirist import scrape_hirist
from pipeline.normalizer import normalize_all
from pipeline.dedup import filter_new_jobs, store_jobs
from pipeline.scorer import SemanticMatcher, composite_score, llm_score
from pipeline.alerter import TelegramAlerter
from config import CONFIG
import logging

log = logging.getLogger(__name__)

def main():
    # 1. Scrape
    raw_jobs = []
    raw_jobs.extend(scrape_jobspy(CONFIG["search_term"], CONFIG["location"]))
    raw_jobs.extend(scrape_wellfound(CONFIG["search_term"]))
    raw_jobs.extend(scrape_hirist(CONFIG["search_term"]))

    # 2. Normalize
    jobs = normalize_all(raw_jobs)
    log.info(f"Scraped {len(jobs)} total jobs")

    # 3. Deduplicate + Store
    new_jobs = filter_new_jobs(jobs)  # returns only jobs not in DB
    store_jobs(new_jobs)
    log.info(f"{len(new_jobs)} new jobs after dedup")

    if not new_jobs:
        return

    # 4. Score (semantic + skill overlap)
    matcher = SemanticMatcher(CONFIG["resume_text"])
    for job in new_jobs:
        job.match_score = composite_score(job, matcher)

    # 5. Filter by threshold
    candidates = [j for j in new_jobs if j.match_score > CONFIG["score_threshold"]]
    candidates.sort(key=lambda j: j.match_score, reverse=True)
    log.info(f"{len(candidates)} jobs above threshold {CONFIG['score_threshold']}")

    # 6. LLM score top candidates
    alerter = TelegramAlerter(CONFIG["telegram_token"], CONFIG["telegram_chat_id"])
    for job in candidates[:20]:  # cap at 20 LLM calls per run
        llm_result = llm_score(job, CONFIG["resume_summary"])
        if llm_result["score"] >= CONFIG["llm_alert_threshold"]:
            alerter.send_job_alert(job, llm_result["score"], llm_result["strengths"])
            mark_alerted(job.id)

if __name__ == "__main__":
    main()
```

---

## 19. What NOT to Build

These components seem useful but are actually over-engineering for personal use:

**Don't build:**
- A REST API frontend (you have no external consumers)
- User authentication (you're the only user)
- A React/Next.js dashboard (Streamlit in 30 lines or just view the SQLite file)
- Docker for local scripts (just use a venv)
- Message queues / Redis (no concurrency needed at personal scale)
- A custom ORM (use raw SQLite3 or a single SQLAlchemy model)
- An elaborate proxy rotation system (your home IP is fine)
- Real-time streaming (batch scraping every 6 hours is sufficient)
- ML model training (pre-trained sentence transformers are better than anything you'd train)
- A cover letter automation pipeline (high effort, low reliability — write them yourself, use AI interactively instead of automating)
- Auto-apply bots (high risk of getting banned; one-click Apply within platforms is fast enough)

**The most important principle:** The faster you get to your first Telegram alert with a real job match, the better. Every hour you spend on infra is an hour not job hunting.

---

## 20. The Minimum Viable Implementation (MVI)

This is the fastest path from zero to working system. You can have this running in 2–4 hours:

### Phase 1 (Day 1, ~2 hours): Basic pipeline

```bash
pip install jobspy sentence-transformers httpx python-telegram-bot python-dotenv
```

```python
# main_v1.py — minimum viable job search bot
import hashlib, sqlite3, json, os
from datetime import datetime
from jobspy import scrape_jobs
from sentence_transformers import SentenceTransformer
import numpy as np
import httpx
from dotenv import load_dotenv

load_dotenv()

RESUME = """
Software Development Engineer Intern at Bespoke Technology (Nurix AI client).
Built conversational AI voice agents integrating STT (Deepgram), LLM (GPT-4.1), TTS (ElevenLabs).
Designed serverless Python pipelines on AWS Lambda for CRM automation.
Skills: Python, FastAPI, LangChain, RAG, FAISS, Hugging Face, Docker, AWS Lambda, PostgreSQL, Redis.
Projects: GeoLLM (FastAPI + Google Earth Engine + FAISS), Prompt2Shell (QLoRA fine-tuning on Phi-3-mini).
"""

SEARCH_TERMS = ["machine learning engineer", "LLM engineer", "AI engineer", "NLP engineer"]
LOCATION = "Bengaluru, India"
SCORE_THRESHOLD = 0.42

# Setup
model = SentenceTransformer("all-MiniLM-L6-v2")
resume_embedding = model.encode(RESUME, normalize_embeddings=True)
db = sqlite3.connect("jobs.db")
db.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        title TEXT, company TEXT, location TEXT,
        is_remote INTEGER, apply_url TEXT, description TEXT,
        platform TEXT, posted_at TEXT, scraped_at TEXT,
        match_score REAL, alerted INTEGER DEFAULT 0
    )
""")
db.commit()

def semantic_score(description: str) -> float:
    emb = model.encode(description or "", normalize_embeddings=True)
    return float(np.dot(resume_embedding, emb))

def alert_telegram(job, score: float):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    msg = f"""🔔 *{job['title']}* — {score:.0%} match
🏢 {job['company']} | {'🌐 Remote' if job.get('is_remote') else job.get('location','')[:30]}
📅 {job.get('date_posted','')[:10]}
[Apply]({job['job_url']}) — {job.get('site','')}"""
    httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
               json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"})

def run():
    for term in SEARCH_TERMS:
        print(f"Scraping: {term}")
        try:
            jobs_df = scrape_jobs(
                site_name=["indeed", "linkedin", "naukri"],
                search_term=term, location=LOCATION,
                results_wanted=30, hours_old=168
            )
        except Exception as e:
            print(f"JobSpy error for '{term}': {e}")
            continue

        for _, row in jobs_df.iterrows():
            job = row.to_dict()
            job_id = f"{job.get('site','?')}:{job.get('id', hashlib.md5(job.get('job_url','').encode()).hexdigest()[:8])}"
            
            # Skip if already seen
            if db.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone():
                continue
            
            score = semantic_score(str(job.get("description", "")))
            db.execute("""
                INSERT OR IGNORE INTO jobs (id,title,company,location,is_remote,apply_url,description,platform,posted_at,scraped_at,match_score)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (job_id, job.get("title"), job.get("company"), job.get("location"),
                  1 if job.get("is_remote") else 0, job.get("job_url"), str(job.get("description",""))[:5000],
                  job.get("site"), str(job.get("date_posted","")), datetime.now().isoformat(), score))
            db.commit()
            
            if score >= SCORE_THRESHOLD:
                print(f"  ✅ MATCH ({score:.0%}): {job.get('title')} @ {job.get('company')}")
                alert_telegram(job, score)
                db.execute("UPDATE jobs SET alerted=1 WHERE id=?", (job_id,))
                db.commit()
            else:
                print(f"  — skip ({score:.0%}): {job.get('title')} @ {job.get('company')}")

if __name__ == "__main__":
    run()
```

Add to crontab:
```
0 9,15,21 * * * cd /home/you/jobsearch && .venv/bin/python main_v1.py >> logs/run.log 2>&1
```

### Phase 2 (Week 2): Add Wellfound + Hirist + LLM scoring

Extend with the platform-specific scrapers described above. Add the LLM scoring step for top matches. This takes the system from "good enough" to "excellent."

### Phase 3 (Optional): Streamlit dashboard

```python
import streamlit as st, sqlite3, pandas as pd

conn = sqlite3.connect("jobs.db")
df = pd.read_sql("SELECT * FROM jobs ORDER BY match_score DESC LIMIT 200", conn)
st.title("Job Pipeline")
st.dataframe(df[["title","company","location","match_score","alerted","platform","posted_at"]])
```

That's the entire UI you need. Run with `streamlit run dashboard.py`.

---

## Quick Reference: Full Stack Decisions

| Component | Recommended Choice | Reasoning |
|---|---|---|
| **Primary scraping** | `JobSpy` library | Handles LinkedIn, Indeed, Naukri out-of-box |
| **Custom scrapers** | `curl_cffi` + direct JSON API | Faster, less detectable than Playwright |
| **Browser automation** | `Playwright` + `playwright-stealth` | Only when JS rendering is required |
| **Stealth** | `curl_cffi` TLS impersonation | Best for HTTP; `Camoufox` for browser |
| **Data model** | Python dataclass `Job` | Simple, type-safe, no ORM needed |
| **Storage** | SQLite (WAL mode) | Zero maintenance, sufficient performance |
| **Deduplication** | Platform ID + content hash | Catches same job from multiple platforms |
| **Resume matching** | `all-MiniLM-L6-v2` semantic | Fast, offline, 80MB model |
| **LLM scoring** | Claude Haiku (top candidates only) | ~$0/month at personal scale |
| **Alerting** | Telegram Bot | Best UX, free, inline buttons |
| **Scheduling** | cron + healthchecks.io | Simplest, most reliable |
| **Deployment** | Local laptop + cron | Free, residential IP, zero infra |
| **Monitoring** | healthchecks.io + Telegram error alerts | Free tier sufficient |

---

*Last updated: May 2026. Platform APIs change — always verify endpoints against DevTools before assuming they still work.*
