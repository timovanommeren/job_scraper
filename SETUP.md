# Job Scraper — Setup & Task Scheduler

## Windows Task Scheduler

Four scheduled tasks keep the pipeline running automatically.

### Task 1 — Feedback Server (runs at every user logon, stays alive)

The feedback server must be running for localhost feedback links to work.
It is registered as a persistent logon task — starts automatically when you log in,
restarts automatically if it crashes.

Register with PowerShell (already done):
```powershell
$action   = New-ScheduledTaskAction `
    -Execute "C:\Python\Python310\pythonw.exe" `
    -Argument "`"C:\Users\timov\Documents\Claude\Projects\Build Job Scraper\job_scraper\feedback\server.py`"" `
    -WorkingDirectory "C:\Users\timov\Documents\Claude\Projects\Build Job Scraper\job_scraper"
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([System.TimeSpan]::Zero) -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 2)
Register-ScheduledTask -TaskName "JobScraperFeedbackServer" -Action $action -Trigger $trigger -Settings $settings -Force
```

Start/stop manually:
```bat
schtasks /run /tn "JobScraperFeedbackServer"
schtasks /end /tn "JobScraperFeedbackServer"
```

Logs: `logs\server.log`

### Task 2 — Daily Scrape (runs every day at 07:00)

Creates/registers the task named `JobScraperDaily`:

```bat
schtasks /create /tn "JobScraperDaily" ^
  /tr "\"C:\Users\timov\Documents\Claude\Projects\Build Job Scraper\job_scraper\run.bat\"" ^
  /sc daily /st 07:00 /f
```

`run.bat` checks if the feedback server is already running and starts it if not,
then runs `python main.py`.

### Task 3 — Weekly Digest (runs every Tuesday at 08:00)

Creates/registers the task named `JobScraperWeeklyDigest`:

```bat
schtasks /create /tn "JobScraperWeeklyDigest" ^
  /tr "\"C:\Python\Python310\python.exe\" \"C:\Users\timov\Documents\Claude\Projects\Build Job Scraper\job_scraper\main.py\" --weekly-digest" ^
  /sc weekly /d TUE /st 08:00 /f
```

The weekly digest emails all jobs added in the last 7 days, grouped by tier
(Strong 8–10 / Relevant 5–7 / Low 1–4). It is independent of the daily run.

### Task 4 — Phone Feedback Sync (legacy — no longer used)

This task ran `feedback/cf_sync.py` hourly to pull phone feedback out of a Cloudflare KV store. The KV store was removed in the **Architecture C** migration: phone ratings now POST directly to the Flask API over a Cloudflare Tunnel, so there is nothing to sync. `cf_sync.py` is a no-op that exits cleanly.

**Do not register this task for a new setup.** If it is already registered from an older install it does no harm (it runs a no-op), but you can remove it:

```bat
schtasks /delete /tn "JobScraperFeedbackSync" /f
```

### Verify tasks

```bat
schtasks /query /tn "JobScraperFeedbackServer"
schtasks /query /tn "JobScraperDaily"
schtasks /query /tn "JobScraperWeeklyDigest"
```

### Run immediately (for testing)

```bat
schtasks /run /tn "JobScraperFeedbackServer"
schtasks /run /tn "JobScraperDaily"
schtasks /run /tn "JobScraperWeeklyDigest"
```

---

## Manual commands

```bash
# Daily run (full pipeline)
python main.py

# Test mode (scrape + score, no DB writes, no email — prints digest)
python main.py --test

# Dry run (scrape + score + DB write, no email)
python main.py --dry-run

# Weekly digest only
python main.py --weekly-digest

# Weekly digest preview (no email)
python main.py --weekly-digest --test

# Backfill missing deadlines for all jobs in DB
python main.py --backfill-deadlines

# Run only one scraper
python main.py --site euraxess

# Re-score last N failed extractions
python main.py --reprocess 10
```

## Feedback server

The server runs automatically at logon via the `JobScraperFeedbackServer` scheduled task.
No manual start needed after initial setup.

Start manually if needed:
```bash
python feedback/server.py
```

Open browser: http://localhost:5001
Logs: logs/server.log

---

## Phone feedback (optional — Cloudflare Worker)

By default, the 1–10 rating row in every email digest links to `localhost:5001` — desktop only. To make it work on your phone (tapping a number records the score remotely), deploy the Cloudflare Worker:

The data path is: email pill → Cloudflare Worker → **Cloudflare Tunnel** → Flask `/api/v1/feedback` on `localhost:5001`. There is no KV store. The Worker reads the tunnel's public hostname from `FLASK_API_URL` in `cloudflare/worker/wrangler.toml` (currently `https://feedback-api.timovanommeren.com`).

1. Install [Wrangler](https://developers.cloudflare.com/workers/wrangler/install-update/): `npm install -g wrangler`
2. Authenticate: `wrangler login`
3. Stand up a Cloudflare Tunnel that exposes the Flask server (`localhost:5001`) at a public hostname. Standard named-tunnel flow (run once):
   ```bash
   cloudflared tunnel login
   cloudflared tunnel create job-feedback
   cloudflared tunnel route dns job-feedback feedback-api.<your-domain>
   # In the tunnel's config.yml, point ingress at the Flask server:
   #   ingress:
   #     - hostname: feedback-api.<your-domain>
   #       service: http://localhost:5001
   #     - service: http_status:404
   cloudflared service install   # runs on boot as the Windows "cloudflared" service
   ```
   Verify it's up: `sc query cloudflared` (STATE = RUNNING) and `curl https://feedback-api.<your-domain>/health` (200).
4. Set `FLASK_API_URL` in `cloudflare/worker/wrangler.toml` to your tunnel hostname.
5. Set the shared HMAC secret (used as the `Authorization: Bearer` token on the Flask `/api/v1/*` routes): `wrangler secret put CF_WORKER_SECRET` (from `cloudflare/worker/`).
6. Deploy the Worker: `wrangler deploy` (from `cloudflare/worker/`). The build hook runs `scripts/generate_worker_form.py` to regenerate the survey form from `config/criteria.yaml`.
7. Add to `.env`:
   ```
   CF_WORKER_URL=https://job-feedback.<your-subdomain>.workers.dev
   CF_WORKER_SECRET=<same secret from step 5>
   ```

> The tunnel name (`job-feedback`) and hostname (`feedback-api.timovanommeren.com`) above reflect the existing deployment — substitute your own. The `cloudflared` commands are the standard Cloudflare named-tunnel flow; the tunnel config itself lives outside this repo.

Once configured, each rating pill in the email generates an HMAC-signed link (valid for a 7-day weekly bucket) that routes through the Worker and records your score. The Worker POSTs the rating straight to the Flask API over a Cloudflare Tunnel, so it lands in the local DB immediately — there is no KV store or batch sync.
