# Job Search Aggregation System

A personal-use CLI pipeline that scrapes job listings from LinkedIn, Indeed, Naukri, Wellfound, Hirist, and Instahyre — scores them against your resume using semantic similarity + LLM analysis — and fires Telegram alerts for the best matches every 6 hours.

---

## Quick Start (5 commands)

```bash
# 1. Create virtual environment and install dependencies
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt

# 2. Install Playwright browser (only needed for future browser scrapers)
playwright install chromium

# 3. Set up environment variables
copy .env.example .env
# Edit .env: add TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, ANTHROPIC_API_KEY

# 4. Initialise the database
python db.py

# 5. Run the pipeline once
python main.py
```

After step 5 you should receive Telegram alerts for top matches within a few minutes.

---

## Telegram Bot Setup

1. Open Telegram and message **@BotFather**
2. Send `/newbot` and follow the prompts — copy the **API token**
3. Start a chat with your bot (send any message to it)
4. Get your **chat ID**:
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
   Look for `"chat":{"id": 123456789}` in the response
5. Add both to `.env`:
   ```
   TELEGRAM_TOKEN=1234567890:ABCdef...
   TELEGRAM_CHAT_ID=123456789
   ```

---

## Cookie Setup (for authenticated platforms)

Wellfound and Instahyre require session cookies. Export them with the [EditThisCookie](https://chrome.google.com/webstore/detail/editthiscookie) Chrome extension:

### Wellfound
1. Log in at https://wellfound.com
2. Open EditThisCookie → Export → copy JSON
3. Save to `cookies/wellfound_cookies.json`

### Instahyre
1. Log in at https://www.instahyre.com
2. Open EditThisCookie → Export → copy JSON
3. Save to `cookies/instahyre_cookies.json`

If cookie files are missing, those scrapers return `[]` gracefully — the pipeline continues with the other platforms.

---

## Running the Pipeline

### Once (manual)

```bash
python main.py
```

### On a schedule (APScheduler — runs every 6 hours, restarts automatically)

```bash
python scheduler.py
```

### Alternative: system cron (Linux/macOS)

```bash
crontab -e
# Add:
0 */6 * * * /path/to/.venv/bin/python /path/to/job_search_automation/main.py >> /path/to/logs/cron.log 2>&1
```

### Windows Task Scheduler

1. Open Task Scheduler → Create Basic Task
2. Trigger: Daily, repeat every 6 hours
3. Action: Start a program
   - Program: `C:\path\to\.venv\Scripts\python.exe`
   - Arguments: `C:\path\to\job_search_automation\main.py`
   - Start in: `C:\path\to\job_search_automation`

---

## Config Customisation

All user settings live in **`config.py`**:

| Setting | Default | Description |
|---|---|---|
| `SEARCH_TERMS` | 7 ML/AI terms | Edit to match your target roles |
| `LOCATION` | Bengaluru, India | Search location for jobspy |
| `HOURS_OLD` | 168 (7 days) | Only scrape jobs posted in last N hours |
| `RESULTS_PER_TERM` | 50 | Max results per search term per platform |
| `SEMANTIC_THRESHOLD` | 0.38 | Minimum semantic similarity to keep a job |
| `LLM_THRESHOLD` | 0.45 | Minimum composite score to send to LLM |
| `LLM_ALERT_THRESHOLD` | 62 | Minimum LLM score to trigger Telegram alert |
| `MAX_LLM_CALLS_PER_RUN` | 25 | Cost cap: max Claude API calls per run |
| `RESUME_TEXT` | Full resume | Replace with your own resume text |
| `YOUR_SKILLS` | Set of skills | Add/remove skills for skill-overlap scoring |
| `SCHEDULE_INTERVAL_HOURS` | 6 | How often to run the pipeline |

To disable LLM scoring entirely (cost = $0), set:
```python
ENABLE_LLM_SCORING = False
```

---

## Dashboard

```bash
streamlit run dashboard.py
```

Opens at http://localhost:8501. Features:

- **Stats bar**: Total, this week, alerted, applied counts
- **Filter sidebar**: Platform, status, score range, date range, remote-only
- **Jobs table**: Sortable, with apply links
- **Job detail**: Full description + LLM analysis + status update buttons
- **Charts**: Platform breakdown bar chart + match score histogram

---

## Telegram Callback Buttons

Each alert has three inline buttons:

| Button | Action |
|---|---|
| ✅ Applied | Sets job status to `applied` in DB |
| ❌ Skip | Sets status to `rejected` |
| 🔖 Save | Sets status to `saved` |

To handle callbacks you need to run a webhook or polling loop (not included — the pipeline itself is callback-unaware; you can add a separate bot polling script if needed).

---

## Troubleshooting

### Platform is blocked / returning 0 results
- Wait 15–30 minutes and retry — temp IP block
- Wellfound/Instahyre: re-export cookies (they expire every few days)
- Check `logs/` for the specific error

### `jobspy` returns empty DataFrame
- Some platforms block headless requests; try reducing `results_per_term`
- LinkedIn often requires delays; the built-in per-term sleep usually helps

### LLM scoring not working
- Check `ANTHROPIC_API_KEY` in `.env`
- Verify with: `python -c "import anthropic; print(anthropic.__version__)"`

### Semantic model is slow on first run
- `all-MiniLM-L6-v2` (~80 MB) is downloaded once to `~/.cache/huggingface`
- Subsequent runs load from cache in ~1 second

### SQLite `database is locked`
- Only one pipeline instance should run at a time
- APScheduler's `max_instances=1` prevents overlap when using `scheduler.py`
- If running via cron, add a lockfile check or use `flock`

### Cookie file format
Cookies must be a JSON array of objects with at least `name` and `value` keys, or a flat dict:
```json
[{"name": "session_id", "value": "abc123"}, ...]
```

---

## Project Structure

```
job_search_automation/
├── main.py                  # Pipeline entry point
├── config.py                # All user config
├── db.py                    # SQLite helpers
├── scheduler.py             # APScheduler loop
├── dashboard.py             # Streamlit dashboard
├── scrapers/
│   ├── jobspy_scraper.py    # LinkedIn + Indeed + Naukri
│   ├── wellfound.py         # Wellfound GraphQL
│   ├── hirist.py            # Hirist.tech JSON API
│   └── instahyre.py         # Instahyre REST API
├── pipeline/
│   ├── models.py            # Job dataclass
│   ├── normalizer.py        # Platform → Job normalizers
│   ├── dedup.py             # Deduplication
│   ├── scorer.py            # Keyword + semantic + LLM scoring
│   └── alerter.py           # Telegram alerts
├── cookies/                 # Session cookies (gitignored)
├── logs/                    # Run logs (gitignored)
├── test_scraper.py          # Standalone scraper test
├── test_scorer.py           # Standalone scorer test
├── requirements.txt
└── .env.example
```

---

## Cost Estimate

At 4 runs/day × 25 LLM calls/run × Claude Haiku pricing (~$0.0003/call):

```
~$0.03/day ≈ $1/month ≈ $9/year
```

Set `MAX_LLM_CALLS_PER_RUN = 0` or `ENABLE_LLM_SCORING = False` to eliminate LLM costs entirely.
