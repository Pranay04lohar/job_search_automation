"""Central configuration — edit this file to customise search terms, resume, thresholds."""

import os

from dotenv import load_dotenv

load_dotenv()

# ── Telegram ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ── OpenRouter (OpenAI-compatible) ─────────────────────────────────────────────
# https://openrouter.ai/docs
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
# Free model for LLM scoring.
# tencent/hy3-preview:free consistently returns null content — unusable.
# google/gemma-3-27b-it:free gives reliable JSON output on this prompt size.
OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free")

# ── Healthcheck (optional — e.g. healthchecks.io) ─────────────────────────────
HEALTHCHECK_URL: str = os.getenv("HEALTHCHECK_URL", "")

# ── Search Configuration ───────────────────────────────────────────────────────

# Two locations cover everything: primary city + pan-India/remote
LOCATIONS: list[str] = [
    "Bengaluru, India",   # primary city; LinkedIn/Indeed return BLR-specific results
    "India",              # catches Hyd/Pune/Noida/Gurgaon/Mumbai/remote in one call
]

SEARCH_TERMS: list[str] = [
    # LLM / GenAI (highest priority)
    "LLM engineer",
    "generative AI engineer",
    "LLM application developer",
    "RAG developer",
    # ML / AI
    "machine learning engineer",
    "AI engineer intern",
    "AI Python developer",
    # NLP / MLOps
    "NLP engineer",
    "MLOps engineer",
    # Voice / Agentic AI
    "voice AI engineer",
    "AI agent developer",
    # Python / Full Stack
    "Python developer machine learning",
    "backend developer AI",
]

HOURS_OLD: int = 24            # Only jobs from last 24 hours
RESULTS_PER_TERM: int = 25     # Per platform per search term per location

# ── Title-based Exclude Filter ─────────────────────────────────────────────────
# Jobs whose title contains any of these (case-insensitive) are dropped early.
EXCLUDED_TITLE_KEYWORDS: list[str] = [
    # Senior / management roles — include abbreviations like "Sr Developer", "Sr. Engineer"
    "senior ", "sr ", "sr.", "lead ", "principal ", "staff engineer", "engineering manager",
    "head of", " vp ", "chief ", "director", "manager",
    # Frontend-only
    "frontend", "front-end", "front end", "react developer",
    "angular developer", "vue developer", "ui developer", "ux engineer",
    # Pure DevOps / Cloud (no AI)
    "devops engineer", "site reliability engineer", "sre engineer",
    "cloud engineer", "infrastructure engineer",
    # Unrelated
    "data engineer", "etl developer", "ios developer", "android developer",
    "sales engineer", "marketing", "business analyst", "scrum master",
]

# ── Scoring Thresholds ─────────────────────────────────────────────────────────
KEYWORD_MIN_HITS: int = 1         # Discard jobs with no relevant keywords at all
SEMANTIC_THRESHOLD: float = 0.25  # Tier 2: keep jobs above this similarity
LLM_THRESHOLD: float = 0.10       # Tier 3: very low floor — semantic gate (0.25) is the real filter
LLM_ALERT_THRESHOLD: int = 40     # Alert if LLM score >= this (lower = more alerts)
MAX_LLM_CALLS_PER_RUN: int = 60   # Groq free tier is generous — score up to 60 per run

# Fallback: when LLM produces 0 alerts, alert top N semantic candidates instead
FALLBACK_COMPOSITE_THRESHOLD: float = 0.42
FALLBACK_MAX_ALERTS: int = 20

# ── Resume Data ────────────────────────────────────────────────────────────────
RESUME_TEXT: str = """
Software Development Engineer Intern at Bespoke Technology (Client: Nurix AI) — Jan 2026–Present
- Built end-to-end conversational AI voice agents integrating STT (Deepgram), LLM (GPT-4.1 mini), TTS (ElevenLabs) across 4 production APIs including MS Dynamics 365
- Designed serverless Python automation pipelines on AWS Lambda for pre-call lead enrichment and post-call CRM updates
- Improved LLM agent reliability ~40% through prompt optimization and API integration testing
- Delivered automated outreach pipeline processing 100+ outbound leads/day

Projects:
GeoLLM — FastAPI + SentenceTransformers + Google Earth Engine + FAISS + Redis + Azure Container Apps
- Hierarchical LLM intent routing, satellite analysis (NDVI/LST/LULC), SSE streaming, MapLibre rendering

Prompt2Shell — Fine-tuned Phi-3-mini (3.8B) via QLoRA, production REST API on FastAPI + Modal
- 30% improvement in shell command accuracy; 80% inference latency reduction

Skills: Python, FastAPI, Flask, LangChain, RAG, FAISS, LLM Orchestration, Prompt Engineering,
QLoRA fine-tuning, Hugging Face, NLP (BERT/SBERT), spaCy, NLTK, AWS Lambda, Docker,
Azure Container Apps, PostgreSQL, Redis, REST APIs, Git, JavaScript, React, Next.js,
PyTorch, Scikit-learn, MongoDB
"""

RESUME_SUMMARY: str = """
Fresher SDE intern with hands-on production experience in LLM orchestration, voice AI pipelines,
RAG systems, and serverless cloud automation. Built real systems used by 100+ daily active leads.
Strong in Python, FastAPI, LangChain, AWS Lambda, FAISS, and Hugging Face. Seeking ML/AI/LLM
engineering roles or internships in India (BLR/Pune/Hyd/NCR) or remote.
"""

YOUR_SKILLS: set[str] = {
    "python", "fastapi", "flask", "langchain", "llm", "large language model",
    "rag", "retrieval augmented generation", "faiss", "vector database",
    "aws lambda", "serverless", "docker", "azure", "postgresql", "redis",
    "sentence transformers", "hugging face", "pytorch", "nlp", "bert", "sbert",
    "machine learning", "deep learning", "gpt", "openai", "anthropic",
    "prompt engineering", "agentic", "voice ai", "stt", "tts", "deepgram",
    "elevenlabs", "crm", "automation", "rest api", "git", "react", "next.js",
    "scikit-learn", "mongodb", "spacy", "nltk", "qlora", "fine-tuning",
    "google earth engine", "geospatial", "sse", "streaming",
}

# ── Schedule ───────────────────────────────────────────────────────────────────
SCHEDULE_INTERVAL_HOURS: int = 6

# ── Paths ──────────────────────────────────────────────────────────────────────
DB_PATH: str = "jobs.db"
LOG_DIR: str = "logs"
COOKIES_DIR: str = "cookies"

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# ── Feature Flags ──────────────────────────────────────────────────────────────
ENABLE_WELLFOUND: bool = True
ENABLE_HIRIST: bool = False   # hirist.com API is dead (503); needs Playwright rewrite
ENABLE_INSTAHYRE: bool = True
# Naukri direct API scraper (no Selenium/cookies required; uses internal JSON API).
# Set True once you've confirmed it works for your account region.
# For better results, export your Naukri session cookies to
# cookies/naukri_cookies.txt (Netscape format) or cookies/naukri_cookies.json
ENABLE_NAUKRI: bool = True
ENABLE_LLM_SCORING: bool = True       # Set False to disable LLM calls (cost = $0)
ENABLE_TELEGRAM_ALERTS: bool = True
SEND_DAILY_SUMMARY: bool = True
