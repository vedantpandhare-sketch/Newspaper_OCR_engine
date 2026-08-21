# Wrapper invoked by the Windows Scheduled Task. Runs the local scrape+notify
# step using the project's venv310 interpreter and logs output to a
# timestamped file so failures can be diagnosed after the fact (no one is
# watching a terminal when this fires from sleep at 7 AM).

$ProjectRoot = "D:\Newspaper_OCR_engine"
$Python      = Join-Path $ProjectRoot "venv310\Scripts\python.exe"
$Script      = Join-Path $ProjectRoot "local_scrape_and_notify.py"
$LogDir      = Join-Path $ProjectRoot "logs"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

$Timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$LogFile   = Join-Path $LogDir "scrape_$Timestamp.log"

Set-Location $ProjectRoot

& $Python $Script *>&1 | Tee-Object -FilePath $LogFile

exit $LASTEXITCODE
