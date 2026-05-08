"""Cross-platform deduplication and DB persistence for Job objects."""

import logging

import db
from pipeline.models import Job

log = logging.getLogger(__name__)


def filter_new_jobs(jobs: list[Job]) -> list[Job]:
    """
    Two-stage deduplication:
      1. job.id  — same platform + external ID (exact match)
      2. job.content_hash — same title+company across platforms

    Returns only genuinely new jobs not already stored in the DB.
    """
    seen_hashes: set[str] = set()  # within this batch
    new_jobs: list[Job] = []

    for job in jobs:
        # Stage 1: exact ID check
        if db.job_exists(job.id):
            log.debug(f"[Dedup] Skipping known ID: {job.id}")
            continue

        # Stage 2: content hash dedup (cross-platform)
        if job.content_hash in seen_hashes:
            log.debug(
                f"[Dedup] In-batch duplicate: {job.title} @ {job.company}"
            )
            continue

        if db.hash_exists(job.content_hash):
            log.info(
                f"[Dedup] Cross-platform duplicate: {job.title} @ {job.company} "
                f"(hash={job.content_hash}, platform={job.platform})"
            )
            continue

        seen_hashes.add(job.content_hash)
        new_jobs.append(job)

    skipped = len(jobs) - len(new_jobs)
    log.info(
        f"[Dedup] {len(new_jobs)} new jobs (skipped {skipped} duplicates)"
    )
    return new_jobs


def store_jobs(jobs: list[Job]) -> int:
    """
    Insert all jobs into the DB using INSERT OR IGNORE.
    Returns count of actually inserted rows.
    """
    inserted = 0
    for job in jobs:
        if db.insert_job(job):
            inserted += 1
    log.info(f"[Store] {inserted}/{len(jobs)} jobs inserted into DB")
    return inserted
