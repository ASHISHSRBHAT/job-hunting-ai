"""Salary helpers. (Step 4 adds estimates for jobs that don't list pay; this module only reads listed pay.)"""
from __future__ import annotations


def listed_salary(row) -> dict | None:
    """Turn a job row's listed salary into a display dict. INR is converted to LPA (lakhs per annum)."""
    lo, hi, cur, per = row["salary_min"], row["salary_max"], row["salary_currency"], row["salary_period"]
    if lo is None and hi is None:
        return None
    lo = lo if lo is not None else hi
    hi = hi if hi is not None else lo
    mult = {"year": 1, "month": 12, "hour": 2080}.get(per or "year", 1)
    lo, hi = lo * mult, hi * mult
    src = "estimated by Adzuna" if row["salary_is_estimate"] else "listed in posting"
    if (cur or "INR").upper() == "INR":
        return {"lpa": [round(lo / 1e5, 1), round(hi / 1e5, 1)], "text": f"₹{lo / 1e5:.0f}–{hi / 1e5:.0f} LPA",
                "source": src, "is_estimate": bool(row["salary_is_estimate"])}
    return {"lpa": None, "text": f"{cur} {lo / 1000:.0f}k–{hi / 1000:.0f}k /yr", "source": src + " (not INR)",
            "is_estimate": bool(row["salary_is_estimate"])}
