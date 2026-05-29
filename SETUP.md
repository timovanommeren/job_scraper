# Job Scraper — Setup & Task Scheduler

## Windows Task Scheduler

Three scheduled tasks keep the pipeline running automatically.

### Task 1 — Feedback Server (runs at every user logon, stays alive)

The feedback server must be running for email links (Interested / Pass / Rate) to work.
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
