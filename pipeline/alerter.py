"""Telegram alerter — sends job alerts with inline action buttons."""

import logging
import re
from datetime import datetime
from typing import Optional

import httpx

import db
from pipeline.models import Job

log = logging.getLogger(__name__)

# Score bar: filled blocks out of 10
_FILLED = "🟩"
_EMPTY = "⬜"


def _score_bar(score: int) -> str:
    """Return an emoji progress bar for a 0–100 LLM score."""
    filled = round(score / 10)
    return _FILLED * filled + _EMPTY * (10 - filled)


def _escape_md(text: str) -> str:
    """Escape special MarkdownV2 characters in dynamic text."""
    # Characters that must be escaped in MarkdownV2
    special = r"\_*[]()~`>#+-=|{}.!"
    return re.sub(r"([" + re.escape(special) + r"])", r"\\\1", str(text))


def _format_salary(job: Job) -> str:
    """Format salary range as human-readable string."""
    if not job.salary_min and not job.salary_max:
        return ""
    def fmt_lpa(val: int) -> str:
        lpa = val / 100_000
        return f"{lpa:.0f}L" if lpa == int(lpa) else f"{lpa:.1f}L"

    if job.salary_min and job.salary_max:
        return f"{fmt_lpa(job.salary_min)}–{fmt_lpa(job.salary_max)} PA"
    if job.salary_max:
        return f"Up to {fmt_lpa(job.salary_max)} PA"
    if job.salary_min:
        return f"From {fmt_lpa(job.salary_min)} PA"
    return ""


def _format_posted_date(dt: Optional[datetime]) -> str:
    if not dt:
        return "Unknown"
    try:
        # %-d is Linux-only and crashes on Windows; build cross-platform date string
        return f"{dt.strftime('%b')} {dt.day}"
    except Exception:
        return str(dt)[:10]


class TelegramAlerter:
    def __init__(self, token: str, chat_id: str) -> None:
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id
        self._client = httpx.Client(timeout=15)

    def _post(self, method: str, payload: dict) -> Optional[dict]:
        """POST to Telegram API. Returns response JSON or None on failure."""
        try:
            resp = self._client.post(f"{self.base_url}/{method}", json=payload)
            data = resp.json()
            if not data.get("ok"):
                log.error(f"[Telegram] API error ({method}): {data.get('description')}")
                return None
            return data
        except Exception as e:
            log.error(f"[Telegram] Request failed ({method}): {e}")
            return None

    def send_job_alert(self, job: Job) -> bool:
        """
        Send a formatted job alert message with inline keyboard.
        Returns True on success, False on failure.
        """
        # Use LLM score when available; fall back to semantic composite × 100
        is_semantic_fallback = not job.llm_score and (
            not job.llm_one_liner or job.llm_one_liner in ("LLM error", "Matched by semantic similarity")
        )
        score = job.llm_score if job.llm_score else round((job.match_score or 0) * 100)
        one_liner = "" if is_semantic_fallback else (job.llm_one_liner or "")
        platform_tag = job.platform.capitalize()
        remote_badge = "🌐 Remote" if job.is_remote else f"📍 {job.location}"

        posted_str = _format_posted_date(job.posted_at)
        salary_str = _format_salary(job)

        # Build MarkdownV2 message
        lines: list[str] = []
        lines.append(
            f"🔔 *{_escape_md(job.title)}* — {score}/100"
        )
        lines.append("")
        lines.append(
            f"🏢 {_escape_md(job.company)} \\| {_escape_md(remote_badge)}"
        )
        lines.append(
            f"📅 {_escape_md(posted_str)} · {_escape_md(platform_tag)}"
        )
        if salary_str:
            lines.append(f"💰 {_escape_md(salary_str)}")
        lines.append("")
        lines.append(_score_bar(score))

        if one_liner:
            lines.append("")
            lines.append(f'_"{_escape_md(one_liner)}"_')

        if job.llm_strengths:
            lines.append("")
            for s in job.llm_strengths[:3]:
                lines.append(f"✅ {_escape_md(s)}")

        if job.llm_gaps:
            lines.append("")
            for g in job.llm_gaps[:2]:
                lines.append(f"⚠️ {_escape_md(g)}")

        text = "\n".join(lines)

        # Inline keyboard
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "Apply ↗", "url": job.apply_url or "https://google.com"},
                ],
                [
                    {"text": "✅ Applied", "callback_data": f"applied:{job.id}"},
                    {"text": "❌ Skip",    "callback_data": f"skip:{job.id}"},
                    {"text": "🔖 Save",   "callback_data": f"save:{job.id}"},
                ],
            ]
        }

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "MarkdownV2",
            "reply_markup": keyboard,
            "disable_web_page_preview": True,
        }

        result = self._post("sendMessage", payload)
        if result:
            db.mark_alerted(job.id)
            log.info(f"[Telegram] Alerted: {job.title} @ {job.company} (score={score})")
            return True
        return False

    def send_error_alert(self, error: str, context: str = "") -> None:
        """Send a plain text error notification."""
        msg = f"⚠️ *Job Search Pipeline Error*\n\n{error}"
        if context:
            msg += f"\n\nContext: {context}"
        self._post("sendMessage", {
            "chat_id": self.chat_id,
            "text": msg,
            "parse_mode": "Markdown",
        })

    def send_daily_summary(self, stats: dict) -> None:
        """
        Send end-of-run summary with counts and top matches including apply links.

        Expected stats keys: total, this_week, alerted, applied, by_platform, top_jobs
        """
        total     = stats.get("total", 0)
        this_week = stats.get("this_week", 0)
        alerted   = stats.get("alerted", 0)
        applied   = stats.get("applied", 0)

        by_platform = stats.get("by_platform", {})
        platform_str = "  ".join(
            f"{p.capitalize()} {c}" for p, c in by_platform.items()
        ) if by_platform else "—"

        lines: list[str] = []
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("📊 *Job Search Run Summary*")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append(f"🗃  Total in DB:  *{total}*")
        lines.append(f"📅  Last 7 days: *{this_week}*")
        lines.append(f"🔔  Alerts sent: *{alerted}*")
        lines.append(f"✅  Applied:     *{applied}*")
        lines.append("")
        lines.append(f"📌  Platforms: {platform_str}")

        top_jobs = stats.get("top_jobs", [])
        if top_jobs:
            lines.append("")
            lines.append("🏆 *Top Matches:*")
            lines.append("")
            for i, j in enumerate(top_jobs, 1):
                score_val = j.get("llm_score") or round((j.get("match_score") or 0) * 100)
                title   = j.get("title", "Unknown Role")
                company = j.get("company", "Unknown Company")
                url     = j.get("apply_url") or ""

                bar_filled = round(score_val / 10)
                bar = "🟩" * bar_filled + "⬜" * (10 - bar_filled)

                if url:
                    lines.append(f"*{i}. [{title}]({url})*")
                else:
                    lines.append(f"*{i}. {title}*")
                lines.append(f"    🏢 {company}  |  {bar}  {score_val}/100")
                if url:
                    lines.append(f"    [Apply ↗]({url})")
                lines.append("")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")

        self._post("sendMessage", {
            "chat_id": self.chat_id,
            "text": "\n".join(lines),
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        })

    def handle_callback(self, callback_data: str) -> None:
        """
        Handle inline button callbacks.
        callback_data format: "action:job_id"
        Actions: applied, skip, save
        """
        try:
            action, job_id = callback_data.split(":", 1)
            status_map = {
                "applied": "applied",
                "skip": "rejected",
                "save": "saved",
            }
            status = status_map.get(action)
            if status:
                db.update_status(job_id, status)
                log.info(f"[Telegram] Callback: job {job_id} → {status}")
            else:
                log.warning(f"[Telegram] Unknown callback action: {action}")
        except Exception as e:
            log.error(f"[Telegram] handle_callback failed: {e}")

    def close(self) -> None:
        self._client.close()
