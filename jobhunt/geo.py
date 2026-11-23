"""Location helpers: is a posting open to someone in India, and what's its canonical city."""
from __future__ import annotations

import re

from .profile_parser import CITIES, normalize_location

_CITY_RX = {city: re.compile(r"\b(" + "|".join(map(re.escape, aliases)) + r")\b", re.I) for city, aliases in CITIES.items()}
_INDIA_RX = re.compile(r"(?i:\bindia\b)|,\s*IN\s*$")  # "India" anywhere, or a trailing ", IN" country code
_REMOTE_RX = re.compile(r"\bremote\b|work from home|\bwfh\b|anywhere|distributed", re.I)
_REMOTE_OK_REGION = re.compile(r"\b(india|apac|asia|asia[- ]pacific|anywhere|worldwide|global|international)\b", re.I)
_FOREIGN_COUNTRY = re.compile(
    r"\b(australia|aus|sydney|melbourne|japan|jpn|tokyo|singapore|sgp|korea|seoul|china|hong kong|taiwan|"
    r"philippines|indonesia|vietnam|thailand|malaysia|new zealand|us|usa|united states|canada|mexico|brazil|"
    r"uk|united kingdom|germany|france|spain|ireland|netherlands|poland|uae|dubai|saudi|israel)\b", re.I)
_FILLER = re.compile(r"\b(remote|fully|hybrid|distributed|work from home|wfh|onsite|on-site|location|flexible|or|and|"
                     r"first|friendly|ok|only|based|team)\b|[-–—,;:/|()&]", re.I)


def city_of(location: str) -> str | None:
    for city, rx in _CITY_RX.items():
        if rx.search(location or ""):
            return city
    return None


def is_india_eligible(location: str, remote: bool = False) -> bool:
    loc = location or ""
    if city_of(loc) or _INDIA_RX.search(loc):
        return True
    if remote or _REMOTE_RX.search(loc):
        if _REMOTE_OK_REGION.search(loc):
            # "Remote, Japan, APAC" is a Japan role; only region-wide wording counts.
            return not _FOREIGN_COUNTRY.search(loc)
        # Bare "Remote" (no region named) is kept and flagged as region-unclear later.
        # Any other named region ("Remote - Texas", "Saudi Arabia", "AMER") means not open to India.
        return not _FILLER.sub(" ", loc).strip()
    return False


def canonical_location(location: str, remote: bool = False) -> str:
    city = city_of(location)
    if city:
        return city
    if remote or _REMOTE_RX.search(location or ""):
        return "Remote"
    return normalize_location(location or "")
