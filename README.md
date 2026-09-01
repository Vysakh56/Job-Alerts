# job-alerts

Twice a day, this pulls fresh Java/AEM job postings off LinkedIn, ranks them
against my resume, and emails me a CSV of the top 10 I haven't seen yet.
Built it because scrolling LinkedIn every morning got old.

Runs on GitHub Actions, triggered by a free external cron service — no
server, no hosting cost.

## Stack

- **Apify** — LinkedIn jobs scraper (free tier)
- **GitHub Actions** — does the actual work (fetch, rank, email)
- **cron-job.org** — fires the workflow on schedule (see below for why)
- **Python** (`requests`, `smtplib`) — filtering, ranking, email

## What it does

- Searches Kochi, Thiruvananthapuram, remote/international, Bangalore,
  Chennai, and pan-India, in that priority order
- Filters out anything senior/lead/architect, keeps entry-level and
  associate roles
- Drops anything explicitly asking for 5+ years, and anything asking for
  4 unless it's AEM, posted in the last 6 hours, and a strong match —
  not worth reaching for otherwise
- Cross-checks the description for an actual years-of-experience figure,
  since LinkedIn's "Entry level" tag and the fine print don't always
  agree — shows up as `Entry level (2-4 yr)` or `Missing (3-5 yr)` when
  they disagree or one's just missing
- Ranking order: freshness (posted ≤6 hrs) → skill category (AEM+Java >
  AEM > Java backend > Java full-stack > no language named) → recency →
  how well the years-required fits my actual 2-3 years → location
  priority → experience tag, last and weakest
- Grabs an application form link or contact email out of the description
  when one's given, so I'm not opening every listing just to find out how
  to apply
- Tracks what's already been sent (`sent_jobs.json`) so nothing repeats,
  with repost detection — if a job's posted date jumps forward or gets
  tagged "reposted," it counts as new again. Entries roll off after 30
  days, same as LinkedIn's own search window, so the file stays small
- Anything that doesn't make the top 10 in a run isn't lost — it sits in
  `pending_pool.json` and gets re-ranked next time
- Email is a CSV: role, company, location, posted date, matched skills,
  experience level, apply form/email if found, apply link

## Why cron-job.org and not GitHub's own schedule

Tried GitHub's built-in `schedule:` trigger first. It's "best effort" —
GitHub can just skip or delay a run under load, no warning, no retry —
and that's exactly what happened, several 8 AM and 1 PM slots never
fired at all. So scheduling now lives outside GitHub entirely:
cron-job.org hits GitHub's API on time and tells it to run the workflow.
GitHub still does all the actual work, it just doesn't decide when
anymore.

## Setting this up for yourself

You'll need three free accounts talking to each other: Apify (fetches
the jobs), Gmail (sends the email), and cron-job.org (times it).

1. Apify — grab an API token from Settings → Integrations. The free tier
   easily covers two runs a day at this scale.
2. Gmail — turn on 2-Step Verification if you haven't, then generate an
   [app password](https://myaccount.google.com/apppasswords). It's
   separate from your real password, revocable anytime, and can only
   send mail — nothing else.
3. Add four repo secrets under Settings → Secrets and variables →
   Actions: `APIFY_TOKEN`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`,
   `RECIPIENT_EMAIL`.
4. Make a GitHub [fine-grained token](https://github.com/settings/tokens?type=beta)
   scoped to just this repo, with Actions set to read-and-write. This is
   what lets an outside service trigger the workflow.
5. On cron-job.org, set up two jobs (8:00 and 13:00, Asia/Kolkata) that
   POST to
   `https://api.github.com/repos/<you>/<repo>/actions/workflows/job-alerts.yml/dispatches`
   with an `Authorization: Bearer <token>` header, `Accept:
   application/vnd.github+json`, `Content-Type: application/json`, and
   body `{"ref":"main","inputs":{"label":"Morning Digest"}}` (change the
   label for the afternoon one).
6. Run it manually from the Actions tab first to make sure the pipeline
   itself works before trusting the schedule.

Nothing sensitive sits in the code — every credential is a GitHub
encrypted secret pulled in via `os.environ`, never hardcoded.

## Tuning it

Everything worth tweaking sits near the top of `job_fetcher.py`:

- `SEARCHES` — cities, keywords, location priority order
- `PROFILE_SKILLS` — keywords used for match scoring
- `TITLE_EXCLUDE` — seniority terms to filter out
- `MAX_JOBS_PER_EMAIL` — jobs per email, default 10
- `DEDUP_WINDOW_DAYS` / `POOL_WINDOW_DAYS` — how long jobs are remembered
- `HIS_EXPERIENCE_YEARS` — the experience level used for the numeric fit score

## Known limitations

- Apify's free-tier credits are limited — fine at this volume, but
  scaling up search count or frequency will burn through them faster
- Doesn't touch my LinkedIn account directly, no login, no "mark as
  viewed." LinkedIn doesn't take kindly to automating a personal
  account, so dedup runs entirely off the local job-ID log instead
- The apply-form/email extraction is a best-effort regex match — it
  won't catch every phrasing, and leaves the column blank rather than
  guessing
