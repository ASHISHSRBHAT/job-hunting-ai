"""SQLite storage. One row per unique (company, title, location)."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "jobs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY,
    dedupe_key      TEXT UNIQUE NOT NULL,
    source          TEXT NOT NULL,          -- greenhouse / lever / ashby / adzuna / ...
    source_id       TEXT,
    company         TEXT NOT NULL,
    title           TEXT NOT NULL,
    location        TEXT,
    remote          INTEGER DEFAULT 0,
    url             TEXT,
    description     TEXT,
    posted_at       TEXT,                   -- ISO date from the source, if given
    salary_min      REAL,                   -- as listed by the source, in salary_currency per salary_period
    salary_max      REAL,
    salary_currency TEXT,
    salary_period   TEXT,                   -- year / month / hour
    salary_is_estimate INTEGER DEFAULT 0,   -- 1 if the source itself flagged it as predicted
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    raw             TEXT
);
CREATE INDEX IF NOT EXISTS jobs_last_seen ON jobs(last_seen);
CREATE TABLE IF NOT EXISTS job_skills (      -- skills a job asks for (profile-independent, so cached)
    job_id      INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    tax_version TEXT NOT NULL,
    skills      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    stats       TEXT
);
"""


@dataclass
class Job:
    source: str
    source_id: str
    company: str
    title: str
    location: str
    url: str
    description: str = ""
    remote: bool = False
    posted_at: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    salary_is_estimate: bool = False
    raw: dict = field(default_factory=dict, repr=False)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+#]+", " ", (s or "").lower())).strip()


def dedupe_key(job: Job) -> str:
    from .geo import canonical_location
    return "|".join([_norm(job.company), _norm(job.title), _norm(canonical_location(job.location, job.remote))])


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def upsert_jobs(con: sqlite3.Connection, jobs: list[Job], seen_at: str) -> tuple[int, int]:
    """Insert new jobs, refresh last_seen on known ones. Returns (new, updated)."""
    new = updated = 0
    for j in jobs:
        d = asdict(j)
        d["raw"] = json.dumps(d["raw"], ensure_ascii=False)[:200_000]
        d["remote"] = int(j.remote)
        d["salary_is_estimate"] = int(j.salary_is_estimate)
        d["dedupe_key"] = dedupe_key(j)
        d["seen"] = seen_at
        cur = con.execute("SELECT id FROM jobs WHERE dedupe_key = ?", (d["dedupe_key"],)).fetchone()
        if cur is None:
            con.execute("""INSERT INTO jobs (dedupe_key, source, source_id, company, title, location, remote, url,
                description, posted_at, salary_min, salary_max, salary_currency, salary_period, salary_is_estimate,
                first_seen, last_seen, raw)
                VALUES (:dedupe_key, :source, :source_id, :company, :title, :location, :remote, :url, :description,
                :posted_at, :salary_min, :salary_max, :salary_currency, :salary_period, :salary_is_estimate,
                :seen, :seen, :raw)""", d)
            new += 1
        else:
            # Keep the richest data: a careers-page posting beats an aggregator snippet; never drop a listed salary.
            con.execute("""UPDATE jobs SET last_seen = :seen,
                description = CASE WHEN length(coalesce(:description,'')) > length(coalesce(description,''))
                                   THEN :description ELSE description END,
                salary_min = coalesce(salary_min, :salary_min), salary_max = coalesce(salary_max, :salary_max),
                salary_currency = coalesce(salary_currency, :salary_currency),
                salary_period = coalesce(salary_period, :salary_period),
                posted_at = coalesce(posted_at, :posted_at)
                WHERE id = :id""", {**d, "id": cur["id"]})
            updated += 1
    con.commit()
    return new, updated
