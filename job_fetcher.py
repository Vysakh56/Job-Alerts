#!/usr/bin/env python3
"""
Job Alerts — fetches Java/AEM jobs from LinkedIn (via Apify), scores them
against Vysakh's profile, dedupes against previously-sent jobs, writes a
CSV, and emails it.

Runs twice a day via GitHub Actions (8:00 AM and 1:00 PM IST).
"""

import os
import csv
import json
import re
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

import requests

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

APIFY_TOKEN = os.environ["APIFY_TOKEN"]
GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT_EMAIL = os.environ["RECIPIENT_EMAIL"]

ACTOR = "valig/linkedin-jobs-scraper"
APIFY_RUN_URL = f"https://api.apify.com/v2/acts/{ACTOR.replace('/', '~')}/run-sync-get-dataset-items"

SENT_JOBS_FILE = os.path.join(os.path.dirname(__file__), "sent_jobs.json")
PENDING_POOL_FILE = os.path.join(os.path.dirname(__file__), "pending_pool.json")
MAX_JOBS_PER_EMAIL = 10
DEDUP_WINDOW_DAYS = 45   # forget SENT jobs older than this so the file doesn't grow forever
POOL_WINDOW_DAYS = 30    # drop PENDING (unsent) jobs from the pool once they're this old

# Vysakh's actual years of experience — used to score how well a job's
# stated experience requirement fits him, independent of LinkedIn's coarse
# Entry level / Associate tag. Treated as the midpoint of a 2-3 year range.
HIS_EXPERIENCE_YEARS = 2.5

# Search plan: (location, keywords, extra url params, tier)
# Tier is used for the location-priority tiebreaker (lower = higher priority).
# Priority order: Kochi > Thiruvananthapuram > Remote > Bangalore > Chennai > rest of India
SEARCHES = [
    ("Kochi, Kerala, India", "Java Developer OR AEM Developer", {"f_E": "2,3"}, 1),
    ("Thiruvananthapuram, Kerala, India", "Java Developer OR AEM Developer", {"f_E": "2,3"}, 2),
    ("India", "Java Developer OR AEM Developer Remote", {"f_WT": "2"}, 3),
    ("Bengaluru, Karnataka, India", "Java Developer OR AEM Developer", {"f_E": "2,3"}, 4),
    ("Chennai, Tamil Nadu, India", "Java Developer OR AEM Developer", {"f_E": "2,3"}, 5),
    ("India", "Java Developer OR AEM Developer OR Backend Engineer", {"f_E": "2,3"}, 6),
]

# Profile keywords used for match scoring (from Vysakh's resume)
PROFILE_SKILLS = [
    "java", "aem", "adobe experience manager", "sling", "sling models",
    "htl", "sightly", "dispatcher", "osgi", "jcr", "javascript",
    "rest api", "rest apis", "ajax", "maven", "git", "sonarqube",
    "full stack", "backend", "spring", "spring boot", "microservices",
]

TITLE_EXCLUDE = [
    "senior", "sr.", "sr ", "lead", "architect", "principal", "manager",
    "director", "head of", "staff engineer", "vp ", "6+", "7+", "8+", "9+", "10+",
]

GOOD_EXP_LEVELS = {"entry level", "associate", "not applicable", ""}


# ---------------------------------------------------------------------------
# FETCH
# ---------------------------------------------------------------------------

def fetch_jobs():
    all_jobs = []
    for location, keywords, extra_params, tier in SEARCHES:
        url_param = [{"key": k, "value": v} for k, v in extra_params.items()]
        payload = {
            "keywords": keywords,
            "location": location,
            "datePosted": "r2592000",  # last 30 days
            "limit": 25,
            "urlParam": url_param,
        }
        try:
            resp = requests.post(
                APIFY_RUN_URL,
                params={"token": APIFY_TOKEN},
                json=payload,
                timeout=120,
            )
            resp.raise_for_status()
            items = resp.json()
            for item in items:
                item["_tier"] = tier
            all_jobs.append(items)
        except Exception as e:
            print(f"[warn] search failed for {location} / {keywords}: {e}")
        time.sleep(1)

    # flatten + dedupe by job id within this run
    seen_ids = set()
    flat = []
    for batch in all_jobs:
        for job in batch:
            jid = job.get("id") or job.get("url")
            if jid in seen_ids:
                continue
            seen_ids.add(jid)
            flat.append(job)
    return flat


# ---------------------------------------------------------------------------
# FILTER + SCORE
# ---------------------------------------------------------------------------

def is_excluded_title(title: str) -> bool:
    t = title.lower()
    return any(bad in t for bad in TITLE_EXCLUDE)


def extract_skills(description: str) -> str:
    if not description:
        return ""
    desc_lower = description.lower()
    found = [s for s in PROFILE_SKILLS if s in desc_lower]
    # de-dupe near-duplicates (e.g. "sling" and "sling models")
    cleaned = []
    for s in found:
        if not any(s != other and s in other for other in found):
            cleaned.append(s)
    return ", ".join(sorted(set(cleaned))[:8]) if cleaned else "Java/AEM (see listing)"


def extract_experience_range(description: str):
    """
    Looks in the job description for an explicit years-of-experience figure,
    since LinkedIn's own "Entry level" / "Associate" tag often doesn't match
    what's actually written in the body (e.g. tagged Entry level but body
    says "3-5 years experience required").

    Returns (min_years, max_years) — either can be None if not stated.
    """
    if not description:
        return None, None
    text = description.lower()

    # "3-5 years", "3 to 5 years", "3–5 yrs"
    m = re.search(r'(\d+)\s*(?:-|to|–|—)\s*(\d+)\s*\+?\s*years?', text)
    if m:
        return int(m.group(1)), int(m.group(2))

    # "2+ years", "minimum 2 years", "at least 2 years", "min. 2 yrs"
    m = re.search(r'(?:minimum|min\.?|at least)\s*(\d+)\+?\s*years?', text)
    if m:
        return int(m.group(1)), None
    m = re.search(r'(\d+)\+\s*years?', text)
    if m:
        return int(m.group(1)), None

    # "up to 5 years", "maximum 5 years", "less than 5 years"
    m = re.search(r'(?:up to|maximum|max\.?|less than)\s*(\d+)\s*years?', text)
    if m:
        return None, int(m.group(1))

    return None, None


def format_exp_level(job) -> str:
    """
    Combines LinkedIn's tagged level with any explicit range found in the
    description, e.g.:
      "Entry level" + description says "3-5 years"  -> "Entry level (3-5 yr)"
      no tag + description says "2-4 years"          -> "Missing (2-4 yr)"
      "Entry level" + description says "min 2 years" -> "Entry level (2-none yr)"
      "Entry level" + description says "up to 5 yrs" -> "Entry level (none-5 yr)"
      "Entry level" + nothing found in description    -> "Entry level"
    """
    raw_level = (job.get("experienceLevel") or "").strip()
    is_tagged = bool(raw_level) and raw_level.lower() != "not applicable"
    level_label = raw_level if is_tagged else "Missing"

    min_y, max_y = extract_experience_range(job.get("description", ""))
    if min_y is None and max_y is None:
        return raw_level if is_tagged else "Not specified"

    min_s = str(min_y) if min_y is not None else "none"
    max_s = str(max_y) if max_y is not None else "none"
    return f"{level_label} ({min_s}-{max_s} yr)"


def match_score(job) -> float:
    """
    Skill-category score — the PRIMARY sort key. Categories, highest to
    lowest fit for a Java/AEM developer profile:

      5.0  AEM + Java both present            (ideal — matches full profile)
      4.0  AEM present, Java not required     (core specialty)
      3.5  Java/Spring Boot, backend-only     (strong language match, no JS)
      3.0  Java/Spring Boot, full-stack/JS    (language match, but full-stack
                                                implies JavaScript, a weaker skill)
      2.0  No specific language named         (generic "software engineer" /
                                                "backend engineer" posting)

    "java" is matched on a word boundary so it never matches inside
    "javascript".
    """
    text = f"{job.get('title','')} {job.get('description','')}".lower()

    has_aem = bool(re.search(
        r'\b(aem|adobe experience manager|sling|htl|sightly|dispatcher|osgi)\b', text
    ))
    has_java = bool(re.search(r'\bjava\b', text)) or bool(re.search(
        r'\b(spring boot|springboot|spring framework)\b', text
    ))
    has_js = bool(re.search(
        r'\b(javascript|react|angular|vue|typescript|front-?end)\b', text
    ))
    is_fullstack = "full stack" in text or "full-stack" in text or "fullstack" in text or has_js

    if has_aem and has_java:
        return 5.0
    if has_aem:
        return 4.0
    if has_java:
        return 3.0 if is_fullstack else 3.5
    return 2.0


def is_fresh(job, hours_threshold: int = 6) -> bool:
    """
    A job posted within the last few hours is treated as urgent and jumps
    to the top of the list — but only if it's actually Java/AEM related
    (not just any job that happened to match a broader search keyword).
    """
    text = f"{job.get('title','')} {job.get('description','')}".lower()
    is_relevant = bool(re.search(r'\b(java|aem|adobe experience manager)\b', text))
    if not is_relevant:
        return False
    age_hours = (datetime.now(timezone.utc) - posted_dt(job)).total_seconds() / 3600
    return age_hours <= hours_threshold


def exp_score(job) -> int:
    level = (job.get("experienceLevel") or "").lower()
    if level in ("entry level",):
        return 2
    if level in ("associate",):
        return 1
    return 0  # "not applicable" / missing — no longer penalized heavily, see filter_and_rank


def exp_fit_score(job) -> float:
    """
    How well the job's ACTUAL stated years-of-experience (parsed from the
    description, not LinkedIn's coarse tag) fits Vysakh's real 2-3 years.
    This outranks location — a numeric mismatch matters more than being in
    the wrong city, but a numeric match matters less than skill fit.

    Higher = better fit.
      - Comfortably qualifies AND range is tight around 2-3 yrs -> highest
      - Comfortably qualifies, wider/one-sided range (e.g. "2+ years",
        "up to 5 years") -> good, but not as high as a tight match
      - No number stated at all -> neutral (0)
      - Needs MORE experience than he has (e.g. "5+ years") -> penalized,
        worse the bigger the gap
      - Overqualified (e.g. "0-1 years") -> mildly penalized
    """
    min_y, max_y = extract_experience_range(job.get("description", ""))
    if min_y is None and max_y is None:
        return 0.0

    if min_y is not None and HIS_EXPERIENCE_YEARS < min_y:
        gap = min_y - HIS_EXPERIENCE_YEARS
        return -gap * 2  # under-qualified: penalized harder

    if max_y is not None and HIS_EXPERIENCE_YEARS > max_y:
        gap = HIS_EXPERIENCE_YEARS - max_y
        return -gap  # overqualified: penalized more lightly

    # He comfortably qualifies — reward tighter ranges around his level
    if min_y is not None and max_y is not None:
        span = max_y - min_y
        return 5.0 - min(span, 4)
    return 3.0  # only one bound given, but he clearly qualifies


def posted_dt(job):
    ds = job.get("postedDate")
    if not ds:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(ds.replace("Z", "+00:00"))
    except Exception:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def filter_and_rank(jobs, already_sent: set):
    filtered = []
    for job in jobs:
        jid = job.get("id") or job.get("url")
        if not jid or jid in already_sent:
            continue
        title = job.get("title", "")
        if is_excluded_title(title):
            continue
        level = (job.get("experienceLevel") or "").lower()
        if level not in GOOD_EXP_LEVELS:
            continue
        filtered.append(job)

    # Sort priority, in order:
    #   1. Freshness — posted <=6 hrs ago (and actually Java/AEM related)
    #      jumps straight to the top, ahead of everything else.
    #   2. Skill-category match — AEM+Java > AEM > Java backend >
    #      Java full-stack > no language named.
    #   3. Posted recency — newer first.
    #   4. Experience NUMBER fit — how well the stated years-required
    #      (parsed from the description) matches his real 2-3 years.
    #      This outranks location.
    #   5. Location priority — Kochi > Trivandrum > Remote/abroad >
    #      Bangalore > Chennai > rest of India.
    #   6. Experience LEVEL tag — Entry level > Associate > Missing.
    #      Last and weak: a "Missing" tag in a top location should still
    #      beat a tagged "Associate" job in a lower-priority location.
    filtered.sort(
        key=lambda j: (
            0 if is_fresh(j) else 1,
            -match_score(j),
            -posted_dt(j).timestamp(),
            -exp_fit_score(j),
            j.get("_tier", 99),
            -exp_score(j),
        )
    )
    return filtered[:MAX_JOBS_PER_EMAIL]


# ---------------------------------------------------------------------------
# DEDUP STORE
# ---------------------------------------------------------------------------

def load_sent_jobs():
    if not os.path.exists(SENT_JOBS_FILE):
        return {}
    with open(SENT_JOBS_FILE, "r") as f:
        return json.load(f)


def save_sent_jobs(store: dict):
    cutoff = datetime.now(timezone.utc) - timedelta(days=DEDUP_WINDOW_DAYS)
    pruned = {
        jid: ts for jid, ts in store.items()
        if datetime.fromisoformat(ts) > cutoff
    }
    with open(SENT_JOBS_FILE, "w") as f:
        json.dump(pruned, f, indent=2)


def load_pending_pool():
    """Jobs we've already fetched at least once but haven't sent yet."""
    if not os.path.exists(PENDING_POOL_FILE):
        return {}
    with open(PENDING_POOL_FILE, "r") as f:
        return json.load(f)


def save_pending_pool(pool: dict):
    cutoff = datetime.now(timezone.utc) - timedelta(days=POOL_WINDOW_DAYS)
    pruned = {}
    for jid, job in pool.items():
        if posted_dt(job) > cutoff:
            pruned[jid] = job
    with open(PENDING_POOL_FILE, "w") as f:
        json.dump(pruned, f, indent=2)


# ---------------------------------------------------------------------------
# CSV + EMAIL
# ---------------------------------------------------------------------------

def write_csv(jobs, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Sl.No", "Job Role", "Company Name", "Location", "Posted Date",
            "Skills Required", "Exp Level", "Apply Link",
        ])
        for i, job in enumerate(jobs, 1):
            writer.writerow([
                i,
                job.get("title", ""),
                job.get("companyName", ""),
                job.get("location", "Not specified"),
                job.get("postedTimeAgo") or job.get("postedDate", ""),
                extract_skills(job.get("description", "")),
                format_exp_level(job),
                job.get("url", ""),
            ])


def send_email(csv_path, jobs, run_label):
    msg = MIMEMultipart()
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = f"Job Matches — {run_label} ({len(jobs)} new)"

    lines = [f"{run_label} job digest — {len(jobs)} new matches (not sent to you before):", ""]
    for i, job in enumerate(jobs, 1):
        lines.append(f"{i}. {job.get('title')} — {job.get('companyName')} ({job.get('postedTimeAgo','')})")
    lines.append("\nFull details in the attached CSV.")
    msg.attach(MIMEText("\n".join(lines), "plain"))

    if jobs:
        with open(csv_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=os.path.basename(csv_path))
        part["Content-Disposition"] = f'attachment; filename="{os.path.basename(csv_path)}"'
        msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, RECIPIENT_EMAIL, msg.as_string())


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    run_label = os.environ.get("RUN_LABEL", "Job Digest")

    sent_store = load_sent_jobs()
    already_sent = set(sent_store.keys())

    # Leftover jobs seen in earlier runs but never sent (e.g. this morning's
    # extras that didn't make the top 10) — carried forward so nothing gets
    # silently dropped.
    pool = load_pending_pool()

    # Fresh fetch, merged into the pool (new jobs added, duplicates ignored).
    raw_jobs = fetch_jobs()
    for job in raw_jobs:
        jid = job.get("id") or job.get("url")
        if jid and jid not in already_sent:
            pool[jid] = job  # overwrite with latest version (fresher postedTimeAgo etc.)

    # Rank the WHOLE pool (old leftovers + new arrivals) together, so a
    # 3-day-old unsent job from this morning can still outrank a brand new
    # one this afternoon if it's a better match.
    candidates = list(pool.values())
    top_jobs = filter_and_rank(candidates, already_sent)

    csv_path = os.path.join(os.path.dirname(__file__), "latest_jobs.csv")
    write_csv(top_jobs, csv_path)

    send_email(csv_path, top_jobs, run_label)

    now_iso = datetime.now(timezone.utc).isoformat()
    sent_ids_this_run = set()
    for job in top_jobs:
        jid = job.get("id") or job.get("url")
        sent_store[jid] = now_iso
        sent_ids_this_run.add(jid)
    save_sent_jobs(sent_store)

    # Remove newly-sent jobs from the pool; everything else stays for next time.
    remaining_pool = {jid: job for jid, job in pool.items() if jid not in sent_ids_this_run}
    save_pending_pool(remaining_pool)

    print(
        f"Sent {len(top_jobs)} jobs. "
        f"Pool remaining for next run: {len(remaining_pool)}. "
        f"Total tracked sent (unexpired): {len(sent_store)}."
    )


if __name__ == "__main__":
    main()
