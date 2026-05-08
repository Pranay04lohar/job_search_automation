"""Canonical Job data model used across the entire pipeline."""

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
    llm_one_liner: Optional[str] = None
    alerted: bool = False
    status: str = "new"              # new | seen | applied | rejected | saved
    raw: dict = field(default_factory=dict)
