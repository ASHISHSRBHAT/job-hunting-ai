"""Job-aggregator APIs that need a free key (in .env). Skipped automatically when the key is missing."""
from __future__ import annotations

import os

from ..db import Job
from ..http import HttpError, fetch_json
from .base import Collector, html_to_text, iso_date


def search_terms(profile: dict, config: dict) -> tuple[list[str], list[str]]:
    roles = profile.get("target_roles") or config.get("target_roles") or profile.get("past_titles", [])[:2]
    locs = profile.get("preferred_locations") or config.get("locations") or [profile.get("location") or "India"]
    # Aggregators search by city; "Remote-India" becomes a nationwide query with a "remote" keyword.
    return list(dict.fromkeys(roles)), list(dict.fromkeys(locs))


class Adzuna(Collector):
    """https://developer.adzuna.com — free tier is rate-limited, so we keep calls to roles × cities × pages."""
    name = "adzuna"
    needs_env = ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")
    pages = 2

    def collect(self) -> list[Job]:
        auth = {"app_id": os.environ["ADZUNA_APP_ID"], "app_key": os.environ["ADZUNA_APP_KEY"]}
        roles, locs = search_terms(self.profile, self.config)
        out = []
        for role in roles:
            for loc in locs:
                remote = loc.lower().startswith("remote")
                q = {"what": f"{role} remote" if remote else role, "results_per_page": 50, "max_days_old": 30,
                     "content-type": "application/json"}
                if not remote:
                    q["where"] = loc
                for page in range(1, self.pages + 1):
                    try:
                        data = fetch_json(f"https://api.adzuna.com/v1/api/jobs/in/search/{page}",
                                          params={**auth, **q}, cache_key_params={**q, "page": page})
                    except HttpError as e:
                        self.errors.append(f"{role}/{loc}: {e}")
                        break
                    results = data.get("results", [])
                    for r in results:
                        out.append(Job(
                            source=self.name, source_id=str(r.get("id")),
                            company=(r.get("company") or {}).get("display_name", "Unknown"),
                            title=html_to_text(r.get("title")), location=(r.get("location") or {}).get("display_name", ""),
                            url=r.get("redirect_url", ""), description=html_to_text(r.get("description")),
                            remote=remote, posted_at=iso_date(r.get("created")),
                            salary_min=r.get("salary_min"), salary_max=r.get("salary_max"),
                            salary_currency="INR" if r.get("salary_min") else None,
                            salary_period="year" if r.get("salary_min") else None,
                            salary_is_estimate=str(r.get("salary_is_predicted")) == "1",
                            raw={k: r.get(k) for k in ("id", "category", "contract_time", "salary_is_predicted")}))
                    if len(results) < 50:
                        break
        return out


class Jooble(Collector):
    """https://jooble.org/api/about — free key by request."""
    name = "jooble"
    needs_env = ("JOOBLE_KEY",)

    def collect(self) -> list[Job]:
        key = os.environ["JOOBLE_KEY"]
        roles, locs = search_terms(self.profile, self.config)
        out = []
        for role in roles:
            for loc in locs:
                body = {"keywords": role, "location": "India" if loc.lower().startswith("remote") else f"{loc}, India",
                        "page": "1", "ResultOnPage": "50"}
                try:
                    data = fetch_json(f"https://jooble.org/api/{key}", json_body=body)
                except HttpError as e:
                    self.errors.append(f"{role}/{loc}: {e}")
                    continue
                for r in data.get("jobs", []):
                    out.append(Job(
                        source=self.name, source_id=str(r.get("id")), company=r.get("company") or "Unknown",
                        title=html_to_text(r.get("title")), location=r.get("location", ""), url=r.get("link", ""),
                        description=html_to_text(r.get("snippet")), remote="remote" in (r.get("type") or "").lower(),
                        posted_at=iso_date(r.get("updated")),
                        raw={k: r.get(k) for k in ("id", "salary", "source", "type")}))  # salary is free text; parsed later
        return out


PUBLISHERS = {"linkedin": "linkedin", "naukri": "naukri", "indeed": "indeed", "glassdoor": "glassdoor",
              "instahyre": "instahyre", "foundit": "foundit", "wellfound": "wellfound", "cutshort": "cutshort", "shine": "shine", "monster": "foundit", "hirist": "hirist", "iimjobs": "iimjobs"}


class JSearch(Collector):
    """JSearch (RapidAPI) returns Google for Jobs listings, which include postings published on LinkedIn,
    Naukri, Indeed, Glassdoor and company sites. One request per role x location per run, to stay in the free tier.
    Sign up: https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch  -> put the key in .env as JSEARCH_KEY."""
    name = "jsearch"
    needs_env = ("JSEARCH_KEY",)
    HOST = "jsearch.p.rapidapi.com"
    MAX_REQUESTS = 4   # free plan = 200 requests/month; 4/day ≈ 120/month. Override with jsearch_max_requests in config.yaml

    # Extra search phrasings for AI roles — LinkedIn/Naukri titles vary a lot.
    AI_SYNONYMS = ["LLM engineer", "GenAI engineer", "Agentic AI engineer", "Applied AI engineer"]

    def queries(self) -> list[tuple[str, str]]:
        roles, locs = search_terms(self.profile, self.config)
        roles = list(roles)
        if any(w in " ".join(roles).lower() for w in ("ai", "llm", "ml", "genai")):
            roles += [r for r in self.AI_SYNONYMS if r.lower() not in {x.lower() for x in roles}]
        # Interleave so the budget covers every location before trying more role phrasings.
        return [(r, l) for r in roles for l in locs]

    def collect(self) -> list[Job]:
        headers_key = os.environ["JSEARCH_KEY"]
        budget = int(self.config.get("jsearch_max_requests", self.MAX_REQUESTS))
        out = []
        for role, loc in self.queries()[:budget]:
                remote = loc.lower().startswith("remote")
                q = {"query": f"{role} {'remote ' if remote else ''}in {'India' if remote else loc + ', India'}",
                     "page": "1", "num_pages": "1", "country": "in", "date_posted": "week"}
                if remote:
                    q["work_from_home"] = "true"
                try:
                    data = fetch_json(f"https://{self.HOST}/search-v2", params=q,
                                      headers={"X-RapidAPI-Key": headers_key, "X-RapidAPI-Host": self.HOST})
                except HttpError as e:
                    self.errors.append(f"{role}/{loc}: {e}" + (" (check JSEARCH_KEY / free-tier quota)" if e.status in (401, 403, 429) else ""))
                    continue
                payload = data.get("data") or []
                for r in (payload.get("jobs", []) if isinstance(payload, dict) else payload):
                    pub = (r.get("job_publisher") or "").lower()
                    site = next((v for k, v in PUBLISHERS.items() if k in pub), "web")
                    # Prefer the LinkedIn/Naukri apply link when Google lists several places to apply.
                    url = r.get("job_apply_link") or r.get("job_google_link") or ""
                    for opt in r.get("apply_options") or []:
                        if any(k in (opt.get("publisher") or "").lower() for k in ("linkedin", "naukri")):
                            url = opt.get("apply_link") or url
                            site = next((v for k, v in PUBLISHERS.items() if k in opt["publisher"].lower()), site)
                            break
                    city = r.get("job_city") or ""
                    loc_text = r.get("job_location") or ", ".join(x for x in (city, r.get("job_state"), r.get("job_country")) if x)
                    per = {"YEAR": "year", "MONTH": "month", "HOUR": "hour"}.get((r.get("job_salary_period") or "").upper())
                    out.append(Job(
                        source=f"{site}-gj", source_id=str(r.get("job_id")), company=r.get("employer_name") or "Unknown",
                        title=(r.get("job_title") or "").strip(), location=loc_text.replace(", IN", ", India"),
                        url=url, description=r.get("job_description") or "", remote=bool(r.get("job_is_remote")),
                        posted_at=(r.get("job_posted_at_datetime_utc") or "")[:10] or None,
                        salary_min=r.get("job_min_salary"), salary_max=r.get("job_max_salary"),
                        salary_currency=r.get("job_salary_currency") or ("INR" if r.get("job_min_salary") else None),
                        salary_period=per, raw={"publisher": r.get("job_publisher"), "job_id": r.get("job_id")}))
        return out
