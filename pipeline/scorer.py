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
    # Core AI/ML — all common abbreviations and spellings included
    "python", "machine learning", "ml engineer", "ai engineer", "ai developer",
    "llm", "nlp", "deep learning", "data science", "artificial intelligence",
    "language model", "neural", "gen ai", "genai", "generative ai",
    "generative artificial", "large language",
    # Frameworks / tools
    "fastapi", "langchain", "rag", "vector", "embedding", "transformer",
    "hugging face", "huggingface", "pytorch", "tensorflow", "scikit",
    # General engineering (keeps python dev / fullstack AI jobs)
    "automation", "backend", "api", "aws", "cloud",
    # Experience level markers
    "intern", "fresher", "entry level", "junior", "graduate",
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
        # Use cached model if available; skip HuggingFace Hub version-check network calls.
        # On first run this downloads the model; subsequent runs load from disk instantly.
        try:
            self._model = SentenceTransformer(
                "all-MiniLM-L6-v2", local_files_only=True
            )
        except Exception:
            log.info("[Scorer] Model not cached yet — downloading from HuggingFace...")
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
RESUME: {resume_summary}

JOB: {title} at {company} ({location}, {employment_type})
{description}

Return ONLY this JSON (no markdown):
{{"score":<0-100>,"verdict":"apply|maybe|skip","strengths":["s1","s2"],"gaps":["g1"],"one_liner":"one sentence"}}"""


def llm_score_job(
    job: Job,
    resume_summary: str,
) -> dict:
    """
    Call an OpenRouter model to score a single job.
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
            resume_summary=resume_summary.strip()[:600],
            title=job.title,
            company=job.company,
            location=job.location,
            employment_type=job.employment_type,
            description=job.description_clean[:800],
        )

        raw_text = _openrouter_chat_completion(
            api_key=config.OPENROUTER_API_KEY,
            model=config.OPENROUTER_MODEL,
            system=LLM_SYSTEM_PROMPT,
            user=prompt,
            max_tokens=250,
        ).strip()

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


# ── OpenRouter helper (OpenAI-compatible Chat Completions) ─────────────────────
def _openrouter_chat_completion(
    *,
    api_key: str,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
) -> str:
    """
    Minimal OpenRouter call using OpenAI-compatible endpoint.
    Returns assistant message content as a string.
    """
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is missing in environment/.env")
    if not model:
        raise RuntimeError("OPENROUTER_MODEL is empty")

    try:
        import httpx  # type: ignore[import]
    except ImportError as e:
        raise ImportError("httpx is not installed. Run: pip install httpx") from e

    # Supports OpenRouter (default) or Groq (set GROQ_API_KEY + model like llama-3.3-70b-versatile)
    import os as _os
    groq_key = _os.getenv("GROQ_API_KEY", "")
    if groq_key:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {groq_key}",
            "Content-Type": "application/json",
        }
    else:
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://localhost",
            "X-Title": "job_search_automation",
        }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }

    import time as _time
    with httpx.Client(timeout=45) as client:
        for attempt in range(2):
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code == 429:
                wait = 8 * (attempt + 1)   # 8s, 16s — then give up fast
                log.warning(f"[LLM] OpenRouter 429 rate limit — waiting {wait}s (attempt {attempt+1}/2)...")
                _time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        else:
            raise RuntimeError("OpenRouter 429 rate limit after 2 retries.")
        data = resp.json()

    try:
        content = data["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"Unexpected OpenRouter response shape: {data}") from e

    if content is None:
        # Free-tier models occasionally return null content (refusal / rate limit)
        finish_reason = data.get("choices", [{}])[0].get("finish_reason", "unknown")
        raise RuntimeError(
            f"OpenRouter returned null content (finish_reason={finish_reason}). "
            "Model may be rate-limiting or refusing the request."
        )
    return content


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
    # Jobs from card-only scrapers (Naukri) have short synthetic descriptions.
    # For those, encode title+skills as a fairer semantic proxy instead of the
    # sparse synthetic description — this prevents good Naukri jobs from being
    # silently dropped just because the card had little text.
    SPARSE_DESC_LEN = 220  # descriptions shorter than this are likely card-only

    log.info(f"[Score] Running semantic scoring on {len(kw_passed)} jobs...")
    semantic_scores = matcher.batch_score(kw_passed)

    # For sparse-description jobs, rescore with title+skills only
    sparse_jobs = [
        j for j in kw_passed if len(j.description_clean) < SPARSE_DESC_LEN
    ]
    sparse_title_scores: dict[int, float] = {}
    if sparse_jobs:
        sparse_texts = [
            (j.title + " " + " ".join(j.skills[:10])).strip()
            for j in sparse_jobs
        ]
        sparse_embeddings = matcher._model.encode(
            sparse_texts, normalize_embeddings=True,
            batch_size=32, show_progress_bar=False,
        )
        try:
            import numpy as _np
        except ImportError:
            _np = None  # type: ignore[assignment]
        sparse_title_scores = {
            id(j): float(_np.dot(matcher.resume_embedding, emb))  # type: ignore[union-attr]
            for j, emb in zip(sparse_jobs, sparse_embeddings)
        }
        log.info(
            f"[Score] {len(sparse_jobs)} jobs with sparse descriptions — "
            "rescoring with title+skills"
        )

    candidates: list[tuple[Job, float]] = []
    dropped_by_platform: dict[str, int] = {}
    for job, sem_score in zip(kw_passed, semantic_scores):
        # Use the better of description-based score or title-only score
        if len(job.description_clean) < SPARSE_DESC_LEN:
            title_score = sparse_title_scores.get(id(job), sem_score)
            effective_score = max(sem_score, title_score)
        else:
            effective_score = sem_score

        if effective_score < semantic_threshold:
            dropped_by_platform[job.platform] = (
                dropped_by_platform.get(job.platform, 0) + 1
            )
            continue
        skill_score = skill_overlap_score(job, your_skills)
        comp = composite_score(effective_score, skill_score)
        job.match_score = comp
        db.update_scores(job.id, match_score=comp)
        candidates.append((job, comp))

    candidates.sort(key=lambda x: x[1], reverse=True)

    if dropped_by_platform:
        drops = ", ".join(f"{p}:{n}" for p, n in sorted(dropped_by_platform.items()))
        log.info(f"[Score] Dropped below semantic threshold by platform — {drops}")

    # Log per-platform pass counts for visibility
    platform_counts: dict[str, int] = {}
    for job, _ in candidates:
        platform_counts[job.platform] = platform_counts.get(job.platform, 0) + 1
    if platform_counts:
        passes = ", ".join(f"{p}:{n}" for p, n in sorted(platform_counts.items()))
        log.info(
            f"[Score] {len(candidates)} candidates above semantic threshold "
            f"{semantic_threshold} — by platform: {passes}"
        )

    if not candidates or not config.ENABLE_LLM_SCORING:
        return [j for j, _ in candidates]

    # ── Tier 3: LLM scoring ────────────────────────────────────────────────────
    # Platform-aware candidate selection: each platform gets a guaranteed minimum
    # allocation of LLM slots so Naukri/Wellfound/Instahyre are never crowded out
    # by the sheer volume of LinkedIn/Indeed results.
    eligible = [(j, c) for j, c in candidates if c >= llm_threshold]

    from collections import defaultdict as _dd
    platform_groups: dict = _dd(list)
    for j, c in eligible:
        platform_groups[j.platform].append((j, c))

    n_platforms = max(1, len(platform_groups))
    # Each platform gets at least (cap / n_platforms) slots, with remaining filled
    # by top-composite candidates regardless of platform.
    per_platform_min = max(5, max_llm_calls // n_platforms)

    llm_candidates: list[tuple] = []
    seen_ids: set[str] = set()

    # First pass: guaranteed slots per platform (sorted by composite within platform)
    for platform, group in sorted(platform_groups.items()):
        for j, c in group[:per_platform_min]:
            if j.id not in seen_ids:
                llm_candidates.append((j, c))
                seen_ids.add(j.id)

    # Second pass: fill remaining cap with top-composite candidates not yet selected
    for j, c in eligible:
        if len(llm_candidates) >= max_llm_calls:
            break
        if j.id not in seen_ids:
            llm_candidates.append((j, c))
            seen_ids.add(j.id)

    # Keep highest composite first so best jobs are scored first
    llm_candidates.sort(key=lambda x: x[1], reverse=True)

    # Log per-platform breakdown of LLM candidates
    llm_by_platform: dict[str, int] = {}
    for j, _ in llm_candidates:
        llm_by_platform[j.platform] = llm_by_platform.get(j.platform, 0) + 1
    platform_breakdown = ", ".join(
        f"{p}:{n}" for p, n in sorted(llm_by_platform.items())
    )
    log.info(
        f"[LLM] Scoring {len(llm_candidates)} jobs "
        f"(composite > {llm_threshold}, cap={max_llm_calls}) "
        f"— by platform: {platform_breakdown}"
    )

    if not llm_candidates:
        # No jobs strong enough for LLM — apply fallback threshold so we don't
        # alert on weak semantic matches that happen to be the "best of a bad batch"
        fallback = [
            j for j, comp in candidates
            if comp >= config.FALLBACK_COMPOSITE_THRESHOLD
        ][:config.FALLBACK_MAX_ALERTS]
        if fallback:
            for j in fallback:
                j.llm_one_liner = j.llm_one_liner or "Matched by semantic similarity"
            log.info(
                f"[LLM] No LLM candidates (all below composite {llm_threshold}) "
                f"— returning {len(fallback)} semantic picks above fallback threshold "
                f"{config.FALLBACK_COMPOSITE_THRESHOLD}"
            )
        else:
            log.info(
                "[LLM] No candidates above fallback threshold either — no alerts this run."
            )
        return fallback

    if not config.OPENROUTER_API_KEY:
        log.error("[LLM] OPENROUTER_API_KEY is missing. Set it in .env to enable LLM scoring.")
        return [j for j, _ in candidates]

    alerted_candidates: list[Job] = []
    consecutive_failures = 0  # abort LLM loop early if API is completely down
    cache_hits = 0

    for job, comp in llm_candidates:
        # Reuse a prior LLM score if the same role (title+company) was already scored.
        # This saves Groq quota for genuinely new jobs.
        cached = db.get_cached_llm_score(job.content_hash)
        if cached:
            result = cached
            cache_hits += 1
            _log.debug(f"[LLM] Cache hit: {job.title} @ {job.company} (score={result['score']})")
        else:
            # Small proactive delay to stay within Groq's rate limit (~30 rpm free tier)
            import time as _t
            _t.sleep(1.2)
            result = llm_score_job(job, resume_summary)

        # If the LLM returned a zero score due to error, count it as a failure.
        # After 2 consecutive failures, stop retrying — the API is down for this run.
        if result["score"] == 0 and result["verdict"] == "skip" and result.get("one_liner") == "LLM error":
            consecutive_failures += 1
            if consecutive_failures >= 2:
                log.warning(
                    "[LLM] 2 consecutive failures — API appears down (rate limit / outage). "
                    "Aborting LLM scoring and falling back to semantic scores."
                )
                break
        else:
            consecutive_failures = 0

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
        f"[LLM] {len(llm_candidates)} scored "
        f"({cache_hits} from cache, {len(llm_candidates) - cache_hits} fresh API calls), "
        f"{len(alerted_candidates)} above alert threshold {llm_alert_threshold}"
    )

    # ── Semantic fallback ─────────────────────────────────────────────────────
    # If LLM scoring produced zero alerts (e.g. free-tier model returned null
    # for every call), fall back to top semantic candidates so you always get
    # at least some alerts every run.
    if not alerted_candidates:
        log.warning(
            "[LLM] No jobs passed LLM threshold — falling back to top semantic candidates."
        )
        fallback = [
            j for j, comp in candidates
            if comp >= config.FALLBACK_COMPOSITE_THRESHOLD
        ][:config.FALLBACK_MAX_ALERTS]
        for j in fallback:
            j.llm_one_liner = j.llm_one_liner or "Matched by semantic similarity"
        log.info(f"[LLM] Fallback: returning {len(fallback)} semantic top picks")
        return fallback

    return alerted_candidates
