"""Assemble the job list shown in the UI: DB rows + match pre-score + salary + 'new' flag."""
from __future__ import annotations

import json

from .db import DB_PATH, connect
from .geo import canonical_location
from .match import Scorer
from .match.keyword import required_years
from .salary import listed_salary
from .skills import TAXONOMY_VERSION, extract_skills

_cache: dict = {"key": None, "jobs": [], "meta": {}}


def refresh_job_skills(con) -> int:
    """Extract required skills for jobs not yet processed with the current taxonomy. Returns how many were done."""
    todo = con.execute("""SELECT j.id, j.title, j.description FROM jobs j LEFT JOIN job_skills s ON s.job_id = j.id
                          WHERE s.job_id IS NULL OR s.tax_version != ?""", (TAXONOMY_VERSION,)).fetchall()
    con.executemany("INSERT OR REPLACE INTO job_skills (job_id, tax_version, skills) VALUES (?, ?, ?)",
                    [(r["id"], TAXONOMY_VERSION, json.dumps({
                        "skills": extract_skills(f"{r['title']}\n{r['description'] or ''}"),
                        "years": required_years(r["title"], r["description"] or "")})) for r in todo])
    con.commit()
    return len(todo)


def _new_cutoff(con) -> str | None:
    """'New' = first seen in the latest run that found anything — except on the very first run (all would be new)."""
    runs = con.execute("SELECT started_at, stats FROM runs WHERE finished_at IS NOT NULL ORDER BY id").fetchall()
    adding = [r["started_at"] for r in runs if json.loads(r["stats"] or "{}").get("new", 0) > 0]
    if len(adding) < 2 and runs and runs[0]["started_at"] == (adding[0] if adding else None):
        return None
    return adding[-1] if adding else None


def _req(raw: str) -> dict:
    d = json.loads(raw)
    return {"req": d["skills"], "req_years": tuple(d["years"])}


def all_jobs(profile: dict, profile_mtime: float) -> tuple[list[dict], dict]:
    if not DB_PATH.exists():
        return [], {"total": 0, "last_run": None, "new_count": 0}
    key = (DB_PATH.stat().st_mtime, profile_mtime)
    if _cache["key"] == key:
        return _cache["jobs"], _cache["meta"]
    con = connect()
    refresh_job_skills(con)
    key = (DB_PATH.stat().st_mtime, profile_mtime)
    cutoff = _new_cutoff(con)
    scorer = Scorer(profile)
    out = []
    for r in con.execute("SELECT j.*, s.skills AS req FROM jobs j JOIN job_skills s ON s.job_id = j.id"):
        m = scorer.score(r["title"], "", r["location"] or "", bool(r["remote"]), **_req(r["req"]))
        city = canonical_location(r["location"] or "", bool(r["remote"]))
        out.append({
            "id": r["id"], "company": r["company"], "title": r["title"], "location": r["location"], "city": city,
            "remote": bool(r["remote"]), "url": r["url"], "source": r["source"], "posted_at": r["posted_at"],
            "first_seen": r["first_seen"][:10], "is_new": bool(cutoff and r["first_seen"] >= cutoff),
            "salary": listed_salary(r), "title_only": len(r["description"] or "") < 250, "region_unclear": city == "Remote" and "india" not in (r["location"] or "").lower()
                       and not any(w in (r["location"] or "").lower() for w in ("global", "apac", "anywhere", "worldwide")),
            **m,
        })
    out.sort(key=lambda j: -j["score"])
    last = con.execute("SELECT started_at, stats FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    meta = {"total": len(out), "new_count": sum(j["is_new"] for j in out),
            "last_run": last["started_at"] if last else None,
            "last_stats": json.loads(last["stats"]) if last else None}
    _cache.update(key=key, jobs=out, meta=meta)
    return out, meta


def job_detail(job_id: int) -> dict | None:
    con = connect()
    r = con.execute("SELECT description FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return {"description": r["description"]} if r else None
