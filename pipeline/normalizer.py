"""Platform-specific raw data → Job dataclass normalizers."""

import hashlib
import logging
import re
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup

from pipeline.models import Job

log = logging.getLogger(__name__)

# USD→INR conversion rate used when parsing dollar salaries
USD_TO_INR = 84
# Lakhs per annum multiplier
LPA_TO_ANNUAL = 100_000


def clean_description(raw: str) -> str:
    """Strip HTML, normalize whitespace, remove non-printable chars, cap at 5000 chars."""
    if not raw:
        return ""
    try:
        soup = BeautifulSoup(raw, "lxml")
        text = soup.get_text(separator=" ")
    except Exception:
        # Fallback: naive tag stripping
        text = re.sub(r"<[^>]+>", " ", raw)

    # Remove non-printable chars (keep newlines and tabs as spaces)
    text = re.sub(r"[^\x20-\x7E\n\t]", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text[:5000]


def compute_content_hash(title: str, company: str) -> str:
    """SHA256[:16] of 'title.lower()|company.lower()' for cross-platform dedup."""
    key = f"{title.lower().strip()}|{company.lower().strip()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def parse_salary_inr(salary_str: str) -> tuple[Optional[int], Optional[int]]:
    """
    Parse salary strings into (annual_min_inr, annual_max_inr).

    Handles: '4-8 LPA', '₹40K/month', '8L–15L', '$10-15K', '10-15 LPA',
             '50000 per month', '6 LPA', etc.
    """
    if not salary_str:
        return None, None

    s = str(salary_str).strip().lower()

    # Remove currency symbols and common noise
    s = s.replace("₹", "").replace(",", "").replace(" ", "")

    try:
        # Detect LPA (lakhs per annum)
        lpa_match = re.search(r"([\d.]+)[–\-to]+([\d.]+)\s*lpa", s)
        if lpa_match:
            lo = float(lpa_match.group(1)) * LPA_TO_ANNUAL
            hi = float(lpa_match.group(2)) * LPA_TO_ANNUAL
            return int(lo), int(hi)

        single_lpa = re.search(r"([\d.]+)\s*lpa", s)
        if single_lpa:
            val = float(single_lpa.group(1)) * LPA_TO_ANNUAL
            return int(val), int(val)

        # L/Lakhs notation: 8L–15L
        l_range = re.search(r"([\d.]+)l[–\-]([\d.]+)l", s)
        if l_range:
            lo = float(l_range.group(1)) * LPA_TO_ANNUAL
            hi = float(l_range.group(2)) * LPA_TO_ANNUAL
            return int(lo), int(hi)

        # Monthly salary in INR: ₹40K/month or 40000/month
        monthly_k = re.search(r"([\d.]+)k/month", s)
        if monthly_k:
            monthly = float(monthly_k.group(1)) * 1000
            return int(monthly * 12), int(monthly * 12)

        monthly_plain = re.search(r"([\d]+)(?:/month|permonth|p\.m\.)", s)
        if monthly_plain:
            monthly = float(monthly_plain.group(1))
            return int(monthly * 12), int(monthly * 12)

        # USD range: $10-15K or $10K-$15K
        usd_match = re.search(r"\$?([\d.]+)k?[–\-]\$?([\d.]+)k?", s)
        if usd_match and "usd" in s or "$" in str(salary_str):
            lo = float(usd_match.group(1))
            hi = float(usd_match.group(2))
            if lo < 500:  # likely in thousands
                lo *= 1000
                hi *= 1000
            return int(lo * USD_TO_INR), int(hi * USD_TO_INR)

    except Exception as e:
        log.debug(f"[Salary] parse failed for '{salary_str}': {e}")

    return None, None


def _parse_employment_type(raw: str) -> str:
    """Normalize employment type strings to canonical values."""
    if not raw:
        return "full_time"
    s = raw.lower()
    if "intern" in s:
        return "internship"
    if "contract" in s or "freelance" in s or "c2h" in s:
        return "contract"
    if "part" in s:
        return "part_time"
    return "full_time"


def _parse_experience(exp_str: str) -> tuple[Optional[int], Optional[int]]:
    """Parse '2-5 years', '0-1 year', 'fresher' etc into (min, max) years."""
    if not exp_str:
        return None, None
    s = str(exp_str).lower()
    if "fresher" in s or "0 year" in s:
        return 0, 1
    match = re.search(r"(\d+)\s*[-–to]+\s*(\d+)", s)
    if match:
        return int(match.group(1)), int(match.group(2))
    single = re.search(r"(\d+)", s)
    if single:
        v = int(single.group(1))
        return v, v
    return None, None


class JobSpyNormalizer:
    """Normalizes raw dicts returned by the jobspy library."""

    def normalize(self, raw: dict) -> Optional[Job]:
        try:
            platform = str(raw.get("site", "unknown")).lower()
            ext_id = str(raw.get("id", "")).strip() or str(raw.get("job_url", "")).strip()
            if not ext_id:
                ext_id = compute_content_hash(
                    str(raw.get("title", "")), str(raw.get("company", ""))
                )

            title = str(raw.get("title", "")).strip()
            company = str(raw.get("company", "")).strip()
            # JobSpy uses pandas; NaN serialises to the string "nan" — reject those
            if not title or not company or company.lower() == "nan" or title.lower() == "nan":
                return None

            location_parts = [
                raw.get("city", ""), raw.get("state", ""), raw.get("country", "")
            ]
            location = ", ".join(p for p in location_parts if p)

            is_remote = bool(raw.get("is_remote", False))
            if not is_remote and "remote" in location.lower():
                is_remote = True

            description = str(raw.get("description", "") or "")
            description_clean = clean_description(description)

            apply_url = str(raw.get("job_url", "") or raw.get("apply_url", "") or "")

            posted_at = None
            raw_date = raw.get("date_posted") or raw.get("posted_at")
            if raw_date:
                try:
                    if isinstance(raw_date, datetime):
                        posted_at = raw_date
                    else:
                        posted_at = datetime.fromisoformat(str(raw_date))
                except Exception:
                    pass

            salary_min, salary_max = parse_salary_inr(
                str(raw.get("min_amount", "") or raw.get("salary", "") or "")
            )
            if salary_min is None and raw.get("max_amount"):
                # jobspy provides separate min/max amounts
                try:
                    interval = str(raw.get("interval", "yearly")).lower()
                    s_min = float(raw.get("min_amount") or 0)
                    s_max = float(raw.get("max_amount") or 0)
                    currency = str(raw.get("currency", "INR")).upper()
                    multiplier = 1
                    if interval == "monthly":
                        multiplier = 12
                    elif interval == "weekly":
                        multiplier = 52
                    elif interval == "hourly":
                        multiplier = 2080
                    if currency == "USD":
                        multiplier *= USD_TO_INR
                    salary_min = int(s_min * multiplier) if s_min else None
                    salary_max = int(s_max * multiplier) if s_max else None
                except Exception:
                    pass

            skills: list[str] = []
            raw_skills = raw.get("job_function") or raw.get("skills") or []
            if isinstance(raw_skills, list):
                skills = [str(s).strip() for s in raw_skills if s]
            elif isinstance(raw_skills, str):
                skills = [s.strip() for s in raw_skills.split(",") if s.strip()]

            emp_type = _parse_employment_type(str(raw.get("job_type", "") or ""))
            exp_min, exp_max = _parse_experience(str(raw.get("experience", "") or ""))

            content_hash = compute_content_hash(title, company)

            return Job(
                id=f"{platform}:{ext_id}",
                title=title,
                company=company,
                location=location,
                is_remote=is_remote,
                employment_type=emp_type,
                description=description,
                description_clean=description_clean,
                apply_url=apply_url,
                posted_at=posted_at,
                scraped_at=datetime.utcnow(),
                platform=platform,
                salary_min=salary_min,
                salary_max=salary_max,
                skills=skills,
                experience_min=exp_min,
                experience_max=exp_max,
                content_hash=content_hash,
                raw=raw,
            )
        except Exception as e:
            log.error(f"[JobSpyNormalizer] Failed: {e} | raw keys: {list(raw.keys())}")
            return None


class WellfoundNormalizer:
    """
    Normalizes raw dicts from the Wellfound Playwright / Apollo-state scraper.

    Accepts two shapes:
    - Old GraphQL shape: startups=[{name}], locations=[{displayName}]
    - New Playwright shape: company=str, location=str, jdURL=str, posted_at=ISO
    """

    def normalize(self, raw: dict) -> Optional[Job]:
        try:
            ext_id = str(raw.get("id", "")).strip()
            title = str(raw.get("title", "")).strip()

            # Company: prefer flat "company" field (new), fall back to startups list (old)
            company = str(raw.get("company", "")).strip()
            if not company:
                startups = raw.get("startups") or [{}]
                if isinstance(startups, list) and startups:
                    company = str(startups[0].get("name", "")).strip()

            if not title or not company:
                return None

            # Location: prefer flat "location" field (new), fall back to locations list (old)
            location = str(raw.get("location", "")).strip()
            if not location:
                locations = raw.get("locations") or []
                if isinstance(locations, list) and locations:
                    location = ", ".join(
                        str(loc.get("displayName", ""))
                        for loc in locations
                        if loc.get("displayName")
                    )

            is_remote = bool(raw.get("remote", False))
            if not is_remote and "remote" in location.lower():
                is_remote = True

            description = str(raw.get("description", "") or "")
            description_clean = clean_description(description)

            # Apply URL: prefer jdURL (new Playwright scraper), then apply_url, then slug
            apply_url = (
                str(raw.get("jdURL") or raw.get("apply_url") or "")
            )
            if not apply_url:
                slug = raw.get("slug", "")
                apply_url = f"https://wellfound.com/jobs/{slug}" if slug else ""

            # Posted date
            posted_at = None
            raw_date = raw.get("posted_at")
            if raw_date:
                try:
                    posted_at = datetime.fromisoformat(str(raw_date))
                except Exception:
                    pass

            salary_min, salary_max = parse_salary_inr(str(raw.get("compensation", "") or ""))
            emp_type = _parse_employment_type(str(raw.get("jobType", "") or ""))
            content_hash = compute_content_hash(title, company)

            return Job(
                id=f"wellfound:{ext_id}" if ext_id else f"wellfound:{content_hash}",
                title=title,
                company=company,
                location=location,
                is_remote=is_remote,
                employment_type=emp_type,
                description=description,
                description_clean=description_clean,
                apply_url=apply_url,
                posted_at=posted_at,
                scraped_at=datetime.utcnow(),
                platform="wellfound",
                salary_min=salary_min,
                salary_max=salary_max,
                content_hash=content_hash,
                raw=raw,
            )
        except Exception as e:
            log.error(f"[WellfoundNormalizer] Failed: {e}")
            return None


class HiristNormalizer:
    """
    Normalizes raw dicts from Hirist.com.

    Handles two API shapes:
      - jobseeker-api.hirist.com/v2/jobfeed  (category feed, new endpoint)
        Fields: companyData.companyName, title, min/max (years), location[{name}]
      - legacy hirist.tech/api/job/search
        Fields: designation, company_name, job_description, etc.
    """

    def normalize(self, raw: dict) -> Optional[Job]:
        try:
            # --- ID ---
            ext_id = str(
                raw.get("jobId") or raw.get("job_id") or raw.get("id") or ""
            ).strip()

            # --- Title ---
            title = str(raw.get("title") or raw.get("designation") or "").strip()

            # --- Company: new API nests it under companyData ---
            company_data = raw.get("companyData") or {}
            company = str(
                company_data.get("companyName")
                or raw.get("company_name")
                or raw.get("company")
                or ""
            ).strip()

            if not title or not company:
                return None

            # --- Location: new API returns a list of {name: "..."} dicts ---
            loc_raw = raw.get("location") or raw.get("city") or ""
            if isinstance(loc_raw, list):
                location = ", ".join(
                    str(loc.get("name", "")) for loc in loc_raw if loc.get("name")
                )
            else:
                location = str(loc_raw).strip()

            is_remote = (
                "remote" in location.lower()
                or bool(raw.get("workFromHome", False))
                or bool(raw.get("is_remote", False))
            )

            # --- Description: not provided by category feed; use empty string ---
            description = str(raw.get("job_description") or raw.get("description") or "")
            description_clean = clean_description(description)

            # --- Apply URL ---
            apply_url = str(raw.get("apply_url") or raw.get("job_url") or "")
            if not apply_url and ext_id:
                apply_url = f"https://www.hirist.com/j/{ext_id}"

            # --- Posted date ---
            posted_at = None
            raw_date = raw.get("posted_on") or raw.get("posted_at")
            if raw_date:
                try:
                    posted_at = datetime.fromisoformat(str(raw_date))
                except Exception:
                    pass

            # --- Salary ---
            salary_str = raw.get("salary") or raw.get("ctc") or ""
            salary_min, salary_max = parse_salary_inr(str(salary_str))

            # --- Skills ---
            skills_raw = raw.get("skills") or raw.get("key_skills") or []
            if isinstance(skills_raw, list):
                skills = [str(s).strip() for s in skills_raw if s]
            elif isinstance(skills_raw, str):
                skills = [s.strip() for s in skills_raw.split(",") if s.strip()]
            else:
                skills = []

            # --- Experience: new API gives min/max years as integers ---
            exp_min_raw = raw.get("min") if "min" in raw else raw.get("experience")
            exp_max_raw = raw.get("max") if "max" in raw else None
            if isinstance(exp_min_raw, (int, float)) or isinstance(exp_max_raw, (int, float)):
                try:
                    exp_min = int(exp_min_raw) if exp_min_raw is not None else None
                    exp_max = int(exp_max_raw) if exp_max_raw is not None else exp_min
                except Exception:
                    exp_min, exp_max = None, None
            else:
                exp_min, exp_max = _parse_experience(str(exp_min_raw or ""))

            emp_type = _parse_employment_type(str(raw.get("job_type") or raw.get("jobType") or ""))
            content_hash = compute_content_hash(title, company)

            return Job(
                id=f"hirist:{ext_id}" if ext_id else f"hirist:{content_hash}",
                title=title,
                company=company,
                location=location,
                is_remote=is_remote,
                employment_type=emp_type,
                description=description,
                description_clean=description_clean,
                apply_url=apply_url,
                posted_at=posted_at,
                scraped_at=datetime.utcnow(),
                platform="hirist",
                salary_min=salary_min,
                salary_max=salary_max,
                skills=skills,
                experience_min=exp_min,
                experience_max=exp_max,
                content_hash=content_hash,
                raw=raw,
            )
        except Exception as e:
            log.error(f"[HiristNormalizer] Failed: {e}")
            return None


class NaukriNormalizer:
    """Normalizes raw dicts from the Naukri.com internal JSON API."""

    def normalize(self, raw: dict) -> Optional[Job]:
        try:
            ext_id = str(raw.get("jobId", "") or "").strip()
            title = str(raw.get("title", "") or "").strip()
            company = str(raw.get("companyName", "") or raw.get("company", "") or "").strip()

            if not title or not company:
                return None

            # Placeholders hold experience / salary / location as typed entries
            placeholders: list[dict] = raw.get("placeholders") or []
            experience_label = ""
            salary_label = ""
            location = ""
            for ph in placeholders:
                ph_type = str(ph.get("type", "")).lower()
                ph_label = str(ph.get("label", ""))
                if ph_type == "experience":
                    experience_label = ph_label
                elif ph_type == "salary":
                    salary_label = ph_label
                elif ph_type == "location":
                    location = ph_label

            is_remote = "remote" in location.lower() or bool(raw.get("isWork_from_home", False))

            description = str(raw.get("jobDescription", "") or "")

            # Playwright DOM scraper only gets card text — build a synthetic description
            # from title + skills so the semantic scorer has meaningful text to work with.
            tags_raw_early = str(raw.get("tagsAndSkills", "") or "")
            if len(description.strip()) < 80 and (title or tags_raw_early):
                description = (
                    f"{title} at {company}. "
                    f"Required skills: {tags_raw_early}. "
                    f"Experience: {experience_label}. Location: {location}."
                )

            description_clean = clean_description(description)

            # Build apply URL from jdURL (relative path) or construct from jobId
            jd_url = str(raw.get("jdURL", "") or "")
            if jd_url:
                apply_url = f"https://www.naukri.com{jd_url}" if jd_url.startswith("/") else jd_url
            elif ext_id:
                apply_url = f"https://www.naukri.com/job-listings-{ext_id}"
            else:
                apply_url = ""

            # posted date: Naukri sometimes returns footerPlaceholderLabel like "3 days ago"
            posted_at = None
            footer = str(raw.get("footerPlaceholderLabel", "") or "")
            if "today" in footer.lower():
                from datetime import date as _date
                posted_at = datetime.combine(_date.today(), datetime.min.time())

            salary_min, salary_max = parse_salary_inr(salary_label)

            # Skills: comma-separated string in tagsAndSkills
            tags_raw = str(raw.get("tagsAndSkills", "") or "")
            skills = [s.strip() for s in tags_raw.split(",") if s.strip()] if tags_raw else []

            exp_min, exp_max = _parse_experience(experience_label)
            emp_type = _parse_employment_type(str(raw.get("jobType", "") or ""))
            content_hash = compute_content_hash(title, company)

            return Job(
                id=f"naukri_api:{ext_id}" if ext_id else f"naukri_api:{content_hash}",
                title=title,
                company=company,
                location=location,
                is_remote=is_remote,
                employment_type=emp_type,
                description=description,
                description_clean=description_clean,
                apply_url=apply_url,
                posted_at=posted_at,
                scraped_at=datetime.utcnow(),
                platform="naukri",
                salary_min=salary_min,
                salary_max=salary_max,
                skills=skills,
                experience_min=exp_min,
                experience_max=exp_max,
                content_hash=content_hash,
                raw=raw,
            )
        except Exception as e:
            log.error(f"[NaukriNormalizer] Failed: {e}")
            return None


class InstahyreNormalizer:
    """Normalizes raw dicts from the Instahyre API."""

    def normalize(self, raw: dict) -> Optional[Job]:
        try:
            ext_id = str(raw.get("id") or raw.get("opportunity_id") or "").strip()
            title = str(raw.get("designation") or raw.get("title") or "").strip()

            company_data = raw.get("company") or {}
            if isinstance(company_data, dict):
                company = str(company_data.get("name") or "").strip()
            else:
                company = str(company_data).strip()

            if not title or not company:
                return None

            location_parts = raw.get("locations") or []
            if isinstance(location_parts, list):
                location = ", ".join(str(l) for l in location_parts if l)
            else:
                location = str(location_parts)
            if not location:
                location = str(raw.get("location") or raw.get("city") or "")

            is_remote = "remote" in location.lower() or bool(raw.get("is_remote", False))

            description = str(raw.get("details") or raw.get("description") or "")
            description_clean = clean_description(description)

            apply_url = str(raw.get("apply_url") or raw.get("url") or "")
            if not apply_url and ext_id:
                apply_url = f"https://www.instahyre.com/job-{ext_id}/"

            posted_at = None
            raw_date = raw.get("posted_at") or raw.get("created_at")
            if raw_date:
                try:
                    posted_at = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
                except Exception:
                    pass

            salary_str = raw.get("ctc") or raw.get("salary") or ""
            salary_min, salary_max = parse_salary_inr(str(salary_str))

            skills_raw = raw.get("skills") or []
            if isinstance(skills_raw, list):
                skills = []
                for s in skills_raw:
                    if isinstance(s, dict):
                        skills.append(str(s.get("name") or s.get("skill") or "").strip())
                    else:
                        skills.append(str(s).strip())
                skills = [s for s in skills if s]
            else:
                skills = []

            exp_min_raw = raw.get("min_experience") or raw.get("experience_min")
            exp_max_raw = raw.get("max_experience") or raw.get("experience_max")
            try:
                exp_min = int(exp_min_raw) if exp_min_raw is not None else None
                exp_max = int(exp_max_raw) if exp_max_raw is not None else None
            except Exception:
                exp_min, exp_max = None, None

            emp_type = _parse_employment_type(str(raw.get("opportunity_type") or ""))
            content_hash = compute_content_hash(title, company)

            return Job(
                id=f"instahyre:{ext_id}" if ext_id else f"instahyre:{content_hash}",
                title=title,
                company=company,
                location=location,
                is_remote=is_remote,
                employment_type=emp_type,
                description=description,
                description_clean=description_clean,
                apply_url=apply_url,
                posted_at=posted_at,
                scraped_at=datetime.utcnow(),
                platform="instahyre",
                salary_min=salary_min,
                salary_max=salary_max,
                skills=skills,
                experience_min=exp_min,
                experience_max=exp_max,
                content_hash=content_hash,
                raw=raw,
            )
        except Exception as e:
            log.error(f"[InstahyreNormalizer] Failed: {e}")
            return None


_NORMALIZERS = {
    "jobspy": JobSpyNormalizer(),
    "linkedin": JobSpyNormalizer(),
    "indeed": JobSpyNormalizer(),
    "naukri": JobSpyNormalizer(),      # from python-jobspy
    "naukri_api": NaukriNormalizer(),  # from our direct API scraper
    "glassdoor": JobSpyNormalizer(),
    "wellfound": WellfoundNormalizer(),
    "hirist": HiristNormalizer(),
    "instahyre": InstahyreNormalizer(),
}


def normalize_all(raw_jobs: list[tuple[str, dict]]) -> list[Job]:
    """
    Normalize a list of (platform_name, raw_dict) tuples into Job objects.
    Invalid or unparseable entries are silently dropped (logged at DEBUG).
    """
    jobs: list[Job] = []
    for platform, raw in raw_jobs:
        normalizer = _NORMALIZERS.get(platform.lower())
        if normalizer is None:
            log.warning(f"[Normalizer] No normalizer for platform '{platform}' — skipping")
            continue
        job = normalizer.normalize(raw)
        if job is not None:
            jobs.append(job)
    log.info(f"[Normalize] {len(jobs)}/{len(raw_jobs)} jobs successfully normalized")
    return jobs
