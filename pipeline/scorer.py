"""
Three-tier scoring pipeline:
  Tier 1  — Keyword pre-filter (instant, no model)
  Tier 2  — Semantic similarity + skill overlap (local, offline)
  Tier 3  — LLM scoring via Claude Haiku (online, capped per run)
"""

import logging
from typing import Optional

import db
import config
from pipeline.models import Job

log = logging.getLogger(__name__)

# ── Tier 1: Keyword Pre-filter ─────────────────────────────────────────────────

REQUIRED_KEYWORDS: list[str] = [
    "python", "machine learning", "ml", "ai", "llm", "nlp", "deep learning",
    "data science", "artificial intelligence", "language model", "neural",
    "fastapi", "langchain", "rag", "vector", "embedding", "transformer",
    "automation", "backend", "api", "aws", "cloud", "intern", "fresher",
]


def keyword_prefilter(job: Job, min_keyword_hits: int = 2) -> bool:
    """Return True if the job description+title contains at least min_keyword_hits keywords."""
    text = (job.title + " " + job.description_clean).lower()
    hits = sum(1 for kw in REQUIRED_KEYWORDS if kw in text)
    return hits >= min_keyword_hits


# ── Tier 2: Semantic Similarity ────────────────────────────────────────────────

class SemanticMatcher:
    """Encodes resume once at init; scores jobs using cosine similarity."""

    def __init__(self, resume_text: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import]
            import numpy as np  # type: ignore[import]
        except ImportError:
            raise ImportError(
                "sentence-transformers is not installed. "
                "Run: pip install sentence-transformers"
            )

        self._np = np
        log.info("[Scorer] Loading sentence-transformers model all-MiniLM-L6-v2...")
        self._model = SentenceTransformer("all-MiniLM-L6-v2")
        self.resume_embedding = self._model.encode(
            resume_text, normalize_embeddings=True, show_progress_bar=False
        )
        log.info("[Scorer] Model loaded and resume encoded.")

    def _encode_job(self, job: Job) -> "np.ndarray":
        text = (job.title + " " + job.description_clean[:2000]).strip()
        return self._model.encode(
            text, normalize_embeddings=True, show_progress_bar=False
        )

    def score(self, job: Job) -> float:
        """Cosine similarity between resume and job (dot product of unit vectors)."""
        job_emb = self._encode_job(job)
        return float(self._np.dot(self.resume_embedding, job_emb))

    def score_text(self, text: str) -> float:
        """Score a raw text string (useful for testing)."""
        emb = self._model.encode(text, normalize_embeddings=True, show_progress_bar=False)
        return float(self._np.dot(self.resume_embedding, emb))

    def batch_score(self, jobs: list[Job]) -> list[float]:
        """Encode all jobs in one batch (much faster than one-by-one)."""
        if not jobs:
            return []
        texts = [
            (job.title + " " + job.description_clean[:2000]).strip()
            for job in jobs
        ]
        embeddings = self._model.encode(
            texts,
            normalize_embeddings=True,
            batch_size=32,
            show_progress_bar=False,
        )
        return [
            float(self._np.dot(self.resume_embedding, emb))
            for emb in embeddings
        ]


# ── Tier 2b: Skill Overlap ─────────────────────────────────────────────────────

def skill_overlap_score(job: Job, your_skills: set[str]) -> float:
    """
    Count how many of your_skills appear in the job title+description (case-insensitive).
    Returns fraction of matched skills (0.0–1.0).
    """
    if not your_skills:
        return 0.0
    text = (job.title + " " + job.description_clean).lower()
    matched = sum(1 for skill in your_skills if skill.lower() in text)
    return matched / len(your_skills)


def composite_score(semantic: float, skill_overlap: float) -> float:
    """Weighted composite: 65% semantic similarity + 35% skill overlap."""
    return 0.65 * semantic + 0.35 * skill_overlap


# ── Tier 3: LLM Scoring (Claude Haiku) ────────────────────────────────────────

LLM_SYSTEM_PROMPT = (
    "You are evaluating job-candidate fit. Be precise and concise. "
    "Always respond with valid JSON only. No markdown, no explanation outside the JSON."
)

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


def llm_score_job(
    job: Job,
    resume_summary: str,
    client: "anthropic.Anthropic",  # type: ignore[name-defined]  # noqa: F821
) -> dict:
    """
    Call Claude Haiku to score a single job.
    Returns a dict with keys: score, verdict, strengths, gaps, one_liner.
    On any error returns a safe default so the pipeline never crashes.
    """
    import json as _json

    default = {
        "score": 0,
        "verdict": "skip",
        "strengths": [],
        "gaps": [],
        "one_liner": "LLM error",
    }

    try:
        prompt = LLM_USER_PROMPT.format(
            resume_summary=resume_summary.strip(),
            title=job.title,
            company=job.company,
            location=job.location,
            employment_type=job.employment_type,
            description=job.description_clean[:2000],
        )

        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=400,
            system=LLM_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_text = message.content[0].text.strip()

        # Strip accidental markdown code fences
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
            raw_text = raw_text.strip()

        result = _json.loads(raw_text)

        # Validate and clamp
        result["score"] = max(0, min(100, int(result.get("score", 0))))
        result["verdict"] = str(result.get("verdict", "skip")).lower()
        if result["verdict"] not in ("apply", "maybe", "skip"):
            result["verdict"] = "skip"
        result["strengths"] = list(result.get("strengths", []))[:3]
        result["gaps"] = list(result.get("gaps", []))[:2]
        result["one_liner"] = str(result.get("one_liner", ""))[:200]

        return result

    except Exception as e:
        log.error(f"[LLM] Scoring failed for '{job.title}' @ '{job.company}': {e}")
        return default


# ── Full Pipeline ──────────────────────────────────────────────────────────────

def run_scoring_pipeline(
    jobs: list[Job],
    matcher: SemanticMatcher,
    your_skills: set[str],
    resume_summary: str,
    semantic_threshold: float,
    llm_threshold: float,
    llm_alert_threshold: int,
    max_llm_calls: int = 25,
) -> list[Job]:
    """
    Full three-tier scoring pipeline:

    1. keyword_prefilter  → discard jobs below min_keyword_hits
    2. batch semantic score all remaining jobs
    3. compute skill_overlap + composite score
    4. persist composite scores to DB
    5. LLM-score jobs with composite > llm_threshold (capped at max_llm_calls)
    6. Return jobs with llm_score >= llm_alert_threshold, sorted by llm_score DESC
    """
    if not jobs:
        return []

    # ── Tier 1: Keyword pre-filter ─────────────────────────────────────────────
    kw_passed = [j for j in jobs if keyword_prefilter(j, config.KEYWORD_MIN_HITS)]
    log.info(
        f"[Score] Keyword filter: {len(kw_passed)}/{len(jobs)} passed "
        f"(min_hits={config.KEYWORD_MIN_HITS})"
    )

    if not kw_passed:
        return []

    # ── Tier 2: Semantic + skill overlap ──────────────────────────────────────
    log.info(f"[Score] Running semantic scoring on {len(kw_passed)} jobs...")
    semantic_scores = matcher.batch_score(kw_passed)

    candidates: list[tuple[Job, float]] = []
    for job, sem_score in zip(kw_passed, semantic_scores):
        if sem_score < semantic_threshold:
            continue
        skill_score = skill_overlap_score(job, your_skills)
        comp = composite_score(sem_score, skill_score)
        job.match_score = comp
        db.update_scores(job.id, match_score=comp)
        candidates.append((job, comp))

    candidates.sort(key=lambda x: x[1], reverse=True)
    log.info(
        f"[Score] {len(candidates)} candidates above semantic threshold {semantic_threshold}"
    )

    if not candidates or not config.ENABLE_LLM_SCORING:
        return [j for j, _ in candidates]

    # ── Tier 3: LLM scoring ────────────────────────────────────────────────────
    llm_candidates = [(j, c) for j, c in candidates if c >= llm_threshold]
    llm_candidates = llm_candidates[:max_llm_calls]
    log.info(
        f"[LLM] Scoring {len(llm_candidates)} jobs "
        f"(composite > {llm_threshold}, cap={max_llm_calls})"
    )

    if not llm_candidates:
        return [j for j, _ in candidates]

    try:
        import anthropic  # type: ignore[import]
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    except ImportError:
        log.error("[LLM] 'anthropic' not installed. Run: pip install anthropic")
        return [j for j, _ in candidates]
    except Exception as e:
        log.error(f"[LLM] Failed to init Anthropic client: {e}")
        return [j for j, _ in candidates]

    alerted_candidates: list[Job] = []

    for job, comp in llm_candidates:
        result = llm_score_job(job, resume_summary, client)

        job.llm_score = result["score"]
        job.llm_verdict = result["verdict"]
        job.llm_strengths = result["strengths"]
        job.llm_gaps = result["gaps"]
        job.llm_one_liner = result.get("one_liner", "")

        db.update_scores(
            job.id,
            match_score=comp,
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

        if result["score"] >= llm_alert_threshold:
            alerted_candidates.append(job)

    alerted_candidates.sort(key=lambda j: j.llm_score or 0, reverse=True)
    log.info(
        f"[LLM] {len(llm_candidates)} scored, "
        f"{len(alerted_candidates)} above alert threshold {llm_alert_threshold}"
    )
    return alerted_candidates
