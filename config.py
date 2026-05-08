"""Central configuration — edit this file to customise search terms, resume, thresholds."""

import os

from dotenv import load_dotenv

load_dotenv()

# ── Telegram ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Anthropic ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# ── Healthcheck (optional — e.g. healthchecks.io) ─────────────────────────────
HEALTHCHECK_URL: str = os.getenv("HEALTHCHECK_URL", "")

# ── Search Configuration ───────────────────────────────────────────────────────
SEARCH_TERMS: list[str] = [
    "machine learning engineer",
    "LLM engineer",
    "AI engineer intern",
    "NLP engineer",
    "generative AI engineer",
    "ML engineer fresher",
    "AI ML intern",
]

LOCATION: str = "Bengaluru, India"
HOURS_OLD: int = 168           # Only jobs from last 7 days
RESULTS_PER_TERM: int = 50     # Per platform per search term

# ── Scoring Thresholds ─────────────────────────────────────────────────────────
KEYWORD_MIN_HITS: int = 2       # Discard jobs with fewer keyword matches
SEMANTIC_THRESHOLD: float = 0.38  # Discard jobs below this semantic similarity
LLM_THRESHOLD: float = 0.45    # Only LLM-score jobs above this composite score
LLM_ALERT_THRESHOLD: int = 62   # Only alert if LLM score >= this
MAX_LLM_CALLS_PER_RUN: int = 25  # Cost guard

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
ENABLE_HIRIST: bool = True
ENABLE_INSTAHYRE: bool = True
ENABLE_LLM_SCORING: bool = True       # Set False to disable LLM calls (cost = $0)
ENABLE_TELEGRAM_ALERTS: bool = True
SEND_DAILY_SUMMARY: bool = True
