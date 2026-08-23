[CmdletBinding()]
param(
    [string]$StatePath = (Join-Path $env:APPDATA 'Smara\desktop.json'),
    [string]$TaskName = 'Smara Desktop Executor'
)

$ErrorActionPreference = 'Stop'
$resolvedState = [System.IO.Path]::GetFullPath($StatePath)
if (-not (Test-Path -LiteralPath $resolvedState -PathType Leaf)) {
    throw "Pair Smara Desktop first; state was not found at $resolvedState"
}

$executable = (Get-Command smara-desktop.exe -ErrorAction Stop).Source
$action = New-ScheduledTaskAction -Execute $executable -Argument "--state `"$resolvedState`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Smara Desktop is installed for the current Windows user and starts at sign-in."
Write-Host "Pause: smara-desktop --state `"$resolvedState`" --pause"
Write-Host "Resume: smara-desktop --state `"$resolvedState`" --resume"

