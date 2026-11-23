"""Fast, free match pre-score (no AI): skills overlap + title relevance + seniority fit + location.

This ranks every job in milliseconds. Later the top ~100 get a deeper Claude review.
"""
from __future__ import annotations

import re

from ..geo import canonical_location
from ..skills import extract_skills

WEIGHTS = {"skills": 0.40, "title": 0.30, "seniority": 0.20, "location": 0.10}

NON_ENGINEERING = re.compile(
    r"\b(account (executive|manager|director)|sales|recruit(er|ing)|talent|marketing|counsel|legal|attorney|"
    r"designer|design lead|finance|accountant|payroll|hr\b|human resources|people partner|customer success|"
    r"support (specialist|manager|representative)|business development|partnerships?|executive assistant|"
    r"office|facilities|procurement|program manager|product manager|product management|technical program|\bpm\b|\btpm\b|copywriter|content|analyst|solutions? consultant|evangelist|community)\b", re.I)
BUILDER_ROLE = re.compile(r"\b(engineer|engineering|developer|architect|builder|member of technical staff|mts|sde|"
                          r"programmer|swe)\b", re.I)
GENERIC_SWE = re.compile(r"\b(software|backend|back-end|platform|python|full[- ]?stack|api|sde|developer|"
                         r"member of technical staff|mts)\b", re.I)
ADJACENT_ENG = re.compile(r"\b(engineer|engineering|scientist|architect|sre|devops|data|infrastructure|security|"
                          r"qa|quality|test|firmware|embedded|frontend|front-end|mobile|ios|android)\b", re.I)
_STOP = {"engineer", "engineering", "developer", "senior", "junior", "lead", "staff", "the", "and", "of", "for", "-"}

# (pattern, typical min years, typical max years)
SENIORITY = [
    (re.compile(r"\bintern(ship)?\b|\btrainee\b", re.I), 0, 1),
    (re.compile(r"\b(director|vp|vice president|head of|chief)\b", re.I), 12, 30),
    (re.compile(r"\bprincipal\b|\bdistinguished\b|\bfellow\b", re.I), 10, 25),
    (re.compile(r"\b(senior staff|staff|lead|manager)\b", re.I), 7, 15),
    (re.compile(r"\b(senior|sr\.?|sde[- ]?(iii|3)|engineer (iii|3)|l5)\b", re.I), 4, 9),
    (re.compile(r"\b(junior|jr\.?|associate|graduate|new grad|entry|sde[- ]?(i|1)\b|engineer (i|1)\b)", re.I), 0, 2),
]
YEARS_RX = re.compile(r"(\d{1,2})\s*\+?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*years?(?:'|’)?\s*(?:of\s+)?"
                      r"(?:\w+\s+){0,4}?(?:experience|exp\b)", re.I)


def required_years(title: str, description: str) -> tuple[float, float, str]:
    """Return (min, max, where-it-came-from). Explicit 'N+ years' in the description beats the title's level."""
    m = YEARS_RX.search(description or "")
    if m and 0 < int(m[1]) <= 25:
        lo = float(m[1])
        hi = float(m[2]) if m[2] and int(m[2]) >= lo else lo + 4
        return lo, hi, f"asks for {m[0].strip()[:40]}"
    for rx, lo, hi in SENIORITY:
        if rx.search(title):
            return lo, hi, f"'{rx.search(title)[0]}' title"
    return 2, 6, "no level stated"


def _seniority_fit(years: float | None, lo: float, hi: float) -> float:
    if years is None:
        return 0.6
    if lo - 0.5 <= years <= hi + 2:   # slightly over-qualified is fine
        return 1.0
    gap = (lo - years) if years < lo else (years - hi - 2)
    return max(0.0, 1 - gap / 4)       # each year short costs 25%


def _title_fit(title: str, role_words: set[str]) -> tuple[float, str]:
    t = title.lower()
    if NON_ENGINEERING.search(t) and not re.search(r"\bengineer", t):
        return 0.0, "not an engineering role"
    hits = [w for w in role_words if re.search(rf"\b{re.escape(w)}\b", t)]
    if hits and BUILDER_ROLE.search(t) and not NON_ENGINEERING.search(t):
        return 1.0, "title matches your target role"
    if hits:
        return 0.4, "AI-related, but not an engineering role"
    if GENERIC_SWE.search(t):
        return 0.6, "general software role"
    if ADJACENT_ENG.search(t):
        return 0.3, "adjacent engineering role"
    return 0.1, "title unrelated to your targets"


def role_vocabulary(profile: dict) -> set[str]:
    """Words that signal a target role. 'AI engineer' -> {'ai', 'artificial intelligence', 'llm', 'genai', ...}."""
    words: set[str] = set()
    for role in profile.get("target_roles") or profile.get("past_titles") or []:
        words |= {w for w in re.findall(r"[a-z0-9+#.]+", role.lower()) if w not in _STOP and len(w) > 1}
    if words & {"ai", "ml", "llm", "genai", "nlp"} or any("machine learning" in r.lower() for r in profile.get("target_roles", [])):
        words |= {"ai", "ml", "llm", "llms", "genai", "machine learning", "applied ai", "agentic", "agents", "nlp",
                  "aiml", "ai/ml", "generative", "rag"}
    return words


class Scorer:
    def __init__(self, profile: dict):
        self.profile = profile
        self.have = {s.lower() for s in profile.get("skills", [])}
        self.years = profile.get("experience_years")
        self.role_words = role_vocabulary(profile)
        self.pref_locs = {canonical_location(l, l.lower().startswith("remote")) for l in profile.get("preferred_locations") or []}
        if not self.pref_locs and profile.get("location"):
            self.pref_locs = {profile["location"]}

    def score(self, title: str, description: str, location: str, remote: bool, req: list[str] | None = None,
              req_years: tuple[float, float, str] | None = None) -> dict:
        if req is None:
            req = extract_skills(f"{title}\n{description}")
        matched = [s for s in req if s.lower() in self.have]
        missing = [s for s in req if s.lower() not in self.have]
        skills = (len(matched) / len(req)) if req else 0.3
        # A couple of required skills is a thin signal; don't let 1/1 look like a perfect match.
        if req and len(req) < 4:
            skills = skills * 0.6 + 0.3 * 0.4

        title_s, title_why = _title_fit(title, self.role_words)
        lo, hi, yrs_src = req_years or required_years(title, description)
        sen = _seniority_fit(self.years, lo, hi)
        if hi <= 1 and (self.years or 0) >= 1.5:
            sen = 0.0  # internship / trainee role for someone already working full-time

        loc_c = canonical_location(location, remote)
        if not self.pref_locs or loc_c in self.pref_locs:
            loc_s = 1.0
        elif loc_c == "Remote":
            loc_s = 0.9
        else:
            loc_s = 0.4

        total = round(100 * (WEIGHTS["skills"] * skills + WEIGHTS["title"] * title_s +
                             WEIGHTS["seniority"] * sen + WEIGHTS["location"] * loc_s))
        if hi <= 1 and sen == 0:
            total = min(total, 40)  # internships stay visible under "Any match" but out of the default view
        if title_s == 0:
            total = min(total, 25)  # skills overlap alone can't make a sales/PM/analyst role a good match
        return {
            "score": total, "matched": matched, "missing": missing,
            "why": self._why(title_s, title_why, matched, missing, sen, lo, hi, yrs_src, loc_s, loc_c),
            "parts": {"skills": round(skills, 2), "title": title_s, "seniority": round(sen, 2), "location": loc_s},
            "req_years": [lo, hi],
        }

    def _why(self, title_s, title_why, matched, missing, sen, lo, hi, yrs_src, loc_s, loc_c) -> str:
        bits = []
        if title_s == 0:
            return "Not an engineering role."
        if matched:
            bits.append(f"you have {len(matched)}/{len(matched) + len(missing)} listed skills ({', '.join(matched[:3])})")
        elif missing:
            bits.append(f"none of its {len(missing)} listed skills are on your profile")
        else:
            bits.append("job lists few recognisable skills")
        bits.insert(0, title_why)
        if hi <= 1 and sen == 0:
            bits.append("internship/trainee role — below your level")
        elif sen < 0.75 and self.years is not None:
            if self.years < lo:
                bits.append(f"wants ~{lo:g}+ yrs vs your {self.years:g} ({yrs_src})")
            else:
                bits.append(f"pitched below your level ({yrs_src})")
        if loc_s < 1:
            bits.append(f"{loc_c} isn't in your preferred locations" if loc_c != "Remote" else "remote")
        s = "; ".join(bits)
        return s[0].upper() + s[1:] + "."
