#!/usr/bin/env python3
"""Job-hunt CLI.

  python run.py ui                  open the local web app (upload resume, edit profile)
  python run.py profile [--force]   parse resume.pdf -> profile.json from the command line
  python run.py collect [--only greenhouse,lever]   fetch jobs from all sources into data/jobs.db
  (more steps coming: match, salary, dashboard, refresh)
"""
import argparse
import json
import sys
from pathlib import Path

from jobhunt.config import load_config

ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "profile.json"


def load_profile() -> dict:
    if not PROFILE.exists():
        sys.exit("No profile.json yet. Run `python run.py ui` and upload your resume first.")
    return json.loads(PROFILE.read_text())


def cmd_collect(args) -> None:
    from jobhunt.collect import run_collect

    run_collect(load_config(), load_profile(), only=args.only.split(",") if args.only else None)


def cmd_profile(args) -> None:
    from jobhunt.profile_parser import parse_resume, save_profile

    cfg = load_config()
    resume = ROOT / (args.resume or cfg.get("resume_path", "resume.pdf"))
    if not resume.exists():
        sys.exit(f"Resume not found: {resume}")
    if PROFILE.exists() and not args.force:
        sys.exit("profile.json already exists (you may have edited it). Use --force to overwrite.")
    profile = parse_resume(resume, cfg)
    save_profile(profile, PROFILE)
    print(json.dumps(profile, indent=2, ensure_ascii=False))
    print(f"\nSaved to {PROFILE}. Review and correct it by hand before the next step.")


def cmd_ui(args) -> None:
    import threading
    import webbrowser

    from jobhunt.web.app import create_app

    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Job Hunt running at {url}  (Ctrl+C to stop)")
    create_app().run(host="127.0.0.1", port=args.port, debug=False)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("profile", help="parse resume into profile.json")
    sp.add_argument("--resume", help="path to resume PDF (default from config.yaml)")
    sp.add_argument("--force", action="store_true", help="overwrite an existing profile.json")
    sp.set_defaults(func=cmd_profile)
    sc = sub.add_parser("collect", help="fetch jobs from all sources")
    sc.add_argument("--only", help="comma-separated collector names")
    sc.set_defaults(func=cmd_collect)
    su = sub.add_parser("ui", help="open the local web app")
    su.add_argument("--port", type=int, default=8766)
    su.add_argument("--no-browser", action="store_true")
    su.set_defaults(func=cmd_ui)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
