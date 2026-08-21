# Run this ONCE, in a normal (non-admin is fine) PowerShell window, to
# register the daily scrape task. Re-run it any time to update the schedule
# - it replaces the existing task with the same name.
#
# What it sets up:
#   - Runs scripts\run_daily_scrape.ps1 daily at $TaskTime
#   - Wakes the machine from SLEEP to run it (does NOT power on a fully
#     shut-down machine - see chat notes: that needs BIOS-level Wake-on-LAN
#     support, which is inconsistent on laptops)
#   - Runs only in your interactive logged-on session (required because
#     scraper.py opens a visible, non-headless Chrome window on purpose)
#   - Skips the run if not on AC power, so it won't drain the battery
#   - If the machine was off/missed the exact time, it catches up and runs
#     as soon as it's next available (StartWhenAvailable)

$TaskName = "NewspaperOCR_DailyScrape"
$TaskTime = "06:45"   # runs well before the GitHub Actions fallback cron
$ScriptPath = "D:\Newspaper_OCR_engine\scripts\run_daily_scrape.ps1"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""

$Trigger = New-ScheduledTaskTrigger -Daily -At $TaskTime

$Settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
# Note: -AllowStartIfOnBatteries omitted on purpose, so it only runs on AC power.

$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName `
    -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal `
    -Description "Scrapes Loksatta/Lokmat via scraper.py (needs residential IP for Cloudflare) and notifies the GitHub Actions pipeline." `
    -Force

Write-Host "Task '$TaskName' registered: runs daily at $TaskTime, wakes from sleep, AC-power only."
Write-Host "To test it immediately without waiting: Start-ScheduledTask -TaskName '$TaskName'"
