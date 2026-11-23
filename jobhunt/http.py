"""Polite HTTP client: on-disk cache, per-host rate limiting, retry with backoff on 429/5xx."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cache"
USER_AGENT = "job-hunt-personal/0.1 (personal job search tool; low volume)"

OK_TTL = 20 * 3600          # re-use successful responses for 20h (so same-day re-runs cost nothing)
MISS_TTL = 7 * 24 * 3600    # remember 404s for a week (bad board names) instead of re-hitting them
MIN_INTERVAL = {"default": 0.5, "api.adzuna.com": 2.5, "jooble.org": 1.0, "jsearch.p.rapidapi.com": 1.2}

_client = httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
_host_locks: dict[str, threading.Lock] = {}
_host_last: dict[str, float] = {}
_guard = threading.Lock()


class HttpError(Exception):
    def __init__(self, status: int, url: str):
        super().__init__(f"HTTP {status} for {url}")
        self.status = status


def _wait_turn(host: str) -> None:
    with _guard:
        lock = _host_locks.setdefault(host, threading.Lock())
    with lock:
        gap = MIN_INTERVAL.get(host, MIN_INTERVAL["default"])
        delay = _host_last.get(host, 0) + gap - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        _host_last[host] = time.monotonic()


def _cache_path(method: str, url: str, params, body) -> Path:
    key = json.dumps([method, url, params or {}, body or {}], sort_keys=True)
    return CACHE_DIR / (hashlib.sha256(key.encode()).hexdigest()[:32] + ".json")


def fetch_json(url: str, *, params: dict | None = None, json_body: dict | None = None,
               cache_key_params: dict | None = None, use_cache: bool = True, headers: dict | None = None):
    """GET (or POST when json_body is given) and return parsed JSON. Raises HttpError on non-2xx.

    cache_key_params: params to use for the cache key instead of `params` (so API keys stay out of it).
    """
    method = "POST" if json_body is not None else "GET"
    cpath = _cache_path(method, url, cache_key_params if cache_key_params is not None else params, json_body)
    if use_cache and cpath.exists():
        cached = json.loads(cpath.read_text())
        ttl = OK_TTL if cached["status"] == 200 else MISS_TTL
        if time.time() - cached["at"] < ttl:
            if cached["status"] != 200:
                raise HttpError(cached["status"], url)
            return cached["data"]

    host = urlparse(url).netloc
    for attempt in range(4):
        _wait_turn(host)
        try:
            r = _client.request(method, url, params=params, json=json_body, headers=headers)
        except httpx.TransportError:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            retry_after = r.headers.get("Retry-After", "")
            time.sleep(min(60, int(retry_after)) if retry_after.isdigit() else 2 ** (attempt + 1))
            continue
        break

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if r.status_code == 200:
        data = r.json()
        cpath.write_text(json.dumps({"at": time.time(), "status": 200, "data": data}))
        return data
    if r.status_code in (404, 410) and "rapidapi.com" not in host:  # only cache "board doesn't exist" answers
        cpath.write_text(json.dumps({"at": time.time(), "status": r.status_code}))
    raise HttpError(r.status_code, url)
