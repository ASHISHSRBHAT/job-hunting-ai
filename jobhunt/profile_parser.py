"""Step 1: parse resume.pdf into profile.json (rule-based, free, offline).

Everything here is a best guess from text; the output is meant to be reviewed and hand-corrected.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from pypdf import PdfReader

from .skills import extract_skills

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

CITIES = {
    "Bangalore": ["bangalore", "bengaluru"], "Hyderabad": ["hyderabad"], "Pune": ["pune"],
    "Chennai": ["chennai"], "Mumbai": ["mumbai", "bombay"], "Delhi NCR": ["delhi", "new delhi", "ncr"],
    "Gurgaon": ["gurgaon", "gurugram"], "Noida": ["noida"], "Kolkata": ["kolkata"],
    "Ahmedabad": ["ahmedabad"], "Kochi": ["kochi", "cochin"], "Jaipur": ["jaipur"],
    "Coimbatore": ["coimbatore"], "Indore": ["indore"], "Chandigarh": ["chandigarh"],
    "Thiruvananthapuram": ["thiruvananthapuram", "trivandrum"], "Mysore": ["mysore", "mysuru"],
}

REMOTE_RX = re.compile(r"\bremote|work from home|wfh\b", re.IGNORECASE)
MISSPELLINGS = {"banglore": "Bangalore", "bangaluru": "Bangalore", "bengalore": "Bangalore",
                "banglaore": "Bangalore", "hydrabad": "Hyderabad", "hyderbad": "Hyderabad", "gurugaon": "Gurgaon"}


def normalize_location(value: str) -> str:
    """Map free-text city names (incl. common misspellings) to the canonical names used for matching."""
    v = value.strip()
    low = v.lower()
    if REMOTE_RX.search(low):
        return "Remote-India" if ("india" in low or low in {"remote", "wfh"}) else v
    if low in MISSPELLINGS:
        return MISSPELLINGS[low]
    for city, aliases in CITIES.items():
        if low == city.lower() or low in aliases:
            return city
    return v


TITLE_WORDS = r"(engineer|developer|analyst|scientist|architect|consultant|manager|lead|intern|programmer|sde|administrator|specialist)"
DEGREE_RX = re.compile(
    r"\b(b\.?\s?tech|b\.?\s?e\.?|m\.?\s?tech|m\.?\s?e\.?|b\.?\s?sc|m\.?\s?sc|bca|mca|mba|ph\.?\s?d|"
    r"bachelor(?:'s)?|master(?:'s)?|diploma|b\.?\s?com)\b", re.IGNORECASE)

SECTION_HEADINGS = {
    "experience": r"(work |professional )?experience|employment( history)?|work history",
    "education": r"education|academic(s| qualifications)?",
    "skills": r"(technical |key )?skills|technologies|tech stack|core competencies",
    "projects": r"projects?",
    "other": r"(professional )?summary|certifications?( (&|and) achievements)?|achievements|awards|profile|"
             r"objective|publications|interests|languages",
}

_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE = rf"(?:{_MON}\s*'?\d{{2,4}}|\d{{1,2}}/\d{{4}}|\d{{4}})"
RANGE_RX = re.compile(
    rf"({_DATE})\s*(?:-|–|—|to)\s*({_DATE}|present|current|now|till date|ongoing)", re.IGNORECASE)


def _is_heading(s: str) -> bool:
    s = s.strip().rstrip(":").strip()
    return any(re.fullmatch(rx, s, re.IGNORECASE) for rx in SECTION_HEADINGS.values())


def rejoin_broken_lines(text: str) -> str:
    """Some PDFs (often bold/styled text) extract one word per line: 'System\nSoftware\nEngineer'.
    Glue runs of single-word lines onto the following line, but emit section headings on their own."""
    out, buf = [], []
    for line in text.splitlines():
        word = line.strip()
        if word and " " not in word and len(word) <= 30:
            buf.append(word)
            # A heading may follow a stray word wrapped from the previous line ("Integration PROFESSIONAL EXPERIENCE").
            for i in range(len(buf)):
                if _is_heading(" ".join(buf[i:])):
                    if i:
                        out.append(" ".join(buf[:i]))
                    out.append(" ".join(buf[i:]))
                    buf = []
                    break
            continue
        if buf:
            line = " ".join(buf) + (" " + word if word else "")
            buf = []
        out.append(line)
    if buf:
        out.append(" ".join(buf))
    return "\n".join(out)


def read_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    return rejoin_broken_lines("\n".join((page.extract_text() or "") for page in reader.pages))


def split_sections(text: str) -> dict[str, str]:
    """Split resume text on lines that look like section headings."""
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for line in text.splitlines():
        stripped = line.strip().rstrip(":").strip()
        if 0 < len(stripped) <= 40:
            for name, rx in SECTION_HEADINGS.items():
                if re.fullmatch(rx, stripped, re.IGNORECASE):
                    current = name
                    sections.setdefault(current, [])
                    break
            else:
                sections.setdefault(current, []).append(line)
                continue
            continue
        sections.setdefault(current, []).append(line)
    return {k: "\n".join(v) for k, v in sections.items()}


def _parse_date(s: str, is_end: bool) -> date | None:
    s = s.strip().lower()
    if s in {"present", "current", "now", "till date", "ongoing"}:
        return date.today()
    if m := re.fullmatch(r"(\d{1,2})/(\d{4})", s):
        return date(int(m[2]), max(1, min(12, int(m[1]))), 1)
    if m := re.fullmatch(r"([a-z]{3})[a-z]*\.?\s*'?(\d{2,4})", s):
        year = int(m[2])
        year += 2000 if year < 100 else 0
        if m[1] not in MONTHS:
            return None
        return date(year, MONTHS[m[1]], 1)
    if re.fullmatch(r"\d{4}", s):
        return date(int(s), 12 if is_end else 1, 1)
    return None


def estimate_experience_years(exp_text: str, full_text: str) -> tuple[float | None, str]:
    """Merge overlapping date ranges in the experience section; fall back to an 'N years' phrase."""
    intervals, skipped = [], 0
    for line in exp_text.splitlines():
        for m in RANGE_RX.finditer(line):
            start, end = _parse_date(m[1], False), _parse_date(m[2], True)
            if not (start and end and start <= end and start.year >= 1980):
                continue
            if re.search(r"\b(intern(ship)?|trainee|apprentice)\b", line, re.IGNORECASE):
                skipped += 1  # internships usually don't count toward "years of experience"
                continue
            intervals.append((start, end))
    if intervals:
        intervals.sort()
        merged = [list(intervals[0])]
        for s, e in intervals[1:]:
            if s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        months = sum((e.year - s.year) * 12 + (e.month - s.month) for s, e in merged)
        note = f"summed {len(intervals)} job date range(s)"
        if skipped:
            note += f", excluded {skipped} internship(s)"
        return round(months / 12, 1), note
    if m := re.search(r"(\d+(?:\.\d+)?)\+?\s*years? of (?:professional |industry |work )?experience",
                      full_text, re.IGNORECASE):
        return float(m[1]), f"phrase '{m[0]}'"
    return None, "not found"


def extract_titles(exp_text: str) -> list[str]:
    titles: list[str] = []
    for line in exp_text.splitlines():
        line = line.strip(" •-–|\t")
        # Titles are usually short lines (or the part before a separator) containing a role word.
        for part in re.split(r"\s+[|@,–—-]\s+|\s+at\s+", line):
            part = RANGE_RX.sub("", part).strip(" ,|()")
            if 3 <= len(part) <= 60 and re.search(rf"\b{TITLE_WORDS}\b", part, re.IGNORECASE) \
                    and len(part.split()) <= 6 and not part.endswith("."):
                if part not in titles:
                    titles.append(part)
                break
    return titles[:8]


def extract_education(edu_text: str) -> list[str]:
    out = []
    for line in edu_text.splitlines():
        line = line.strip(" •-\t")
        if DEGREE_RX.search(line) and len(line) <= 160 and line not in out:
            out.append(line)
    return out[:5]


def extract_location(header_text: str, full_text: str) -> str | None:
    for text in (header_text, full_text):  # prefer the contact/header block
        for city, aliases in CITIES.items():
            if re.search(r"\b(" + "|".join(aliases) + r")\b", text, re.IGNORECASE):
                return city
    return None


def parse_resume(path: Path, config: dict) -> dict:
    text = read_pdf_text(path)
    if len(text.strip()) < 100:
        raise SystemExit(f"Only {len(text.strip())} characters extracted from {path}. "
                         "Is it a scanned image? Export a text-based PDF instead.")
    sec = split_sections(text)
    exp_text = sec.get("experience", "")
    years, years_src = estimate_experience_years(exp_text or text, text)

    # Skills listed explicitly vs merely mentioned anywhere — both count, but we record which.
    listed = extract_skills(sec.get("skills", ""))
    mentioned = [s for s in extract_skills(text) if s not in listed]

    profile = {
        "skills": listed + mentioned,
        "skills_listed_in_skills_section": listed,
        "experience_years": config.get("experience_years") or years,
        "past_titles": extract_titles(exp_text),
        "education": extract_education(sec.get("education", "")),
        "location": extract_location(sec.get("header", ""), text),
        "target_roles": config.get("target_roles") or [],
        "preferred_locations": config.get("locations") or [],
        "min_salary_lpa": config.get("min_salary_lpa"),
        "_parse_notes": {
            "experience_years_from_resume": years,
            "experience_years_source": years_src,
            "sections_found": [k for k in sec if k != "header"],
            "chars_extracted": len(text),
        },
    }
    return profile


def save_profile(profile: dict, out: Path) -> None:
    out.write_text(json.dumps(profile, indent=2, ensure_ascii=False))
