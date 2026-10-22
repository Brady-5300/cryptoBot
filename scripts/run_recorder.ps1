# Supervisor loop: keeps the recorder running, restarting with capped backoff if it exits.
# Launched at boot by the scheduled task from install_recorder_task.ps1.
param(
    [string]$Python = "$env:USERPROFILE\.venvs\cryptobot\Scripts\python.exe",
    [string]$DataDir = "C:\cryptobot-data"
)

$ErrorActionPreference = "Continue"
$env:CRYPTOBOT_DATA_DIR = $DataDir
$repo = Split-Path -Parent $PSScriptRoot
$supLog = Join-Path $DataDir "logs\supervisor.log"
New-Item -ItemType Directory -Force (Split-Path $supLog) | Out-Null

$delay = 5
while ($true) {
    $started = Get-Date
    Add-Content $supLog "$(Get-Date -Format o) starting recorder"
    & $Python -m basis.data.recorder 2>&1 | Out-Null   # recorder writes its own log file
    $code = $LASTEXITCODE
    $ranFor = ((Get-Date) - $started).TotalSeconds
    Add-Content $supLog "$(Get-Date -Format o) recorder exited code=$code after ${ranFor}s"
    if ($ranFor -gt 300) { $delay = 5 } else { $delay = [Math]::Min($delay * 2, 300) }
    Start-Sleep -Seconds $delay
}
