"""
Experience requirement filter — drop roles that explicitly need too much tenure.

Uses normalized Job.experience_min / experience_max when present, plus a small
regex pass over title + description when those fields are empty (common on
LinkedIn/Indeed extracts).
"""

from __future__ import annotations

import re
from typing import Optional

from pipeline.models import Job

_RANGE_RE = re.compile(
    r"(\d+)\s*[-–to]+\s*(\d+)\s*(?:years?|yrs?\.?|y\/o)",
    re.IGNORECASE,
)
_MIN_WORD_RE = re.compile(
    r"(?:minimum|min\.?|at least|requires?|need)\s*(?:of\s*)?(\d+)\s*(?:\+)?\s*(?:years?|yrs?)",
    re.IGNORECASE,
)
_PLUS_RE = re.compile(r"\b(\d+)\s*\+\s*(?:years?|yrs?)", re.IGNORECASE)


def _infer_min_years_from_text(title: str, description: str, max_chars: int = 1500) -> Optional[int]:
    """
    Infer a conservative "minimum years required" from free text.
    Returns None if nothing clear is found.
    """
    blob = f"{title}\n{description[:max_chars]}".lower()
    candidates: list[int] = []

    for m in _RANGE_RE.finditer(blob):
        try:
            candidates.append(int(m.group(1)))
        except ValueError:
            pass

    for m in _MIN_WORD_RE.finditer(blob):
        try:
            candidates.append(int(m.group(1)))
        except ValueError:
            pass

    for m in _PLUS_RE.finditer(blob):
        try:
            candidates.append(int(m.group(1)))
        except ValueError:
            pass

    if not candidates:
        return None
    # Multiple mentions — take the highest lower bound (strictest gate)
    return max(candidates)


def effective_minimum_years(job: Job) -> Optional[float]:
    """Best-effort minimum years required for the role."""
    structured = job.experience_min
    inferred = _infer_min_years_from_text(
        job.title or "",
        job.description_clean or job.description or "",
    )

    parts = [x for x in (structured, inferred) if x is not None]
    if not parts:
        return None
    return float(max(parts))


def passes_experience_cap(job: Job, max_min_years: float, strict_when_unknown: bool) -> bool:
    """
    True if the job should be kept.

    Drop when effective minimum required experience is >= max_min_years
    (e.g. max_min_years=3 drops roles that require 3+ years minimum).

    When strict_when_unknown is False, jobs with no parsable experience signal
    are kept (common for sparse listings).
    """
    eff = effective_minimum_years(job)
    if eff is None:
        return strict_when_unknown is False
    return eff < max_min_years


def filter_jobs_by_experience(
    jobs: list[Job],
    max_min_years: float,
    strict_when_unknown: bool,
) -> tuple[list[Job], int]:
    """Return (kept_jobs, dropped_count)."""
    kept: list[Job] = []
    for j in jobs:
        if passes_experience_cap(j, max_min_years, strict_when_unknown):
            kept.append(j)
    return kept, len(jobs) - len(kept)
