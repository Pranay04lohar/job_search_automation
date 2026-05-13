"""
preview_daily.py — Full-quality preview of all good jobs already in DB.

Run this ONCE to:
  1. LLM-score every job with match_score >= 0.42 that has no valid LLM score yet.
  2. Send Telegram alerts for the top matches (best LLM score first).
  3. Send a daily-style summary.

This shows you exactly what a fresh daily cron run will look like, using the
627+ jobs already accumulated from today's scrape runs.

Usage:
    .venv\\Scripts\\python.exe preview_daily.py
"""

import logging
import time

import config
import db
from pipeline.alerter import TelegramAlerter
from pipeline.experience_filter import filter_jobs_by_experience
from pipeline.scorer import llm_score_job
from pipeline.models import Job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("preview")

# ── Tuning — reads from config.py so changes in one place apply everywhere ─────
SEMANTIC_MIN     = config.SEMANTIC_THRESHOLD        # mirrors config.SEMANTIC_THRESHOLD
LLM_CAP          = config.MAX_LLM_CALLS_PER_RUN    # mirrors config.MAX_LLM_CALLS_PER_RUN
ALERT_THRESHOLD  = config.LLM_ALERT_THRESHOLD       # mirrors config.LLM_ALERT_THRESHOLD
MAX_ALERTS       = 40                               # hard cap on Telegram alerts per preview run
# ───────────────────────────────────────────────────────────────────────────────


def _fetch_candidates(min_semantic: float) -> list[Job]:
    """Return unalerted jobs with match_score >= min_semantic, sorted best-first."""
    conn = db.get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE match_score >= ?
              AND alerted = 0
              AND company != 'nan'
              AND company != ''
            ORDER BY COALESCE(llm_score, 0) DESC, match_score DESC
            """,
            (min_semantic,),
        ).fetchall()
    finally:
        conn.close()
    return [db._row_to_job(r) for r in rows]


def main() -> None:
    db.init_db()

    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.error("TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set in .env — aborting.")
        return

    alerter = TelegramAlerter(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)

    # ── 1. Fetch all good semantic candidates ──────────────────────────────────
    candidates = _fetch_candidates(SEMANTIC_MIN)
    log.info(f"Found {len(candidates)} jobs with semantic score >= {SEMANTIC_MIN}")

    if getattr(config, "ENABLE_EXPERIENCE_CAP_FILTER", True):
        before_e = len(candidates)
        candidates, dropped_e = filter_jobs_by_experience(
            candidates,
            config.MAX_MIN_EXPERIENCE_YEARS,
            config.EXPERIENCE_FILTER_STRICT_UNKNOWN,
        )
        log.info(
            f"Experience cap (< {config.MAX_MIN_EXPERIENCE_YEARS} yr min required): "
            f"dropped {dropped_e} ({len(candidates)} remaining)"
        )

    # ── 2. LLM-score jobs that don't have a valid LLM score yet ───────────────
    needs_llm = [
        j for j in candidates
        if (j.llm_score is None or j.llm_score == 0)
    ]
    log.info(
        f"{len(needs_llm)} jobs need LLM scoring "
        f"(capped at {LLM_CAP} calls)"
    )

    if not config.OPENROUTER_API_KEY:
        log.warning("OPENROUTER_API_KEY not set — skipping LLM scoring, using semantic only.")
        needs_llm = []

    llm_done = 0
    for job in needs_llm[:LLM_CAP]:
        result = llm_score_job(job, config.RESUME_SUMMARY)
        job.llm_score   = result["score"]
        job.llm_verdict = result["verdict"]
        job.llm_strengths = result["strengths"]
        job.llm_gaps    = result["gaps"]
        job.llm_one_liner = result.get("one_liner", "")
        db.update_scores(
            job.id,
            match_score=job.match_score,
            llm_score=result["score"],
            llm_verdict=result["verdict"],
            llm_strengths=result["strengths"],
            llm_gaps=result["gaps"],
            llm_one_liner=result.get("one_liner"),
        )
        log.info(
            f"[LLM] {job.title} @ {job.company}: "
            f"score={result['score']} verdict={result['verdict']}"
        )
        llm_done += 1
        time.sleep(4.0)   # ~15 req/min — stays under free-tier rate limit

    log.info(f"LLM scoring complete: {llm_done} jobs scored.")

    # ── 3. Pick top jobs to alert ──────────────────────────────────────────────
    # Prefer jobs with a real LLM score; fall back to semantic for the rest
    def _sort_key(j: Job) -> tuple:
        return (j.llm_score or 0, j.match_score or 0)

    alert_pool = [
        j for j in candidates
        if (j.llm_score or 0) >= ALERT_THRESHOLD
    ]

    # If LLM produced fewer than 5 alerts, pad with top semantic matches
    if len(alert_pool) < 5:
        sem_only = [
            j for j in candidates
            if (j.llm_score is None or j.llm_score == 0)
               and (j.match_score or 0) >= SEMANTIC_MIN
        ]
        sem_only.sort(key=_sort_key, reverse=True)
        alert_pool += sem_only[:max(0, 10 - len(alert_pool))]

    alert_pool.sort(key=_sort_key, reverse=True)
    to_alert = alert_pool[:MAX_ALERTS]

    log.info(f"Sending {len(to_alert)} alerts (LLM≥{ALERT_THRESHOLD} + top semantic)")

    # ── 4. Send alerts ─────────────────────────────────────────────────────────
    sent = 0
    for job in to_alert:
        success = alerter.send_job_alert(job)
        if success:
            db.mark_alerted(job.id)
            sent += 1
        time.sleep(0.5)

    log.info(f"Sent {sent}/{len(to_alert)} alerts")

    # ── 5. Summary message ─────────────────────────────────────────────────────
    stats = db.get_stats()
    alerter.send_daily_summary(stats)

    alerter.close()
    log.info("Preview run complete.")


if __name__ == "__main__":
    main()
