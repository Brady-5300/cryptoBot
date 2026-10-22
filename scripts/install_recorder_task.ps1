# Registers a Windows scheduled task that starts the recorder supervisor at boot, whether or not
# anyone is logged in, and restarts the task if it dies. Run once from an elevated PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\install_recorder_task.ps1
# Remove with: Unregister-ScheduledTask -TaskName CryptoBotRecorder -Confirm:$false
param([string]$DataDir = "C:\cryptobot-data")

$script = Join-Path $PSScriptRoot "run_recorder.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -DataDir `"$DataDir`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType S4U -RunLevel Limited

Register-ScheduledTask -TaskName "CryptoBotRecorder" -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force
Write-Host "Registered. Start now with: Start-ScheduledTask -TaskName CryptoBotRecorder"
