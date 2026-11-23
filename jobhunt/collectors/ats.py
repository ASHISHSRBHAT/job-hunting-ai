"""Company careers-page collectors via the public job-board APIs of applicant tracking systems.

These endpoints are published by the ATS vendors for exactly this purpose (embedding a company's
job board elsewhere). No login, no scraping. Board names live in companies.yaml.
"""
from __future__ import annotations

import re

from ..db import Job
from ..geo import is_india_eligible
from ..http import HttpError, fetch_json
from .base import Collector, html_to_text, iso_date, iso_from_ms, norm_period


TECH_TITLE = re.compile(r"engineer|developer|\bai\b|\bml\b|machine learning|data|software|scientist|architect|"
                        r"devops|\bsre\b|backend|platform|llm|genai|research", re.I)


class _ATSCollector(Collector):
    ats = ""

    def boards(self) -> list[str]:
        return list(self.config.get("companies", {}).get(self.ats, []) or [])

    def collect(self) -> list[Job]:
        out: list[Job] = []
        self.board_stats: dict[str, tuple[int, int]] = {}
        for board in self.boards():
            try:
                jobs = self.fetch_board(board)
            except HttpError as e:
                self.errors.append(f"{board}: {e}")
                continue
            except Exception as e:  # one broken board must never stop the run
                self.errors.append(f"{board}: {type(e).__name__}: {e}")
                continue
            india = [j for j in jobs if is_india_eligible(j.location, j.remote)]
            self.board_stats[board] = (len(jobs), len(india))
            out.extend(india)
        return out

    def fetch_board(self, board: str) -> list[Job]:
        raise NotImplementedError


class Greenhouse(_ATSCollector):
    name = ats = "greenhouse"

    def fetch_board(self, board):
        data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", params={"content": "true"})
        company = (data.get("meta") or {}).get("name") or board
        jobs = []
        for j in data.get("jobs", []):
            locs = [(j.get("location") or {}).get("name", "")] + [o.get("name", "") for o in j.get("offices", [])]
            loc = " / ".join(dict.fromkeys(x for x in locs if x))
            jobs.append(Job(
                source=self.name, source_id=str(j["id"]), company=j.get("company_name") or company,
                title=j.get("title", "").strip(), location=loc, url=j.get("absolute_url", ""),
                description=html_to_text(j.get("content")), posted_at=iso_date(j.get("first_published") or j.get("updated_at")),
                remote="remote" in loc.lower(), raw={k: j.get(k) for k in ("id", "title", "location", "metadata")}))
        return jobs


class Lever(_ATSCollector):
    name = ats = "lever"

    def fetch_board(self, board):
        data = fetch_json(f"https://api.lever.co/v0/postings/{board}", params={"mode": "json"})
        jobs = []
        for j in data:
            cat = j.get("categories") or {}
            locs = cat.get("allLocations") or [cat.get("location", "")]
            loc = " / ".join(x for x in locs if x)
            sal = j.get("salaryRange") or {}
            desc = "\n".join(filter(None, [j.get("descriptionPlain"), *[
                f"{l.get('text', '')}\n{html_to_text(l.get('content'))}" for l in j.get("lists", [])],
                j.get("additionalPlain")]))
            jobs.append(Job(
                source=self.name, source_id=j["id"], company=board.replace("-", " ").title(),
                title=j.get("text", "").strip(), location=loc, url=j.get("hostedUrl", ""), description=desc,
                remote=j.get("workplaceType") == "remote" or "remote" in loc.lower(),
                posted_at=iso_from_ms(j.get("createdAt")),
                salary_min=sal.get("min"), salary_max=sal.get("max"), salary_currency=sal.get("currency"),
                salary_period=norm_period(sal.get("interval")),
                raw={k: j.get(k) for k in ("id", "categories", "workplaceType", "salaryRange")}))
        return jobs


class Ashby(_ATSCollector):
    name = ats = "ashby"

    def fetch_board(self, board):
        data = fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}",
                          params={"includeCompensation": "true"})
        jobs = []
        for j in data.get("jobs", []):
            if j.get("isListed") is False:
                continue
            locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations", []) or []]
            addr = ((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry", "")
            loc = " / ".join(dict.fromkeys(x for x in locs + [addr] if x))
            smin = smax = cur = per = None
            for c in ((j.get("compensation") or {}).get("summaryComponents") or []):
                if c.get("compensationType") == "Salary":
                    smin, smax, cur, per = c.get("minValue"), c.get("maxValue"), c.get("currencyCode"), norm_period(c.get("interval"))
                    break
            jobs.append(Job(
                source=self.name, source_id=j.get("id", ""), company=board.replace("-", " ").title(),
                title=j.get("title", "").strip(), location=loc, url=j.get("jobUrl") or j.get("applyUrl", ""),
                description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
                remote=bool(j.get("isRemote")) or j.get("workplaceType") == "Remote",
                posted_at=iso_date(j.get("publishedAt")), salary_min=smin, salary_max=smax,
                salary_currency=cur, salary_period=per,
                raw={k: j.get(k) for k in ("id", "location", "workplaceType", "compensation")}))
        return jobs


class SmartRecruiters(_ATSCollector):
    """Public Posting API. List endpoint lacks descriptions, so details are fetched only for India postings."""
    name = ats = "smartrecruiters"

    def fetch_board(self, board):
        jobs, offset = [], 0
        while True:
            data = fetch_json(f"https://api.smartrecruiters.com/v1/companies/{board}/postings",
                              params={"country": "in", "limit": 100, "offset": offset})
            for p in data.get("content", []):
                l = p.get("location") or {}
                loc = ", ".join(x for x in (l.get("city"), l.get("region"), "India" if l.get("country") == "in" else l.get("country")) if x)
                detail = {}
                if not TECH_TITLE.search(p.get("name", "")):
                    continue  # big employers list hundreds of non-tech roles; skip them before the per-posting call
                try:
                    detail = fetch_json(f"https://api.smartrecruiters.com/v1/companies/{board}/postings/{p['id']}")
                except HttpError:
                    pass
                sections = ((detail.get("jobAd") or {}).get("sections") or {})
                desc = "\n".join(html_to_text((sections.get(k) or {}).get("text")) for k in
                                 ("jobDescription", "qualifications", "additionalInformation"))
                jobs.append(Job(
                    source=self.name, source_id=p["id"], company=(p.get("company") or {}).get("name") or board,
                    title=p.get("name", "").strip(), location=loc,
                    url=detail.get("postingUrl") or f"https://jobs.smartrecruiters.com/{board}/{p['id']}",
                    description=desc.strip(), remote=bool(l.get("remote")), posted_at=iso_date(p.get("releasedDate")),
                    raw={k: p.get(k) for k in ("id", "location", "experienceLevel", "department")}))
            offset += 100
            if offset >= data.get("totalFound", 0) or offset >= 500:
                break
        return jobs
