"""
main.py — Automated Job Search Pipeline
Run once:    python main.py
Scheduled:   python scheduler.py
"""

import logging
import os
import random
import time
from datetime import datetime
from pathlib import Path

import config
import db
from pipeline.dedup import filter_new_jobs, store_jobs
from pipeline.normalizer import normalize_all
from pipeline.scorer import SemanticMatcher, run_scoring_pipeline
from pipeline.alerter import TelegramAlerter
from scrapers.jobspy_scraper import scrape_jobspy

if config.ENABLE_WELLFOUND:
    from scrapers.wellfound import scrape_wellfound
if config.ENABLE_HIRIST:
    from scrapers.hirist import scrape_hirist
if config.ENABLE_INSTAHYRE:
    from scrapers.instahyre import scrape_instahyre


def _setup_logging() -> None:
    """Configure file + console logging."""
    log_dir = Path(config.LOG_DIR)
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def _ping_healthcheck(url: str, suffix: str = "") -> None:
    """Ping a healthchecks.io-style URL. Fails silently."""
    if not url:
        return
    try:
        import httpx
        httpx.get(url.rstrip("/") + suffix, timeout=5)
    except Exception:
        pass


def run_pipeline() -> None:
    """Single end-to-end pipeline execution."""
    _setup_logging()
    log = logging.getLogger("pipeline")

    run_start = datetime.now()
    log.info(f"{'=' * 60}")
    log.info(f"Pipeline run started at {run_start.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"{'=' * 60}")

    alerter: TelegramAlerter | None = None
    if config.ENABLE_TELEGRAM_ALERTS and config.TELEGRAM_TOKEN:
        alerter = TelegramAlerter(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)

    _ping_healthcheck(config.HEALTHCHECK_URL, "/start")

    try:
        # ── Ensure DB is initialised ────────────────────────────────────────────
        db.init_db()

        # ── Scrape all platforms ────────────────────────────────────────────────
        raw_tagged: list[tuple[str, dict]] = []

        # JobSpy (LinkedIn + Indeed + Naukri)
        log.info("[Scrape] Starting JobSpy (LinkedIn + Indeed + Naukri)...")
        jobspy_results = scrape_jobspy(
            config.SEARCH_TERMS,
            config.LOCATION,
            hours_old=config.HOURS_OLD,
            results_per_term=config.RESULTS_PER_TERM,
        )
        for r in jobspy_results:
            platform = str(r.get("site", "indeed")).lower()
            raw_tagged.append((platform, r))
        log.info(f"[Scrape] JobSpy: {len(jobspy_results)} raw jobs")

        time.sleep(random.uniform(10.0, 20.0))

        # Wellfound
        if config.ENABLE_WELLFOUND:
            log.info("[Scrape] Starting Wellfound...")
            wf_results = scrape_wellfound(config.SEARCH_TERMS)
            for r in wf_results:
                raw_tagged.append(("wellfound", r))
            log.info(f"[Scrape] Wellfound: {len(wf_results)} raw jobs")
            time.sleep(random.uniform(10.0, 20.0))

        # Hirist
        if config.ENABLE_HIRIST:
            log.info("[Scrape] Starting Hirist...")
            hirist_results = scrape_hirist(config.SEARCH_TERMS, location="bangalore")
            for r in hirist_results:
                raw_tagged.append(("hirist", r))
            log.info(f"[Scrape] Hirist: {len(hirist_results)} raw jobs")
            time.sleep(random.uniform(10.0, 20.0))

        # Instahyre
        if config.ENABLE_INSTAHYRE:
            log.info("[Scrape] Starting Instahyre...")
            ih_results = scrape_instahyre(config.SEARCH_TERMS)
            for r in ih_results:
                raw_tagged.append(("instahyre", r))
            log.info(f"[Scrape] Instahyre: {len(ih_results)} raw jobs")

        total_raw = len(raw_tagged)
        log.info(f"[Scrape] Total raw jobs collected: {total_raw}")

        # ── Normalize ──────────────────────────────────────────────────────────
        jobs = normalize_all(raw_tagged)
        log.info(f"[Normalize] {len(jobs)} jobs normalized from {total_raw} raw")

        # ── Deduplicate & store ────────────────────────────────────────────────
        new_jobs = filter_new_jobs(jobs)
        inserted = store_jobs(new_jobs)
        log.info(f"[Store] {inserted} new jobs saved to DB")

        if not new_jobs:
            log.info("[Pipeline] No new jobs to score. Run complete.")
            _ping_healthcheck(config.HEALTHCHECK_URL)
            return

        # ── Score ──────────────────────────────────────────────────────────────
        log.info(f"[Score] Initialising semantic matcher...")
        matcher = SemanticMatcher(config.RESUME_TEXT)

        alert_candidates = run_scoring_pipeline(
            jobs=new_jobs,
            matcher=matcher,
            your_skills=config.YOUR_SKILLS,
            resume_summary=config.RESUME_SUMMARY,
            semantic_threshold=config.SEMANTIC_THRESHOLD,
            llm_threshold=config.LLM_THRESHOLD,
            llm_alert_threshold=config.LLM_ALERT_THRESHOLD,
            max_llm_calls=config.MAX_LLM_CALLS_PER_RUN,
        )
        log.info(f"[Score] {len(alert_candidates)} jobs ready to alert")

        # ── Alert ──────────────────────────────────────────────────────────────
        alerted_count = 0
        if alerter and alert_candidates:
            for job in alert_candidates:
                success = alerter.send_job_alert(job)
                if success:
                    alerted_count += 1
                time.sleep(0.5)  # Telegram rate limit: 30 messages/sec
            log.info(f"[Alert] {alerted_count} Telegram alerts sent")
        elif not alerter:
            log.info("[Alert] Telegram disabled or not configured")

        # ── Daily summary ──────────────────────────────────────────────────────
        if config.SEND_DAILY_SUMMARY and alerter:
            stats = db.get_stats()
            alerter.send_daily_summary(stats)

        elapsed = (datetime.now() - run_start).total_seconds()
        log.info(
            f"[Pipeline] Run complete in {elapsed:.1f}s | "
            f"raw={total_raw} normalized={len(jobs)} new={len(new_jobs)} "
            f"scored={len(alert_candidates)} alerted={alerted_count}"
        )
        _ping_healthcheck(config.HEALTHCHECK_URL)

    except Exception as e:
        log.exception(f"[Pipeline] Unhandled exception: {e}")
        if alerter:
            alerter.send_error_alert(str(e), context="main pipeline")
        _ping_healthcheck(config.HEALTHCHECK_URL, "/fail")
        raise
    finally:
        if alerter:
            alerter.close()


if __name__ == "__main__":
    run_pipeline()
