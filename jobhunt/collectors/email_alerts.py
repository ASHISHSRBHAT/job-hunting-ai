"""LinkedIn / Naukri job-alert emails -> jobs.

These are emails the sites send *you*; reading your own inbox is not scraping their sites.
Two inputs (both optional, both used if available):
  1. IMAP (read-only): IMAP_USER + IMAP_PASSWORD in .env. For Gmail use an App Password.
  2. Folder: any .eml files saved into data/alerts/ (drag emails there from Apple Mail / Outlook).
"""
from __future__ import annotations

import email
import imaplib
import os
import re
from datetime import date, datetime, timedelta, timezone
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup, NavigableString

from ..db import ROOT, Job
from ..geo import city_of, is_india_eligible
from .base import Collector

ALERTS_DIR = ROOT / "data" / "alerts"
LOOKBACK_DAYS = 14

SENDERS = {"linkedin": re.compile(r"linkedin\.com$", re.I), "naukri": re.compile(r"naukri\.com$", re.I)}
JOB_URL = {
    "linkedin": re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#]*-)?(\d{6,})", re.I),
    "naukri": re.compile(r"naukri\.com/job-listings-([a-z0-9-]+?)-?(\d{9,})(?:[/?#]|$)", re.I),
}
NOISE = re.compile(r"^(view job|view all|apply( now)?|easy apply|see (all|more) jobs|actively recruiting|promoted|"
                   r"be an early applicant|new|save|\d+ (applicants?|connections?).*|.*school alum.*|"
                   r"\d+\s*(day|hour|week|minute)s? ago|today|posted.*|unsubscribe.*|manage alerts?.*|"
                   r"recommended for you|jobs? (you may be interested in|matching).*|·|\||-)$", re.I)
EXP_RX = re.compile(r"(\d{1,2})\s*-\s*(\d{1,2})\s*(?:yrs?|years?)", re.I)
SAL_RX = re.compile(r"(?:₹|rs\.?|inr)?\s*(\d+(?:\.\d+)?)\s*(?:l|lpa|lacs?|lakhs?)?\s*(?:-|–|to)\s*(?:₹|rs\.?)?\s*"
                    r"(\d+(?:\.\d+)?)\s*(l\b|lpa|lacs?|lakhs?)(?:\s*p\.?a\.?|/yr)?", re.I)


def _unwrap(url: str) -> str:
    """Follow tracking redirects that carry the real URL in a query param."""
    for _ in range(3):
        qs = parse_qs(urlparse(url).query)
        inner = next((v[0] for k, v in qs.items() if k.lower() in ("url", "u", "redirect", "target", "dest") and
                      v and v[0].startswith("http")), None)
        if not inner:
            break
        url = unquote(inner)
    return url


def _canonical(site: str, m: re.Match) -> tuple[str, str]:
    if site == "linkedin":
        return m[1], f"https://www.linkedin.com/jobs/view/{m[1]}/"
    return m[2], f"https://www.naukri.com/job-listings-{m[1]}-{m[2]}"


def _parse_salary(text: str):
    m = SAL_RX.search(text)
    if not m:
        return None, None
    lo, hi = float(m[1]), float(m[2])
    if lo > hi or hi > 500:
        return None, None
    return lo * 1e5, hi * 1e5


def _place_like(s: str) -> bool:
    """Short, digit-free, not a skills list — plausibly a city/country name."""
    from ..skills import extract_skills
    return (len(s) <= 40 and not re.search(r"\d", s) and len(extract_skills(s)) < 2
            and bool(re.fullmatch(r"[A-Z][\w .,'()-]*", s)))


def _fields_from_lines(title: str, lines: list[str]) -> dict:
    """Given a job title and the short text lines that follow it in the email, pick out company/location/etc."""
    lines = [l for l in (re.sub(r"\s+", " ", x).strip(" ·|•-") for x in lines) if l and not NOISE.match(l) and l != title]
    company = location = exp = None
    sal = (None, None)
    for l in lines:
        if exp is None and EXP_RX.search(l) and len(l) < 25:
            exp = l
            continue
        if sal == (None, None) and SAL_RX.search(l):
            sal = _parse_salary(l)
            continue
        if re.search(r"not disclosed", l, re.I):
            continue
        # "Acme · Bengaluru, Karnataka, India (Hybrid)" on one line
        parts = [p.strip() for p in re.split(r"\s+[·•|]\s+", l) if p.strip()]
        for p in parts:
            looks_loc = city_of(p) or re.search(r"\b(india|remote|hybrid|on-?site)\b", p, re.I)
            if looks_loc and location is None:
                location = p
            elif company is None and not looks_loc and len(p) <= 80:
                company = p
            elif location is None and _place_like(p):
                location = p   # an unrecognised place ("London") — kept so the India filter can reject it
        # keep scanning: salary / experience often come after company + location
    return {"company": company, "location": location, "exp": exp, "salary": sal}


def parse_alert(msg: Message) -> list[Job]:
    sender = parseaddr(msg.get("From", ""))[1].lower()
    site = next((s for s, rx in SENDERS.items() if rx.search(sender.split("@")[-1])), None)
    if not site:
        return []
    try:
        sent = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc).date().isoformat()
    except Exception:
        sent = None
    html = text = None
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype in ("text/html", "text/plain") and not part.get_filename():
            payload = part.get_payload(decode=True) or b""
            body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            if ctype == "text/html" and html is None:
                html = body
            elif ctype == "text/plain" and text is None:
                text = body

    found: dict[str, dict] = {}
    if html:
        soup = BeautifulSoup(html, "html.parser")
        anchors = []
        for a in soup.find_all("a", href=True):
            m = JOB_URL[site].search(_unwrap(a["href"]))
            if m:
                anchors.append((a, m))
        job_ids = {_canonical(site, m)[0] for _, m in anchors}
        for a, m in anchors:
            jid, url = _canonical(site, m)
            t = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
            if not t or NOISE.match(t) or len(t) > 120 or (jid in found and found[jid]["title"]):
                continue
            # Collect the text that follows this title, up to the next job's link.
            lines, n = [], 0
            for node in a.next_elements:
                if getattr(node, "name", None) == "a" and node is not a and node.get("href"):
                    nm = JOB_URL[site].search(_unwrap(node["href"]))
                    if nm and _canonical(site, nm)[0] != jid and _canonical(site, nm)[0] in job_ids:
                        break
                if isinstance(node, NavigableString) and node.parent is not a and node.strip():
                    lines.append(str(node))
                    n += 1
                    if n >= 12:
                        break
            found[jid] = {"title": t, "url": url, **_fields_from_lines(t, lines)}
    if not found and text:
        # Plain-text alerts: "<title>\n<company>\n<location>\nView job: <url>"
        blocks = re.split(r"\n-{5,}\n|\n\s*\n", text)
        for b in blocks:
            m = JOB_URL[site].search(b)
            if not m:
                continue
            jid, url = _canonical(site, m)
            lines = [l.strip() for l in b.splitlines() if l.strip() and "http" not in l]
            lines = [l for l in lines if not NOISE.match(l)]
            if lines:
                found[jid] = {"title": lines[0], "url": url, **_fields_from_lines(lines[0], lines[1:])}

    jobs = []
    for jid, f in found.items():
        if not f.get("title") or not f.get("company"):
            continue
        lo, hi = f["salary"]
        desc = f"From a {site.title()} job alert email. Open the posting for the full description."
        if f.get("exp") and (em := EXP_RX.search(f["exp"])):
            desc += f"\nRequires {em[1]}-{em[2]} years experience."  # wording the matcher's years regex understands
        jobs.append(Job(
            source=f"{site}-alert", source_id=jid, company=f["company"], title=f["title"],
            location=f.get("location") or city_of(f["url"].replace("-", " ")) or "India (not stated)", url=f["url"], description=desc,
            remote=bool(re.search(r"remote", f.get("location") or "", re.I)), posted_at=sent,
            salary_min=lo, salary_max=hi, salary_currency="INR" if lo else None, salary_period="year" if lo else None,
            raw={"site": site, "email_date": sent, "exp": f.get("exp")}))
    return jobs


class EmailAlerts(Collector):
    name = "email-alerts"

    def collect(self) -> list[Job]:
        msgs: list[Message] = []
        if os.environ.get("IMAP_USER") and os.environ.get("IMAP_PASSWORD"):
            try:
                msgs += self._from_imap()
            except Exception as e:
                self.errors.append(f"IMAP: {type(e).__name__}: {e}")
        if ALERTS_DIR.exists():
            for p in sorted(ALERTS_DIR.glob("*.eml")):
                try:
                    msgs.append(email.message_from_bytes(p.read_bytes()))
                except Exception as e:
                    self.errors.append(f"{p.name}: {e}")
        self.emails_read = len(msgs)
        jobs: list[Job] = []
        for m in msgs:
            try:
                jobs += parse_alert(m)
            except Exception as e:
                self.errors.append(f"parse '{m.get('Subject', '')[:40]}': {type(e).__name__}: {e}")
        return [j for j in jobs if is_india_eligible(j.location, j.remote)]

    def _from_imap(self) -> list[Message]:
        host = os.environ.get("IMAP_HOST", "imap.gmail.com")
        since = (date.today() - timedelta(days=LOOKBACK_DAYS)).strftime("%d-%b-%Y")
        out = []
        with imaplib.IMAP4_SSL(host) as M:
            M.login(os.environ["IMAP_USER"], os.environ["IMAP_PASSWORD"])
            M.select("INBOX", readonly=True)  # read-only: nothing is marked read, moved or deleted
            ids: set[bytes] = set()
            for domain in ("linkedin.com", "naukri.com"):
                typ, data = M.search(None, "SINCE", since, "FROM", domain)
                if typ == "OK" and data and data[0]:
                    ids |= set(data[0].split())
            for i in sorted(ids, key=int)[-300:]:
                typ, data = M.fetch(i, "(BODY.PEEK[])")
                if typ == "OK" and data and isinstance(data[0], tuple):
                    out.append(email.message_from_bytes(data[0][1]))
        return out
