# Job Alerts — Automated LinkedIn Digest (Java/AEM, India)

Sends you a CSV of up to 10 new Java/AEM job matches to
**vysakhvenugopalan@gmail.com** every day at **8:00 AM** and **1:00 PM IST**,
never repeating a job you've already been sent.

Runs entirely on **GitHub Actions** (free) + **Apify** (free-tier scraper) +
**Gmail SMTP** (free). No servers, no paid hosting.

---

## How it works

1. `job_fetcher.py` calls the Apify LinkedIn-jobs-scraper for 5 searches:
   Kochi, Bangalore, Chennai, rest-of-India, and Remote/foreign-hiring roles.
2. Jobs are filtered to exclude Senior/Lead/Architect titles and experience
   levels above Associate.
3. Remaining jobs are ranked by:
   1. **Profile match** — keyword overlap with your resume (Java, AEM, Sling
      Models, HTL, Dispatcher, etc.)
   2. **Posted date** — newest first
   3. **Experience level** — Entry level > Associate > Not specified
   4. **Location priority** — Kochi > Bangalore > Chennai > rest of India >
      Remote/abroad
4. The top 10 **new** jobs (i.e. not in `sent_jobs.json`) are written to a
   CSV and emailed to you.
5. Those job IDs get added to `sent_jobs.json`, which the workflow commits
   back to the repo — so the afternoon run (and every day after) won't
   repeat them. Entries auto-expire after 45 days so the file doesn't grow
   forever.

**Columns in the CSV:** `Sl.No, Job Role, Company Name, Posted Date, Skills
Required, Exp Level, Apply Link`

---

## One-time setup (about 10 minutes)

### 1. Create a GitHub repo
- Create a new **private** repository (e.g. `job-alerts`).
- Upload all the files in this folder, keeping the `.github/workflows/`
  folder structure intact.

### 2. Get an Apify API token (free tier)
- Sign up at [apify.com](https://apify.com) (free plan gives monthly credits,
  which is enough for 2 runs/day at this scale).
- Go to **Settings → Integrations** and copy your API token.

### 3. Create a Gmail App Password
Gmail blocks plain-password SMTP logins, so you need an "app password":
- Turn on 2-Step Verification on your Google account if it isn't already:
  https://myaccount.google.com/security
- Go to https://myaccount.google.com/apppasswords
- Create a new app password (name it "job-alerts"), copy the 16-character
  code.

### 4. Add secrets to your GitHub repo
In your repo: **Settings → Secrets and variables → Actions → New repository
secret**. Add these four:

| Secret name | Value |
|---|---|
| `APIFY_TOKEN` | your Apify API token |
| `GMAIL_ADDRESS` | the Gmail address you'll send *from* (can be the same or a different account) |
| `GMAIL_APP_PASSWORD` | the 16-character app password from step 3 |
| `RECIPIENT_EMAIL` | `vysakhvenugopalan@gmail.com` |

### 5. Enable the workflow
- GitHub Actions is enabled by default on new repos. The schedule in
  `.github/workflows/job-alerts.yml` will start firing automatically at the
  next 8 AM or 1 PM IST slot.
- To test it immediately: go to the **Actions** tab → **Job Alerts** →
  **Run workflow** (this uses the `workflow_dispatch` trigger).

---

## Notes & limitations

- **GitHub Actions cron schedules can lag by a few minutes** during high load
  on GitHub's shared runners — expect the email within ~15 minutes of 8 AM /
  1 PM IST, not to the second.
- **Apify free-tier credits** are limited per month. Two runs/day at ~10
  jobs each should fit comfortably, but if you add more searches or higher
  limits later, keep an eye on usage at apify.com/billing.
- I intentionally did **not** wire this up to your LinkedIn login to check
  "viewed/clicked" status — LinkedIn's terms prohibit automated access via
  personal accounts and doing so risks a ban. The dedup file achieves the
  same practical goal (never see the same job twice) without that risk.
- If a scheduled run fails (e.g. Apify hiccup), check the **Actions** tab
  for the error log — nothing will silently break for good, but that run's
  email won't go out.

## Adjusting things later
- **Change job count per email:** edit `MAX_JOBS_PER_EMAIL` in `job_fetcher.py`.
- **Add/remove cities or searches:** edit the `SEARCHES` list.
- **Tweak skill keywords / seniority filter:** edit `PROFILE_SKILLS` and
  `TITLE_EXCLUDE`.
- **Change times:** edit the two `cron:` lines in the workflow file (remember
  they're in UTC, IST is UTC+5:30).
