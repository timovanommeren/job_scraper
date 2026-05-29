@echo off
cd /d "C:\Users\timov\Documents\Claude\Projects\Build Job Scraper\job_scraper"

REM ── Start feedback server only if not already running ─────────────────────────
netstat -ano | findstr "127.0.0.1:5001" >nul 2>&1
if errorlevel 1 (
    echo Starting feedback server...
    start "Job Scraper — Feedback Server" /min "C:\Python\Python310\pythonw.exe" feedback\server.py
    timeout /t 2 /nobreak >nul
) else (
    echo Feedback server already running on port 5001.
)

REM ── Run main pipeline ─────────────────────────────────────────────────────────
python main.py >> logs\task_scheduler_output.log 2>&1
