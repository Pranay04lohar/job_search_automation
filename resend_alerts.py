"""
resend_alerts.py — Send Telegram alerts for jobs already in DB that weren't alerted.

Usage:
    .\.venv\Scripts\python resend_alerts.py
"""

import logging
import time

import config
import db
from pipeline.alerter import TelegramAlerter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("resend")


def main() -> None:
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        print("TELEGRAM_TOKEN or TELEGRAM_CHAT_ID not set in .env")
        return

    alerter = TelegramAlerter(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)

    conn = db.get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE alerted = 0
              AND (match_score IS NOT NULL OR llm_score IS NOT NULL)
            ORDER BY COALESCE(llm_score, 0) DESC, match_score DESC
            LIMIT 30
            """
        ).fetchall()
    finally:
        conn.close()

    jobs = [db._row_to_job(r) for r in rows]
    log.info(f"Found {len(jobs)} unalerted scored jobs in DB")

    sent = 0
    for job in jobs:
        success = alerter.send_job_alert(job)
        if success:
            sent += 1
            log.info(f"Alerted: {job.title} @ {job.company}")
        time.sleep(0.5)

    log.info(f"Done — {sent}/{len(jobs)} alerts sent")
    alerter.close()


if __name__ == "__main__":
    main()
