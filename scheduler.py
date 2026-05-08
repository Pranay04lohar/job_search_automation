"""APScheduler-based scheduler — runs the pipeline every N hours."""

import logging

import config
from main import run_pipeline

log = logging.getLogger(__name__)


def start_scheduler() -> None:
    """Start the blocking scheduler. Ctrl+C to stop."""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:
        log.error(
            "[Scheduler] 'APScheduler' not installed. Run: pip install APScheduler"
        )
        return

    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    scheduler = BlockingScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(
        run_pipeline,
        trigger=IntervalTrigger(hours=config.SCHEDULE_INTERVAL_HOURS),
        id="job_search",
        name="Job Search Pipeline",
        max_instances=1,          # Prevent overlapping runs
        coalesce=True,            # If multiple triggers missed, run only once
        misfire_grace_time=3600,  # 1-hour grace window for misfires
    )

    log.info(
        f"[Scheduler] Starting. Pipeline will run every "
        f"{config.SCHEDULE_INTERVAL_HOURS} hour(s). Press Ctrl+C to stop."
    )

    # Run immediately on startup before entering the scheduled loop
    log.info("[Scheduler] Running initial pipeline pass now...")
    try:
        run_pipeline()
    except Exception as e:
        log.error(f"[Scheduler] Initial run failed: {e}")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("[Scheduler] Stopped.")


if __name__ == "__main__":
    start_scheduler()
