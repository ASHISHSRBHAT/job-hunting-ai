"""Run every collector, store results in SQLite, record the run."""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

from .collectors.aggregators import Adzuna, Jooble, JSearch
from .collectors.email_alerts import EmailAlerts
from .collectors.ats import Ashby, Greenhouse, Lever, SmartRecruiters
from .db import ROOT, connect, now_iso, upsert_jobs

# To add a source: write a Collector subclass and append it here.
COLLECTORS = [Greenhouse, Lever, Ashby, SmartRecruiters, Adzuna, Jooble, JSearch, EmailAlerts]


def run_collect(config: dict, profile: dict, only: list[str] | None = None, verbose: bool = True) -> dict:
    load_dotenv(ROOT / ".env")
    con = connect()
    started = now_iso()
    run_id = con.execute("INSERT INTO runs (started_at) VALUES (?)", (started,)).lastrowid
    con.commit()

    active, skipped = [], {}
    for cls in COLLECTORS:
        if only and cls.name not in only:
            continue
        missing = [v for v in cls.needs_env if not os.environ.get(v)]
        if missing:
            skipped[cls.name] = f"no {', '.join(missing)} in .env"
            continue
        active.append(cls(config, profile))

    def _run(c):
        t = time.monotonic()
        try:
            jobs = c.collect()
        except Exception as e:
            c.errors.append(f"collector crashed: {type(e).__name__}: {e}")
            jobs = []
        return c, jobs, time.monotonic() - t

    stats = {"sources": {}, "skipped": skipped}
    with ThreadPoolExecutor(max_workers=len(active) or 1) as ex:  # different hosts, so parallel is still polite
        for c, jobs, secs in ex.map(_run, active):
            new, updated = upsert_jobs(con, jobs, started)
            stats["sources"][c.name] = {"fetched": len(jobs), "new": new, "already_known": updated,
                                        "errors": c.errors, "seconds": round(secs, 1)}
            if hasattr(c, "emails_read"):
                stats["sources"][c.name]["emails_read"] = c.emails_read
                stats["sources"][c.name]["imap"] = bool(os.environ.get("IMAP_USER"))
    from .jobs_view import refresh_job_skills
    refresh_job_skills(con)  # pre-compute so the UI opens instantly
    total = con.execute("SELECT count(*) FROM jobs").fetchone()[0]
    stats.update(total_in_db=total, new=sum(s["new"] for s in stats["sources"].values()))
    con.execute("UPDATE runs SET finished_at=?, stats=? WHERE id=?", (now_iso(), json.dumps(stats), run_id))
    con.commit()

    if verbose:
        print(f"{'source':<16}{'fetched':>8}{'new':>6}{'dup/known':>11}{'errors':>8}{'secs':>7}")
        for name, s in stats["sources"].items():
            print(f"{name:<16}{s['fetched']:>8}{s['new']:>6}{s['already_known']:>11}{len(s['errors']):>8}{s['seconds']:>7}")
            for e in s["errors"][:5]:
                print(f"    ! {e}")
        for name, why in skipped.items():
            print(f"{name:<16}  skipped ({why})")
        print(f"\n{stats['new']} new jobs this run · {total} jobs in database (data/jobs.db)")
    return stats
