"""Cookie loader helpers for scrapers.

Supports:
- JSON cookie exports (list-of-objects or flat dict)
- Netscape HTTP Cookie File exports (common from "cookies.txt" exporters)
"""

from __future__ import annotations

import json
from pathlib import Path


def load_cookies_any(path: Path) -> dict[str, str]:
    """
    Load cookies from a single file path.
    Returns a dict of {cookie_name: cookie_value}. Returns {} on any failure.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            return {}

        # Netscape cookie file
        if text.startswith("# Netscape HTTP Cookie File"):
            return _parse_netscape_cookie_file(text)

        # JSON export
        data = json.loads(text)
        if isinstance(data, list):
            out: dict[str, str] = {}
            for c in data:
                if isinstance(c, dict) and "name" in c and "value" in c:
                    out[str(c["name"])] = str(c["value"])
            return out
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception:
        return {}
    return {}


def load_cookies_first_existing(paths: list[Path]) -> dict[str, str]:
    """Try paths in order; load the first that exists and parses."""
    for p in paths:
        if p.exists():
            cookies = load_cookies_any(p)
            if cookies:
                return cookies
    return {}


def _parse_netscape_cookie_file(text: str) -> dict[str, str]:
    """
    Parse Netscape HTTP Cookie File format.
    Format (tab-separated):
      domain  flag  path  secure  expiration  name  value
    """
    cookies: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            # Some exporters use spaces; fall back to any whitespace
            parts = line.split()
        if len(parts) < 7:
            continue

        name = parts[5].strip()
        value = parts[6].strip()
        if name:
            cookies[name] = value
    return cookies

