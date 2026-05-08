"""SQLite database connection, schema, and helper functions."""

import json
import logging
import sqlite3
from datetime import date, datetime
from typing import Optional


class _DateEncoder(json.JSONEncoder):
    """Encode date/datetime objects as ISO strings so raw dicts are always serialisable."""
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)

import config
from pipeline.models import Job

log = logging.getLogger(__name__)

SCHEMA_SQL = """
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
    skills TEXT,
    experience_min INTEGER,
    experience_max INTEGER,
    match_score REAL,
    llm_score INTEGER,
    llm_verdict TEXT,
    llm_strengths TEXT,
    llm_gaps TEXT,
    llm_one_liner TEXT,
    alerted INTEGER DEFAULT 0,
    status TEXT DEFAULT 'new',
    raw TEXT
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
"""


def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with WAL mode and performance pragmas set."""
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=10000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def init_db() -> None:
    """Create tables and indexes if they don't exist."""
    conn = get_connection()
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        log.info("[DB] Schema initialized")
    finally:
        conn.close()


def _job_to_row(job: Job) -> dict:
    """Convert a Job dataclass to a flat dict suitable for DB insertion."""
    return {
        "id": job.id,
        "content_hash": job.content_hash,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "is_remote": int(job.is_remote),
        "employment_type": job.employment_type,
        "description": job.description,
        "description_clean": job.description_clean,
        "apply_url": job.apply_url,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "scraped_at": job.scraped_at.isoformat(),
        "platform": job.platform,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.salary_currency,
        "skills": json.dumps(job.skills, cls=_DateEncoder),
        "experience_min": job.experience_min,
        "experience_max": job.experience_max,
        "match_score": job.match_score,
        "llm_score": job.llm_score,
        "llm_verdict": job.llm_verdict,
        "llm_strengths": json.dumps(job.llm_strengths, cls=_DateEncoder),
        "llm_gaps": json.dumps(job.llm_gaps, cls=_DateEncoder),
        "llm_one_liner": job.llm_one_liner,
        "alerted": int(job.alerted),
        "status": job.status,
        "raw": json.dumps(job.raw, cls=_DateEncoder),
    }


def _row_to_job(row: sqlite3.Row) -> Job:
    """Convert a DB row back to a Job dataclass."""
    d = dict(row)

    def parse_dt(s: Optional[str]) -> Optional[datetime]:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return None

    return Job(
        id=d["id"],
        content_hash=d["content_hash"],
        title=d["title"],
        company=d["company"],
        location=d.get("location") or "",
        is_remote=bool(d.get("is_remote", 0)),
        employment_type=d.get("employment_type") or "",
        description=d.get("description") or "",
        description_clean=d.get("description_clean") or "",
        apply_url=d.get("apply_url") or "",
        posted_at=parse_dt(d.get("posted_at")),
        scraped_at=parse_dt(d.get("scraped_at")) or datetime.utcnow(),
        platform=d["platform"],
        salary_min=d.get("salary_min"),
        salary_max=d.get("salary_max"),
        salary_currency=d.get("salary_currency") or "INR",
        skills=json.loads(d["skills"]) if d.get("skills") else [],
        experience_min=d.get("experience_min"),
        experience_max=d.get("experience_max"),
        match_score=d.get("match_score"),
        llm_score=d.get("llm_score"),
        llm_verdict=d.get("llm_verdict"),
        llm_strengths=json.loads(d["llm_strengths"]) if d.get("llm_strengths") else [],
        llm_gaps=json.loads(d["llm_gaps"]) if d.get("llm_gaps") else [],
        llm_one_liner=d.get("llm_one_liner"),
        alerted=bool(d.get("alerted", 0)),
        status=d.get("status") or "new",
        raw=json.loads(d["raw"]) if d.get("raw") else {},
    )


def insert_job(job: Job) -> bool:
    """INSERT OR IGNORE a job. Returns True if actually inserted (new row)."""
    conn = get_connection()
    try:
        row = _job_to_row(job)
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO jobs (
                id, content_hash, title, company, location, is_remote, employment_type,
                description, description_clean, apply_url, posted_at, scraped_at, platform,
                salary_min, salary_max, salary_currency, skills, experience_min, experience_max,
                match_score, llm_score, llm_verdict, llm_strengths, llm_gaps, llm_one_liner,
                alerted, status, raw
            ) VALUES (
                :id, :content_hash, :title, :company, :location, :is_remote, :employment_type,
                :description, :description_clean, :apply_url, :posted_at, :scraped_at, :platform,
                :salary_min, :salary_max, :salary_currency, :skills, :experience_min, :experience_max,
                :match_score, :llm_score, :llm_verdict, :llm_strengths, :llm_gaps, :llm_one_liner,
                :alerted, :status, :raw
            )
            """,
            row,
        )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        log.error(f"[DB] insert_job failed for {job.id}: {e}")
        return False
    finally:
        conn.close()


def job_exists(job_id: str) -> bool:
    """Check if a job with this ID already exists in the DB."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return row is not None
    finally:
        conn.close()


def hash_exists(content_hash: str) -> bool:
    """Check if a job with this content hash exists (cross-platform dedup)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM jobs WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def update_scores(
    job_id: str,
    match_score: float,
    llm_score: Optional[int] = None,
    llm_verdict: Optional[str] = None,
    llm_strengths: Optional[list[str]] = None,
    llm_gaps: Optional[list[str]] = None,
    llm_one_liner: Optional[str] = None,
) -> None:
    """Update scoring fields for a job."""
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE jobs SET
                match_score = ?,
                llm_score = ?,
                llm_verdict = ?,
                llm_strengths = ?,
                llm_gaps = ?,
                llm_one_liner = ?
            WHERE id = ?
            """,
            (
                match_score,
                llm_score,
                llm_verdict,
                json.dumps(llm_strengths or []),
                json.dumps(llm_gaps or []),
                llm_one_liner,
                job_id,
            ),
        )
        conn.commit()
    except Exception as e:
        log.error(f"[DB] update_scores failed for {job_id}: {e}")
    finally:
        conn.close()


def mark_alerted(job_id: str) -> None:
    """Mark a job as alerted so it won't be re-sent."""
    conn = get_connection()
    try:
        conn.execute("UPDATE jobs SET alerted = 1 WHERE id = ?", (job_id,))
        conn.commit()
    finally:
        conn.close()


def update_status(job_id: str, status: str) -> None:
    """Update job status (new | seen | applied | rejected | saved)."""
    conn = get_connection()
    try:
        conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
        conn.commit()
    finally:
        conn.close()


def get_jobs_for_review(limit: int = 50) -> list[Job]:
    """Return new, high-score, unalerted jobs sorted by match_score DESC."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'new' AND alerted = 0
            ORDER BY match_score DESC NULLS LAST
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_row_to_job(r) for r in rows]
    finally:
        conn.close()


def get_stats() -> dict:
    """Return aggregate stats for dashboard and summary messages."""
    conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        this_week = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE posted_at >= date('now', '-7 days')"
        ).fetchone()[0]
        alerted = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE alerted = 1"
        ).fetchone()[0]
        applied = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'applied'"
        ).fetchone()[0]

        platform_rows = conn.execute(
            "SELECT platform, COUNT(*) as cnt FROM jobs GROUP BY platform ORDER BY cnt DESC"
        ).fetchall()
        by_platform = {r["platform"]: r["cnt"] for r in platform_rows}

        top_jobs = conn.execute(
            """
            SELECT title, company, llm_score, match_score, apply_url FROM jobs
            WHERE llm_score IS NOT NULL OR match_score IS NOT NULL
            ORDER BY COALESCE(llm_score, 0) DESC, match_score DESC
            LIMIT 5
            """
        ).fetchall()
        top = [dict(r) for r in top_jobs]

        return {
            "total": total,
            "this_week": this_week,
            "alerted": alerted,
            "applied": applied,
            "by_platform": by_platform,
            "top_jobs": top,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("Database initialized successfully at:", config.DB_PATH)
