# Cursor Prompt: Automated Personal Job Search Aggregation System

> Paste this entire file as your first message in a Cursor Composer (Ctrl+I) session with Claude Sonnet as the model. It is a complete spec — Cursor will scaffold the full project from it.

---

## Project Overview

Build a **personal job search aggregation and alerting system** — a CLI Python pipeline (no web server, no frontend, no Docker required) that:

1. Scrapes job listings from LinkedIn, Naukri, Indeed, Wellfound, Hirist, and Instahyre
2. Normalizes all jobs into a single canonical data model
3. Deduplicates across platforms using content hashing
4. Scores each job against my resume using semantic similarity (sentence-transformers) + skill overlap
5. Runs top candidates through an LLM for detailed match scoring
6. Fires Telegram alerts with inline action buttons for high-match jobs
7. Runs on a cron/APScheduler schedule (every 6 hours)
8. Stores everything in SQLite with WAL mode

**This is personal-use only.** No auth system, no REST API, no multi-user architecture, no cloud infra required. Optimize for: minimum code, maximum reliability, lowest maintenance burden.

---

## Tech Stack (Non-Negotiable)

| Layer | Choice | Reason |
|---|---|---|
| Language | Python 3.11+ | ML ecosystem |
| Primary scraping | `jobspy` library | Handles LinkedIn/Indeed/Naukri/Glassdoor out-of-box |
| Custom scrapers | `curl_cffi` + direct JSON/GraphQL API | TLS fingerprint impersonation, no browser needed |
| Browser automation | `playwright` + `playwright-stealth` | Only if JS rendering required |
| Stealth/evasion | `curl_cffi` with `impersonate="chrome124"` | Avoids JA3 fingerprint detection |
| Semantic matching | `sentence-transformers` (`all-MiniLM-L6-v2`) | Fast, offline, 80MB |
| LLM scoring | Anthropic Claude Haiku via `anthropic` SDK | Cheap (~$9/year at personal scale) |
| Storage | SQLite with WAL mode + FTS5 | Zero maintenance, single file |
| Alerting | Telegram Bot API via `httpx` | Free, inline buttons, instant |
| Scheduling | `APScheduler` | In-process, retry callbacks, no cron management |
| Config | `.env` + `python-dotenv` | Simple, secure |
| Env management | `uv` (preferred) or `pip` + `venv` | |

---

## Project File Structure

Scaffold exactly this structure:

```
jobsearch/
├── main.py                     # Entry point: orchestrates full pipeline
├── config.py                   # All user config: search terms, resume, thresholds, credentials
├── db.py                       # SQLite connection, schema creation, query helpers
├── scrapers/
│   ├── __init__.py
│   ├── jobspy_scraper.py       # LinkedIn + Indeed + Naukri via jobspy library
│   ├── wellfound.py            # Wellfound GraphQL API scraper
│   ├── hirist.py               # Hirist.tech direct HTTP JSON scraper
│   └── instahyre.py            # Instahyre direct HTTP + BeautifulSoup scraper
├── pipeline/
│   ├── __init__.py
│   ├── models.py               # Job dataclass (canonical data model)
│   ├── normalizer.py           # Platform-specific raw data → Job dataclass
│   ├── dedup.py                # Content hashing, cross-platform deduplication
│   ├── scorer.py               # Semantic scoring + skill overlap + LLM scoring
│   └── alerter.py              # Telegram alerts with inline buttons
├── scheduler.py                # APScheduler setup (alternative to cron)
├── dashboard.py                # Optional: minimal Streamlit dashboard (read-only)
├── cookies/
│   └── .gitkeep                # Store platform cookies here (gitignored)
├── logs/
│   └── .gitkeep
├── .env.example                # Template for environment variables
├── .gitignore
├── requirements.txt
└── README.md
```

---

## Detailed Implementation Spec

### `pipeline/models.py` — Canonical Job Model

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass
class Job:
    id: str                          # "platform:external_id" e.g. "naukri:12345678"
    title: str
    company: str
    location: str
    is_remote: bool
    employment_type: str             # full_time | internship | contract | part_time
    description: str
    description_clean: str           # HTML-stripped, whitespace-normalized
    apply_url: str
    posted_at: Optional[datetime]
    scraped_at: datetime
    platform: str                    # linkedin | naukri | indeed | wellfound | hirist | instahyre
    salary_min: Optional[int] = None         # Annual INR
    salary_max: Optional[int] = None
    salary_currency: str = "INR"
    skills: list[str] = field(default_factory=list)
    experience_min: Optional[int] = None    # years
    experience_max: Optional[int] = None
    content_hash: str = ""           # SHA256[:16] of (title.lower + company.lower)
    match_score: Optional[float] = None     # 0.0–1.0 semantic score
    llm_score: Optional[int] = None         # 0–100 LLM score
    llm_verdict: Optional[str] = None       # "apply" | "maybe" | "skip"
    llm_strengths: list[str] = field(default_factory=list)
    llm_gaps: list[str] = field(default_factory=list)
    alerted: bool = False
    status: str = "new"              # new | seen | applied | rejected | saved
    raw: dict = field(default_factory=dict)
```

### `db.py` — SQLite Schema

Create the DB with this exact schema:

```sql
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    is_remote INTEGER DEFAULT 0,
    employment_type TEXT,
    description TEXT,
    description_clean TEXT,
    apply_url TEXT,
    posted_at TEXT,
    scraped_at TEXT NOT NULL,
    platform TEXT NOT NULL,
    salary_min INTEGER,
    salary_max INTEGER,
    salary_currency TEXT DEFAULT 'INR',
    skills TEXT,                  -- JSON array as text
    experience_min INTEGER,
    experience_max INTEGER,
    match_score REAL,
    llm_score INTEGER,
    llm_verdict TEXT,
    llm_strengths TEXT,           -- JSON array as text
    llm_gaps TEXT,                -- JSON array as text
    alerted INTEGER DEFAULT 0,
    status TEXT DEFAULT 'new',
    raw TEXT                      -- full JSON blob
);

CREATE INDEX IF NOT EXISTS idx_platform ON jobs(platform);
CREATE INDEX IF NOT EXISTS idx_posted_at ON jobs(posted_at);
CREATE INDEX IF NOT EXISTS idx_match_score ON jobs(match_score DESC);
CREATE INDEX IF NOT EXISTS idx_alerted ON jobs(alerted);
CREATE INDEX IF NOT EXISTS idx_content_hash ON jobs(content_hash);
CREATE INDEX IF NOT EXISTS idx_status ON jobs(status);

CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(
    title, company, description_clean, skills,
    content=jobs, content_rowid=rowid
);
```

Enable WAL mode and set pragmas on every connection:
```python
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
conn.execute("PRAGMA cache_size=10000")
conn.execute("PRAGMA temp_store=MEMORY")
```

DB helper functions to implement:
- `get_connection()` → returns connection with pragmas set
- `insert_job(job: Job)` → INSERT OR IGNORE
- `job_exists(job_id: str) → bool`
- `hash_exists(content_hash: str) → bool` — for cross-platform dedup
- `update_scores(job_id: str, match_score: float, llm_score: int, ...)` 
- `mark_alerted(job_id: str)`
- `update_status(job_id: str, status: str)`
- `get_jobs_for_review(limit=50) → list[Job]` — returns new high-score unalerted jobs

### `scrapers/jobspy_scraper.py`

Use the `jobspy` library. Implement:

```python
def scrape_jobspy(
    search_terms: list[str],
    location: str,
    hours_old: int = 168,       # 7 days
    results_per_term: int = 50,
) -> list[dict]:
```

- Iterate over each `search_term` in `search_terms`
- Call `scrape_jobs(site_name=["linkedin", "indeed", "naukri"], ...)`
- Catch all exceptions per term, log them, continue to next term
- Return deduplicated raw dicts (deduplicate by job_url within this function)
- Add a 3–5 second random sleep between terms to avoid rate limiting

Key `scrape_jobs` params to use:
```python
scrape_jobs(
    site_name=["indeed", "linkedin", "naukri"],
    search_term=term,
    location=location,
    results_wanted=results_per_term,
    hours_old=hours_old,
    country_indeed="India",
    linkedin_fetch_description=True,
)
```

### `scrapers/wellfound.py`

Wellfound uses GraphQL. Implement a direct GraphQL query scraper:

```python
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

def scrape_wellfound(search_terms: list[str], remote: bool = True) -> list[dict]:
```

- Load session cookies from `cookies/wellfound_cookies.json` if it exists
- Use `curl_cffi` with `impersonate="chrome124"` for all requests
- Include `x-csrf-token` header from cookies
- Return raw job dicts from `edges[].node`
- If auth fails or cookie file missing, log warning and return `[]` (graceful degradation)

### `scrapers/hirist.py`

Hirist has a simple JSON API:

```python
HIRIST_API = "https://www.hirist.tech/api/job/search"

def scrape_hirist(search_terms: list[str], location: str = "bangalore") -> list[dict]:
```

- Use `curl_cffi` with `impersonate="chrome124"`
- Query params: `q={term}&loc={location}&page=0`
- Paginate up to 3 pages if results exist
- Return raw job dicts
- Add 2–4 second sleep between pages

### `scrapers/instahyre.py`

Instahyre requires session cookies:

```python
def scrape_instahyre(search_terms: list[str]) -> list[dict]:
```

- Load cookies from `cookies/instahyre_cookies.json` if exists
- Use `httpx` with cookie jar
- Search endpoint: `https://www.instahyre.com/api/v1/opportunity/?format=json&q={term}&page=1`
- Parse JSON response, paginate up to 3 pages
- Gracefully return `[]` if cookies missing or auth fails

### `pipeline/normalizer.py`

Implement a normalizer class per platform:

```python
class JobSpyNormalizer:
    def normalize(self, raw: dict) -> Job: ...

class WellfoundNormalizer:
    def normalize(self, raw: dict) -> Job: ...

class HiristNormalizer:
    def normalize(self, raw: dict) -> Job: ...

class InstahyreNormalizer:
    def normalize(self, raw: dict) -> Job: ...

def normalize_all(raw_jobs: list[tuple[str, dict]]) -> list[Job]:
    """Takes list of (platform_name, raw_dict) tuples, returns normalized Jobs."""
```

Each normalizer must:
1. Map platform fields to `Job` dataclass fields
2. Clean description HTML via BeautifulSoup → plain text → normalize whitespace
3. Compute `content_hash`: `SHA256(title.lower().strip() + "|" + company.lower().strip())[:16]`
4. Parse salary strings into `salary_min`/`salary_max` int (annual INR)
5. Parse `posted_at` into `datetime` object
6. Set `id` as `"{platform}:{external_id}"`
7. Never raise — log parse errors and return partial data

Salary parsing helper (implement in `normalizer.py`):
```python
def parse_salary_inr(salary_str: str) -> tuple[int | None, int | None]:
    """Handles: '4-8 LPA', '₹40K/month', '8L–15L', '$10-15K' (convert to INR at 84 rate)"""
```

Description cleaner:
```python
def clean_description(raw: str) -> str:
    """Strip HTML, normalize whitespace, remove non-printable chars, max 5000 chars."""
```

### `pipeline/dedup.py`

```python
def filter_new_jobs(jobs: list[Job]) -> list[Job]:
    """
    Returns only jobs not already in the DB.
    Two-stage dedup:
    1. Check job.id (same platform, same external ID)
    2. Check job.content_hash (same title+company across platforms)
    For stage 2: if hash exists, log "cross-platform duplicate: {title} @ {company}" and skip.
    """

def store_jobs(jobs: list[Job]) -> int:
    """INSERT OR IGNORE all jobs. Returns count of actually inserted rows."""
```

### `pipeline/scorer.py`

Implement three scoring tiers:

**Tier 1: Keyword pre-filter** (instant, no model needed)
```python
REQUIRED_KEYWORDS = [
    "python", "machine learning", "ml", "ai", "llm", "nlp", "deep learning",
    "data science", "artificial intelligence", "language model", "neural",
    "fastapi", "langchain", "rag", "vector", "embedding", "transformer",
    "automation", "backend", "api", "aws", "cloud", "intern", "fresher",
]

def keyword_prefilter(job: Job, min_keyword_hits: int = 2) -> bool:
    """Returns True if description+title contains at least min_keyword_hits keywords."""
```

**Tier 2: Semantic similarity** (sentence-transformers, local, offline)
```python
class SemanticMatcher:
    def __init__(self, resume_text: str):
        # Load model once: SentenceTransformer("all-MiniLM-L6-v2")
        # Encode resume_text into self.resume_embedding (normalized)

    def score(self, job: Job) -> float:
        # Encode job.title + " " + job.description_clean[:2000]
        # Return cosine similarity (dot product of normalized vectors)

    def batch_score(self, jobs: list[Job]) -> list[float]:
        # Encode all in one batch (batch_size=32) — much faster
```

**Tier 2b: Skill overlap score**
```python
# Define candidate's skills set in config.py
# Compute: matched_skills / total_skills_in_set → 0.0–1.0

def skill_overlap_score(job: Job, your_skills: set[str]) -> float:
    """Count how many of your skills appear in title+description (case-insensitive)."""
```

**Composite score:**
```python
def composite_score(semantic: float, skill_overlap: float) -> float:
    return 0.65 * semantic + 0.35 * skill_overlap
```

**Tier 3: LLM scoring** (Anthropic Claude Haiku — only for top candidates)
```python
LLM_SYSTEM_PROMPT = """You are evaluating job-candidate fit. Be precise and concise.
Always respond with valid JSON only. No markdown, no explanation outside the JSON."""

LLM_USER_PROMPT = """
CANDIDATE RESUME SUMMARY:
{resume_summary}

JOB POSTING:
Title: {title}
Company: {company}
Location: {location}
Employment Type: {employment_type}
Description (truncated to 2000 chars):
{description}

Evaluate fit. Return JSON with exactly these fields:
{{
  "score": <integer 0-100>,
  "verdict": "<apply|maybe|skip>",
  "strengths": ["<max 3 specific strengths>"],
  "gaps": ["<max 2 specific gaps>"],
  "one_liner": "<single sentence summary>"
}}
"""

def llm_score_job(job: Job, resume_summary: str, client: anthropic.Anthropic) -> dict:
    """
    Calls Claude Haiku. Returns dict with score, verdict, strengths, gaps, one_liner.
    On any error: returns {"score": 0, "verdict": "skip", "strengths": [], "gaps": [], "one_liner": "LLM error"}
    Use model: "claude-haiku-4-5-20251001", max_tokens=400
    """
```

Full scoring pipeline:
```python
def run_scoring_pipeline(
    jobs: list[Job],
    matcher: SemanticMatcher,
    your_skills: set[str],
    resume_summary: str,
    semantic_threshold: float,    # e.g. 0.38
    llm_threshold: float,         # e.g. 0.45 (only LLM-score above this)
    llm_alert_threshold: int,     # e.g. 65 (only alert if LLM score >= this)
    max_llm_calls: int = 25,      # cap per run to control cost
) -> list[Job]:
    """
    1. keyword_prefilter → discard if < min_keyword_hits
    2. batch semantic score all remaining jobs
    3. compute skill_overlap + composite score
    4. store composite scores in DB
    5. LLM score jobs with composite > llm_threshold (up to max_llm_calls)
    6. Return jobs with llm_score >= llm_alert_threshold, sorted by llm_score DESC
    """
```

### `pipeline/alerter.py`

```python
class TelegramAlerter:
    def __init__(self, token: str, chat_id: str):
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id

    def send_job_alert(self, job: Job) -> bool:
        """
        Sends a formatted Telegram message with:
        - Job title, company, location/remote badge
        - Posted date
        - Salary range (if available)
        - LLM score bar (emoji progress bar 0-10 blocks)
        - LLM one-liner summary
        - Strengths (max 3, as bullet list)
        - Gaps (max 2, if any)
        - [Apply] button (link), [✅ Applied] [❌ Skip] [🔖 Save] inline buttons (callback)
        
        Use parse_mode="MarkdownV2". Escape all dynamic text properly.
        Returns True on success, False on failure (don't crash the pipeline on Telegram errors).
        """

    def send_error_alert(self, error: str, context: str = "") -> None:
        """Send plain text error notification to yourself."""

    def send_daily_summary(self, stats: dict) -> None:
        """
        Send end-of-run summary:
        - Total scraped, new, scored, alerted
        - Per-platform breakdown
        - Top 3 matches (title, company, score)
        """

    def handle_callback(self, callback_data: str) -> None:
        """
        Parse callback_data format: "action:job_id"
        Actions: applied, skip, save
        Update DB accordingly via update_status()
        """
```

**Telegram message format example:**

```
🔔 *LLM Engineer* — 82/100

🏢 Yellow\.ai \| 🌐 Remote
📅 May 5 · Indeed
💰 8–15 LPA

🟩🟩🟩🟩🟩🟩🟩🟩⬜⬜

_"Strong LLM/RAG background is a direct match for this conversational AI role\."_

✅ LangChain experience, AWS Lambda pipelines, voice AI exposure
⚠️ No Node\.js background mentioned

[Apply ↗](https://example.com/apply)
```

Inline keyboard:
```
[✅ Applied]  [❌ Skip]  [🔖 Save]
```

### `config.py`

All user-configurable values in one place:

```python
import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Anthropic ─────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── Search Configuration ───────────────────────────────────
SEARCH_TERMS = [
    "machine learning engineer",
    "LLM engineer",
    "AI engineer intern",
    "NLP engineer",
    "generative AI engineer",
    "ML engineer fresher",
    "AI ML intern",
]

LOCATION = "Bengaluru, India"
HOURS_OLD = 168          # Only jobs from last 7 days
RESULTS_PER_TERM = 50    # Per platform per search term

# ── Scoring Thresholds ────────────────────────────────────
KEYWORD_MIN_HITS = 2         # Discard jobs with fewer keyword matches
SEMANTIC_THRESHOLD = 0.38    # Discard jobs below this semantic similarity
LLM_THRESHOLD = 0.45         # Only LLM-score jobs above this
LLM_ALERT_THRESHOLD = 62     # Only alert if LLM score >= this
MAX_LLM_CALLS_PER_RUN = 25   # Cost guard

# ── Resume Data ───────────────────────────────────────────
RESUME_TEXT = """
Software Development Engineer Intern at Bespoke Technology (Client: Nurix AI) — Jan 2026–Present
- Built end-to-end conversational AI voice agents integrating STT (Deepgram), LLM (GPT-4.1 mini), TTS (ElevenLabs) across 4 production APIs including MS Dynamics 365
- Designed serverless Python automation pipelines on AWS Lambda for pre-call lead enrichment and post-call CRM updates
- Improved LLM agent reliability ~40% through prompt optimization and API integration testing
- Delivered automated outreach pipeline processing 100+ outbound leads/day

Projects:
GeoLLM — FastAPI + SentenceTransformers + Google Earth Engine + FAISS + Redis + Azure Container Apps
- Hierarchical LLM intent routing, satellite analysis (NDVI/LST/LULC), SSE streaming, MapLibre rendering

Prompt2Shell — Fine-tuned Phi-3-mini (3.8B) via QLoRA, production REST API on FastAPI + Modal
- 30% improvement in shell command accuracy; 80% inference latency reduction

Skills: Python, FastAPI, Flask, LangChain, RAG, FAISS, LLM Orchestration, Prompt Engineering,
QLoRA fine-tuning, Hugging Face, NLP (BERT/SBERT), spaCy, NLTK, AWS Lambda, Docker,
Azure Container Apps, PostgreSQL, Redis, REST APIs, Git, JavaScript, React, Next.js,
PyTorch, Scikit-learn, MongoDB
"""

RESUME_SUMMARY = """
Fresher SDE intern with hands-on production experience in LLM orchestration, voice AI pipelines,
RAG systems, and serverless cloud automation. Built real systems used by 100+ daily active leads.
Strong in Python, FastAPI, LangChain, AWS Lambda, FAISS, and Hugging Face. Seeking ML/AI/LLM
engineering roles or internships in India (BLR/Pune/Hyd/NCR) or remote.
"""

YOUR_SKILLS = {
    "python", "fastapi", "flask", "langchain", "llm", "large language model",
    "rag", "retrieval augmented generation", "faiss", "vector database",
    "aws lambda", "serverless", "docker", "azure", "postgresql", "redis",
    "sentence transformers", "hugging face", "pytorch", "nlp", "bert", "sbert",
    "machine learning", "deep learning", "gpt", "openai", "anthropic",
    "prompt engineering", "agentic", "voice ai", "stt", "tts", "deepgram",
    "elevenlabs", "crm", "automation", "rest api", "git", "react", "next.js",
    "scikit-learn", "mongodb", "spacy", "nltk", "qlora", "fine-tuning",
    "google earth engine", "geospatial", "sse", "streaming",
}

# ── Schedule ──────────────────────────────────────────────
SCHEDULE_INTERVAL_HOURS = 6

# ── Paths ─────────────────────────────────────────────────
DB_PATH = "jobs.db"
LOG_DIR = "logs"
COOKIES_DIR = "cookies"

# ── Feature Flags ─────────────────────────────────────────
ENABLE_WELLFOUND = True
ENABLE_HIRIST = True
ENABLE_INSTAHYRE = True
ENABLE_LLM_SCORING = True        # Set False to disable LLM calls (cost = 0)
ENABLE_TELEGRAM_ALERTS = True
SEND_DAILY_SUMMARY = True
```

### `main.py` — Full Pipeline Orchestrator

```python
"""
main.py — Automated Job Search Pipeline
Run: python main.py
Schedule: cron or APScheduler (see scheduler.py)
"""

def run_pipeline():
    """Single pipeline execution. Call this from cron or scheduler."""
    # 1. Log run start
    # 2. Ping healthcheck start URL (if configured)
    # 3. Scrape all platforms concurrently where possible
    # 4. Combine raw results with platform tags
    # 5. Normalize → Job dataclasses
    # 6. Deduplicate → filter to only new jobs
    # 7. Store new jobs in DB
    # 8. Run scoring pipeline on new jobs
    # 9. Send Telegram alerts for high-score jobs
    # 10. Send daily summary if SEND_DAILY_SUMMARY=True
    # 11. Log stats, ping healthcheck end URL
    # 12. On any unhandled exception: send Telegram error alert, re-raise

if __name__ == "__main__":
    run_pipeline()
```

Implement with comprehensive logging at every step:
```python
log.info(f"[Scrape] {platform}: {len(results)} raw jobs")
log.info(f"[Normalize] {len(jobs)} total normalized")
log.info(f"[Dedup] {len(new_jobs)} new (skipped {len(jobs)-len(new_jobs)} duplicates)")
log.info(f"[Score] {len(candidates)} above threshold {config.SEMANTIC_THRESHOLD}")
log.info(f"[LLM] Scored {llm_scored} jobs, {alerted} alerted")
```

### `scheduler.py` — APScheduler Setup

```python
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from main import run_pipeline
import config, logging

def start_scheduler():
    scheduler = BlockingScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(
        run_pipeline,
        trigger=IntervalTrigger(hours=config.SCHEDULE_INTERVAL_HOURS),
        id="job_search",
        name="Job Search Pipeline",
        max_instances=1,          # Prevent overlapping runs
        coalesce=True,            # If multiple triggers missed, run once
        misfire_grace_time=3600,  # 1 hour grace window
    )
    log.info(f"Scheduler started. Running every {config.SCHEDULE_INTERVAL_HOURS} hours.")
    scheduler.start()
```

### `dashboard.py` — Optional Streamlit UI

Minimal read-only dashboard. Implement only these views:

1. **Stats bar**: Total jobs, this week, alerts sent, applied
2. **Jobs table**: Filterable by platform, status, score range, date range
3. **Job detail**: Click a row → show full description + LLM analysis + action buttons (update status)
4. **Platform breakdown**: Bar chart of jobs by platform
5. **Score distribution**: Histogram of match scores

Use only: `streamlit`, `pandas`, `altair` (for charts). Nothing else.

```bash
streamlit run dashboard.py
```

---

## Anti-Bot & Stealth Requirements

Implement these rules across ALL scrapers:

1. **Random delays:** Between every request: `time.sleep(random.uniform(2.0, 5.5))`
2. **Between search terms:** `time.sleep(random.uniform(5.0, 10.0))`
3. **Between platforms:** `time.sleep(random.uniform(10.0, 20.0))`

4. **Headers for all `curl_cffi` requests:**
```python
STEALTH_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "DNT": "1",
}
```

5. **Cookie loading pattern:** Each scraper tries to load `cookies/{platform}_cookies.json`. If missing, log a warning and return `[]` — never crash.

6. **Rate limit detection:** If response status is 429 or 403, sleep 60–120 seconds and retry once. After second failure, log error and return `[]`.

7. **Playwright stealth** (for any future browser-based scraper):
```python
from playwright_stealth import stealth_sync
# Always apply stealth before page.goto()
stealth_sync(page)
context = browser.new_context(
    viewport={"width": 1366, "height": 768},
    locale="en-IN",
    timezone_id="Asia/Kolkata",
)
```

---

## Error Handling Philosophy

Every function must follow this pattern:

```python
def scrape_platform(search_terms: list[str]) -> list[dict]:
    results = []
    for term in search_terms:
        try:
            # ... scraping logic
            results.extend(term_results)
            log.info(f"[Hirist] '{term}': {len(term_results)} jobs")
        except Exception as e:
            log.error(f"[Hirist] Failed for '{term}': {type(e).__name__}: {e}")
            # Never re-raise — degraded data is better than no data
    return results
```

The pipeline must never crash completely due to one platform failing. Each platform is isolated.

---

## Environment Variables (`.env.example`)

```dotenv
# Required
TELEGRAM_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
ANTHROPIC_API_KEY=sk-ant-...

# Optional
HEALTHCHECK_URL=https://hc-ping.com/your-uuid-here
LOG_LEVEL=INFO
```

---

## `requirements.txt`

```
jobspy>=1.1.0
curl-cffi>=0.7.0
playwright>=1.44.0
playwright-stealth>=1.0.6
sentence-transformers>=3.0.0
anthropic>=0.30.0
httpx>=0.27.0
beautifulsoup4>=4.12.0
lxml>=5.2.0
python-dotenv>=1.0.0
APScheduler>=3.10.0
rapidfuzz>=3.9.0
streamlit>=1.35.0
pandas>=2.2.0
altair>=5.3.0
```

---

## README.md Requirements

Write a `README.md` with:

1. **Quick Start** (5 commands to get first alert running)
2. **Cookie setup guide** for each platform (manual export instructions)
3. **Telegram bot setup** (BotFather steps + getting chat_id)
4. **Running the pipeline** (manual + scheduled)
5. **Config customization** (how to change search terms, thresholds, resume)
6. **Dashboard usage**
7. **Crontab setup** alternative to APScheduler
8. **Troubleshooting** (platform blocked, cookie expired, etc.)

---

## Implementation Order (Tell Cursor to Build in This Sequence)

Ask Cursor to implement in this exact order to keep the build testable at each step:

1. **`pipeline/models.py`** — Job dataclass
2. **`db.py`** — Schema, connection, helper functions
3. **`config.py`** — All configuration
4. **`pipeline/normalizer.py`** — Normalizers + helpers (clean_description, parse_salary_inr)
5. **`pipeline/dedup.py`** — filter_new_jobs, store_jobs
6. **`scrapers/jobspy_scraper.py`** — Primary scraper (test this standalone first)
7. **`pipeline/scorer.py`** — Keyword filter + SemanticMatcher + composite score
8. **`pipeline/alerter.py`** — Telegram alerter
9. **`main.py`** — Full pipeline orchestrator
10. **`scrapers/wellfound.py`** — GraphQL scraper
11. **`scrapers/hirist.py`** — Hirist HTTP scraper
12. **`scrapers/instahyre.py`** — Instahyre scraper
13. **`scheduler.py`** — APScheduler setup
14. **LLM scoring in `scorer.py`** — Add after base pipeline works
15. **`dashboard.py`** — Add last, entirely optional
16. **`README.md`** — Final

---

## Testing Checkpoints

After each phase, implement a quick test:

```python
# test_scraper.py — run standalone to verify each scraper
if __name__ == "__main__":
    from scrapers.jobspy_scraper import scrape_jobspy
    results = scrape_jobspy(["machine learning engineer"], "Bengaluru, India", hours_old=168, results_per_term=10)
    print(f"Got {len(results)} raw jobs")
    for r in results[:3]:
        print(r.get("title"), "|", r.get("company"), "|", r.get("job_url"))
```

```python
# test_scorer.py
if __name__ == "__main__":
    from pipeline.scorer import SemanticMatcher
    from config import RESUME_TEXT
    matcher = SemanticMatcher(RESUME_TEXT)
    score = matcher.score_text("LLM Engineer role requiring Python, LangChain, RAG pipelines, FastAPI, and AWS Lambda experience")
    print(f"Score: {score:.3f}")  # Should be > 0.5 for this candidate
```

---

## What NOT to Build (Hard Constraints)

Do NOT implement any of these — they are explicitly out of scope:

- REST API / FastAPI server
- User authentication / JWT / sessions
- Docker / docker-compose (unless explicitly asked later)
- React / Vue / any JS frontend
- PostgreSQL (SQLite only)
- Redis (not needed at personal scale)
- Auto-apply bots (only alert, never auto-apply)
- Resume auto-generation / cover letter automation
- Multi-user support
- Cloud deployment automation (just local scripts)
- Message queues / Celery / task workers
- Any paid third-party services beyond Anthropic API (no ScraperAPI, no Apify, no proxy services)

---

## First Message After Scaffolding

Once Cursor has built the full structure, run this to verify the foundation works:

```bash
# 1. Install deps
pip install -r requirements.txt
playwright install chromium

# 2. Copy env
cp .env.example .env
# Edit .env with your Telegram token, chat ID, Anthropic key

# 3. Init DB
python db.py

# 4. Test primary scraper
python -c "
from scrapers.jobspy_scraper import scrape_jobspy
jobs = scrape_jobspy(['machine learning engineer'], 'Bengaluru, India', hours_old=168, results_per_term=10)
print(f'Scraped: {len(jobs)} jobs')
print(jobs[0] if jobs else 'No results')
"

# 5. Run full pipeline once
python main.py
```

---

## Cursor-Specific Instructions

When you paste this into Cursor Composer:

- Tell Cursor: **"Implement this entire spec completely. Do not skip any component. Write production-quality Python code with full type hints, docstrings, comprehensive error handling, and logging throughout. After scaffolding all files, run the test checkpoint for the JobSpy scraper and fix any import errors."**

- If Cursor asks about architecture choices: **"Follow the spec exactly. Do not suggest alternatives."**

- If Cursor generates partial files: **"Complete the implementation. Do not leave TODO comments or placeholder functions — implement everything fully."**

- After completion: **"Add inline comments explaining non-obvious logic (regex patterns, API endpoint parameters, scoring weights). Keep comments concise."**

---

*This prompt encodes: architecture, all data models, all API endpoints, scoring pipeline, alert format, error handling philosophy, implementation order, and test checkpoints. Cursor has everything it needs to build this in one session.*
