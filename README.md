# job-alerts

Twice a day, this pulls fresh Java/AEM job postings off LinkedIn, ranks them
against my resume, and emails me a CSV of the top 10 I haven't seen yet.
Built it because scrolling LinkedIn every morning got old.

Runs on GitHub Actions — no server, no hosting cost.

## Stack

- **Apify** — LinkedIn jobs scraper (free tier)
- **GitHub Actions** — cron schedule + execution
- **Python** (`requests`, `smtplib`) — filtering, ranking, email

## What it does

- Searches Kochi, Thiruvananthapuram, remote/international, Bangalore,
  Chennai, and pan-India — in that priority order
- Filters out anything senior/lead/architect level, keeps entry-level and
  associate roles
- Cross-checks the description text for an actual years-of-experience
  figure, since LinkedIn's "Entry level" tag and the fine print in the post
  don't always agree — output looks like `Entry level (2-4 yr)` or
  `Missing (3-5 yr)` when the tag and the text disagree or one's absent
- Scores every job on keyword overlap with my skills (Java, AEM, Sling
  Models, HTL, Dispatcher, etc.), then sorts by that, then recency, then
  experience level, then location priority
- Keeps a running log (`sent_jobs.json`) so nothing gets emailed twice, and
  a `pending_pool.json` so jobs that don't make the top 10 in one run stick
  around and get re-ranked next time instead of disappearing
- Emails the top 10 as a CSV: role, company, location, posted date, matched
  skills, experience level, apply link

Schedule: 8 AM and 1 PM IST daily (`.github/workflows/job-alerts.yml`).

## Running your own copy

1. Fork/clone this repo.
2. Get an [Apify](https://apify.com) API token (Settings → Integrations)
   — free tier covers this easily at 2 runs/day.
3. Generate a Gmail [app password](https://myaccount.google.com/apppasswords)
   (needs 2-Step Verification on first). This is separate from your real
   password and can be revoked anytime — it only allows sending mail
   through SMTP, nothing else.
4. Add repo secrets under **Settings → Secrets and variables → Actions**:
   - `APIFY_TOKEN`
   - `GMAIL_ADDRESS`
   - `GMAIL_APP_PASSWORD`
   - `RECIPIENT_EMAIL`
5. Trigger a manual run from the **Actions** tab to confirm it works before
   waiting for the schedule.

Nothing sensitive lives in the code — all credentials are GitHub encrypted
secrets, referenced via `os.environ`, never hardcoded.

## Tuning it

Everything worth tweaking is near the top of `job_fetcher.py`:

- `SEARCHES` — cities/keywords/location priority order
- `PROFILE_SKILLS` — keywords used for match scoring
- `TITLE_EXCLUDE` — seniority terms to filter out
- `MAX_JOBS_PER_EMAIL` — how many jobs per email (default 10)
- `DEDUP_WINDOW_DAYS` / `POOL_WINDOW_DAYS` — how long jobs are remembered

## Known limitations

- GitHub's free scheduled runs can lag a few minutes past the exact cron
  time under load
- Apify free-tier credits are finite — fine at this volume, but scaling up
  search count/frequency will eat into them faster
- Doesn't touch my LinkedIn account directly (no login, no "mark as
  viewed") — LinkedIn's ToS doesn't look kindly on automating a personal
  account, so dedup is handled entirely through the local job-ID log instead
