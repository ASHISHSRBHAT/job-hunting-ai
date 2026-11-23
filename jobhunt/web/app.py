"""Local web UI: upload resume, review/edit the parsed profile.

Binds to 127.0.0.1 only — nothing is exposed to your network.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from ..config import load_config
from ..jobs_view import all_jobs, job_detail
from ..profile_parser import normalize_location, parse_resume, save_profile
from ..skills import SKILLS

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "profile.json"
RESUME = ROOT / "resume.pdf"
MAX_MB = 10

EDITABLE = {
    "skills": list, "past_titles": list, "education": list, "target_roles": list,
    "preferred_locations": list, "location": str, "experience_years": float, "min_salary_lpa": float,
}


_collect = {"running": False, "error": None, "stats": None}


def _config() -> dict:
    return load_config()


def _run_collect_bg() -> None:
    from ..collect import run_collect
    try:
        profile = json.loads(PROFILE.read_text())
        _collect["stats"] = run_collect(load_config(), profile, verbose=False)
        _collect["error"] = None
    except Exception as e:  # surfaced in the UI
        _collect["error"] = f"{type(e).__name__}: {e}"
    finally:
        _collect["running"] = False


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_MB * 1024 * 1024

    @app.get("/")
    def index():
        return render_template("index.html", known_skills=sorted(SKILLS))

    @app.get("/api/profile")
    def get_profile():
        if not PROFILE.exists():
            return jsonify({"profile": None, "has_resume": RESUME.exists()})
        return jsonify({"profile": json.loads(PROFILE.read_text()), "has_resume": RESUME.exists()})

    @app.post("/api/resume")
    def upload_resume():
        f = request.files.get("resume")
        if not f or not f.filename:
            return jsonify({"error": "No file received."}), 400
        data = f.read()
        if not data.startswith(b"%PDF"):
            return jsonify({"error": "That doesn't look like a PDF. Export your resume as PDF and try again."}), 400
        RESUME.write_bytes(data)
        try:
            profile = parse_resume(RESUME, _config())
        except SystemExit as e:  # parser signals unreadable PDFs this way
            return jsonify({"error": str(e)}), 422
        # Keep preferences the user already entered in the UI across re-uploads.
        if PROFILE.exists():
            old = json.loads(PROFILE.read_text())
            for k in ("target_roles", "preferred_locations", "min_salary_lpa"):
                if old.get(k) and not profile.get(k):
                    profile[k] = old[k]
        save_profile(profile, PROFILE)
        return jsonify({"profile": profile})

    @app.put("/api/profile")
    def put_profile():
        incoming = request.get_json(silent=True) or {}
        profile = json.loads(PROFILE.read_text()) if PROFILE.exists() else {}
        for key, typ in EDITABLE.items():
            if key not in incoming:
                continue
            val = incoming[key]
            if typ is list:
                val = [str(v).strip() for v in (val or []) if str(v).strip()]
            elif typ is float:
                val = float(val) if val not in (None, "") else None
            else:
                val = (str(val).strip() or None) if val is not None else None
            profile[key] = val
        # Guard against units mix-ups: 2000000 or 20,00,000 typed as rupees -> 20 LPA.
        sal = profile.get("min_salary_lpa")
        if sal and sal >= 1000:
            profile["min_salary_lpa"] = round(sal / 100000, 1)
        if profile.get("preferred_locations"):
            locs = [normalize_location(x) for x in profile["preferred_locations"]]
            profile["preferred_locations"] = list(dict.fromkeys(locs))
        if profile.get("location"):
            profile["location"] = normalize_location(profile["location"])
        profile["reviewed"] = True
        save_profile(profile, PROFILE)
        return jsonify({"profile": profile})

    @app.get("/api/jobs")
    def get_jobs():
        if not PROFILE.exists():
            return jsonify({"jobs": [], "meta": {"total": 0}, "error": "Upload your resume on the Profile tab first."})
        profile = json.loads(PROFILE.read_text())
        jobs, meta = all_jobs(profile, PROFILE.stat().st_mtime)
        return jsonify({"jobs": jobs, "meta": {**meta, "collecting": _collect["running"]}})

    @app.get("/api/jobs/<int:job_id>")
    def get_job(job_id: int):
        d = job_detail(job_id)
        return (jsonify(d), 200) if d else (jsonify({"error": "not found"}), 404)

    @app.post("/api/jobs/manual")
    def add_manual_job():
        """Add a job you found yourself (e.g. on LinkedIn/Naukri) by pasting its details."""
        import hashlib
        import re as _re

        from ..db import Job, connect, now_iso, upsert_jobs
        from ..geo import is_india_eligible

        d = request.get_json(silent=True) or {}
        title, company = (d.get("title") or "").strip(), (d.get("company") or "").strip()
        url, desc = (d.get("url") or "").strip(), (d.get("description") or "").strip()
        if not title or not company:
            return jsonify({"error": "Title and company are required."}), 400
        if url and not _re.match(r"https?://", url):
            return jsonify({"error": "The link should start with http:// or https://"}), 400
        site = next((s for s in ("linkedin", "naukri", "indeed", "glassdoor", "instahyre", "wellfound")
                     if s in url.lower()), "manual")
        loc = (d.get("location") or "").strip() or "India (not stated)"
        lo = hi = None
        if m := _re.search(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)", d.get("salary") or ""):
            lo, hi = float(m[1]) * 1e5, float(m[2]) * 1e5
        job = Job(source=f"{site}-pasted" if site != "manual" else "manual",
                  source_id=hashlib.sha1((url or title + company).encode()).hexdigest()[:16],
                  company=company, title=title, location=loc, url=url, description=desc,
                  remote="remote" in loc.lower(), posted_at=now_iso()[:10],
                  salary_min=lo, salary_max=hi, salary_currency="INR" if lo else None, salary_period="year" if lo else None)
        con = connect()
        new, _ = upsert_jobs(con, [job], now_iso())
        from ..db import dedupe_key
        row = con.execute("SELECT id FROM jobs WHERE dedupe_key = ?", (dedupe_key(job),)).fetchone()
        return jsonify({"id": row["id"], "new": bool(new),
                        "warning": None if is_india_eligible(loc, job.remote) else "Location doesn't look like India/remote."})

    @app.post("/api/collect")
    def start_collect():
        if not PROFILE.exists():
            return jsonify({"error": "Upload your resume first."}), 400
        if not _collect["running"]:
            _collect["running"] = True
            threading.Thread(target=_run_collect_bg, daemon=True).start()
        return jsonify({"running": True})

    @app.get("/api/collect")
    def collect_status():
        return jsonify(_collect)

    @app.errorhandler(413)
    def too_big(_):
        return jsonify({"error": f"File is larger than {MAX_MB} MB."}), 413

    return app
