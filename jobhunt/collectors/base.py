"""Collector interface + shared helpers. Add a source = add a module with a Collector subclass and register it."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone

from ..db import Job


class Collector:
    name: str = "base"
    needs_env: tuple[str, ...] = ()   # env vars required; collector is skipped if any are missing

    def __init__(self, config: dict, profile: dict):
        self.config = config
        self.profile = profile
        self.errors: list[str] = []

    def collect(self) -> list[Job]:
        raise NotImplementedError


def html_to_text(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(s)                      # Greenhouse double-escapes its HTML
    s = re.sub(r"(?i)<br\s*/?>|</(p|li|h\d|div)>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "• ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def iso_from_ms(ms) -> str | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return None


def iso_date(s: str | None) -> str | None:
    return s[:10] if s else None


PERIODS = {"year": "year", "1 year": "year", "yearly": "year", "annual": "year", "per-year-salary": "year",
           "month": "month", "1 month": "month", "monthly": "month", "per-month-salary": "month",
           "hour": "hour", "1 hour": "hour", "hourly": "hour", "per-hour-wage": "hour"}


def norm_period(p: str | None) -> str | None:
    return PERIODS.get((p or "").strip().lower()) if p else None
